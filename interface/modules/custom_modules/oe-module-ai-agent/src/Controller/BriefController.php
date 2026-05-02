<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Controller;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\AiAgent\DTO\BriefRequest;
use OpenEMR\Modules\AiAgent\DTO\BriefResponse;
use OpenEMR\Modules\AiAgent\DTO\LlmCallLogEntry;
use OpenEMR\Modules\AiAgent\DTO\LlmCallVerificationStatus;
use OpenEMR\Modules\AiAgent\DTO\ResponseMeta;
use OpenEMR\Modules\AiAgent\Service\AuditLogService;
use OpenEMR\Modules\AiAgent\Service\BearerTokenMinter;
use OpenEMR\Modules\AiAgent\Service\PatientAccessValidator;
use OpenEMR\Modules\AiAgent\Service\SidecarClient;
use Ramsey\Uuid\Uuid;
use Throwable;

final class BriefController
{
    public function __construct(
        private readonly SidecarClient $sidecarClient,
        private readonly PatientAccessValidator $patientAccessValidator,
        private readonly BearerTokenMinter $bearerTokenMinter,
        private readonly AuditLogService $auditLogService,
    ) {
    }

    public static function default(): self
    {
        return new self(
            SidecarClient::fromEnvironment(),
            new PatientAccessValidator(),
            BearerTokenMinter::default(),
            AuditLogService::default(),
        );
    }

    /**
     * @return array<string, mixed>
     */
    public function generate(string $pid, HttpRestRequest $request): array
    {
        $requestId = Uuid::uuid4()->toString();
        $patientId = ctype_digit($pid) ? (int) $pid : 0;
        $userId = $this->resolveUserId();
        $requestHash = $this->hashCanonical(['pid' => $pid, 'request_id' => $requestId]);

        $userUuid = $userId !== 0 ? $this->resolveUserUuid($userId) : null;
        if ($userUuid === null) {
            return $this->finalize(
                requestId: $requestId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'no_authenticated_user']),
                status: LlmCallVerificationStatus::Denied,
                verificationFailures: null,
                httpStatus: 401,
                payload: ['error' => 'no_authenticated_user', 'request_id' => $requestId],
                errorCode: 'no_authenticated_user',
            );
        }

        if (!$this->patientAccessValidator->canRead($pid)) {
            return $this->finalize(
                requestId: $requestId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'forbidden']),
                status: LlmCallVerificationStatus::Denied,
                verificationFailures: null,
                httpStatus: 403,
                payload: ['error' => 'forbidden', 'request_id' => $requestId],
                errorCode: 'forbidden',
            );
        }

        $patientUuid = $this->resolvePatientUuid($pid);
        if ($patientUuid === null) {
            return $this->finalize(
                requestId: $requestId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'patient_not_found']),
                status: LlmCallVerificationStatus::Failed,
                verificationFailures: null,
                httpStatus: 404,
                payload: ['error' => 'patient_not_found', 'request_id' => $requestId],
                errorCode: 'patient_not_found',
            );
        }

        try {
            $bearerToken = $this->bearerTokenMinter->mintForUser(
                $userUuid,
                BearerTokenMinter::FHIR_READ_SCOPES,
            );
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: token mint failed: ' . $e->getMessage());
            return $this->finalize(
                requestId: $requestId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'token_mint_failed']),
                status: LlmCallVerificationStatus::Failed,
                verificationFailures: null,
                httpStatus: 500,
                payload: ['error' => 'token_mint_failed', 'request_id' => $requestId],
                errorCode: 'token_mint_failed',
                errorDetail: substr($e->getMessage(), 0, 1000),
            );
        }

        $fhirBaseUrl = (string) (getenv('AI_AGENT_FHIR_BASE_URL')
            ?: 'http://openemr/apis/default/fhir');

        $briefRequest = new BriefRequest(
            patientUuid: $patientUuid,
            fhirBaseUrl: $fhirBaseUrl,
            bearerToken: $bearerToken,
            requestId: $requestId,
            userId: $userUuid,
            sessionId: $this->resolveSessionId(),
        );

        $startedAt = microtime(true);
        try {
            $briefResponse = $this->sidecarClient->fetchBrief($briefRequest);
        } catch (Throwable $e) {
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            return $this->finalize(
                requestId: $requestId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'sidecar_unreachable']),
                status: LlmCallVerificationStatus::Failed,
                verificationFailures: null,
                httpStatus: 502,
                payload: ['error' => 'sidecar_unreachable', 'request_id' => $requestId],
                errorCode: 'sidecar_unreachable',
                errorDetail: substr($e->getMessage(), 0, 1000),
                latencyMs: $latencyMs,
            );
        }

        $payload = $briefResponse->toArray();
        $errorCode = $this->firstFailureRule($briefResponse->verificationFailures);

        return $this->finalize(
            requestId: $requestId,
            userId: $userId,
            patientId: $patientId,
            modelId: $briefResponse->modelId,
            requestHash: $requestHash,
            responseHash: $this->hashCanonical($payload),
            status: $this->classifyOutcome($briefResponse),
            verificationFailures: $briefResponse->verificationFailures,
            httpStatus: 200,
            payload: $payload,
            meta: $briefResponse->meta,
            errorCode: $errorCode,
        );
    }

    /**
     * @param list<array<string, mixed>>|null $verificationFailures
     * @param array<string, mixed>            $payload
     *
     * @return array<string, mixed>
     */
    private function finalize(
        string $requestId,
        int $userId,
        int $patientId,
        string $modelId,
        string $requestHash,
        string $responseHash,
        LlmCallVerificationStatus $status,
        ?array $verificationFailures,
        int $httpStatus,
        array $payload,
        ?ResponseMeta $meta = null,
        ?string $errorCode = null,
        ?string $errorDetail = null,
        ?int $latencyMs = null,
    ): array {
        // When the sidecar returned a meta block, prefer its numbers.
        // PHP-side latency is only used for failure paths where there's no meta.
        $resolvedLatencyMs = $meta !== null ? $meta->latencyMsTotal : $latencyMs;
        $resolvedCostMicros = $meta?->costUsdMicros();
        $stepsJson = $meta !== null && $meta->steps !== []
            ? json_encode($meta->steps, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)
            : null;

        $this->auditLogService->record(new LlmCallLogEntry(
            requestId: $requestId,
            userId: $userId,
            patientId: $patientId,
            actionType: AuditLogService::ACTION_BRIEF_READ,
            modelId: $modelId,
            promptTokens: $meta !== null ? $meta->promptTokens : 0,
            completionTokens: $meta !== null ? $meta->completionTokens : 0,
            requestHash: $requestHash,
            responseHash: $responseHash,
            verificationFailures: $verificationFailures,
            verificationStatus: $status,
            latencyMs: $resolvedLatencyMs,
            costUsdMicros: $resolvedCostMicros,
            stepsJson: $stepsJson,
            errorCode: $errorCode,
            errorDetail: $errorDetail,
        ));

        if ($httpStatus !== 200) {
            http_response_code($httpStatus);
        }

        return $payload;
    }

    /**
     * @param list<array<string, mixed>> $failures
     */
    private function firstFailureRule(array $failures): ?string
    {
        if ($failures === []) {
            return null;
        }
        $rule = $failures[0]['rule'] ?? null;
        return is_string($rule) && $rule !== '' ? $rule : 'verification_failed';
    }

    private function classifyOutcome(BriefResponse $response): LlmCallVerificationStatus
    {
        $itemCount = count($response->items);
        $failureCount = count($response->verificationFailures);

        if ($itemCount === 0) {
            return LlmCallVerificationStatus::Failed;
        }
        if ($failureCount > 0) {
            return LlmCallVerificationStatus::Partial;
        }

        return LlmCallVerificationStatus::Passed;
    }

    /**
     * @param array<string, mixed> $value
     */
    private function hashCanonical(array $value): string
    {
        return hash(
            'sha256',
            json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
        );
    }

    private function resolveUserId(): int
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $authUserId = $session->get('authUserID');
        if (is_int($authUserId)) {
            return $authUserId;
        }
        if (is_string($authUserId) && ctype_digit($authUserId)) {
            return (int) $authUserId;
        }

        return 0;
    }

    /**
     * Hash the PHP session id for the Langfuse session tag.
     *
     * Why hash: ``session_id()`` is the live cookie token; sending it to an
     * external observability tool would treat it like a credential. The hash
     * is stable for the lifetime of one OpenEMR login, which is exactly the
     * grouping Langfuse's "session" concept expects.
     */
    private function resolveSessionId(): ?string
    {
        $rawSessionId = session_id();
        if (!is_string($rawSessionId) || $rawSessionId === '') {
            return null;
        }

        return substr(hash('sha256', $rawSessionId), 0, 32);
    }

    private function resolveUserUuid(int $userId): ?string
    {
        $row = QueryUtils::fetchRecords(
            'SELECT uuid FROM users WHERE id = ? LIMIT 1',
            [$userId],
            true,
        );
        if ($row === [] || empty($row[0]['uuid'])) {
            return null;
        }

        return UuidRegistry::uuidToString($row[0]['uuid']);
    }

    private function resolvePatientUuid(string $pid): ?string
    {
        $row = QueryUtils::fetchRecords(
            'SELECT uuid FROM patient_data WHERE pid = ? LIMIT 1',
            [$pid],
            true,
        );
        if ($row === [] || empty($row[0]['uuid'])) {
            return null;
        }

        return UuidRegistry::uuidToString($row[0]['uuid']);
    }
}

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
use OpenEMR\Modules\AiAgent\DTO\ChatMessage;
use OpenEMR\Modules\AiAgent\DTO\ChatRequest;
use OpenEMR\Modules\AiAgent\DTO\ChatTurnResponse;
use OpenEMR\Modules\AiAgent\DTO\LlmCallLogEntry;
use OpenEMR\Modules\AiAgent\DTO\LlmCallVerificationStatus;
use OpenEMR\Modules\AiAgent\DTO\ResponseMeta;
use OpenEMR\Modules\AiAgent\Service\AuditLogService;
use OpenEMR\Modules\AiAgent\Service\BearerTokenMinter;
use OpenEMR\Modules\AiAgent\Service\DocumentIngestionRepository;
use OpenEMR\Modules\AiAgent\Service\PatientAccessValidator;
use OpenEMR\Modules\AiAgent\Service\SidecarClient;
use Ramsey\Uuid\Uuid;
use Throwable;

final class ChatController
{
    public function __construct(
        private readonly SidecarClient $sidecarClient,
        private readonly PatientAccessValidator $patientAccessValidator,
        private readonly BearerTokenMinter $bearerTokenMinter,
        private readonly AuditLogService $auditLogService,
        private readonly DocumentIngestionRepository $documentIngestionRepository,
    ) {
    }

    public static function default(): self
    {
        return new self(
            SidecarClient::fromEnvironment(),
            new PatientAccessValidator(),
            BearerTokenMinter::default(),
            AuditLogService::default(),
            new DocumentIngestionRepository(),
        );
    }

    /**
     * @return array<string, mixed>
     */
    public function turn(string $pid, HttpRestRequest $request): array
    {
        $requestId = Uuid::uuid4()->toString();
        $patientId = ctype_digit($pid) ? (int) $pid : 0;
        $userId = $this->resolveUserId();
        $body = $this->decodeBody($request);
        $conversationId = $this->extractConversationId($body);
        $messages = $this->extractMessages($body);
        $requestHash = $this->hashCanonical([
            'pid' => $pid,
            'request_id' => $requestId,
            'conversation_id' => $conversationId,
            'message_count' => count($messages),
        ]);

        $userUuid = $userId !== 0 ? $this->resolveUserUuid($userId) : null;
        if ($userUuid === null) {
            return $this->finalize(
                requestId: $requestId,
                conversationId: $conversationId,
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
                conversationId: $conversationId,
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

        if ($messages === []) {
            return $this->finalize(
                requestId: $requestId,
                conversationId: $conversationId,
                userId: $userId,
                patientId: $patientId,
                modelId: 'unknown',
                requestHash: $requestHash,
                responseHash: $this->hashCanonical(['error' => 'empty_messages']),
                status: LlmCallVerificationStatus::Failed,
                verificationFailures: null,
                httpStatus: 400,
                payload: ['error' => 'empty_messages', 'request_id' => $requestId],
                errorCode: 'empty_messages',
            );
        }

        $patientUuid = $this->resolvePatientUuid($pid);
        if ($patientUuid === null) {
            return $this->finalize(
                requestId: $requestId,
                conversationId: $conversationId,
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
                BearerTokenMinter::CHAT_READ_SCOPES,
            );
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: token mint failed: ' . $e->getMessage());
            return $this->finalize(
                requestId: $requestId,
                conversationId: $conversationId,
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
        $documentContext = $patientId > 0
            ? $this->documentIngestionRepository->documentManifestContextRows($patientId, $patientUuid)
            : [];
        $requestHash = $this->hashCanonical([
            'pid' => $pid,
            'request_id' => $requestId,
            'conversation_id' => $conversationId,
            'message_count' => count($messages),
            'document_context_count' => count($documentContext),
            'document_context_hash' => $this->hashCanonical($documentContext),
        ]);

        $chatRequest = new ChatRequest(
            patientUuid: $patientUuid,
            fhirBaseUrl: $fhirBaseUrl,
            bearerToken: $bearerToken,
            requestId: $requestId,
            conversationId: $conversationId,
            messages: $messages,
            documentContext: $documentContext,
            userId: $userUuid,
        );

        $startedAt = microtime(true);
        try {
            $chatResponse = $this->sidecarClient->fetchChatTurn($chatRequest);
        } catch (Throwable $e) {
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            return $this->finalize(
                requestId: $requestId,
                conversationId: $conversationId,
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

        $payload = $chatResponse->toArray();
        $errorCode = $this->firstFailureRule($chatResponse->verificationFailures);

        return $this->finalize(
            requestId: $requestId,
            conversationId: $chatResponse->conversationId,
            userId: $userId,
            patientId: $patientId,
            modelId: $chatResponse->modelId,
            requestHash: $requestHash,
            responseHash: $this->hashCanonical($payload),
            status: $this->classifyOutcome($chatResponse),
            verificationFailures: $chatResponse->verificationFailures,
            httpStatus: 200,
            payload: $payload,
            meta: $chatResponse->meta,
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
        ?string $conversationId,
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
        $resolvedLatencyMs = $meta !== null ? $meta->latencyMsTotal : $latencyMs;
        $resolvedCostMicros = $meta?->costUsdMicros();
        $stepsJson = $meta !== null && $meta->steps !== []
            ? json_encode($meta->steps, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)
            : null;

        $this->auditLogService->record(new LlmCallLogEntry(
            requestId: $requestId,
            userId: $userId,
            patientId: $patientId,
            actionType: AuditLogService::ACTION_CHAT_TURN,
            modelId: $modelId,
            promptTokens: $meta !== null ? $meta->promptTokens : 0,
            completionTokens: $meta !== null ? $meta->completionTokens : 0,
            requestHash: $requestHash,
            responseHash: $responseHash,
            verificationFailures: $verificationFailures,
            verificationStatus: $status,
            conversationId: $conversationId,
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

    private function classifyOutcome(ChatTurnResponse $response): LlmCallVerificationStatus
    {
        $factCount = count($response->facts);
        $failureCount = count($response->verificationFailures);
        $hasNarrative = trim($response->narrative) !== '';

        if (!$hasNarrative && $factCount === 0) {
            return LlmCallVerificationStatus::Failed;
        }
        if ($failureCount > 0) {
            return LlmCallVerificationStatus::Partial;
        }

        return LlmCallVerificationStatus::Passed;
    }

    /**
     * @return array<string, mixed>
     */
    private function decodeBody(HttpRestRequest $request): array
    {
        $raw = (string) $request->getContent();
        if ($raw === '') {
            return [];
        }
        try {
            /** @var mixed $decoded */
            $decoded = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        } catch (Throwable) {
            return [];
        }

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * @param array<string, mixed> $body
     */
    private function extractConversationId(array $body): ?string
    {
        $value = $body['conversation_id'] ?? null;
        if (!is_string($value) || trim($value) === '') {
            return null;
        }

        return $value;
    }

    /**
     * @param array<string, mixed> $body
     *
     * @return list<ChatMessage>
     */
    private function extractMessages(array $body): array
    {
        $raw = $body['messages'] ?? [];
        if (!is_array($raw)) {
            return [];
        }
        $messages = [];
        foreach ($raw as $item) {
            if (!is_array($item)) {
                continue;
            }
            /** @var array<string, mixed> $item */
            $role = (string) ($item['role'] ?? '');
            $content = (string) ($item['content'] ?? '');
            if ($content === '') {
                continue;
            }
            $messages[] = ChatMessage::fromArray([
                'role' => $role,
                'content' => $content,
            ]);
        }

        return $messages;
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

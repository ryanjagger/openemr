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
use OpenEMR\Modules\AiAgent\Service\BearerTokenMinter;
use OpenEMR\Modules\AiAgent\Service\PatientAccessValidator;
use OpenEMR\Modules\AiAgent\Service\SidecarClient;
use Throwable;

final class BriefController
{
    public function __construct(
        private readonly SidecarClient $sidecarClient,
        private readonly PatientAccessValidator $patientAccessValidator,
        private readonly BearerTokenMinter $bearerTokenMinter,
    ) {
    }

    public static function default(): self
    {
        return new self(
            SidecarClient::fromEnvironment(),
            new PatientAccessValidator(),
            BearerTokenMinter::default(),
        );
    }

    /**
     * @return array<string, mixed>
     */
    public function generate(string $pid, HttpRestRequest $request): array
    {
        $requestId = bin2hex(random_bytes(16));

        if (!$this->patientAccessValidator->canRead($pid)) {
            http_response_code(403);
            return [
                'error' => 'forbidden',
                'request_id' => $requestId,
            ];
        }

        $userUuid = $this->resolveUserUuid();
        if ($userUuid === null) {
            http_response_code(401);
            return ['error' => 'no_authenticated_user', 'request_id' => $requestId];
        }

        $patientUuid = $this->resolvePatientUuid($pid);
        if ($patientUuid === null) {
            http_response_code(404);
            return ['error' => 'patient_not_found', 'request_id' => $requestId];
        }

        try {
            $bearerToken = $this->bearerTokenMinter->mintForUser(
                $userUuid,
                BearerTokenMinter::FHIR_READ_SCOPES,
            );
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: token mint failed: ' . $e->getMessage());
            http_response_code(500);
            return ['error' => 'token_mint_failed', 'request_id' => $requestId];
        }

        $fhirBaseUrl = (string) (getenv('AI_AGENT_FHIR_BASE_URL')
            ?: 'http://openemr/apis/default/fhir');

        $briefRequest = new BriefRequest(
            patientUuid: $patientUuid,
            fhirBaseUrl: $fhirBaseUrl,
            bearerToken: $bearerToken,
            requestId: $requestId,
        );

        try {
            return $this->sidecarClient->fetchBrief($briefRequest)->toArray();
        } catch (Throwable $e) {
            http_response_code(502);
            return [
                'error' => 'sidecar_unreachable',
                'request_id' => $requestId,
            ];
        }
    }

    private function resolveUserUuid(): ?string
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $authUserId = $session->get('authUserID');
        if (!is_int($authUserId) && !is_string($authUserId)) {
            return null;
        }
        $row = QueryUtils::fetchRecords(
            'SELECT uuid FROM users WHERE id = ? LIMIT 1',
            [$authUserId],
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

<?php

/**
 * API endpoints for manual document ingestion into co-pilot chat context.
 *
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
use OpenEMR\Modules\AiAgent\Service\DocumentIngestionLauncher;
use OpenEMR\Modules\AiAgent\Service\DocumentIngestionRepository;
use OpenEMR\Modules\AiAgent\Service\PatientAccessValidator;
use Symfony\Component\HttpFoundation\JsonResponse;
use Throwable;

final class DocumentIngestionController
{
    public function __construct(
        private readonly DocumentIngestionRepository $repository,
        private readonly PatientAccessValidator $patientAccessValidator,
        private readonly DocumentIngestionLauncher $launcher,
    ) {
    }

    public static function default(): self
    {
        return new self(
            new DocumentIngestionRepository(),
            new PatientAccessValidator(),
            new DocumentIngestionLauncher(),
        );
    }

    /**
     * @return JsonResponse
     */
    public function recent(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead($pid)) {
            return $this->error('forbidden', 403);
        }

        $username = $this->username();
        if ($username === '') {
            return $this->error('no_authenticated_user', 401);
        }

        return $this->json([
            'documents' => $this->repository->recentEligibleDocuments(
                patientId: $patientId,
                username: $username,
            ),
        ]);
    }

    /**
     * @return JsonResponse
     */
    public function ingest(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead($pid)) {
            return $this->error('forbidden', 403);
        }

        $userId = $this->userId();
        $username = $this->username();
        if ($userId === 0 || $username === '') {
            return $this->error('no_authenticated_user', 401);
        }

        try {
            $job = $this->repository->createJob(
                patientId: $patientId,
                userId: $userId,
                username: $username,
                selectedDocuments: $this->selectedDocuments($request),
            );
            $this->launcher->launch();
        } catch (\InvalidArgumentException $e) {
            return $this->error('no_eligible_documents', 400, $e->getMessage());
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: document ingestion enqueue failed: ' . $e->getMessage());
            return $this->error('document_ingestion_enqueue_failed', 500);
        }

        return $this->json($job);
    }

    /**
     * @return JsonResponse
     */
    public function job(string $pid, string $jobId, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead($pid)) {
            return $this->error('forbidden', 403);
        }

        try {
            $job = $this->repository->jobStatus($patientId, $jobId);
            if (($job['status'] ?? null) === 'pending') {
                $this->launcher->launch();
            }

            return $this->json($job);
        } catch (Throwable) {
            return $this->error('job_not_found', 404);
        }
    }

    public function indexed(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return $this->error('forbidden', 403);
        }

        return $this->json([
            'documents' => $this->repository->indexedDocumentManifests(
                patientId: $patientId,
                limit: $this->queryInt($request, 'limit', 25),
                documentType: $this->queryString($request, 'document_type'),
                query: $this->queryString($request, 'query'),
            ),
        ]);
    }

    public function indexedFacts(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return $this->error('forbidden', 403);
        }
        $patientUuid = $this->patientUuid($patientId);
        if ($patientUuid === null) {
            return $this->error('patient_not_found', 404);
        }

        return $this->json([
            'facts' => $this->repository->searchIndexedDocumentFacts(
                patientId: $patientId,
                patientUuid: $patientUuid,
                filters: [
                    'document_uuid' => $this->queryString($request, 'document_uuid'),
                    'document_type' => $this->queryString($request, 'document_type'),
                    'fact_type' => $this->queryString($request, 'fact_type'),
                    'query' => $this->queryString($request, 'query'),
                    'observed_on_from' => $this->queryString($request, 'observed_on_from'),
                    'observed_on_to' => $this->queryString($request, 'observed_on_to'),
                    'limit' => $this->queryInt($request, 'limit', 50),
                ],
            ),
        ]);
    }

    /**
     * @return list<array{document_id: int, document_type: string}>
     */
    private function selectedDocuments(HttpRestRequest $request): array
    {
        $body = $this->decodeBody($request);
        $documents = $body['documents'] ?? [];
        if (!is_array($documents)) {
            return [];
        }

        $selected = [];
        foreach ($documents as $document) {
            if (!is_array($document)) {
                continue;
            }
            $id = $document['id'] ?? $document['document_id'] ?? null;
            $type = $document['document_type'] ?? null;
            if (!is_int($id) && !(is_string($id) && ctype_digit($id))) {
                continue;
            }
            if (!is_string($type)) {
                continue;
            }
            $selected[] = [
                'document_id' => (int) $id,
                'document_type' => $type,
            ];
        }

        return $selected;
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
            $decoded = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        } catch (Throwable) {
            return [];
        }

        return is_array($decoded) ? $decoded : [];
    }

    private function patientId(string $pid): int
    {
        if (ctype_digit($pid)) {
            return (int) $pid;
        }
        if (!UuidRegistry::isValidStringUUID($pid)) {
            return 0;
        }
        $rows = QueryUtils::fetchRecords(
            'SELECT `pid` FROM `patient_data` WHERE `uuid` = ? LIMIT 1',
            [UuidRegistry::uuidToBytes($pid)],
            true,
        );

        return $rows === [] ? 0 : (int) $rows[0]['pid'];
    }

    private function patientUuid(int $patientId): ?string
    {
        $rows = QueryUtils::fetchRecords(
            'SELECT `uuid` FROM `patient_data` WHERE `pid` = ? LIMIT 1',
            [$patientId],
            true,
        );
        if ($rows === [] || empty($rows[0]['uuid'])) {
            return null;
        }

        return UuidRegistry::uuidToString($rows[0]['uuid']);
    }

    private function queryString(HttpRestRequest $request, string $key): ?string
    {
        $value = $request->query->get($key);
        if (!is_string($value) && !is_numeric($value)) {
            return null;
        }
        $text = trim((string) $value);

        return $text === '' ? null : $text;
    }

    private function queryInt(HttpRestRequest $request, string $key, int $default): int
    {
        $value = $request->query->get($key);
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value) && ctype_digit($value)) {
            return (int) $value;
        }

        return $default;
    }

    private function userId(): int
    {
        $value = SessionWrapperFactory::getInstance()->getActiveSession()->get('authUserID');
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value) && ctype_digit($value)) {
            return (int) $value;
        }

        return 0;
    }

    private function username(): string
    {
        $value = SessionWrapperFactory::getInstance()->getActiveSession()->get('authUser');

        return is_string($value) ? $value : '';
    }

    /**
     * @param array<string, mixed> $payload
     */
    private function json(array $payload, int $httpStatus = 200): JsonResponse
    {
        return new JsonResponse($payload, $httpStatus);
    }

    private function error(string $code, int $httpStatus, ?string $detail = null): JsonResponse
    {
        $payload = ['error' => $code];
        if ($detail !== null) {
            $payload['detail'] = $detail;
        }

        return $this->json($payload, $httpStatus);
    }
}

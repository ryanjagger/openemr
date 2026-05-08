<?php

/**
 * Background worker that extracts uploaded document content for co-pilot chat.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Modules\AiAgent\DTO\IntakeIngestionRequest;
use OpenEMR\Modules\AiAgent\DTO\LabIngestionRequest;
use OpenEMR\Modules\AiAgent\DTO\LlmCallLogEntry;
use OpenEMR\Modules\AiAgent\DTO\LlmCallVerificationStatus;
use OpenEMR\Modules\AiAgent\DTO\ResponseMeta;
use Ramsey\Uuid\Uuid;
use Throwable;

final class DocumentIngestionWorker
{
    private const DEFAULT_MAX_DOCUMENT_BYTES = 10485760;

    public function __construct(
        private readonly DocumentIngestionRepository $repository,
        private readonly SidecarClient $sidecarClient,
        private readonly AuditLogService $auditLogService,
        private readonly AiLabIngestionService $labIngestionService,
        private readonly AiIntakeIngestionService $intakeIngestionService,
        private readonly int $maxDocumentBytes = self::DEFAULT_MAX_DOCUMENT_BYTES,
    ) {
    }

    public static function default(): self
    {
        return new self(
            new DocumentIngestionRepository(),
            SidecarClient::fromEnvironment(),
            AuditLogService::default(),
            AiLabIngestionService::default(),
            AiIntakeIngestionService::default(),
            self::maxDocumentBytesFromEnvironment(),
        );
    }

    public function processPendingJobs(int $limit = 3): void
    {
        foreach ($this->repository->pendingJobs($limit) as $job) {
            try {
                $this->processJob($job);
            } catch (Throwable $e) {
                $this->repository->markJobFailed((int) $job['id'], $e->getMessage());
            }
        }
    }

    /**
     * @param array<string, mixed> $job
     */
    private function processJob(array $job): void
    {
        $jobId = (int) $job['id'];
        $this->repository->markJobProcessing($jobId);

        foreach ($this->repository->pendingDocumentsForJob($jobId) as $jobDocument) {
            $this->processDocument($job, $jobDocument);
        }

        $this->repository->finalizeJob($jobId);
    }

    /**
     * @param array<string, mixed> $job
     * @param array<string, mixed> $jobDocument
     */
    private function processDocument(array $job, array $jobDocument): void
    {
        $requestId = Uuid::uuid4()->toString();
        $this->repository->markDocumentProcessing((int) $jobDocument['id']);

        try {
            $document = new \Document($jobDocument['document_id']);
            if ($document->is_deleted() || $document->has_expired()) {
                throw new \RuntimeException('Document is deleted or expired.');
            }
            if ((int) $document->get_foreign_id() !== (int) $job['patient_id']) {
                throw new \RuntimeException('Document is no longer attached to the ingestion job patient.');
            }

            $data = $document->get_data();
            if (!is_string($data) || $data === '') {
                throw new \RuntimeException('Document content could not be read.');
            }
            if (strlen($data) > $this->maxDocumentBytes) {
                throw new \RuntimeException('Document exceeds the configured AI ingestion size limit.');
            }

            $requestPayload = [
                'request_id' => $requestId,
                'document_uuid' => (string) $jobDocument['document_uuid'],
                'document_type' => (string) $jobDocument['document_type'],
                'filename' => (string) $jobDocument['filename'],
                'mime_type' => (string) $jobDocument['mimetype'],
                'content_base64' => base64_encode($data),
            ];
            $response = $this->sidecarClient->extractDocument($requestPayload);
            $this->routeExtraction($job, $jobDocument, $response);
            $this->recordAudit(
                requestId: $requestId,
                userId: (int) $job['user_id'],
                patientId: (int) $job['patient_id'],
                requestPayload: [
                    'document_uuid' => $jobDocument['document_uuid'],
                    'document_type' => $jobDocument['document_type'],
                    'filename' => $jobDocument['filename'],
                    'mime_type' => $jobDocument['mimetype'],
                    'content_sha256' => hash('sha256', $data),
                ],
                responsePayload: $response,
                status: LlmCallVerificationStatus::Passed,
                errorCode: null,
                errorDetail: null,
            );
        } catch (Throwable $e) {
            $message = substr($e->getMessage(), 0, 1000);
            $this->repository->markDocumentFailed((int) $jobDocument['id'], $message);
            $this->recordAudit(
                requestId: $requestId,
                userId: (int) $job['user_id'],
                patientId: (int) $job['patient_id'],
                requestPayload: [
                    'document_uuid' => $jobDocument['document_uuid'] ?? null,
                    'document_type' => $jobDocument['document_type'] ?? null,
                    'filename' => $jobDocument['filename'] ?? null,
                    'mime_type' => $jobDocument['mimetype'] ?? null,
                ],
                responsePayload: ['error' => 'document_ingestion_failed'],
                status: LlmCallVerificationStatus::Failed,
                errorCode: 'document_ingestion_failed',
                errorDetail: $message,
            );
        }
    }

    /**
     * Routes the sidecar's extraction output to its native target.
     *
     * Lab reports → procedure_result via AiLabIngestionService (Phase 1).
     * Intake forms → questionnaire_response via AiIntakeIngestionService
     * (Phase 3).
     *
     * @param array<string, mixed> $job
     * @param array<string, mixed> $jobDocument
     * @param array<string, mixed> $response
     */
    private function routeExtraction(array $job, array $jobDocument, array $response): void
    {
        $documentType = (string) ($jobDocument['document_type'] ?? '');
        $modelId = $response['model_id'] ?? null;
        $modelIdString = is_string($modelId) ? $modelId : null;

        if ($documentType === 'lab_report') {
            $request = LabIngestionRequest::fromExtraction($job, $jobDocument, $response);
            $this->labIngestionService->ingest($request);
            $this->repository->markDocumentCompleted((int) $jobDocument['id'], $modelIdString);

            return;
        }

        if ($documentType === 'intake_form') {
            $request = IntakeIngestionRequest::fromExtraction($job, $jobDocument, $response);
            $this->intakeIngestionService->ingest($request);
            $this->repository->markDocumentCompleted((int) $jobDocument['id'], $modelIdString);

            return;
        }

        // The schema constrains document_type to lab_report or intake_form
        // (see DocumentIngestionSchema). Anything else is a programming
        // error in the upstream selection code.
        throw new \RuntimeException("Unsupported AI ingestion document_type: {$documentType}");
    }

    /**
     * @param array<string, mixed> $requestPayload
     * @param array<string, mixed> $responsePayload
     */
    private function recordAudit(
        string $requestId,
        int $userId,
        int $patientId,
        array $requestPayload,
        array $responsePayload,
        LlmCallVerificationStatus $status,
        ?string $errorCode,
        ?string $errorDetail,
    ): void {
        $metaPayload = $responsePayload['meta'] ?? [];
        $meta = is_array($metaPayload) ? ResponseMeta::fromArray($metaPayload) : ResponseMeta::empty();
        $stepsJson = $meta->steps !== []
            ? json_encode($meta->steps, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)
            : null;

        $this->auditLogService->record(new LlmCallLogEntry(
            requestId: $requestId,
            userId: $userId,
            patientId: $patientId,
            actionType: AuditLogService::ACTION_DOCUMENT_EXTRACT,
            modelId: is_string($responsePayload['model_id'] ?? null) ? $responsePayload['model_id'] : 'unknown',
            promptTokens: $meta->promptTokens,
            completionTokens: $meta->completionTokens,
            requestHash: $this->hashCanonical($requestPayload),
            responseHash: $this->hashCanonical($responsePayload),
            verificationFailures: null,
            verificationStatus: $status,
            latencyMs: $meta->latencyMsTotal,
            costUsdMicros: $meta->costUsdMicros(),
            stepsJson: $stepsJson,
            errorCode: $errorCode,
            errorDetail: $errorDetail,
        ));
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

    private static function maxDocumentBytesFromEnvironment(): int
    {
        $raw = getenv('AI_AGENT_MAX_DOCUMENT_BYTES');
        if (!is_string($raw) || !ctype_digit($raw)) {
            return self::DEFAULT_MAX_DOCUMENT_BYTES;
        }

        return max(1, (int) $raw);
    }
}

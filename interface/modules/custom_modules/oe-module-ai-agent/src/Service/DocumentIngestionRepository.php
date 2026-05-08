<?php

/**
 * Persistence for AI document ingestion jobs.
 *
 * Post-Phase-5: AI-extracted facts no longer live in the shadow
 * ai_document_facts / ai_document_source_snippets tables. Lab results land
 * in procedure_result (see AiLabIngestionService) and intake answers land
 * in questionnaire_response (see AiIntakeIngestionService). This repository
 * is now strictly for managing the ingestion-job lifecycle (queue, claim,
 * status, completion) plus the discovery of unindexed documents the user
 * may want to extract.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use DateTimeImmutable;
use DateTimeZone;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;
use Ramsey\Uuid\Uuid;

final class DocumentIngestionRepository
{
    private const ELIGIBLE_MIMETYPES = [
        'application/pdf',
        'image/png',
    ];

    private const DOCUMENT_TYPES = [
        'lab_report',
        'intake_form',
    ];

    private readonly DocumentIngestionSchema $schema;

    public function __construct(?DocumentIngestionSchema $schema = null)
    {
        $this->schema = $schema ?? new DocumentIngestionSchema();
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function recentEligibleDocuments(
        int $patientId,
        string $username,
        int $days = 30,
        int $limit = 25,
    ): array {
        $this->schema->ensureInstalled();

        $since = (new DateTimeImmutable('now', new DateTimeZone('UTC')))
            ->modify('-' . max(1, $days) . ' days')
            ->format('Y-m-d');

        $rows = QueryUtils::fetchRecords(
            'SELECT '
            . 'docs.id, docs.uuid, docs.name, docs.mimetype, docs.docdate, docs.`date`, '
            . 'category.name AS category_name '
            . 'FROM `documents` docs '
            . 'LEFT JOIN `categories_to_documents` ctd ON ctd.document_id = docs.id '
            . 'LEFT JOIN `categories` category ON category.id = ctd.category_id '
            . 'WHERE docs.foreign_id = ? '
            . 'AND docs.deleted = 0 '
            . 'AND (ctd.category_id IS NULL OR category.id IS NOT NULL) '
            . 'AND docs.mimetype IN (?, ?) '
            . 'AND ('
            . '  (docs.docdate IS NOT NULL AND docs.docdate <> "0000-00-00" AND docs.docdate >= ?) '
            . '  OR ((docs.docdate IS NULL OR docs.docdate = "0000-00-00") AND DATE(docs.`date`) >= ?)'
            . ') '
            . 'ORDER BY COALESCE(NULLIF(docs.docdate, "0000-00-00"), DATE(docs.`date`)) DESC, docs.id DESC '
            . 'LIMIT ' . max(1, $limit),
            [
                $patientId,
                self::ELIGIBLE_MIMETYPES[0],
                self::ELIGIBLE_MIMETYPES[1],
                $since,
                $since,
            ],
            true,
        );

        $documents = [];
        $seenDocuments = [];
        foreach ($rows as $row) {
            $documentId = (int) $row['id'];
            if (isset($seenDocuments[$documentId])) {
                continue;
            }
            if (empty($row['uuid'])) {
                continue;
            }
            $document = new \Document($documentId);
            if (!$this->canUseDocument($document, $patientId, $username)) {
                continue;
            }
            $documents[] = $this->documentRowForApi($row);
            $seenDocuments[$documentId] = true;
        }

        return $this->withIndexedStatus($documents, $patientId);
    }

    /**
     * @param list<array{document_id: int, document_type: string}> $selectedDocuments
     *
     * @return array<string, mixed>
     */
    public function createJob(
        int $patientId,
        int $userId,
        string $username,
        array $selectedDocuments,
    ): array {
        $this->schema->ensureInstalled();

        $byId = $this->loadEligibleDocumentsById($patientId, $username);
        $jobDocuments = [];
        $seen = [];

        foreach ($selectedDocuments as $selection) {
            $documentId = $selection['document_id'];
            $documentType = $selection['document_type'];
            if (isset($seen[$documentId]) || !in_array($documentType, self::DOCUMENT_TYPES, true)) {
                continue;
            }
            if (!isset($byId[$documentId])) {
                continue;
            }
            $jobDocuments[] = [
                ...$byId[$documentId],
                'document_type' => $documentType,
            ];
            $seen[$documentId] = true;
        }

        if ($jobDocuments === []) {
            throw new \InvalidArgumentException('No eligible documents were selected for ingestion.');
        }

        $jobUuid = Uuid::uuid4()->toString();
        $jobId = QueryUtils::sqlInsert(
            'INSERT INTO `ai_document_ingestion_jobs` '
            . '(`job_uuid`, `patient_id`, `user_id`, `status`, `document_count`) '
            . 'VALUES (?, ?, ?, "pending", ?)',
            [$jobUuid, $patientId, $userId, count($jobDocuments)],
        );

        foreach ($jobDocuments as $document) {
            QueryUtils::sqlInsert(
                'INSERT INTO `ai_document_ingestion_documents` '
                . '(`job_id`, `patient_id`, `document_id`, `document_uuid`, `document_type`, '
                . '`status`, `filename`, `mimetype`, `docdate`) '
                . 'VALUES (?, ?, ?, ?, ?, "pending", ?, ?, ?)',
                [
                    $jobId,
                    $patientId,
                    $document['id'],
                    $document['uuid'],
                    $document['document_type'],
                    $document['filename'],
                    $document['mimetype'],
                    $document['docdate'],
                ],
            );
        }
        $this->wakeBackgroundService();

        return $this->jobStatus($patientId, $jobUuid);
    }

    /**
     * @return array<string, mixed>
     */
    public function jobStatus(int $patientId, string $jobUuid): array
    {
        $this->schema->ensureInstalled();

        $rows = QueryUtils::fetchRecords(
            'SELECT * FROM `ai_document_ingestion_jobs` WHERE `patient_id` = ? AND `job_uuid` = ? LIMIT 1',
            [$patientId, $jobUuid],
            true,
        );
        if ($rows === []) {
            throw new \RuntimeException('Document ingestion job was not found.');
        }
        $job = $rows[0];
        $documents = QueryUtils::fetchRecords(
            'SELECT `document_id`, `document_uuid`, `document_type`, `status`, `filename`, '
            . '`mimetype`, `docdate`, `model_id`, `error_detail`, `updated_at` '
            . 'FROM `ai_document_ingestion_documents` WHERE `job_id` = ? ORDER BY `id`',
            [$job['id']],
            true,
        );

        return [
            'job_id' => $job['job_uuid'],
            'status' => $job['status'],
            'document_count' => (int) $job['document_count'],
            'processed_count' => (int) $job['processed_count'],
            'failed_count' => (int) $job['failed_count'],
            'error_detail' => $job['error_detail'],
            'created_at' => $job['created_at'],
            'updated_at' => $job['updated_at'],
            'completed_at' => $job['completed_at'],
            'documents' => array_map(
                static fn (array $document): array => [
                    'id' => (int) $document['document_id'],
                    'uuid' => $document['document_uuid'],
                    'document_type' => $document['document_type'],
                    'status' => $document['status'],
                    'filename' => $document['filename'],
                    'mimetype' => $document['mimetype'],
                    'docdate' => $document['docdate'],
                    'model_id' => $document['model_id'],
                    'error_detail' => $document['error_detail'],
                    'updated_at' => $document['updated_at'],
                ],
                $documents,
            ),
        ];
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function pendingJobs(int $limit = 3): array
    {
        $this->schema->ensureInstalled();

        return QueryUtils::fetchRecords(
            'SELECT * FROM `ai_document_ingestion_jobs` '
            . 'WHERE `status` = "pending" ORDER BY `id` ASC LIMIT ' . max(1, $limit),
            [],
            true,
        );
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function pendingDocumentsForJob(int $jobId): array
    {
        $this->schema->ensureInstalled();

        return QueryUtils::fetchRecords(
            'SELECT * FROM `ai_document_ingestion_documents` '
            . 'WHERE `job_id` = ? AND `status` = "pending" ORDER BY `id` ASC',
            [$jobId],
            true,
        );
    }

    public function markJobProcessing(int $jobId): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_jobs` SET `status` = "processing", `updated_at` = NOW() WHERE `id` = ?',
            [$jobId],
        );
    }

    public function markDocumentProcessing(int $jobDocumentId): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_documents` SET `status` = "processing", `updated_at` = NOW() WHERE `id` = ?',
            [$jobDocumentId],
        );
    }

    public function markDocumentFailed(int $jobDocumentId, string $errorDetail): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_documents` '
            . 'SET `status` = "failed", `error_detail` = ?, `updated_at` = NOW() WHERE `id` = ?',
            [substr($errorDetail, 0, 4000), $jobDocumentId],
        );
    }

    public function markDocumentCompleted(int $jobDocumentId, ?string $modelId): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_documents` '
            . 'SET `status` = "completed", `model_id` = ?, `error_detail` = NULL, `updated_at` = NOW() WHERE `id` = ?',
            [$modelId, $jobDocumentId],
        );
    }

    public function markJobFailed(int $jobId, string $errorDetail): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_jobs` '
            . 'SET `status` = "failed", `error_detail` = ?, `completed_at` = NOW(), `updated_at` = NOW() '
            . 'WHERE `id` = ?',
            [substr($errorDetail, 0, 4000), $jobId],
        );
    }

    public function finalizeJob(int $jobId): void
    {
        $rows = QueryUtils::fetchRecords(
            'SELECT '
            . 'SUM(CASE WHEN `status` = "completed" THEN 1 ELSE 0 END) AS completed_count, '
            . 'SUM(CASE WHEN `status` = "failed" THEN 1 ELSE 0 END) AS failed_count, '
            . 'COUNT(*) AS total_count '
            . 'FROM `ai_document_ingestion_documents` WHERE `job_id` = ?',
            [$jobId],
            true,
        );
        $counts = $rows[0] ?? ['completed_count' => 0, 'failed_count' => 0, 'total_count' => 0];
        $completed = (int) $counts['completed_count'];
        $failed = (int) $counts['failed_count'];
        $total = (int) $counts['total_count'];

        $status = match (true) {
            $total === 0 => 'failed',
            $failed === 0 && $completed === $total => 'completed',
            $completed > 0 => 'partial',
            default => 'failed',
        };

        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_jobs` '
            . 'SET `status` = ?, `processed_count` = ?, `failed_count` = ?, '
            . '`completed_at` = NOW(), `updated_at` = NOW() WHERE `id` = ?',
            [$status, $completed, $failed, $jobId],
        );
    }

    private function wakeBackgroundService(): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `background_services` SET `active` = 1, `next_run` = NOW() - INTERVAL 1 SECOND '
            . 'WHERE `name` = ?',
            [DocumentIngestionLauncher::BACKGROUND_SERVICE_NAME],
        );
    }

    /**
     * @return array<int, array<string, mixed>>
     */
    private function loadEligibleDocumentsById(int $patientId, string $username): array
    {
        $documents = [];
        foreach ($this->recentEligibleDocuments($patientId, $username) as $row) {
            $documents[(int) $row['id']] = $row;
        }

        return $documents;
    }

    private function canUseDocument(\Document $document, int $patientId, string $username): bool
    {
        return !$document->is_deleted()
            && !$document->has_expired()
            && (int) $document->get_foreign_id() === $patientId
            && $document->can_access($username);
    }

    /**
     * @param array<string, mixed> $row
     *
     * @return array<string, mixed>
     */
    private function documentRowForApi(array $row): array
    {
        return [
            'id' => (int) $row['id'],
            'uuid' => UuidRegistry::uuidToString($row['uuid']),
            'filename' => (string) $row['name'],
            'mimetype' => (string) $row['mimetype'],
            'docdate' => $this->nullableDate($row['docdate'] ?? null),
            'category_name' => $this->nullableString($row['category_name'] ?? null),
        ];
    }

    /**
     * Decorates each candidate document with whether it has already been
     * ingested for this patient. After Phase 5 the source of truth is just
     * ``ai_document_ingestion_documents.status = 'completed'`` — the
     * downstream native rows (procedure_result / questionnaire_response)
     * are queryable through their own FHIR endpoints, so the chat does not
     * need a fact count or document-type echo here.
     *
     * @param list<array<string, mixed>> $documents
     *
     * @return list<array<string, mixed>>
     */
    private function withIndexedStatus(array $documents, int $patientId): array
    {
        if ($documents === []) {
            return [];
        }

        $documentIds = array_values(array_unique(array_map(
            static fn (array $document): int => (int) $document['id'],
            $documents,
        )));
        $placeholders = implode(', ', array_fill(0, count($documentIds), '?'));
        $rows = QueryUtils::fetchRecords(
            'SELECT did.`document_id`, did.`document_type`, '
            . 'MAX(did.`updated_at`) AS `indexed_at` '
            . 'FROM `ai_document_ingestion_documents` did '
            . 'WHERE did.`patient_id` = ? AND did.`status` = "completed" '
            . 'AND did.`document_id` IN (' . $placeholders . ') '
            . 'GROUP BY did.`document_id`, did.`document_type`',
            [$patientId, ...$documentIds],
            true,
        );

        $statusByDocumentId = [];
        foreach ($rows as $row) {
            $statusByDocumentId[(int) $row['document_id']] = [
                'document_type' => $this->nullableString($row['document_type'] ?? null),
                'indexed_at' => $this->isoDateTime($row['indexed_at'] ?? null),
            ];
        }

        return array_map(function (array $document) use ($statusByDocumentId): array {
            $status = $statusByDocumentId[(int) $document['id']] ?? null;
            $document['already_ingested'] = $status !== null;
            $document['indexed_document_type'] = $status['document_type'] ?? null;
            $document['indexed_at'] = $status['indexed_at'] ?? null;

            return $document;
        }, $documents);
    }

    private function nullableDate(mixed $value): ?string
    {
        if (!is_string($value) || trim($value) === '' || $value === '0000-00-00') {
            return null;
        }

        return substr($value, 0, 10);
    }

    private function nullableString(mixed $value): ?string
    {
        if (!is_string($value) && !is_numeric($value)) {
            return null;
        }
        $text = trim((string) $value);

        return $text === '' ? null : $text;
    }

    private function isoDateTime(mixed $value): string
    {
        if (!is_string($value) || trim($value) === '') {
            return (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format(DATE_ATOM);
        }

        return (new DateTimeImmutable($value, new DateTimeZone('UTC')))->format(DATE_ATOM);
    }
}

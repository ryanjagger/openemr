<?php

/**
 * Persistence for AI document ingestion jobs and extracted source facts.
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

    private const MAX_CONTEXT_FACTS_PER_DOCUMENT = 80;
    private const MAX_CONTEXT_SNIPPETS_PER_FACT = 3;

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

        return $documents;
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

    public function markJobFailed(int $jobId, string $errorDetail): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_jobs` '
            . 'SET `status` = "failed", `error_detail` = ?, `completed_at` = NOW(), `updated_at` = NOW() '
            . 'WHERE `id` = ?',
            [substr($errorDetail, 0, 4000), $jobId],
        );
    }

    /**
     * @param array<string, mixed> $extraction
     */
    public function persistExtraction(array $jobDocument, array $extraction): void
    {
        $patientId = (int) $jobDocument['patient_id'];
        $documentId = (int) $jobDocument['document_id'];
        $documentUuid = (string) $jobDocument['document_uuid'];
        $documentType = (string) $jobDocument['document_type'];

        QueryUtils::sqlStatementThrowException(
            'DELETE snippets FROM `ai_document_source_snippets` snippets '
            . 'LEFT JOIN `ai_document_facts` facts ON facts.id = snippets.fact_id '
            . 'WHERE snippets.patient_id = ? AND snippets.document_id = ?',
            [$patientId, $documentId],
        );
        QueryUtils::sqlStatementThrowException(
            'DELETE FROM `ai_document_facts` WHERE `patient_id` = ? AND `document_id` = ?',
            [$patientId, $documentId],
        );

        $facts = $extraction['facts'] ?? [];
        if (!is_array($facts)) {
            $facts = [];
        }
        foreach ($facts as $fact) {
            if (!is_array($fact)) {
                continue;
            }
            /** @var array<string, mixed> $fact */
            $metadata = $this->factMetadata($fact, $extraction);
            $factId = QueryUtils::sqlInsert(
                'INSERT INTO `ai_document_facts` '
                . '(`patient_id`, `document_id`, `document_uuid`, `document_type`, `fact_type`, '
                . '`label`, `value_text`, `value_numeric`, `unit`, `observed_on`, `metadata_json`) '
                . 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    $patientId,
                    $documentId,
                    $documentUuid,
                    $documentType,
                    $this->stringValue($fact['fact_type'] ?? 'document_fact', 'document_fact'),
                    $this->nullableString($fact['label'] ?? null),
                    $this->nullableString($fact['value_text'] ?? $fact['answer'] ?? null),
                    $this->nullableNumeric($fact['value_numeric'] ?? null),
                    $this->nullableString($fact['unit'] ?? null),
                    $this->nullableDate($fact['observed_on'] ?? null),
                    json_encode($metadata, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
                ],
            );

            $snippets = $fact['source_snippets'] ?? [];
            if (!is_array($snippets)) {
                continue;
            }
            foreach ($snippets as $snippet) {
                if (!is_array($snippet)) {
                    continue;
                }
                $text = $this->nullableString($snippet['text'] ?? null);
                if ($text === null) {
                    continue;
                }
                QueryUtils::sqlInsert(
                    'INSERT INTO `ai_document_source_snippets` '
                    . '(`fact_id`, `patient_id`, `document_id`, `document_uuid`, `page_number`, `snippet_text`, `bbox_json`) '
                    . 'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    [
                        $factId,
                        $patientId,
                        $documentId,
                        $documentUuid,
                        $this->nullableInt($snippet['page_number'] ?? null),
                        $text,
                        $this->jsonOrNull($snippet['bbox'] ?? null),
                    ],
                );
            }
        }

        QueryUtils::sqlStatementThrowException(
            'UPDATE `ai_document_ingestion_documents` '
            . 'SET `status` = "completed", `model_id` = ?, `error_detail` = NULL, `updated_at` = NOW() WHERE `id` = ?',
            [
                $this->nullableString($extraction['model_id'] ?? null),
                $jobDocument['id'],
            ],
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
     * @return list<array<string, mixed>>
     */
    public function documentContextRows(int $patientId, string $patientUuid, int $limit = 12): array
    {
        $this->schema->ensureInstalled();

        $documents = QueryUtils::fetchRecords(
            'SELECT `document_id`, `document_uuid`, `document_type`, `filename`, `mimetype`, '
            . 'MAX(`updated_at`) AS `last_updated` '
            . 'FROM `ai_document_ingestion_documents` '
            . 'WHERE `patient_id` = ? AND `status` = "completed" '
            . 'GROUP BY `document_id`, `document_uuid`, `document_type`, `filename`, `mimetype` '
            . 'ORDER BY MAX(`updated_at`) DESC LIMIT ' . max(1, $limit),
            [$patientId],
            true,
        );

        $rows = [];
        foreach ($documents as $document) {
            $facts = $this->factsForDocument((int) $document['document_id']);
            if ($facts === []) {
                continue;
            }
            $firstSnippet = $this->firstSnippet($facts);
            $rows[] = [
                'resource_type' => 'DocumentReference',
                'resource_id' => $document['document_uuid'],
                'patient_id' => $patientUuid,
                'last_updated' => $this->isoDateTime($document['last_updated']),
                'fields' => [
                    'source' => 'indexed_document',
                    'document_type' => $document['document_type'],
                    'filename' => $document['filename'],
                    'mimetype' => $document['mimetype'],
                    'facts' => $facts,
                ],
                'verbatim_excerpt' => $firstSnippet,
            ];
        }

        return $rows;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function documentManifestContextRows(int $patientId, string $patientUuid, int $limit = 12): array
    {
        return array_map(
            fn (array $document): array => $this->manifestDocumentReferenceRow($document, $patientUuid),
            $this->indexedDocumentManifests($patientId, $limit),
        );
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function indexedDocumentManifests(
        int $patientId,
        int $limit = 25,
        ?string $documentType = null,
        ?string $query = null,
    ): array {
        $this->schema->ensureInstalled();

        $conditions = [
            'did.patient_id = ?',
            'did.status = "completed"',
            'docs.deleted = 0',
        ];
        $binds = [$patientId, $patientId];
        $type = $this->nullableString($documentType);
        if ($type !== null) {
            $conditions[] = 'did.document_type = ?';
            $binds[] = $type;
        }
        $text = $this->nullableString($query);
        if ($text !== null) {
            $like = '%' . $text . '%';
            $conditions[] = '(did.filename LIKE ? OR did.document_uuid LIKE ? OR did.document_type LIKE ?)';
            $binds[] = $like;
            $binds[] = $like;
            $binds[] = $like;
        }

        $documents = QueryUtils::fetchRecords(
            'SELECT did.`document_id`, did.`document_uuid`, did.`document_type`, did.`filename`, '
            . 'did.`mimetype`, did.`docdate`, did.`model_id`, did.`updated_at` AS `last_updated` '
            . 'FROM `ai_document_ingestion_documents` did '
            . 'JOIN ('
            . '  SELECT `document_id`, MAX(`id`) AS `latest_id` '
            . '  FROM `ai_document_ingestion_documents` '
            . '  WHERE `patient_id` = ? AND `status` = "completed" '
            . '  GROUP BY `document_id`'
            . ') latest ON latest.latest_id = did.id '
            . 'JOIN `documents` docs ON docs.id = did.document_id '
            . 'WHERE ' . implode(' AND ', $conditions) . ' '
            . 'ORDER BY did.`updated_at` DESC LIMIT ' . $this->boundedLimit($limit, 25, 100),
            $binds,
            true,
        );

        $manifests = [];
        foreach ($documents as $document) {
            $summary = $this->factSummaryForDocument((int) $document['document_id']);
            if (($summary['fact_count'] ?? 0) <= 0) {
                continue;
            }
            $manifests[] = [
                'document_id' => (int) $document['document_id'],
                'document_uuid' => (string) $document['document_uuid'],
                'document_type' => (string) $document['document_type'],
                'filename' => (string) $document['filename'],
                'mimetype' => (string) $document['mimetype'],
                'docdate' => $this->nullableDate($document['docdate'] ?? null),
                'model_id' => $this->nullableString($document['model_id'] ?? null),
                'last_updated' => $this->isoDateTime($document['last_updated'] ?? null),
                'fact_count' => (int) $summary['fact_count'],
                'document_summary' => $summary['document_summary'] ?? null,
                'extraction_confidence' => $summary['extraction_confidence'] ?? null,
            ];
        }

        return $manifests;
    }

    /**
     * @param array<string, mixed> $filters
     *
     * @return list<array<string, mixed>>
     */
    public function searchIndexedDocumentFacts(
        int $patientId,
        string $patientUuid,
        array $filters,
    ): array {
        $this->schema->ensureInstalled();

        $conditions = [
            'facts.patient_id = ?',
            'did.status = "completed"',
            'docs.deleted = 0',
        ];
        $binds = [$patientId, $patientId];
        $this->appendStringFilter($conditions, $binds, 'facts.document_uuid', $filters['document_uuid'] ?? null);
        $this->appendStringFilter($conditions, $binds, 'facts.document_type', $filters['document_type'] ?? null);
        $this->appendStringFilter($conditions, $binds, 'facts.fact_type', $filters['fact_type'] ?? null);
        $this->appendDateFilter($conditions, $binds, 'facts.observed_on', '>=', $filters['observed_on_from'] ?? null);
        $this->appendDateFilter($conditions, $binds, 'facts.observed_on', '<=', $filters['observed_on_to'] ?? null);

        $query = $this->nullableString($filters['query'] ?? null);
        if ($query !== null) {
            $like = '%' . $query . '%';
            $conditions[] = '('
                . 'facts.label LIKE ? OR facts.value_text LIKE ? OR facts.metadata_json LIKE ? '
                . 'OR EXISTS ('
                . '  SELECT 1 FROM `ai_document_source_snippets` snippets '
                . '  WHERE snippets.fact_id = facts.id AND snippets.snippet_text LIKE ?'
                . ')'
                . ')';
            $binds[] = $like;
            $binds[] = $like;
            $binds[] = $like;
            $binds[] = $like;
        }

        $facts = QueryUtils::fetchRecords(
            'SELECT facts.*, did.`filename`, did.`mimetype`, did.`docdate`, did.`updated_at` AS `document_updated_at` '
            . 'FROM `ai_document_facts` facts '
            . 'JOIN ('
            . '  SELECT `document_id`, MAX(`id`) AS `latest_id` '
            . '  FROM `ai_document_ingestion_documents` '
            . '  WHERE `patient_id` = ? AND `status` = "completed" '
            . '  GROUP BY `document_id`'
            . ') latest ON latest.document_id = facts.document_id '
            . 'JOIN `ai_document_ingestion_documents` did ON did.id = latest.latest_id '
            . 'JOIN `documents` docs ON docs.id = facts.document_id '
            . 'WHERE ' . implode(' AND ', $conditions) . ' '
            . 'ORDER BY COALESCE(facts.`observed_on`, DATE(did.`updated_at`)) DESC, facts.`id` ASC '
            . 'LIMIT ' . $this->boundedLimit($filters['limit'] ?? null, 50, 100),
            $binds,
            true,
        );

        return array_map(
            fn (array $fact): array => $this->indexedFactDocumentReferenceRow($fact, $patientUuid),
            $facts,
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
     * @return list<array<string, mixed>>
     */
    private function factsForDocument(int $documentId): array
    {
        $facts = QueryUtils::fetchRecords(
            'SELECT * FROM `ai_document_facts` WHERE `document_id` = ? ORDER BY `id` ASC '
            . 'LIMIT ' . self::MAX_CONTEXT_FACTS_PER_DOCUMENT,
            [$documentId],
            true,
        );

        return array_map(function (array $fact): array {
            $snippets = QueryUtils::fetchRecords(
                'SELECT `page_number`, `snippet_text`, `bbox_json` '
                . 'FROM `ai_document_source_snippets` WHERE `fact_id` = ? ORDER BY `id` ASC '
                . 'LIMIT ' . self::MAX_CONTEXT_SNIPPETS_PER_FACT,
                [$fact['id']],
                true,
            );
            return [
                'fact_type' => $fact['fact_type'],
                'label' => $fact['label'],
                'value_text' => $fact['value_text'],
                'value_numeric' => $fact['value_numeric'],
                'unit' => $fact['unit'],
                'observed_on' => $fact['observed_on'],
                'metadata' => $this->decodeJson($fact['metadata_json'] ?? null),
                'source_snippets' => array_map(
                    fn (array $snippet): array => [
                        'page_number' => $this->nullableInt($snippet['page_number'] ?? null),
                        'text' => $snippet['snippet_text'],
                        'bbox' => $this->decodeJson($snippet['bbox_json'] ?? null),
                    ],
                    $snippets,
                ),
            ];
        }, $facts);
    }

    /**
     * @param list<array<string, mixed>> $facts
     */
    private function firstSnippet(array $facts): ?string
    {
        foreach ($facts as $fact) {
            $snippets = $fact['source_snippets'] ?? [];
            if (!is_array($snippets)) {
                continue;
            }
            foreach ($snippets as $snippet) {
                if (is_array($snippet) && isset($snippet['text']) && is_string($snippet['text'])) {
                    return substr($snippet['text'], 0, 500);
                }
            }
        }

        return null;
    }

    /**
     * @param array<string, mixed> $document
     *
     * @return array<string, mixed>
     */
    private function manifestDocumentReferenceRow(array $document, string $patientUuid): array
    {
        return [
            'resource_type' => 'DocumentReference',
            'resource_id' => $document['document_uuid'],
            'patient_id' => $patientUuid,
            'last_updated' => $document['last_updated'],
            'fields' => [
                'source' => 'indexed_document_manifest',
                'document_uuid' => $document['document_uuid'],
                'document_type' => $document['document_type'],
                'filename' => $document['filename'],
                'mimetype' => $document['mimetype'],
                'docdate' => $document['docdate'],
                'model_id' => $document['model_id'],
                'fact_count' => $document['fact_count'],
                'document_summary' => $document['document_summary'],
                'extraction_confidence' => $document['extraction_confidence'],
            ],
            'verbatim_excerpt' => $this->nullableString($document['document_summary'] ?? null),
        ];
    }

    /**
     * @param array<string, mixed> $fact
     *
     * @return array<string, mixed>
     */
    private function indexedFactDocumentReferenceRow(array $fact, string $patientUuid): array
    {
        $snippets = $this->sourceSnippetsForFact((int) $fact['id'], self::MAX_CONTEXT_SNIPPETS_PER_FACT);

        return [
            'resource_type' => 'IndexedDocumentFact',
            'resource_id' => (string) $fact['document_uuid'] . '#fact-' . (string) $fact['id'],
            'patient_id' => $patientUuid,
            'last_updated' => $this->isoDateTime($fact['document_updated_at'] ?? $fact['created_at'] ?? null),
            'fields' => [
                'source' => 'indexed_document_fact',
                'document_uuid' => $fact['document_uuid'],
                'document_id' => (int) $fact['document_id'],
                'document_type' => $fact['document_type'],
                'filename' => $fact['filename'],
                'mimetype' => $fact['mimetype'],
                'docdate' => $this->nullableDate($fact['docdate'] ?? null),
                'fact_id' => (int) $fact['id'],
                'fact_type' => $fact['fact_type'],
                'label' => $fact['label'],
                'value_text' => $fact['value_text'],
                'value_numeric' => $fact['value_numeric'],
                'unit' => $fact['unit'],
                'observed_on' => $this->nullableDate($fact['observed_on'] ?? null),
                'metadata' => $this->decodeJson($fact['metadata_json'] ?? null),
                'source_snippets' => $snippets,
            ],
            'verbatim_excerpt' => $this->firstSnippetText($snippets),
        ];
    }

    /**
     * @return array{fact_count: int, document_summary: string|null, extraction_confidence: float|null}
     */
    private function factSummaryForDocument(int $documentId): array
    {
        $countRows = QueryUtils::fetchRecords(
            'SELECT COUNT(*) AS `fact_count` FROM `ai_document_facts` WHERE `document_id` = ?',
            [$documentId],
            true,
        );
        $metadataRows = QueryUtils::fetchRecords(
            'SELECT `metadata_json` FROM `ai_document_facts` WHERE `document_id` = ? ORDER BY `id` ASC LIMIT 1',
            [$documentId],
            true,
        );
        $metadata = $metadataRows !== [] ? $this->decodeJson($metadataRows[0]['metadata_json'] ?? null) : null;
        $confidence = $metadata['extraction_confidence'] ?? null;

        return [
            'fact_count' => (int) ($countRows[0]['fact_count'] ?? 0),
            'document_summary' => $this->nullableString($metadata['document_summary'] ?? null),
            'extraction_confidence' => is_int($confidence) || is_float($confidence) || is_numeric($confidence)
                ? (float) $confidence
                : null,
        ];
    }

    /**
     * @return list<array{page_number: int|null, text: string, bbox: array<string, mixed>|list<mixed>|null}>
     */
    private function sourceSnippetsForFact(int $factId, int $limit): array
    {
        $snippets = QueryUtils::fetchRecords(
            'SELECT `page_number`, `snippet_text`, `bbox_json` '
            . 'FROM `ai_document_source_snippets` WHERE `fact_id` = ? ORDER BY `id` ASC '
            . 'LIMIT ' . $this->boundedLimit($limit, 3, 10),
            [$factId],
            true,
        );

        return array_map(
            fn (array $snippet): array => [
                'page_number' => $this->nullableInt($snippet['page_number'] ?? null),
                'text' => (string) $snippet['snippet_text'],
                'bbox' => $this->decodeJson($snippet['bbox_json'] ?? null),
            ],
            $snippets,
        );
    }

    /**
     * @param list<array<string, mixed>> $snippets
     */
    private function firstSnippetText(array $snippets): ?string
    {
        foreach ($snippets as $snippet) {
            $text = $this->nullableString($snippet['text'] ?? null);
            if ($text !== null) {
                return substr($text, 0, 500);
            }
        }

        return null;
    }

    /**
     * @param array<string, mixed> $fact
     * @param array<string, mixed> $extraction
     *
     * @return array<string, mixed>
     */
    private function factMetadata(array $fact, array $extraction): array
    {
        return [
            'question' => $fact['question'] ?? null,
            'answer' => $fact['answer'] ?? null,
            'reference_range' => $fact['reference_range'] ?? null,
            'flag' => $fact['flag'] ?? null,
            'document_summary' => $extraction['document_summary'] ?? null,
            'extraction_confidence' => $extraction['extraction_confidence'] ?? null,
        ];
    }

    /**
     * @param list<string> $conditions
     * @param list<mixed>  $binds
     */
    private function appendStringFilter(array &$conditions, array &$binds, string $column, mixed $value): void
    {
        $text = $this->nullableString($value);
        if ($text === null) {
            return;
        }
        $conditions[] = $column . ' = ?';
        $binds[] = $text;
    }

    /**
     * @param list<string> $conditions
     * @param list<mixed>  $binds
     */
    private function appendDateFilter(
        array &$conditions,
        array &$binds,
        string $column,
        string $operator,
        mixed $value,
    ): void {
        $date = $this->nullableDate($value);
        if ($date === null) {
            return;
        }
        $conditions[] = $column . ' ' . $operator . ' ?';
        $binds[] = $date;
    }

    private function boundedLimit(mixed $value, int $default, int $max): int
    {
        $limit = $default;
        if (is_int($value)) {
            $limit = $value;
        } elseif (is_string($value) && ctype_digit($value)) {
            $limit = (int) $value;
        }

        return max(1, min($limit, $max));
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

    private function stringValue(mixed $value, string $fallback): string
    {
        $text = $this->nullableString($value);

        return $text ?? $fallback;
    }

    private function nullableNumeric(mixed $value): ?string
    {
        if (!is_int($value) && !is_float($value) && !is_string($value)) {
            return null;
        }
        if (!is_numeric($value)) {
            return null;
        }

        return (string) $value;
    }

    private function nullableInt(mixed $value): ?int
    {
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value) && ctype_digit($value)) {
            return (int) $value;
        }

        return null;
    }

    private function jsonOrNull(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }

        return json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    }

    /**
     * @return array<string, mixed>|list<mixed>|null
     */
    private function decodeJson(mixed $value): array|null
    {
        if (!is_string($value) || trim($value) === '') {
            return null;
        }
        $decoded = json_decode($value, true);

        return is_array($decoded) ? $decoded : null;
    }

    private function isoDateTime(mixed $value): string
    {
        if (!is_string($value) || trim($value) === '') {
            return (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format(DATE_ATOM);
        }

        return (new DateTimeImmutable($value, new DateTimeZone('UTC')))->format(DATE_ATOM);
    }
}

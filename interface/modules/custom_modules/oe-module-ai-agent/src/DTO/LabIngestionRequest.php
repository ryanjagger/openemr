<?php

/**
 * Aggregate input for AiLabIngestionService::ingest().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class LabIngestionRequest
{
    /**
     * @param list<LabFact> $facts
     */
    public function __construct(
        public int $jobId,
        public int $documentId,
        public string $documentUuid,
        public int $patientId,
        public int $userId,
        public string $modelId,
        public ?float $extractionConfidence,
        public array $facts,
    ) {
    }

    /**
     * @param array<string, mixed> $job          Row from ai_document_ingestion_jobs.
     * @param array<string, mixed> $jobDocument  Row from ai_document_ingestion_documents.
     * @param array<string, mixed> $extraction   Sidecar response payload.
     */
    public static function fromExtraction(array $job, array $jobDocument, array $extraction): self
    {
        $facts = [];
        $rawFacts = $extraction['facts'] ?? [];
        if (is_array($rawFacts)) {
            foreach ($rawFacts as $fact) {
                if (!is_array($fact)) {
                    continue;
                }
                if (($fact['fact_type'] ?? null) !== 'lab_result') {
                    continue;
                }
                $facts[] = LabFact::fromExtraction($fact);
            }
        }

        $confidence = $extraction['extraction_confidence'] ?? null;
        $modelId = $extraction['model_id'] ?? null;

        return new self(
            jobId: (int) $job['id'],
            documentId: (int) $jobDocument['document_id'],
            documentUuid: (string) $jobDocument['document_uuid'],
            patientId: (int) $job['patient_id'],
            userId: (int) $job['user_id'],
            modelId: is_string($modelId) ? $modelId : 'unknown',
            extractionConfidence: (is_int($confidence) || is_float($confidence)) ? (float) $confidence : null,
            facts: $facts,
        );
    }
}

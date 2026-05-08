<?php

/**
 * Aggregate input for AiIntakeIngestionService::ingest().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class IntakeIngestionRequest
{
    /**
     * @param list<IntakeAnswer> $answers
     */
    public function __construct(
        public int $jobId,
        public int $documentId,
        public string $documentUuid,
        public int $patientId,
        public int $userId,
        public string $filename,
        public string $modelId,
        public ?float $extractionConfidence,
        public array $answers,
    ) {
    }

    /**
     * @param array<string, mixed> $job
     * @param array<string, mixed> $jobDocument
     * @param array<string, mixed> $extraction
     */
    public static function fromExtraction(array $job, array $jobDocument, array $extraction): self
    {
        $answers = [];
        $rawFacts = $extraction['facts'] ?? [];
        if (is_array($rawFacts)) {
            $position = 0;
            foreach ($rawFacts as $fact) {
                if (!is_array($fact)) {
                    continue;
                }
                if (($fact['fact_type'] ?? null) !== 'intake_answer') {
                    continue;
                }
                $position++;
                $answer = IntakeAnswer::fromExtraction($fact, $position);
                if ($answer !== null) {
                    $answers[] = $answer;
                }
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
            filename: (string) ($jobDocument['filename'] ?? 'intake.pdf'),
            modelId: is_string($modelId) ? $modelId : 'unknown',
            extractionConfidence: (is_int($confidence) || is_float($confidence)) ? (float) $confidence : null,
            answers: $answers,
        );
    }
}

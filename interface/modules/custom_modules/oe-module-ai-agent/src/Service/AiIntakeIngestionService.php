<?php

/**
 * Lands AI-extracted intake-form answers in OpenEMR's native FHIR
 * Questionnaire / QuestionnaireResponse tables.
 *
 * Each upload produces:
 *   - One row in `questionnaire_repository` (the Questionnaire definition,
 *     category='ai_extracted', name pinned to the source filename + a short
 *     hash of the document UUID so re-uploads do not collide).
 *   - One row in `questionnaire_response` (the patient's answers, status
 *     'completed', creator_user_id = the chat user who triggered ingestion).
 *   - One row per answer in `ai_questionnaire_response_provenance`, keyed by
 *     (questionnaire_response_id, link_id), carrying page/bbox/snippet/
 *     confidence/model for chat-side citation.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Modules\AiAgent\DTO\IntakeAnswer;
use OpenEMR\Modules\AiAgent\DTO\IntakeIngestionRequest;
use OpenEMR\Services\QuestionnaireResponseService;
use OpenEMR\Services\QuestionnaireService;
use RuntimeException;

final class AiIntakeIngestionService
{
    public const QUESTIONNAIRE_CATEGORY = 'ai_extracted';
    public const RESPONSE_STATUS = 'completed';

    public function __construct(
        private readonly QuestionnaireService $questionnaireService,
        private readonly QuestionnaireResponseService $questionnaireResponseService,
    ) {
    }

    public static function default(): self
    {
        return new self(
            new QuestionnaireService(),
            new QuestionnaireResponseService(),
        );
    }

    /**
     * @return array{questionnaire_repository_id:int,questionnaire_response_id:int,answer_count:int}
     */
    public function ingest(IntakeIngestionRequest $request): array
    {
        if ($request->answers === []) {
            return [
                'questionnaire_repository_id' => 0,
                'questionnaire_response_id' => 0,
                'answer_count' => 0,
            ];
        }

        $answers = $this->dedupeAnswers($request->answers);
        $name = $this->buildQuestionnaireName($request);

        $questionnaireJson = $this->buildQuestionnaireJson($name, $answers);
        $repoId = $this->questionnaireService->saveQuestionnaireResource(
            $questionnaireJson,
            $name,
            null,
            null,
            null,
            'Questionnaire',
            self::QUESTIONNAIRE_CATEGORY,
            $request->userId,
        );
        if (!is_int($repoId) || $repoId <= 0) {
            throw new RuntimeException('Failed to save AI-extracted Questionnaire definition.');
        }

        $repoRow = QueryUtils::fetchRecords(
            'SELECT `questionnaire_id`, `questionnaire` FROM `questionnaire_repository` WHERE `id` = ? LIMIT 1',
            [$repoId],
        );
        if ($repoRow === []) {
            throw new RuntimeException("Questionnaire repository row {$repoId} disappeared after insert.");
        }
        $questionnaireId = (string) $repoRow[0]['questionnaire_id'];
        $questionnaireContent = (string) $repoRow[0]['questionnaire'];

        $responseJson = $this->buildResponseJson($questionnaireId, $request, $answers);
        $saveResult = $this->questionnaireResponseService->saveQuestionnaireResponse(
            $responseJson,
            $request->patientId,
            null,
            null,
            null,
            $questionnaireContent,
            $questionnaireId,
            null,
            false,
            [],
            $request->userId,
        );
        $responseId = is_array($saveResult) ? (int) ($saveResult['id'] ?? 0) : 0;
        if ($responseId <= 0) {
            throw new RuntimeException('Failed to save AI-extracted QuestionnaireResponse.');
        }

        $this->writeProvenance($request, $responseId, $answers);

        return [
            'questionnaire_repository_id' => $repoId,
            'questionnaire_response_id' => $responseId,
            'answer_count' => count($answers),
        ];
    }

    /**
     * @param list<IntakeAnswer> $answers
     * @return list<IntakeAnswer>
     */
    private function dedupeAnswers(array $answers): array
    {
        $seen = [];
        $out = [];
        $position = 0;
        foreach ($answers as $answer) {
            $position++;
            $linkId = $answer->linkId;
            if (isset($seen[$linkId])) {
                $linkId = sprintf('%s-%d', $answer->linkId, $position);
            }
            $seen[$linkId] = true;

            if ($linkId === $answer->linkId) {
                $out[] = $answer;
                continue;
            }
            $out[] = new IntakeAnswer(
                linkId: $linkId,
                question: $answer->question,
                answerType: $answer->answerType,
                answerText: $answer->answerText,
                answerOptions: $answer->answerOptions,
                sourceSnippets: $answer->sourceSnippets,
            );
        }

        return $out;
    }

    private function buildQuestionnaireName(IntakeIngestionRequest $request): string
    {
        $shortUuid = substr($request->documentUuid, 0, 8);
        $base = trim($request->filename) !== '' ? $request->filename : 'intake.pdf';

        return "AI-extracted intake: {$base} [{$shortUuid}]";
    }

    /**
     * @param list<IntakeAnswer> $answers
     */
    private function buildQuestionnaireJson(string $name, array $answers): string
    {
        $items = [];
        foreach ($answers as $answer) {
            $item = [
                'linkId' => $answer->linkId,
                'text' => $answer->question,
                'type' => $answer->answerType,
            ];
            if ($answer->answerType === 'choice' && $answer->answerOptions !== []) {
                $item['answerOption'] = array_map(
                    static fn (string $opt): array => ['valueString' => $opt],
                    $answer->answerOptions,
                );
            }
            $items[] = $item;
        }

        return (string) json_encode(
            [
                'resourceType' => 'Questionnaire',
                'status' => 'active',
                'title' => $name,
                'name' => $this->slugifyName($name),
                'item' => $items,
            ],
            JSON_THROW_ON_ERROR,
        );
    }

    /**
     * @param list<IntakeAnswer> $answers
     */
    private function buildResponseJson(
        string $questionnaireId,
        IntakeIngestionRequest $request,
        array $answers,
    ): string {
        $items = [];
        foreach ($answers as $answer) {
            $items[] = [
                'linkId' => $answer->linkId,
                'text' => $answer->question,
                'answer' => [$this->buildAnswerValue($answer)],
            ];
        }

        return (string) json_encode(
            [
                'resourceType' => 'QuestionnaireResponse',
                'status' => self::RESPONSE_STATUS,
                'questionnaire' => 'Questionnaire/' . $questionnaireId,
                'item' => $items,
            ],
            JSON_THROW_ON_ERROR,
        );
    }

    /**
     * @return array<string, mixed>
     */
    private function buildAnswerValue(IntakeAnswer $answer): array
    {
        $text = $answer->answerText ?? '';

        return match ($answer->answerType) {
            'boolean' => ['valueBoolean' => $this->parseBoolean($text)],
            'integer' => ['valueInteger' => (int) $text],
            'decimal' => ['valueDecimal' => (float) $text],
            'date' => ['valueDate' => $text],
            // 'string' and 'choice' both serialize as valueString. FHIR allows
            // valueString for choice answers when the Questionnaire's
            // answerOption uses valueString.
            default => ['valueString' => $text],
        };
    }

    private function parseBoolean(string $value): bool
    {
        $normalized = strtolower(trim($value));

        return in_array($normalized, ['yes', 'y', 'true', '1', 'positive', 'present'], true);
    }

    private function slugifyName(string $name): string
    {
        $slug = strtolower((string) preg_replace('/[^A-Za-z0-9]+/', '_', $name));

        return trim($slug, '_') ?: 'ai_extracted_intake';
    }

    /**
     * @param list<IntakeAnswer> $answers
     */
    private function writeProvenance(
        IntakeIngestionRequest $request,
        int $responseId,
        array $answers,
    ): void {
        foreach ($answers as $answer) {
            $primarySnippet = $answer->sourceSnippets[0] ?? null;
            QueryUtils::sqlInsert(
                'INSERT INTO `ai_questionnaire_response_provenance` '
                . '(`questionnaire_response_id`, `link_id`, `document_id`, `extraction_job_id`, '
                . '`page_number`, `bbox_json`, `snippet_text`, '
                . '`extraction_confidence`, `extraction_model`) '
                . 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    $responseId,
                    $answer->linkId,
                    $request->documentId,
                    $request->jobId,
                    $primarySnippet?->pageNumber,
                    $primarySnippet !== null && $primarySnippet->bbox !== null
                        ? json_encode($primarySnippet->bbox, JSON_THROW_ON_ERROR)
                        : null,
                    $primarySnippet?->text,
                    $request->extractionConfidence,
                    $request->modelId,
                ],
            );
        }
    }
}

<?php

/**
 * AI-extracted answer to one intake-form question, ready to land in
 * a FHIR Questionnaire/QuestionnaireResponse pair.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class IntakeAnswer
{
    public const ANSWER_TYPES = ['string', 'boolean', 'choice', 'integer', 'decimal', 'date'];

    /**
     * @param list<string>           $answerOptions Available choices when answerType='choice'.
     * @param list<LabSourceSnippet> $sourceSnippets
     */
    public function __construct(
        public string $linkId,
        public string $question,
        public string $answerType,
        public ?string $answerText,
        public array $answerOptions,
        public array $sourceSnippets,
    ) {
    }

    /**
     * @param array<string, mixed> $fact     Raw fact dict from sidecar response.
     * @param int                  $position 1-based position in the document, used
     *                                       as a fallback link_id when the LLM
     *                                       did not emit one.
     */
    public static function fromExtraction(array $fact, int $position): ?self
    {
        $question = self::nullableString($fact['question'] ?? $fact['label'] ?? null);
        if ($question === null) {
            return null;
        }
        $answerText = self::nullableString($fact['answer'] ?? $fact['value_text'] ?? null);
        $linkId = self::nullableString($fact['link_id'] ?? null) ?? sprintf('q%d', $position);
        $answerType = self::nullableString($fact['answer_type'] ?? null);
        if ($answerType === null || !in_array($answerType, self::ANSWER_TYPES, true)) {
            $answerType = 'string';
        }

        $rawOptions = $fact['answer_options'] ?? [];
        $options = [];
        if (is_array($rawOptions)) {
            foreach ($rawOptions as $option) {
                if (is_string($option)) {
                    $trimmed = trim($option);
                    if ($trimmed !== '') {
                        $options[] = $trimmed;
                    }
                }
            }
        }

        $snippets = [];
        $rawSnippets = $fact['source_snippets'] ?? [];
        if (is_array($rawSnippets)) {
            foreach ($rawSnippets as $snippet) {
                if (!is_array($snippet)) {
                    continue;
                }
                $text = $snippet['text'] ?? null;
                if (!is_string($text) || $text === '') {
                    continue;
                }
                $page = $snippet['page_number'] ?? null;
                $bbox = $snippet['bbox'] ?? null;
                $snippets[] = new LabSourceSnippet(
                    pageNumber: is_int($page) ? $page : null,
                    text: $text,
                    bbox: is_array($bbox) ? self::normalizeBbox($bbox) : null,
                );
            }
        }

        return new self(
            linkId: $linkId,
            question: $question,
            answerType: $answerType,
            answerText: $answerText,
            answerOptions: $options,
            sourceSnippets: $snippets,
        );
    }

    /**
     * @param array<string, mixed> $bbox
     * @return array<string, float>
     */
    private static function normalizeBbox(array $bbox): array
    {
        $out = [];
        foreach ($bbox as $key => $value) {
            if (is_string($key) && (is_int($value) || is_float($value))) {
                $out[$key] = (float) $value;
            }
        }

        return $out;
    }

    private static function nullableString(mixed $value): ?string
    {
        if (!is_string($value)) {
            return null;
        }
        $trimmed = trim($value);

        return $trimmed === '' ? null : $trimmed;
    }
}

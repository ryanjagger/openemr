<?php

/**
 * AI-extracted lab fact, as supplied by the document-extraction sidecar.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class LabFact
{
    /**
     * @param list<LabSourceSnippet> $sourceSnippets
     */
    public function __construct(
        public string $label,
        public ?string $valueText,
        public ?float $valueNumeric,
        public ?string $unit,
        public ?string $referenceRange,
        public ?string $flag,
        public ?string $observedOn,
        public array $sourceSnippets,
    ) {
    }

    /**
     * @param array<string, mixed> $fact Raw fact dict from the sidecar response.
     */
    public static function fromExtraction(array $fact): self
    {
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

        $label = $fact['label'] ?? null;
        if (!is_string($label) || trim($label) === '') {
            $label = 'AI extracted result';
        }

        return new self(
            label: $label,
            valueText: self::nullableString($fact['value_text'] ?? null),
            valueNumeric: self::nullableFloat($fact['value_numeric'] ?? null),
            unit: self::nullableString($fact['unit'] ?? null),
            referenceRange: self::nullableString($fact['reference_range'] ?? null),
            flag: self::nullableString($fact['flag'] ?? null),
            observedOn: self::nullableString($fact['observed_on'] ?? null),
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

    private static function nullableFloat(mixed $value): ?float
    {
        if (is_int($value) || is_float($value)) {
            return (float) $value;
        }

        return null;
    }
}

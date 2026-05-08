<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class BriefItem
{
    /**
     * @param list<string>           $verbatimExcerpts
     * @param list<Citation>         $citations
     * @param list<SourceProvenance> $sourceProvenance
     */
    public function __construct(
        public string $type,
        public string $text,
        public array $verbatimExcerpts,
        public array $citations,
        public array $sourceProvenance,
        public bool $verified,
        public ?int $anchor = null,
    ) {
    }

    /**
     * @param array{
     *     type?: string,
     *     text?: string,
     *     verbatim_excerpts?: list<string>,
     *     citations?: list<array{resource_type?: string, resource_id?: string}>,
     *     source_provenance?: list<array{
     *         resource_type?: string,
     *         resource_id?: string,
     *         document_id?: string,
     *         page?: int|null,
     *         bbox?: array<mixed>|null,
     *         snippet?: string|null,
     *         confidence?: float|int|null,
     *         model?: string|null,
     *         link_id?: string|null
     *     }>,
     *     verified?: bool,
     *     anchor?: int|null
     * } $payload
     */
    public static function fromArray(array $payload): self
    {
        $citations = [];
        foreach ($payload['citations'] ?? [] as $citation) {
            $citations[] = Citation::fromArray($citation);
        }
        $sourceProvenance = [];
        foreach ($payload['source_provenance'] ?? [] as $source) {
            $sourceProvenance[] = SourceProvenance::fromArray($source);
        }

        $rawAnchor = $payload['anchor'] ?? null;
        $anchor = is_int($rawAnchor) ? $rawAnchor : null;

        return new self(
            type: (string) ($payload['type'] ?? ''),
            text: (string) ($payload['text'] ?? ''),
            verbatimExcerpts: array_values(array_map('strval', $payload['verbatim_excerpts'] ?? [])),
            citations: $citations,
            sourceProvenance: $sourceProvenance,
            verified: (bool) ($payload['verified'] ?? false),
            anchor: $anchor,
        );
    }
}

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
     * @param list<string>   $verbatimExcerpts
     * @param list<Citation> $citations
     */
    public function __construct(
        public string $type,
        public string $text,
        public array $verbatimExcerpts,
        public array $citations,
        public bool $verified,
    ) {
    }

    /**
     * @param array{
     *     type?: string,
     *     text?: string,
     *     verbatim_excerpts?: list<string>,
     *     citations?: list<array{resource_type?: string, resource_id?: string}>,
     *     verified?: bool
     * } $payload
     */
    public static function fromArray(array $payload): self
    {
        $citations = [];
        foreach ($payload['citations'] ?? [] as $citation) {
            $citations[] = Citation::fromArray($citation);
        }

        return new self(
            type: (string) ($payload['type'] ?? ''),
            text: (string) ($payload['text'] ?? ''),
            verbatimExcerpts: array_values(array_map('strval', $payload['verbatim_excerpts'] ?? [])),
            citations: $citations,
            verified: (bool) ($payload['verified'] ?? false),
        );
    }
}

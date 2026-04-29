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

final readonly class BriefResponse
{
    /**
     * @param list<BriefItem>            $items
     * @param list<array<string, mixed>> $verificationFailures
     */
    public function __construct(
        public string $requestId,
        public string $modelId,
        public array $items,
        public array $verificationFailures,
    ) {
    }

    /**
     * @param array{
     *     request_id?: string,
     *     model_id?: string,
     *     items?: list<array<string, mixed>>,
     *     verification_failures?: list<array<string, mixed>>
     * } $payload
     */
    public static function fromArray(array $payload): self
    {
        $items = [];
        foreach ($payload['items'] ?? [] as $rawItem) {
            /** @var array<string, mixed> $rawItem */
            $items[] = BriefItem::fromArray($rawItem);
        }

        return new self(
            requestId: (string) ($payload['request_id'] ?? ''),
            modelId: (string) ($payload['model_id'] ?? 'unknown'),
            items: $items,
            verificationFailures: array_values($payload['verification_failures'] ?? []),
        );
    }

    /**
     * @return array{
     *     request_id: string,
     *     model_id: string,
     *     items: list<array{
     *         type: string,
     *         text: string,
     *         verbatim_excerpts: list<string>,
     *         citations: list<array{resource_type: string, resource_id: string}>,
     *         verified: bool
     *     }>,
     *     verification_failures: list<array<string, mixed>>
     * }
     */
    public function toArray(): array
    {
        return [
            'request_id' => $this->requestId,
            'model_id' => $this->modelId,
            'items' => array_map(
                static fn (BriefItem $item): array => [
                    'type' => $item->type,
                    'text' => $item->text,
                    'verbatim_excerpts' => $item->verbatimExcerpts,
                    'citations' => array_map(
                        static fn (Citation $c): array => [
                            'resource_type' => $c->resourceType,
                            'resource_id' => $c->resourceId,
                        ],
                        $item->citations,
                    ),
                    'verified' => $item->verified,
                ],
                $this->items,
            ),
            'verification_failures' => $this->verificationFailures,
        ];
    }
}

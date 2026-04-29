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

final readonly class ChatTurnResponse
{
    /**
     * @param list<BriefItem>            $facts
     * @param list<array<string, mixed>> $verificationFailures
     */
    public function __construct(
        public string $requestId,
        public string $conversationId,
        public string $modelId,
        public string $narrative,
        public array $facts,
        public array $verificationFailures,
        public ResponseMeta $meta,
    ) {
    }

    /**
     * @param array{
     *     request_id?: string,
     *     conversation_id?: string,
     *     model_id?: string,
     *     narrative?: string,
     *     facts?: list<array<string, mixed>>,
     *     verification_failures?: list<array<string, mixed>>,
     *     meta?: array<string, mixed>
     * } $payload
     */
    public static function fromArray(array $payload): self
    {
        $facts = [];
        foreach ($payload['facts'] ?? [] as $rawFact) {
            /** @var array<string, mixed> $rawFact */
            $facts[] = BriefItem::fromArray($rawFact);
        }

        $rawMeta = $payload['meta'] ?? null;
        $meta = is_array($rawMeta) ? ResponseMeta::fromArray($rawMeta) : ResponseMeta::empty();

        return new self(
            requestId: (string) ($payload['request_id'] ?? ''),
            conversationId: (string) ($payload['conversation_id'] ?? ''),
            modelId: (string) ($payload['model_id'] ?? 'unknown'),
            narrative: (string) ($payload['narrative'] ?? ''),
            facts: $facts,
            verificationFailures: array_values($payload['verification_failures'] ?? []),
            meta: $meta,
        );
    }

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'request_id' => $this->requestId,
            'conversation_id' => $this->conversationId,
            'model_id' => $this->modelId,
            'narrative' => $this->narrative,
            'facts' => array_map(
                static fn (BriefItem $fact): array => [
                    'type' => $fact->type,
                    'text' => $fact->text,
                    'verbatim_excerpts' => $fact->verbatimExcerpts,
                    'citations' => array_map(
                        static fn (Citation $c): array => [
                            'resource_type' => $c->resourceType,
                            'resource_id' => $c->resourceId,
                        ],
                        $fact->citations,
                    ),
                    'verified' => $fact->verified,
                    'anchor' => $fact->anchor,
                ],
                $this->facts,
            ),
            'verification_failures' => $this->verificationFailures,
            'meta' => $this->meta->toArray(),
        ];
    }
}

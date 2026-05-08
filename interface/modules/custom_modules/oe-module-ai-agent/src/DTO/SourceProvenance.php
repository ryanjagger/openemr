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

final readonly class SourceProvenance
{
    private const BBOX_SOURCES = ['text_layer', 'ocr', 'llm'];
    private const BBOX_TARGETS = ['row', 'value', 'field', 'snippet'];

    /**
     * @param array<mixed>|null $bbox
     */
    public function __construct(
        public string $resourceType,
        public string $resourceId,
        public string $documentId,
        public ?int $page,
        public ?array $bbox,
        public ?string $snippet,
        public ?float $confidence,
        public ?string $model,
        public ?string $linkId,
        public ?string $bboxSource = null,
        public ?float $bboxConfidence = null,
        public ?string $bboxTarget = null,
    ) {
    }

    /**
     * @param array{
     *     resource_type?: string,
     *     resource_id?: string,
     *     document_id?: string,
     *     page?: int|null,
     *     bbox?: array<mixed>|null,
     *     snippet?: string|null,
     *     confidence?: float|int|null,
     *     model?: string|null,
     *     link_id?: string|null,
     *     bbox_source?: string|null,
     *     bbox_confidence?: float|int|null,
     *     bbox_target?: string|null
     * } $payload
     */
    public static function fromArray(array $payload): self
    {
        $rawPage = $payload['page'] ?? null;
        $rawConfidence = $payload['confidence'] ?? null;
        $rawBboxConfidence = $payload['bbox_confidence'] ?? null;

        return new self(
            resourceType: (string) ($payload['resource_type'] ?? ''),
            resourceId: (string) ($payload['resource_id'] ?? ''),
            documentId: (string) ($payload['document_id'] ?? ''),
            page: is_int($rawPage) ? $rawPage : null,
            bbox: is_array($payload['bbox'] ?? null) ? $payload['bbox'] : null,
            snippet: self::nullableString($payload['snippet'] ?? null),
            confidence: is_int($rawConfidence) || is_float($rawConfidence) ? (float) $rawConfidence : null,
            model: self::nullableString($payload['model'] ?? null),
            linkId: self::nullableString($payload['link_id'] ?? null),
            bboxSource: self::nullableEnum($payload['bbox_source'] ?? null, self::BBOX_SOURCES),
            bboxConfidence: is_int($rawBboxConfidence) || is_float($rawBboxConfidence)
                ? (float) $rawBboxConfidence
                : null,
            bboxTarget: self::nullableEnum($payload['bbox_target'] ?? null, self::BBOX_TARGETS),
        );
    }

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'resource_type' => $this->resourceType,
            'resource_id' => $this->resourceId,
            'document_id' => $this->documentId,
            'page' => $this->page,
            'bbox' => $this->bbox,
            'snippet' => $this->snippet,
            'confidence' => $this->confidence,
            'model' => $this->model,
            'link_id' => $this->linkId,
            'bbox_source' => $this->bboxSource,
            'bbox_confidence' => $this->bboxConfidence,
            'bbox_target' => $this->bboxTarget,
        ];
    }

    private static function nullableString(?string $value): ?string
    {
        return $value !== null && $value !== '' ? $value : null;
    }

    /**
     * @param list<string> $allowed
     */
    private static function nullableEnum(mixed $value, array $allowed): ?string
    {
        if (!is_string($value) || !in_array($value, $allowed, true)) {
            return null;
        }

        return $value;
    }
}

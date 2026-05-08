<?php

/**
 * Source-snippet provenance for a single AI-extracted lab fact.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

final readonly class LabSourceSnippet
{
    public const BBOX_SOURCES = ['text_layer', 'ocr', 'llm'];
    public const BBOX_TARGETS = ['row', 'value', 'field', 'snippet'];

    /**
     * @param array<string, float>|null $bbox            Normalized {x, y, width, height} in page
     *                                                   coordinates. Stored as JSON; the chat layer
     *                                                   is the only consumer.
     * @param string|null               $bboxSource      One of self::BBOX_SOURCES, identifying the
     *                                                   localization strategy that produced $bbox.
     * @param float|null                $bboxConfidence  0..1 score from the localizer.
     * @param string|null               $bboxTarget      One of self::BBOX_TARGETS, identifying what
     *                                                   the bbox encloses (lab row, intake field,
     *                                                   bare snippet, etc).
     */
    public function __construct(
        public ?int $pageNumber,
        public string $text,
        public ?array $bbox,
        public ?string $bboxSource = null,
        public ?float $bboxConfidence = null,
        public ?string $bboxTarget = null,
    ) {
    }
}

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
    /**
     * @param array<string, float>|null $bbox Bounding box, typically {x, y, width, height}
     *                                        in the document's coordinate space. Stored as
     *                                        JSON; the chat layer is the only consumer.
     */
    public function __construct(
        public ?int $pageNumber,
        public string $text,
        public ?array $bbox,
    ) {
    }
}

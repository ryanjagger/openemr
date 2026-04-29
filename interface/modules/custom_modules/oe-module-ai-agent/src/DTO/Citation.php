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

final readonly class Citation
{
    public function __construct(
        public string $resourceType,
        public string $resourceId,
    ) {
    }

    /**
     * @param array{resource_type?: string, resource_id?: string} $payload
     */
    public static function fromArray(array $payload): self
    {
        return new self(
            resourceType: (string) ($payload['resource_type'] ?? ''),
            resourceId: (string) ($payload['resource_id'] ?? ''),
        );
    }
}

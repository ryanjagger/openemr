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

final readonly class ChatMessage
{
    public const ROLE_USER = 'user';
    public const ROLE_ASSISTANT = 'assistant';

    public function __construct(
        public string $role,
        public string $content,
    ) {
    }

    /**
     * @param array{role?: string, content?: string} $payload
     */
    public static function fromArray(array $payload): self
    {
        $role = (string) ($payload['role'] ?? '');
        if ($role !== self::ROLE_USER && $role !== self::ROLE_ASSISTANT) {
            $role = self::ROLE_USER;
        }

        return new self(
            role: $role,
            content: (string) ($payload['content'] ?? ''),
        );
    }

    /**
     * @return array{role: string, content: string}
     */
    public function toArray(): array
    {
        return [
            'role' => $this->role,
            'content' => $this->content,
        ];
    }
}

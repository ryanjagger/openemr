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

final readonly class ChatRequest
{
    /**
     * @param list<ChatMessage>          $messages
     * @param list<array<string, mixed>> $documentContext
     */
    public function __construct(
        public string $patientUuid,
        public string $fhirBaseUrl,
        public string $bearerToken,
        public string $requestId,
        public ?string $conversationId,
        public array $messages,
        public array $documentContext = [],
        public ?string $userId = null,
        public ?string $sessionId = null,
    ) {
    }

    /**
     * @return array{
     *     patient_uuid: string,
     *     fhir_base_url: string,
     *     bearer_token: string,
     *     request_id: string,
     *     conversation_id: string|null,
     *     messages: list<array{role: string, content: string}>,
     *     document_context: list<array<string, mixed>>,
     *     user_id: string|null,
     *     session_id: string|null
     * }
     */
    public function toArray(): array
    {
        return [
            'patient_uuid' => $this->patientUuid,
            'fhir_base_url' => $this->fhirBaseUrl,
            'bearer_token' => $this->bearerToken,
            'request_id' => $this->requestId,
            'conversation_id' => $this->conversationId,
            'messages' => array_map(
                static fn (ChatMessage $m): array => $m->toArray(),
                $this->messages,
            ),
            'document_context' => $this->documentContext,
            'user_id' => $this->userId,
            'session_id' => $this->sessionId,
        ];
    }
}

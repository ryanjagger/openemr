<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Modules\AiAgent\DTO\BriefRequest;
use OpenEMR\Modules\AiAgent\DTO\BriefResponse;
use OpenEMR\Modules\AiAgent\DTO\ChatRequest;
use OpenEMR\Modules\AiAgent\DTO\ChatTurnResponse;
use RuntimeException;
use Symfony\Component\HttpClient\HttpClient;
use Symfony\Contracts\HttpClient\Exception\TransportExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;

final class SidecarClient
{
    private const TIMEOUT_SECONDS = 30.0;

    public function __construct(
        private readonly string $baseUrl,
        private readonly string $internalAuthSecret,
        private readonly HttpClientInterface $httpClient = new \Symfony\Component\HttpClient\NativeHttpClient(),
    ) {
    }

    public static function fromEnvironment(): self
    {
        $baseUrl = getenv('AI_AGENT_SIDECAR_URL') ?: 'http://oe-ai-agent:8000';
        $secret = getenv('INTERNAL_AUTH_SECRET') ?: '';
        if ($secret === '') {
            throw new RuntimeException('INTERNAL_AUTH_SECRET is not configured');
        }

        return new self(
            baseUrl: rtrim($baseUrl, '/'),
            internalAuthSecret: $secret,
            httpClient: HttpClient::create(),
        );
    }

    public function fetchBrief(BriefRequest $request): BriefResponse
    {
        $decoded = $this->postJson('/v1/brief', $request->toArray());

        return BriefResponse::fromArray($decoded);
    }

    public function fetchChatTurn(ChatRequest $request): ChatTurnResponse
    {
        $decoded = $this->postJson('/v1/chat', $request->toArray());

        return ChatTurnResponse::fromArray($decoded);
    }

    /**
     * @param array<string, mixed> $body
     *
     * @return array<string, mixed>
     */
    private function postJson(string $path, array $body): array
    {
        try {
            $response = $this->httpClient->request(
                method: 'POST',
                url: $this->baseUrl . $path,
                options: [
                    'headers' => [
                        'Content-Type' => 'application/json',
                        'X-Internal-Auth' => $this->internalAuthSecret,
                    ],
                    'body' => json_encode($body, JSON_THROW_ON_ERROR),
                    'timeout' => self::TIMEOUT_SECONDS,
                ],
            );
            $status = $response->getStatusCode();
            if ($status !== 200) {
                $raw = $response->getContent(throw: false);
                error_log("oe-module-ai-agent: sidecar HTTP {$status}: " . substr($raw, 0, 400));
                throw new RuntimeException("Sidecar returned HTTP {$status}");
            }
            /** @var array<string, mixed> $decoded */
            $decoded = json_decode($response->getContent(), true, flags: JSON_THROW_ON_ERROR);

            return $decoded;
        } catch (TransportExceptionInterface $e) {
            throw new RuntimeException('Sidecar transport error', previous: $e);
        }
    }
}

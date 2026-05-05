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
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use RuntimeException;
use Symfony\Component\HttpClient\HttpClient;
use Symfony\Contracts\HttpClient\Exception\TransportExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;

final class SidecarClient
{
    private const BRIEF_TIMEOUT_SECONDS = 30.0;
    private const CHAT_TIMEOUT_SECONDS = 90.0;
    private const DOCUMENT_TIMEOUT_SECONDS = 180.0;

    public function __construct(
        private readonly string $baseUrl,
        private readonly string $internalAuthSecret,
        private readonly HttpClientInterface $httpClient = new \Symfony\Component\HttpClient\NativeHttpClient(),
        private readonly LoggerInterface $logger = new NullLogger(),
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
        $decoded = $this->postJson(
            '/v1/brief',
            $request->toArray(),
            $request->requestId,
            self::BRIEF_TIMEOUT_SECONDS,
        );

        return BriefResponse::fromArray($decoded);
    }

    public function fetchChatTurn(ChatRequest $request): ChatTurnResponse
    {
        $decoded = $this->postJson(
            '/v1/chat',
            $request->toArray(),
            $request->requestId,
            self::timeoutFromEnvironment('AI_AGENT_CHAT_TIMEOUT_SECONDS', self::CHAT_TIMEOUT_SECONDS),
        );

        return ChatTurnResponse::fromArray($decoded);
    }

    /**
     * @param array<string, mixed> $request
     *
     * @return array<string, mixed>
     */
    public function extractDocument(array $request): array
    {
        $requestId = (string) ($request['request_id'] ?? '');

        return $this->postJson('/v1/documents/extract', $request, $requestId, self::DOCUMENT_TIMEOUT_SECONDS);
    }

    /**
     * @param array<string, mixed> $body
     *
     * @return array<string, mixed>
     */
    private function postJson(
        string $path,
        array $body,
        string $requestId,
        float $timeoutSeconds,
    ): array {
        $startedAt = microtime(true);
        $this->logger->debug('sidecar.request.start', [
            'path' => $path,
            'request_id' => $requestId,
        ]);
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
                    'timeout' => $timeoutSeconds,
                ],
            );
            $status = $response->getStatusCode();
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            if ($status !== 200) {
                $raw = $response->getContent(throw: false);
                $this->logger->warning('sidecar.request.http_error', [
                    'path' => $path,
                    'request_id' => $requestId,
                    'status' => $status,
                    'latency_ms' => $latencyMs,
                    'body_preview' => substr($raw, 0, 400),
                ]);
                throw new RuntimeException("Sidecar returned HTTP {$status}");
            }
            /** @var array<string, mixed> $decoded */
            $decoded = json_decode($response->getContent(), true, flags: JSON_THROW_ON_ERROR);
            $this->logger->info('sidecar.request.complete', [
                'path' => $path,
                'request_id' => $requestId,
                'status' => $status,
                'latency_ms' => $latencyMs,
            ]);

            return $decoded;
        } catch (TransportExceptionInterface $e) {
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            $this->logger->error('sidecar.request.transport_error', [
                'path' => $path,
                'request_id' => $requestId,
                'latency_ms' => $latencyMs,
                'error' => $e->getMessage(),
            ]);
            throw new RuntimeException('Sidecar transport error', previous: $e);
        }
    }

    private static function timeoutFromEnvironment(string $key, float $defaultSeconds): float
    {
        $raw = getenv($key);
        if (!is_string($raw) || !is_numeric($raw)) {
            return $defaultSeconds;
        }

        return max(1.0, (float) $raw);
    }
}

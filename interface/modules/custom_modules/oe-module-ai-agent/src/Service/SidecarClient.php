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
use OpenEMR\Modules\AiAgent\Exception\SidecarRequestException;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use RuntimeException;
use Symfony\Component\HttpClient\HttpClient;
use Symfony\Contracts\HttpClient\Exception\TransportExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;

final class SidecarClient
{
    private const BRIEF_TIMEOUT_SECONDS = 30.0;
    // Supervisor + evidence + (optional) extractor + finalize routinely takes
    // longer than a single-LLM-call chat. Observed a 197s turn on staging
    // (extractor + multi-tool evidence_retriever) that blew past the 180s
    // cap. Sidecar's extractor poll is 120s and trips an EXTRACTION_PENDING
    // path on slow intake/lab OCR, but finalize + verifier still need ~60s
    // headroom on top. 240s gives that margin without making the user wait
    // forever on a stuck request. Override per-deploy with
    // AI_AGENT_CHAT_TIMEOUT_SECONDS.
    private const CHAT_TIMEOUT_SECONDS = 240.0;
    private const DOCUMENT_TIMEOUT_SECONDS = 180.0;
    private const PDF_PREVIEW_TIMEOUT_SECONDS = 15.0;

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
     * @return array<string, mixed>
     */
    public function fetchChatStatus(string $requestId): array
    {
        return $this->getJson(
            '/v1/chat/status/' . rawurlencode($requestId),
            $requestId,
            3.0,
        );
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
     * @param array{x: float, y: float, width: float, height: float}|null $bbox
     */
    public function renderPdfPagePreview(string $pdfData, int $page, ?array $bbox, string $bboxUnit): string
    {
        $body = [
            'content_base64' => base64_encode($pdfData),
            'page' => max(1, $page),
            'bbox_unit' => in_array($bboxUnit, ['normalized', 'percent', 'pixels'], true)
                ? $bboxUnit
                : 'normalized',
        ];
        if ($bbox !== null) {
            $body['bbox'] = [
                'x' => $bbox['x'],
                'y' => $bbox['y'],
                'width' => $bbox['width'],
                'height' => $bbox['height'],
            ];
        }

        return $this->postBinary(
            '/v1/documents/pdf-page-preview',
            $body,
            'source-preview',
            self::PDF_PREVIEW_TIMEOUT_SECONDS,
        );
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
                $errorCode = self::errorCodeFromBody($raw);
                $errorDetail = self::errorDetailFromBody($raw);
                $this->logger->warning('sidecar.request.http_error', [
                    'path' => $path,
                    'request_id' => $requestId,
                    'status' => $status,
                    'latency_ms' => $latencyMs,
                    'error_code' => $errorCode,
                    'body_preview' => substr($raw, 0, 400),
                ]);
                $message = "Sidecar returned HTTP {$status}";
                if ($errorDetail !== null) {
                    $message .= ": {$errorDetail}";
                }
                throw new SidecarRequestException($status, $errorCode, $errorDetail, $message);
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

    /**
     * @param array<string, mixed> $body
     */
    private function postBinary(
        string $path,
        array $body,
        string $requestId,
        float $timeoutSeconds,
    ): string {
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
                        'Accept' => 'image/png',
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
                $errorCode = self::errorCodeFromBody($raw);
                $errorDetail = self::errorDetailFromBody($raw);
                $this->logger->warning('sidecar.request.http_error', [
                    'path' => $path,
                    'request_id' => $requestId,
                    'status' => $status,
                    'latency_ms' => $latencyMs,
                    'error_code' => $errorCode,
                    'body_preview' => substr($raw, 0, 400),
                ]);
                $message = "Sidecar returned HTTP {$status}";
                if ($errorDetail !== null) {
                    $message .= ": {$errorDetail}";
                }
                throw new SidecarRequestException($status, $errorCode, $errorDetail, $message);
            }
            $this->logger->info('sidecar.request.complete', [
                'path' => $path,
                'request_id' => $requestId,
                'status' => $status,
                'latency_ms' => $latencyMs,
            ]);

            return $response->getContent();
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

    /**
     * @return array<string, mixed>
     */
    private function getJson(string $path, string $requestId, float $timeoutSeconds): array
    {
        $startedAt = microtime(true);
        $this->logger->debug('sidecar.request.start', [
            'path' => $path,
            'request_id' => $requestId,
        ]);
        try {
            $response = $this->httpClient->request(
                method: 'GET',
                url: $this->baseUrl . $path,
                options: [
                    'headers' => [
                        'X-Internal-Auth' => $this->internalAuthSecret,
                    ],
                    'timeout' => $timeoutSeconds,
                ],
            );
            $status = $response->getStatusCode();
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            if ($status !== 200) {
                $raw = $response->getContent(throw: false);
                $errorCode = self::errorCodeFromBody($raw);
                $errorDetail = self::errorDetailFromBody($raw);
                $this->logger->warning('sidecar.request.http_error', [
                    'path' => $path,
                    'request_id' => $requestId,
                    'status' => $status,
                    'latency_ms' => $latencyMs,
                    'error_code' => $errorCode,
                    'body_preview' => substr($raw, 0, 400),
                ]);
                $message = "Sidecar returned HTTP {$status}";
                if ($errorDetail !== null) {
                    $message .= ": {$errorDetail}";
                }
                throw new SidecarRequestException($status, $errorCode, $errorDetail, $message);
            }
            /** @var array<string, mixed> $decoded */
            $decoded = json_decode($response->getContent(), true, flags: JSON_THROW_ON_ERROR);

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

    private static function errorDetailFromBody(string $body): ?string
    {
        $decoded = self::decodeErrorBody($body);
        if ($decoded === null) {
            return null;
        }

        $detail = $decoded['detail'] ?? $decoded['error'] ?? null;
        if (is_string($detail) && $detail !== '') {
            return substr($detail, 0, 240);
        }
        if (!is_array($detail)) {
            return null;
        }

        $parts = [];
        foreach (['message', 'error', 'request_id'] as $key) {
            if (is_string($detail[$key] ?? null) && $detail[$key] !== '') {
                $parts[] = $key . '=' . $detail[$key];
            }
        }

        return $parts === [] ? null : substr(implode(' ', $parts), 0, 240);
    }

    private static function errorCodeFromBody(string $body): ?string
    {
        $decoded = self::decodeErrorBody($body);
        if ($decoded === null) {
            return null;
        }

        $detail = $decoded['detail'] ?? null;
        if (is_array($detail) && is_string($detail['error'] ?? null) && $detail['error'] !== '') {
            return substr($detail['error'], 0, 80);
        }
        if (is_string($decoded['error'] ?? null) && $decoded['error'] !== '') {
            return substr($decoded['error'], 0, 80);
        }

        return null;
    }

    /**
     * @return array<string, mixed>|null
     */
    private static function decodeErrorBody(string $body): ?array
    {
        try {
            $decoded = json_decode($body, true, flags: JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return null;
        }

        return is_array($decoded) ? $decoded : null;
    }
}

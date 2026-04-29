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

/**
 * Per-request observability envelope returned by the Python sidecar.
 *
 * Carries the four-question rollup (tokens, cost, latency) and the
 * per-step trace so the audit log + admin viewer can answer "what
 * happened on this request" without needing a second datastore.
 */
final readonly class ResponseMeta
{
    /**
     * @param list<array<string, mixed>> $steps Flat list of step records,
     *     each shaped like {name, duration_ms, status, error?, attrs?}.
     */
    public function __construct(
        public int $promptTokens,
        public int $completionTokens,
        public int $totalTokens,
        public float $costUsd,
        public int $latencyMsTotal,
        public array $steps,
    ) {
    }

    public static function empty(): self
    {
        return new self(0, 0, 0, 0.0, 0, []);
    }

    /**
     * @param array<string, mixed> $payload
     */
    public static function fromArray(array $payload): self
    {
        $rawUsage = $payload['usage'] ?? [];
        /** @var array<string, mixed> $usage */
        $usage = is_array($rawUsage) ? $rawUsage : [];

        $rawSteps = $payload['steps'] ?? [];
        $steps = [];
        if (is_array($rawSteps)) {
            foreach ($rawSteps as $rawStep) {
                if (! is_array($rawStep)) {
                    continue;
                }
                /** @var array<string, mixed> $rawStep */
                $steps[] = $rawStep;
            }
        }

        return new self(
            promptTokens: self::asInt($usage['prompt_tokens'] ?? 0),
            completionTokens: self::asInt($usage['completion_tokens'] ?? 0),
            totalTokens: self::asInt($usage['total_tokens'] ?? 0),
            costUsd: self::asFloat($usage['cost_usd'] ?? 0.0),
            latencyMsTotal: self::asInt($usage['latency_ms_total'] ?? 0),
            steps: $steps,
        );
    }

    private static function asInt(mixed $value): int
    {
        return is_int($value) || is_float($value) ? (int) $value
            : (is_string($value) && is_numeric($value) ? (int) $value : 0);
    }

    private static function asFloat(mixed $value): float
    {
        return is_int($value) || is_float($value) ? (float) $value
            : (is_string($value) && is_numeric($value) ? (float) $value : 0.0);
    }

    /**
     * @return array{
     *     usage: array{
     *         prompt_tokens: int,
     *         completion_tokens: int,
     *         total_tokens: int,
     *         cost_usd: float,
     *         latency_ms_total: int
     *     },
     *     steps: list<array<string, mixed>>
     * }
     */
    public function toArray(): array
    {
        return [
            'usage' => [
                'prompt_tokens' => $this->promptTokens,
                'completion_tokens' => $this->completionTokens,
                'total_tokens' => $this->totalTokens,
                'cost_usd' => $this->costUsd,
                'latency_ms_total' => $this->latencyMsTotal,
            ],
            'steps' => $this->steps,
        ];
    }

    /**
     * Cost in integer micros (1 USD = 1_000_000 micros) for DB storage.
     */
    public function costUsdMicros(): int
    {
        if ($this->costUsd <= 0.0) {
            return 0;
        }
        return (int) round($this->costUsd * 1_000_000);
    }
}

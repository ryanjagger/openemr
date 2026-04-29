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

use OpenEMR\Common\Database\QueryUtils;

/**
 * Read-only repository for the admin observability viewer.
 *
 * Returns plain associative arrays (not DTOs) because the admin page
 * renders directly to HTML — no need to construct typed objects when
 * each row is consumed once at render time. Cost is read as micros from
 * the DB and converted to USD at the boundary.
 */
final class AgentLogQueryService
{
    private const LIST_HARD_LIMIT = 200;

    /**
     * @param array{
     *     action?: string|null,
     *     status?: string|null,
     *     error_code?: string|null,
     * } $filters
     *
     * @return list<array<string, mixed>>
     */
    public function recent(int $limit = 100, array $filters = []): array
    {
        $effectiveLimit = max(1, min(self::LIST_HARD_LIMIT, $limit));

        $where = [];
        $bindings = [];
        if (! empty($filters['action'])) {
            $where[] = '`action_type` = ?';
            $bindings[] = $filters['action'];
        }
        if (! empty($filters['status'])) {
            $where[] = '`verification_status` = ?';
            $bindings[] = $filters['status'];
        }
        if (! empty($filters['error_code'])) {
            $where[] = '`error_code` = ?';
            $bindings[] = $filters['error_code'];
        }
        $whereSql = $where === [] ? '' : 'WHERE ' . implode(' AND ', $where);

        $sql = 'SELECT '
            . '`id`, `request_id`, `conversation_id`, `user_id`, `patient_id`, '
            . '`action_type`, `model_id`, `prompt_tokens`, `completion_tokens`, '
            . '`latency_ms`, `cost_usd_micros`, `verification_status`, '
            . '`error_code`, `created_at` '
            . "FROM `llm_call_log` {$whereSql} "
            . 'ORDER BY `created_at` DESC, `id` DESC '
            . "LIMIT {$effectiveLimit}";

        /** @var list<array<string, mixed>> $rows */
        $rows = QueryUtils::fetchRecords($sql, $bindings, true);

        return $rows;
    }

    /**
     * @return array<string, mixed>|null
     */
    public function findByRequestId(string $requestId): ?array
    {
        if ($requestId === '') {
            return null;
        }

        /** @var list<array<string, mixed>> $rows */
        $rows = QueryUtils::fetchRecords(
            'SELECT * FROM `llm_call_log` WHERE `request_id` = ? ORDER BY `id` DESC LIMIT 1',
            [$requestId],
            true,
        );

        return $rows === [] ? null : $rows[0];
    }

    /**
     * @return array{
     *     count: int,
     *     total_prompt_tokens: int,
     *     total_completion_tokens: int,
     *     total_cost_usd_micros: int,
     *     avg_latency_ms: float|null,
     *     error_count: int,
     * }
     */
    public function aggregateLast24h(): array
    {
        /** @var list<array<string, mixed>> $rows */
        $rows = QueryUtils::fetchRecords(
            'SELECT '
            . 'COUNT(*) AS row_count, '
            . 'COALESCE(SUM(`prompt_tokens`), 0) AS prompt_tokens_total, '
            . 'COALESCE(SUM(`completion_tokens`), 0) AS completion_tokens_total, '
            . 'COALESCE(SUM(`cost_usd_micros`), 0) AS cost_micros_total, '
            . 'AVG(`latency_ms`) AS latency_avg, '
            . "COALESCE(SUM(CASE WHEN `error_code` IS NOT NULL THEN 1 ELSE 0 END), 0) AS error_count "
            . 'FROM `llm_call_log` '
            . 'WHERE `created_at` >= DATE_SUB(NOW(), INTERVAL 24 HOUR)',
            [],
            true,
        );

        $row = $rows[0] ?? [];
        $latencyAvg = $row['latency_avg'] ?? null;

        return [
            'count' => (int) ($row['row_count'] ?? 0),
            'total_prompt_tokens' => (int) ($row['prompt_tokens_total'] ?? 0),
            'total_completion_tokens' => (int) ($row['completion_tokens_total'] ?? 0),
            'total_cost_usd_micros' => (int) ($row['cost_micros_total'] ?? 0),
            'avg_latency_ms' => $latencyAvg === null ? null : (float) $latencyAvg,
            'error_count' => (int) ($row['error_count'] ?? 0),
        ];
    }
}

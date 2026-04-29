<?php

/**
 * AI Agent observability viewer — recent requests list + detail.
 *
 * Renders the last N rows from llm_call_log so admins can answer
 * the four observability questions for any specific request:
 *  - what did the agent do (steps_json timeline)
 *  - how long did each step take (per-step duration_ms)
 *  - did any tools fail (status='error' rows in steps_json)
 *  - tokens / cost (prompt_tokens, completion_tokens, cost_usd_micros)
 *
 * Single file with two views (?request_id=... → detail) to keep the
 * route surface trivial.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Core\Header;
use OpenEMR\Modules\AiAgent\Service\AgentLogQueryService;

if (! AclMain::aclCheckCore('admin', 'super')) {
    http_response_code(403);
    echo xlt('Access denied');
    exit;
}

$queryService = new AgentLogQueryService();
$requestIdParam = isset($_GET['request_id']) && is_string($_GET['request_id'])
    ? trim($_GET['request_id'])
    : '';

if ($requestIdParam !== '') {
    $detail = $queryService->findByRequestId($requestIdParam);
    render_detail_page($detail, $requestIdParam);
    exit;
}

$filters = [
    'action' => isset($_GET['action']) && is_string($_GET['action']) ? trim($_GET['action']) : null,
    'status' => isset($_GET['status']) && is_string($_GET['status']) ? trim($_GET['status']) : null,
    'error_code' => isset($_GET['error']) && is_string($_GET['error']) ? trim($_GET['error']) : null,
];
$filters = array_filter($filters, static fn ($v): bool => $v !== null && $v !== '');
/** @var array{action?: string|null, status?: string|null, error_code?: string|null} $filters */

$rows = $queryService->recent(100, $filters);
$summary = $queryService->aggregateLast24h();

render_list_page($rows, $summary, $filters);

/**
 * @param list<array<string, mixed>> $rows
 * @param array{
 *     count: int,
 *     total_prompt_tokens: int,
 *     total_completion_tokens: int,
 *     total_cost_usd_micros: int,
 *     avg_latency_ms: float|null,
 *     error_count: int
 * } $summary
 * @param array{action?: string|null, status?: string|null, error_code?: string|null} $filters
 */
function render_list_page(array $rows, array $summary, array $filters): void
{
    ?>
<!DOCTYPE html>
<html>
<head>
    <title><?= xlt('AI Agent — Recent Requests') ?></title>
    <?php Header::setupHeader(['common']); ?>
    <style>
        .oe-ai-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }
        .oe-ai-summary .card { padding: 12px; border: 1px solid #dee2e6; border-radius: 4px; background: #f8f9fa; }
        .oe-ai-summary .card .label { font-size: 11px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.5px; }
        .oe-ai-summary .card .value { font-size: 22px; font-weight: 600; margin-top: 4px; }
        .oe-ai-table tbody tr { cursor: pointer; }
        .oe-ai-table tbody tr:hover { background: #f1f3f5; }
        .oe-ai-status-passed { color: #198754; }
        .oe-ai-status-partial { color: #fd7e14; }
        .oe-ai-status-failed,
        .oe-ai-status-denied { color: #dc3545; }
        .oe-ai-mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; }
        .oe-ai-error-pill { display: inline-block; padding: 1px 6px; background: #f8d7da; color: #842029; border-radius: 4px; font-size: 11px; }
    </style>
</head>
<body class="body_top">
    <div class="container-fluid mt-3">
        <h2><?= xlt('AI Agent — Recent Requests') ?></h2>
        <p class="text-muted"><?= xlt('Last 100 agent invocations. Click a row for the per-step trace.') ?></p>

        <div class="oe-ai-summary">
            <div class="card">
                <div class="label"><?= xlt('Last 24h') ?></div>
                <div class="value"><?= attr($summary['count']) ?></div>
            </div>
            <div class="card">
                <div class="label"><?= xlt('Avg latency') ?></div>
                <div class="value">
                    <?= $summary['avg_latency_ms'] === null
                        ? '—'
                        : attr((string) (int) round($summary['avg_latency_ms']))
                          . ' <small>ms</small>' ?>
                </div>
            </div>
            <div class="card">
                <div class="label"><?= xlt('Tokens (in / out)') ?></div>
                <div class="value oe-ai-mono">
                    <?= attr((string) $summary['total_prompt_tokens']) ?>
                    / <?= attr((string) $summary['total_completion_tokens']) ?>
                </div>
            </div>
            <div class="card">
                <div class="label"><?= xlt('Cost (USD)') ?></div>
                <div class="value">
                    $<?= attr(format_usd($summary['total_cost_usd_micros'])) ?>
                </div>
            </div>
            <div class="card">
                <div class="label"><?= xlt('Errors') ?></div>
                <div class="value oe-ai-status-failed">
                    <?= attr((string) $summary['error_count']) ?>
                </div>
            </div>
        </div>

        <form method="get" class="form-inline mb-3">
            <label class="mr-2"><?= xlt('Action') ?>:</label>
            <select name="action" class="form-control form-control-sm mr-3">
                <option value=""><?= xlt('All') ?></option>
                <option value="brief.read"<?= ($filters['action'] ?? '') === 'brief.read' ? ' selected' : '' ?>>brief.read</option>
                <option value="chat.turn"<?= ($filters['action'] ?? '') === 'chat.turn' ? ' selected' : '' ?>>chat.turn</option>
            </select>
            <label class="mr-2"><?= xlt('Status') ?>:</label>
            <select name="status" class="form-control form-control-sm mr-3">
                <option value=""><?= xlt('All') ?></option>
                <?php foreach (['passed', 'partial', 'failed', 'denied'] as $s) : ?>
                    <option value="<?= attr($s) ?>"<?= ($filters['status'] ?? '') === $s ? ' selected' : '' ?>><?= attr($s) ?></option>
                <?php endforeach; ?>
            </select>
            <label class="mr-2"><?= xlt('Error code') ?>:</label>
            <input type="text" name="error" value="<?= attr((string) ($filters['error_code'] ?? '')) ?>"
                   class="form-control form-control-sm mr-3" placeholder="e.g. agent_error">
            <button type="submit" class="btn btn-sm btn-primary"><?= xlt('Filter') ?></button>
            <a href="?" class="btn btn-sm btn-link"><?= xlt('Reset') ?></a>
        </form>

        <table class="table table-sm oe-ai-table">
            <thead>
                <tr>
                    <th><?= xlt('Time (UTC)') ?></th>
                    <th><?= xlt('Action') ?></th>
                    <th><?= xlt('Patient') ?></th>
                    <th><?= xlt('Model') ?></th>
                    <th class="text-right"><?= xlt('Latency') ?></th>
                    <th class="text-right"><?= xlt('Tokens') ?></th>
                    <th class="text-right"><?= xlt('Cost') ?></th>
                    <th><?= xlt('Status') ?></th>
                    <th><?= xlt('Error') ?></th>
                </tr>
            </thead>
            <tbody>
                <?php if ($rows === []) : ?>
                    <tr><td colspan="9" class="text-center text-muted py-4"><?= xlt('No agent requests recorded yet.') ?></td></tr>
                <?php else : ?>
                    <?php foreach ($rows as $row) : ?>
                        <?php
                        $rid = (string) ($row['request_id'] ?? '');
                        $href = '?request_id=' . urlencode($rid);
                        $status = (string) ($row['verification_status'] ?? '');
                        $errorCode = $row['error_code'] ?? null;
                        $latency = $row['latency_ms'] ?? null;
                        $promptTokens = (int) ($row['prompt_tokens'] ?? 0);
                        $completionTokens = (int) ($row['completion_tokens'] ?? 0);
                        $costMicros = (int) ($row['cost_usd_micros'] ?? 0);
                        ?>
                        <tr onclick="window.location.href='<?= attr($href) ?>'">
                            <td class="oe-ai-mono"><?= text((string) ($row['created_at'] ?? '')) ?></td>
                            <td><?= text((string) ($row['action_type'] ?? '')) ?></td>
                            <td><?= text((string) ($row['patient_id'] ?? '')) ?></td>
                            <td class="oe-ai-mono"><?= text((string) ($row['model_id'] ?? '')) ?></td>
                            <td class="text-right oe-ai-mono">
                                <?= $latency === null ? '—' : text((string) (int) $latency) . ' ms' ?>
                            </td>
                            <td class="text-right oe-ai-mono">
                                <?= text((string) ($promptTokens + $completionTokens)) ?>
                                <small class="text-muted">(<?= text((string) $promptTokens) ?>+<?= text((string) $completionTokens) ?>)</small>
                            </td>
                            <td class="text-right oe-ai-mono">
                                $<?= text(format_usd($costMicros)) ?>
                            </td>
                            <td class="oe-ai-status-<?= attr($status) ?>"><?= text($status) ?></td>
                            <td>
                                <?php if (is_string($errorCode) && $errorCode !== '') : ?>
                                    <span class="oe-ai-error-pill"><?= text($errorCode) ?></span>
                                <?php endif; ?>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                <?php endif; ?>
            </tbody>
        </table>
    </div>
</body>
</html>
    <?php
}

/**
 * @param array<string, mixed>|null $row
 */
function render_detail_page(?array $row, string $requestId): void
{
    ?>
<!DOCTYPE html>
<html>
<head>
    <title><?= xlt('AI Agent — Request Detail') ?></title>
    <?php Header::setupHeader(['common']); ?>
    <style>
        .oe-ai-mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; }
        .oe-ai-step { display: grid; grid-template-columns: 220px 1fr 100px; gap: 8px; padding: 8px; border-left: 3px solid #adb5bd; margin-bottom: 6px; background: #f8f9fa; }
        .oe-ai-step.error { border-left-color: #dc3545; background: #fdf2f2; }
        .oe-ai-step.ok { border-left-color: #198754; }
        .oe-ai-step .name { font-weight: 600; }
        .oe-ai-step .ms { text-align: right; font-family: ui-monospace, SFMono-Regular, monospace; color: #495057; }
        .oe-ai-step .attrs { font-size: 12px; color: #6c757d; }
        .oe-ai-step .attrs code { background: transparent; color: #495057; }
        .oe-ai-step .err { color: #842029; font-size: 12px; }
        .oe-ai-bar { height: 4px; background: #adb5bd; margin-top: 6px; border-radius: 2px; max-width: 100%; }
        .oe-ai-bar.error { background: #dc3545; }
        .oe-ai-bar.ok { background: #198754; }
        dl.row dt { text-align: left; }
        dl.row { margin-bottom: 0; }
    </style>
</head>
<body class="body_top">
    <div class="container-fluid mt-3">
        <p>
            <a href="?" class="btn btn-sm btn-link">&larr; <?= xlt('Back to recent') ?></a>
        </p>
        <h2><?= xlt('Request') ?> <span class="oe-ai-mono"><?= text($requestId) ?></span></h2>

        <?php if ($row === null) : ?>
            <div class="alert alert-warning"><?= xlt('No row found for this request_id.') ?></div>
            </div></body></html>
            <?php
            return;
        endif;

        $steps = decode_steps((string) ($row['steps_json'] ?? ''));
        $maxMs = max(array_map(static fn (array $s): int => (int) ($s['duration_ms'] ?? 0), $steps) ?: [1]);
        ?>

        <div class="card mb-3">
            <div class="card-body">
                <dl class="row">
                    <dt class="col-sm-3"><?= xlt('Time (UTC)') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono"><?= text((string) ($row['created_at'] ?? '')) ?></dd>

                    <dt class="col-sm-3"><?= xlt('Action') ?></dt>
                    <dd class="col-sm-9"><?= text((string) ($row['action_type'] ?? '')) ?></dd>

                    <dt class="col-sm-3"><?= xlt('Conversation') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono"><?= text((string) ($row['conversation_id'] ?? '—')) ?></dd>

                    <dt class="col-sm-3"><?= xlt('User / Patient') ?></dt>
                    <dd class="col-sm-9"><?= text((string) ($row['user_id'] ?? '—')) ?> / <?= text((string) ($row['patient_id'] ?? '—')) ?></dd>

                    <dt class="col-sm-3"><?= xlt('Model') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono"><?= text((string) ($row['model_id'] ?? '')) ?></dd>

                    <dt class="col-sm-3"><?= xlt('Latency') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono">
                        <?= $row['latency_ms'] === null ? '—' : text((string) (int) $row['latency_ms']) . ' ms' ?>
                    </dd>

                    <dt class="col-sm-3"><?= xlt('Tokens') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono">
                        <?= text((string) (int) ($row['prompt_tokens'] ?? 0)) ?>
                        + <?= text((string) (int) ($row['completion_tokens'] ?? 0)) ?>
                        = <?= text((string) ((int) ($row['prompt_tokens'] ?? 0) + (int) ($row['completion_tokens'] ?? 0))) ?>
                    </dd>

                    <dt class="col-sm-3"><?= xlt('Cost') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono">
                        $<?= text(format_usd((int) ($row['cost_usd_micros'] ?? 0))) ?>
                    </dd>

                    <dt class="col-sm-3"><?= xlt('Status') ?></dt>
                    <dd class="col-sm-9"><?= text((string) ($row['verification_status'] ?? '')) ?></dd>

                    <?php if (! empty($row['error_code'])) : ?>
                        <dt class="col-sm-3"><?= xlt('Error') ?></dt>
                        <dd class="col-sm-9">
                            <code><?= text((string) $row['error_code']) ?></code>
                            <?php if (! empty($row['error_detail'])) : ?>
                                <div class="text-muted small mt-1"><?= text((string) $row['error_detail']) ?></div>
                            <?php endif; ?>
                        </dd>
                    <?php endif; ?>

                    <dt class="col-sm-3"><?= xlt('Integrity checksum') ?></dt>
                    <dd class="col-sm-9 oe-ai-mono text-muted" style="word-break: break-all;">
                        <?= text((string) ($row['integrity_checksum'] ?? '')) ?>
                    </dd>
                </dl>
            </div>
        </div>

        <h4><?= xlt('Step trace') ?></h4>
        <p class="text-muted small">
            <?= xlt('Steps in chronological start order. Bar width is duration relative to the longest step.') ?>
        </p>

        <?php if ($steps === []) : ?>
            <p class="text-muted"><?= xlt('No step trace recorded.') ?></p>
        <?php else : ?>
            <?php foreach ($steps as $step) : ?>
                <?php
                $stepName = (string) ($step['name'] ?? '?');
                $stepStatus = (string) ($step['status'] ?? 'ok');
                $stepDuration = (int) ($step['duration_ms'] ?? 0);
                $stepError = $step['error'] ?? null;
                $widthPct = $maxMs > 0 ? (int) round(($stepDuration / $maxMs) * 100) : 0;
                $stepAttrs = $step['attrs'] ?? [];
                ?>
                <div class="oe-ai-step <?= attr($stepStatus) ?>">
                    <div>
                        <div class="name oe-ai-mono"><?= text($stepName) ?></div>
                        <?php if (is_array($stepAttrs) && $stepAttrs !== []) : ?>
                            <div class="attrs">
                                <?php foreach ($stepAttrs as $k => $v) : ?>
                                    <?php if (! is_scalar($v)) {
                                        continue;
                                    } ?>
                                    <code><?= text((string) $k) ?>=<?= text((string) $v) ?></code>
                                <?php endforeach; ?>
                            </div>
                        <?php endif; ?>
                        <?php if (is_string($stepError) && $stepError !== '') : ?>
                            <div class="err"><?= text($stepError) ?></div>
                        <?php endif; ?>
                    </div>
                    <div>
                        <div class="oe-ai-bar <?= attr($stepStatus) ?>"
                             style="width: <?= attr((string) $widthPct) ?>%;"></div>
                    </div>
                    <div class="ms"><?= text((string) $stepDuration) ?> ms</div>
                </div>
            <?php endforeach; ?>
        <?php endif; ?>
    </div>
</body>
</html>
    <?php
}

/**
 * @return list<array<string, mixed>>
 */
function decode_steps(string $stepsJson): array
{
    if ($stepsJson === '') {
        return [];
    }
    try {
        /** @var mixed $decoded */
        $decoded = json_decode($stepsJson, true, flags: JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        return [];
    }
    if (! is_array($decoded)) {
        return [];
    }
    $out = [];
    foreach ($decoded as $row) {
        if (is_array($row)) {
            /** @var array<string, mixed> $row */
            $out[] = $row;
        }
    }
    return $out;
}

function format_usd(int $micros): string
{
    if ($micros <= 0) {
        return '0.0000';
    }
    return number_format($micros / 1_000_000, 4, '.', '');
}

<?php

/**
 * AI Agent patient-summary panel — Phase 1 walking-skeleton.
 *
 * Rendered inline above the existing demographics card list via
 * RenderEvent::EVENT_SECTION_LIST_RENDER_BEFORE.
 *
 * Expects locals from Bootstrap::renderBriefPanel():
 *   string $pid
 *   string $apiCsrfToken
 *   string $publicPath  Absolute web path to /public assets
 */

declare(strict_types=1);

/** @var string $pid */
/** @var string $apiCsrfToken */
/** @var string $publicPath */
?>
<section class="card mb-2" id="oe-ai-agent-panel"
    data-pid="<?= htmlspecialchars($pid, ENT_QUOTES, 'UTF-8') ?>"
    data-csrf="<?= htmlspecialchars($apiCsrfToken, ENT_QUOTES, 'UTF-8') ?>">
    <div class="card-header bg-light d-flex justify-content-between align-items-center py-2">
        <strong>AI Patient Brief</strong>
        <button type="button" class="btn btn-sm btn-primary" id="oe-ai-agent-generate">
            Generate brief
        </button>
    </div>
    <div class="card-body p-3">
        <div id="oe-ai-agent-content" class="text-muted">
            Click <em>Generate brief</em> to summarize this chart.
        </div>
    </div>
</section>
<script src="<?= htmlspecialchars($publicPath, ENT_QUOTES, 'UTF-8') ?>/js/brief_panel.js?v=0.3.0"></script>

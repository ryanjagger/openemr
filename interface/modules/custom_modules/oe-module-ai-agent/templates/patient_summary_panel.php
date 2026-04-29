<?php

/**
 * AI Agent patient-summary panel — Phase 1 walking-skeleton.
 *
 * Rendered inline above the existing demographics card list via
 * RenderEvent::EVENT_SECTION_LIST_RENDER_BEFORE. Uses the same
 * card markup as the rest of the demographics dashboard so the
 * collapse handler in demographics.php manages the fa-expand /
 * fa-compress icon and persists the user's open/closed pref.
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
    <div class="card-body p-1">
        <h6 class="card-title mb-0 d-flex p-1 justify-content-between">
            <a class="text-left font-weight-bolder" href="#" data-toggle="collapse"
                data-target="#oe-ai-agent-panel-body" aria-expanded="true"
                aria-controls="oe-ai-agent-panel-body">
                AI Patient Brief
                <i class="ml-1 fa fa-fw fa-compress" data-target="#oe-ai-agent-panel-body"></i>
            </a>
        </h6>
        <div id="oe-ai-agent-panel-body" class="card-text collapse show">
            <div class="clearfix pt-2 px-2 pb-2">
                <div class="d-flex justify-content-end mb-2">
                    <button type="button" class="btn btn-sm btn-primary" id="oe-ai-agent-generate">
                        Generate brief
                    </button>
                </div>
                <div id="oe-ai-agent-content" class="text-muted">
                    Click <em>Generate brief</em> to summarize this chart.
                </div>
            </div>
        </div>
    </div>
</section>
<script src="<?= htmlspecialchars($publicPath, ENT_QUOTES, 'UTF-8') ?>/js/brief_panel.js?v=0.6.0"></script>

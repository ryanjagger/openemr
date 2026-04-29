<?php

/**
 * AI Agent chat panel — multi-turn follow-ups about the current chart.
 *
 * Rendered inline below the AI Patient Brief card via the same
 * RenderEvent::EVENT_SECTION_LIST_RENDER_BEFORE hook. Uses the standard
 * OpenEMR card markup (data-toggle="collapse" + fa-expand/fa-compress)
 * so demographics.php manages the icon swap and persists the user's
 * open/closed pref. Default-collapsed because most chart visits don't
 * need a chat turn.
 *
 * Expects locals from Bootstrap::renderChatPanel():
 *   string $pid
 *   string $apiCsrfToken
 *   string $publicPath  Absolute web path to /public assets
 */

declare(strict_types=1);

/** @var string $pid */
/** @var string $apiCsrfToken */
/** @var string $publicPath */
?>
<section class="card mb-2" id="oe-ai-agent-chat-panel"
    data-pid="<?= htmlspecialchars($pid, ENT_QUOTES, 'UTF-8') ?>"
    data-csrf="<?= htmlspecialchars($apiCsrfToken, ENT_QUOTES, 'UTF-8') ?>">
    <div class="card-body p-1">
        <h6 class="card-title mb-0 d-flex p-1 justify-content-between">
            <a class="text-left font-weight-bolder collapsed" href="#" data-toggle="collapse"
                data-target="#oe-ai-agent-chat-body" aria-expanded="false"
                aria-controls="oe-ai-agent-chat-body">
                Ask about this chart
                <i class="ml-1 fa fa-fw fa-expand" data-target="#oe-ai-agent-chat-body"></i>
            </a>
        </h6>
        <div id="oe-ai-agent-chat-body" class="card-text collapse">
            <div class="clearfix pt-2 px-2 pb-2">
                <div id="oe-ai-agent-chat-log" class="mb-2" style="max-height: 320px; overflow-y: auto;">
                    <div class="text-muted small">
                        Ask a question grounded in this patient's chart. The agent will not give clinical advice.
                    </div>
                </div>
                <form id="oe-ai-agent-chat-form" class="d-flex" autocomplete="off">
                    <input type="text" id="oe-ai-agent-chat-input" class="form-control mr-2"
                        maxlength="500" required>
                    <button type="submit" class="btn btn-sm btn-primary" id="oe-ai-agent-chat-send">Send</button>
                </form>
                <div class="small text-muted mt-2">
                    Conversation is cleared when you reload or leave the chart.
                </div>
            </div>
        </div>
    </div>
</section>
<script src="<?= htmlspecialchars($publicPath, ENT_QUOTES, 'UTF-8') ?>/js/chat_panel.js?v=0.6.0"></script>

(function () {
    'use strict';

    var panel = document.getElementById('oe-ai-agent-chat-panel');
    if (!panel) {
        return;
    }

    var log = document.getElementById('oe-ai-agent-chat-log');
    var form = document.getElementById('oe-ai-agent-chat-form');
    var input = document.getElementById('oe-ai-agent-chat-input');
    var sendBtn = document.getElementById('oe-ai-agent-chat-send');
    var pid = panel.getAttribute('data-pid');
    var csrf = panel.getAttribute('data-csrf');
    var endpoint = '/apis/default/api/ai/chat/' + encodeURIComponent(pid);
    var STATUS_POLL_INTERVAL_MS = 1000;

    // In-memory conversation state — ephemeral by design (per ARCH chat
    // addendum). A reload throws this away; the server's conversation
    // store also TTLs after 30 minutes.
    var state = {
        conversationId: null,
        messages: [],
        pending: false,
        pendingIndicator: null,
        statusPollTimer: null,
        statusPollInFlight: false,
        statusPollGeneration: 0,
        pendingRequestId: null
    };

    // Maps controller error codes to human-readable copy. Codes here must
    // match the strings ChatController.finalize() puts in the JSON body.
    var ERROR_COPY = {
        forbidden: 'You don\'t have access to this patient\'s chart.',
        no_authenticated_user: 'Your session has expired. Please log in again and try again.',
        patient_not_found: 'Patient record not found.',
        token_mint_failed: 'Could not authorize the AI service for this chart.',
        sidecar_unreachable: 'The AI service is unreachable.',
        model_overloaded: 'The AI model provider is temporarily overloaded. Please try again in a moment.',
        empty_response: 'The AI service did not produce an answer. Please try again or ask a more specific question.',
        empty_messages: 'Type a question first.',
        http_error: 'The AI service returned an unexpected response.',
        network: 'Could not reach the AI service.'
    };

    function appendUserBubble(text) {
        var wrap = document.createElement('div');
        wrap.className = 'mb-2 text-right';

        var bubble = document.createElement('span');
        bubble.className = 'd-inline-block px-2 py-1 rounded bg-primary text-white';
        bubble.style.maxWidth = '85%';
        bubble.style.whiteSpace = 'pre-wrap';
        bubble.textContent = text;
        wrap.appendChild(bubble);

        log.appendChild(wrap);
        log.scrollTop = log.scrollHeight;
    }

    function appendAssistantBubble(narrative, facts, failures, meta) {
        var wrap = document.createElement('div');
        wrap.className = 'mb-2';

        var bubble = document.createElement('div');
        bubble.className = 'd-inline-block px-2 py-1 rounded';
        bubble.style.background = '#f1f3f5';
        bubble.style.maxWidth = '95%';

        var narrativeEl = document.createElement('div');
        narrativeEl.style.whiteSpace = 'pre-wrap';
        narrativeEl.appendChild(renderNarrative(narrative || '', facts || []));
        bubble.appendChild(narrativeEl);

        if (facts && facts.length > 0) {
            var factsList = document.createElement('div');
            factsList.className = 'mt-2';
            facts.forEach(function (fact) {
                factsList.appendChild(renderFactCard(fact));
            });
            bubble.appendChild(factsList);
        }

        if (failures && failures.length > 0) {
            var note = document.createElement('div');
            note.className = 'small text-muted mt-2';
            note.textContent = failures.length +
                ' verifier issue' + (failures.length === 1 ? '' : 's');
            note.title = failures.map(function (f) {
                return (f.rule || '?') + ': ' + (f.detail || '');
            }).join('\n');
            bubble.appendChild(note);
        }

        appendUsageFooter(bubble, meta);

        wrap.appendChild(bubble);
        log.appendChild(wrap);
        log.scrollTop = log.scrollHeight;
    }

    function appendUsageFooter(parent, meta) {
        if (!meta || !meta.usage) {
            return;
        }
        var usage = meta.usage;
        var parts = [];
        if (typeof usage.latency_ms_total === 'number' && usage.latency_ms_total >= 0) {
            parts.push(usage.latency_ms_total + ' ms');
        }
        var totalTokens = typeof usage.total_tokens === 'number' && usage.total_tokens > 0
            ? usage.total_tokens
            : (
                typeof usage.prompt_tokens === 'number' && typeof usage.completion_tokens === 'number'
                    ? usage.prompt_tokens + usage.completion_tokens
                    : 0
            );
        if (totalTokens > 0) {
            parts.push(totalTokens + ' tok');
        }
        if (typeof usage.cost_usd === 'number' && usage.cost_usd > 0) {
            parts.push('$' + usage.cost_usd.toFixed(4));
        }
        if (parts.length === 0) {
            return;
        }
        var footer = document.createElement('div');
        footer.className = 'small text-muted mt-2';
        footer.textContent = parts.join(' · ');
        parent.appendChild(footer);
    }

    function appendErrorBubble(code, requestId) {
        var wrap = document.createElement('div');
        wrap.className = 'mb-2';

        var alert = document.createElement('div');
        alert.className = 'alert alert-danger mb-0';

        var msg = document.createElement('div');
        msg.textContent = ERROR_COPY[code] || 'Something went wrong.';
        alert.appendChild(msg);

        if (requestId) {
            var rid = document.createElement('div');
            rid.className = 'small text-muted mt-1';
            rid.textContent = 'Request ID: ' + requestId;
            alert.appendChild(rid);
        }

        wrap.appendChild(alert);
        log.appendChild(wrap);
        log.scrollTop = log.scrollHeight;
    }

    function startPendingIndicator() {
        stopPendingIndicator(false);
        state.statusPollGeneration += 1;

        var wrap = document.createElement('div');
        wrap.className = 'mb-2 oe-ai-agent-pending-row';
        wrap.setAttribute('role', 'status');
        wrap.setAttribute('aria-live', 'polite');

        var bubble = document.createElement('div');
        bubble.className = 'd-inline-block px-3 py-2 rounded border bg-light oe-ai-agent-pending-bubble';
        bubble.style.maxWidth = '95%';

        var line = document.createElement('div');
        line.className = 'd-flex align-items-center';

        var spinner = document.createElement('span');
        spinner.className = 'spinner-border spinner-border-sm text-info mr-2';
        spinner.setAttribute('aria-hidden', 'true');
        line.appendChild(spinner);

        var status = document.createElement('span');
        status.className = 'oe-ai-agent-pending-status';
        status.textContent = 'AI response in progress…';
        line.appendChild(status);

        var meta = document.createElement('div');
        meta.className = 'small text-muted mt-1 oe-ai-agent-pending-meta';

        bubble.appendChild(line);
        bubble.appendChild(meta);
        wrap.appendChild(bubble);
        log.appendChild(wrap);

        state.pendingIndicator = {
            wrap: wrap,
            status: status,
            meta: meta
        };
        updatePendingStatus('AI response in progress…', 'Waiting for the AI service.');
        pollChatStatus();
        state.statusPollTimer = setInterval(pollChatStatus, STATUS_POLL_INTERVAL_MS);
        log.scrollTop = log.scrollHeight;
    }

    function stopPendingIndicator(clearRequestId) {
        state.statusPollGeneration += 1;
        state.statusPollInFlight = false;
        if (state.statusPollTimer !== null) {
            clearInterval(state.statusPollTimer);
            state.statusPollTimer = null;
        }
        if (state.pendingIndicator && state.pendingIndicator.wrap.parentNode) {
            state.pendingIndicator.wrap.parentNode.removeChild(state.pendingIndicator.wrap);
        }
        state.pendingIndicator = null;
        if (clearRequestId !== false) {
            state.pendingRequestId = null;
        }
    }

    function updatePendingStatus(text, detail) {
        if (!state.pendingIndicator) {
            return;
        }
        state.pendingIndicator.status.textContent = text;
        state.pendingIndicator.meta.textContent = detail || '';
        log.scrollTop = log.scrollHeight;
    }

    function pollChatStatus() {
        if (!state.pending || !state.pendingRequestId || state.statusPollInFlight) {
            return;
        }
        state.statusPollInFlight = true;
        var generation = state.statusPollGeneration;
        fetch(statusEndpoint(state.pendingRequestId), {
            method: 'GET',
            credentials: 'same-origin',
            headers: {
                'APICSRFTOKEN': csrf,
                'Accept': 'application/json'
            }
        }).then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok, data: data };
            }, function () {
                return { ok: response.ok, data: null };
            });
        }).then(function (result) {
            if (
                generation !== state.statusPollGeneration
                || !state.pending
                || !result.ok
                || !result.data
                || typeof result.data !== 'object'
            ) {
                return;
            }
            updatePendingFromStatus(result.data);
        }).catch(function () {
            // Chat failure handling belongs to the primary request. A status
            // poll failure should not replace the assistant's pending state.
        }).then(function () {
            if (generation === state.statusPollGeneration) {
                state.statusPollInFlight = false;
            }
        });
    }

    function statusEndpoint(requestId) {
        return '/apis/default/api/ai/chat/' + encodeURIComponent(pid) +
            '/status/' + encodeURIComponent(requestId);
    }

    function updatePendingFromStatus(status) {
        if (!status || typeof status !== 'object' || status.state === 'unknown') {
            return;
        }
        var stage = typeof status.stage === 'string' && status.stage
            ? status.stage
            : 'AI response in progress…';
        var detail = typeof status.detail === 'string' ? status.detail : '';
        if (status.worker && !detail) {
            detail = 'Worker: ' + status.worker;
        }
        updatePendingStatus(stage, detail);
    }

    function newRequestId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }

        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var random = window.crypto && typeof window.crypto.getRandomValues === 'function'
                ? window.crypto.getRandomValues(new Uint8Array(1))[0] & 15
                : Math.floor(Math.random() * 16);
            var value = c === 'x' ? random : (random & 3) | 8;
            return value.toString(16);
        });
    }

    // Render narrative with [^N] anchors as clickable pills that highlight
    // the matching fact card. Falls back to plain text if no anchors.
    function renderNarrative(text, facts) {
        var fragment = document.createDocumentFragment();
        var pattern = /\[\^?(\d+)\]/g;
        var lastIndex = 0;
        var match;
        while ((match = pattern.exec(text)) !== null) {
            if (match.index > lastIndex) {
                fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
            }
            var anchor = parseInt(match[1], 10);
            var pill = document.createElement('a');
            pill.href = '#';
            pill.className = 'badge badge-info';
            pill.textContent = match[1];
            pill.style.marginLeft = '2px';
            pill.style.marginRight = '2px';
            pill.style.cursor = 'pointer';
            pill.addEventListener('click', function (a) {
                return function (ev) {
                    ev.preventDefault();
                    flashFactCard(a);
                };
            }(anchor));
            fragment.appendChild(pill);
            lastIndex = pattern.lastIndex;
        }
        if (lastIndex < text.length) {
            fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
        }
        return fragment;
    }

    function renderFactCard(fact) {
        var card = document.createElement('div');
        card.className = 'border rounded p-2 mb-1 bg-white';
        if (typeof fact.anchor === 'number') {
            card.setAttribute('data-anchor', String(fact.anchor));
        }

        var header = document.createElement('div');
        header.className = 'd-flex align-items-start';

        if (typeof fact.anchor === 'number') {
            var anchorBadge = document.createElement('span');
            anchorBadge.className = 'badge badge-info mr-2';
            anchorBadge.textContent = String(fact.anchor);
            header.appendChild(anchorBadge);
        }

        var typeBadge = document.createElement('span');
        typeBadge.className = 'badge badge-secondary mr-2';
        typeBadge.textContent = fact.type || 'fact';
        header.appendChild(typeBadge);

        var textEl = document.createElement('span');
        textEl.textContent = fact.text || '';
        header.appendChild(textEl);

        card.appendChild(header);

        var excerpts = (fact.verbatim_excerpts || []).filter(function (e) {
            return typeof e === 'string' && e.length > 0;
        });
        if (excerpts.length > 0) {
            var details = document.createElement('details');
            details.className = 'mt-1 small';

            var summary = document.createElement('summary');
            summary.className = 'text-muted';
            summary.style.cursor = 'pointer';
            summary.textContent = 'show source';
            details.appendChild(summary);

            var quoteWrap = document.createElement('div');
            quoteWrap.className = 'border-left pl-2 mt-1 text-monospace';
            quoteWrap.style.borderColor = '#dee2e6';
            excerpts.forEach(function (excerpt, idx) {
                var line = document.createElement('div');
                line.style.whiteSpace = 'pre-wrap';
                line.textContent = excerpt;
                if (idx > 0) {
                    line.classList.add('mt-1');
                }
                quoteWrap.appendChild(line);
            });
            details.appendChild(quoteWrap);
            card.appendChild(details);
        }

        return card;
    }

    function flashFactCard(anchor) {
        var card = log.querySelector('[data-anchor="' + anchor + '"]');
        if (!card) {
            return;
        }
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        var original = card.style.boxShadow;
        card.style.transition = 'box-shadow 0.4s';
        card.style.boxShadow = '0 0 0 3px rgba(23, 162, 184, 0.5)';
        setTimeout(function () {
            card.style.boxShadow = original;
        }, 800);
    }

    function setPending(pending) {
        state.pending = pending;
        sendBtn.disabled = pending;
        input.disabled = pending;
        sendBtn.textContent = pending ? 'Thinking…' : 'Send';
    }

    function send(text) {
        var trimmed = (text || '').trim();
        if (!trimmed || state.pending) {
            return;
        }

        state.messages.push({ role: 'user', content: trimmed });
        appendUserBubble(trimmed);
        input.value = '';
        state.pendingRequestId = newRequestId();
        setPending(true);
        startPendingIndicator();

        var payload = {
            request_id: state.pendingRequestId,
            conversation_id: state.conversationId,
            messages: state.messages
        };

        fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'APICSRFTOKEN': csrf,
                'Accept': 'application/json'
            },
            body: JSON.stringify(payload)
        }).then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok, status: response.status, data: data };
            }, function () {
                return { ok: response.ok, status: response.status, data: null };
            });
        }).then(function (result) {
            var data = result.data;
            stopPendingIndicator();
            // Treat non-JSON or non-object responses as http_error so we
            // don't TypeError our way into a misleading "network" message.
            if (!result.ok || !data || typeof data !== 'object') {
                var code = (data && data.error) || 'http_error';
                var requestId = data && data.request_id;
                appendErrorBubble(code, requestId);
                state.messages.pop();
                return;
            }
            if (data.error) {
                appendErrorBubble(data.error, data.request_id);
                state.messages.pop();
                return;
            }
            if (isBlankAssistantResponse(data)) {
                appendErrorBubble('empty_response', data.request_id);
                state.messages.pop();
                return;
            }
            if (data.conversation_id) {
                state.conversationId = data.conversation_id;
            }
            state.messages.push({
                role: 'assistant',
                content: data.narrative || ''
            });
            appendAssistantBubble(data.narrative, data.facts, data.verification_failures, data.meta);
        }).catch(function () {
            stopPendingIndicator();
            appendErrorBubble('network', null);
            state.messages.pop();
        }).then(function () {
            setPending(false);
            input.focus();
        });
    }

    function isBlankAssistantResponse(data) {
        return !((typeof data.narrative === 'string' && data.narrative.trim() !== '') ||
            (Array.isArray(data.facts) && data.facts.length > 0) ||
            (Array.isArray(data.verification_failures) && data.verification_failures.length > 0));
    }

    form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        send(input.value);
    });
})();

(function () {
    'use strict';

    var panel = document.getElementById('oe-ai-agent-panel');
    if (!panel) {
        return;
    }

    var button = document.getElementById('oe-ai-agent-generate');
    var content = document.getElementById('oe-ai-agent-content');
    var pid = panel.getAttribute('data-pid');
    var csrf = panel.getAttribute('data-csrf');
    var endpoint = '/apis/default/api/ai/brief/' + encodeURIComponent(pid);

    function setIdle() {
        content.classList.remove('text-danger');
        content.classList.add('text-muted');
        content.textContent = 'Click Generate brief to summarize this chart.';
        button.disabled = false;
    }

    function setLoading() {
        content.classList.remove('text-danger');
        content.classList.remove('text-muted');
        content.textContent = 'Generating brief…';
        button.disabled = true;
    }

    // Maps controller error codes to human-readable copy. Codes here must
    // match the strings BriefController.finalize() puts in the JSON body.
    var ERROR_COPY = {
        forbidden: 'You don\'t have access to this patient\'s chart.',
        no_authenticated_user: 'Your session has expired. Please log in again and try again.',
        patient_not_found: 'Patient record not found.',
        token_mint_failed: 'Could not authorize the AI service for this chart.',
        sidecar_unreachable: 'The AI service is unreachable.',
        http_error: 'The AI service returned an unexpected response.',
        network: 'Could not reach the AI service.'
    };

    var RETRYABLE = {
        token_mint_failed: true,
        sidecar_unreachable: true,
        http_error: true,
        network: true
    };

    function setError(code, fallbackDetail, requestId) {
        content.classList.remove('text-muted', 'text-danger');
        content.innerHTML = '';

        var alert = document.createElement('div');
        alert.className = 'alert alert-danger mb-0';

        var msg = document.createElement('div');
        msg.textContent = ERROR_COPY[code] || fallbackDetail || 'Something went wrong.';
        alert.appendChild(msg);

        if (requestId) {
            // Surfacing request_id lets a clinician quote it to support; the
            // audit log row for this attempt has the same id.
            var rid = document.createElement('div');
            rid.className = 'small text-muted mt-1';
            rid.textContent = 'Request ID: ' + requestId;
            alert.appendChild(rid);
        }

        if (RETRYABLE[code]) {
            var retryBtn = document.createElement('button');
            retryBtn.type = 'button';
            retryBtn.className = 'btn btn-sm btn-outline-danger mt-2';
            retryBtn.textContent = 'Retry';
            retryBtn.addEventListener('click', generate);
            alert.appendChild(retryBtn);
        }

        content.appendChild(alert);
        button.disabled = false;
    }

    function renderItem(item) {
        var li = document.createElement('li');
        li.className = 'mb-2';

        var badge = document.createElement('span');
        badge.className = 'badge badge-secondary mr-2';
        badge.textContent = item.type || 'item';
        li.appendChild(badge);
        li.appendChild(document.createTextNode(item.text || ''));

        var excerpts = (item.verbatim_excerpts || []).filter(function (e) {
            return typeof e === 'string' && e.length > 0;
        });
        if (excerpts.length === 0) {
            return li;
        }

        // Verbatim source: lets the doc spot-check the paraphrase against the
        // literal chart text (ARCH §6.3 mitigation for citation-but-misinterpretation).
        var details = document.createElement('details');
        details.className = 'mt-1 ml-4 small';

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

        li.appendChild(details);
        return li;
    }

    function renderItems(data) {
        content.classList.remove('text-muted', 'text-danger');
        var items = (data && data.items) || [];
        var failures = (data && data.verification_failures) || [];

        content.innerHTML = '';

        if (items.length === 0) {
            var empty = document.createElement('div');
            empty.className = 'text-muted';
            empty.textContent = 'No verified items to report — open chart manually.';
            content.appendChild(empty);
        } else {
            var ul = document.createElement('ul');
            ul.className = 'list-unstyled mb-0';
            items.forEach(function (item) {
                ul.appendChild(renderItem(item));
            });
            content.appendChild(ul);
        }

        if (failures.length > 0) {
            var note = document.createElement('div');
            note.className = 'small text-muted mt-2';
            note.textContent = failures.length +
                ' item' + (failures.length === 1 ? '' : 's') +
                ' dropped by verifier';
            note.title = failures.map(function (f) {
                return (f.rule || '?') + ': ' + (f.detail || '');
            }).join('\n');
            content.appendChild(note);
        }

        appendUsageFooter(content, data && data.meta);

        button.disabled = false;
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
        if (typeof usage.total_tokens === 'number' && usage.total_tokens > 0) {
            parts.push(usage.total_tokens + ' tok');
        } else if (
            typeof usage.prompt_tokens === 'number' && typeof usage.completion_tokens === 'number'
            && (usage.prompt_tokens + usage.completion_tokens) > 0
        ) {
            parts.push((usage.prompt_tokens + usage.completion_tokens) + ' tok');
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

    function generate() {
        setLoading();
        fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'APICSRFTOKEN': csrf,
                'Accept': 'application/json'
            },
            body: '{}'
        }).then(function (response) {
            // Parse JSON regardless of status — the controller returns
            // {error, request_id} on 4xx/5xx too.
            return response.json().then(function (data) {
                return { ok: response.ok, status: response.status, data: data };
            }, function () {
                return { ok: response.ok, status: response.status, data: null };
            });
        }).then(function (result) {
            var data = result.data;
            if (!result.ok || (data && data.error)) {
                var code = (data && data.error) || 'http_error';
                var requestId = data && data.request_id;
                setError(code, 'HTTP ' + result.status, requestId);
                return;
            }
            renderItems(data);
        }).catch(function (err) {
            setError('network', err && err.message, null);
        });
    }

    button.addEventListener('click', generate);

    setIdle();
})();

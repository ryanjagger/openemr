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
    var recentDocsEndpoint = '/apis/default/api/ai/documents/recent/' + encodeURIComponent(pid);
    var ingestDocsEndpoint = '/apis/default/api/ai/documents/ingest/' + encodeURIComponent(pid);
    var docLoadBtn = document.getElementById('oe-ai-agent-doc-load');
    var docIngestBtn = document.getElementById('oe-ai-agent-doc-ingest');
    var docList = document.getElementById('oe-ai-agent-doc-list');
    var docStatus = document.getElementById('oe-ai-agent-doc-status');

    // In-memory conversation state — ephemeral by design (per ARCH chat
    // addendum). A reload throws this away; the server's conversation
    // store also TTLs after 30 minutes.
    var state = {
        conversationId: null,
        messages: [],
        pending: false
    };

    // Maps controller error codes to human-readable copy. Codes here must
    // match the strings ChatController.finalize() puts in the JSON body.
    var ERROR_COPY = {
        forbidden: 'You don\'t have access to this patient\'s chart.',
        no_authenticated_user: 'Your session has expired. Please log in again and try again.',
        patient_not_found: 'Patient record not found.',
        token_mint_failed: 'Could not authorize the AI service for this chart.',
        sidecar_unreachable: 'The AI service is unreachable.',
        empty_messages: 'Type a question first.',
        http_error: 'The AI service returned an unexpected response.',
        document_ingestion_enqueue_failed: 'Could not start document ingestion.',
        job_not_found: 'Document ingestion job was not found.',
        no_eligible_documents: 'Select at least one recent PDF or PNG document.',
        network: 'Could not reach the AI service.'
    };

    function setDocStatus(text, isError) {
        if (!docStatus) {
            return;
        }
        docStatus.className = 'small mb-2 ' + (isError ? 'text-danger' : 'text-muted');
        docStatus.textContent = text;
    }

    function fetchJson(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok, status: response.status, data: data };
            }, function () {
                return { ok: response.ok, status: response.status, data: null };
            });
        });
    }

    function loadRecentDocuments() {
        if (!docList || !docIngestBtn) {
            return;
        }
        if (docLoadBtn) {
            docLoadBtn.disabled = true;
        }
        docIngestBtn.classList.add('d-none');
        docList.textContent = '';
        setDocStatus('Loading recent PDF/PNG documents...', false);

        fetchJson(recentDocsEndpoint, {
            method: 'GET',
            credentials: 'same-origin',
            headers: {
                'APICSRFTOKEN': csrf,
                'Accept': 'application/json'
            }
        }).then(function (result) {
            var data = result.data;
            if (!result.ok || !data || typeof data !== 'object') {
                setDocStatus(ERROR_COPY[(data && data.error) || 'http_error'], true);
                return;
            }
            renderRecentDocuments(data.documents || []);
        }).catch(function () {
            setDocStatus(ERROR_COPY.network, true);
        }).finally(function () {
            if (docLoadBtn) {
                docLoadBtn.disabled = false;
            }
        });
    }

    function renderRecentDocuments(documents) {
        docList.textContent = '';
        if (!documents.length) {
            setDocStatus('No recent eligible PDF or PNG documents found.', false);
            return;
        }

        var table = document.createElement('table');
        table.className = 'table table-sm table-bordered bg-white mb-2';
        var tbody = document.createElement('tbody');
        documents.forEach(function (doc) {
            var tr = document.createElement('tr');
            tr.setAttribute('data-document-id', String(doc.id));

            var nameCell = document.createElement('td');
            var title = document.createElement('div');
            var filename = document.createElement('span');
            filename.textContent = doc.filename || ('Document ' + doc.id);
            title.appendChild(filename);
            if (doc.already_ingested) {
                var indexedBadge = document.createElement('span');
                indexedBadge.className = 'badge badge-success ml-2';
                indexedBadge.setAttribute('title', 'Already ingested');
                var check = document.createElement('i');
                check.className = 'fa fa-check mr-1';
                check.setAttribute('aria-hidden', 'true');
                indexedBadge.appendChild(check);
                indexedBadge.appendChild(document.createTextNode('Indexed'));
                title.appendChild(indexedBadge);
            }
            nameCell.appendChild(title);
            var meta = document.createElement('div');
            meta.className = 'small text-muted';
            meta.textContent = [
                doc.docdate,
                doc.category_name,
                doc.mimetype,
                indexedSummary(doc)
            ].filter(Boolean).join(' · ');
            nameCell.appendChild(meta);
            tr.appendChild(nameCell);

            var typeCell = document.createElement('td');
            typeCell.style.width = '190px';
            var select = document.createElement('select');
            select.className = 'form-control form-control-sm';
            select.setAttribute('data-document-type', 'true');
            [
                ['skip', 'Skip'],
                ['lab_report', 'Lab report'],
                ['intake_form', 'Intake form']
            ].forEach(function (optionPair) {
                var option = document.createElement('option');
                option.value = optionPair[0];
                option.textContent = optionPair[1];
                select.appendChild(option);
            });
            typeCell.appendChild(select);
            tr.appendChild(typeCell);
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        docList.appendChild(table);
        docIngestBtn.classList.remove('d-none');
        setDocStatus('Confirm each document type, then ingest the selected documents.', false);
    }

    function indexedSummary(doc) {
        if (!doc.already_ingested) {
            return null;
        }
        var parts = ['indexed'];
        if (doc.indexed_document_type) {
            parts.push(String(doc.indexed_document_type).replace('_', ' '));
        }
        if (doc.indexed_fact_count) {
            parts.push(String(doc.indexed_fact_count) + ' facts');
        }
        return parts.join(' · ');
    }

    function selectedDocumentsForIngestion() {
        if (!docList) {
            return [];
        }
        return Array.prototype.slice.call(docList.querySelectorAll('tr[data-document-id]')).map(function (row) {
            var select = row.querySelector('[data-document-type]');
            if (!select || select.value === 'skip') {
                return null;
            }
            return {
                id: parseInt(row.getAttribute('data-document-id'), 10),
                document_type: select.value
            };
        }).filter(function (doc) {
            return doc && doc.id > 0;
        });
    }

    function startDocumentIngestion() {
        if (!docIngestBtn) {
            return;
        }
        var documents = selectedDocumentsForIngestion();
        if (!documents.length) {
            setDocStatus(ERROR_COPY.no_eligible_documents, true);
            return;
        }
        docIngestBtn.disabled = true;
        setDocStatus('Starting document ingestion...', false);
        fetchJson(ingestDocsEndpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'APICSRFTOKEN': csrf,
                'Accept': 'application/json'
            },
            body: JSON.stringify({ documents: documents })
        }).then(function (result) {
            var data = result.data;
            if (!result.ok || !data || typeof data !== 'object') {
                setDocStatus(ERROR_COPY[(data && data.error) || 'http_error'], true);
                docIngestBtn.disabled = false;
                return;
            }
            renderJobStatus(data);
            pollDocumentJob(data.job_id);
        }).catch(function () {
            setDocStatus(ERROR_COPY.network, true);
            docIngestBtn.disabled = false;
        });
    }

    function pollDocumentJob(jobId) {
        if (!jobId) {
            return;
        }
        window.setTimeout(function () {
            fetchJson('/apis/default/api/ai/documents/jobs/' + encodeURIComponent(pid) + '/' + encodeURIComponent(jobId), {
                method: 'GET',
                credentials: 'same-origin',
                headers: {
                    'APICSRFTOKEN': csrf,
                    'Accept': 'application/json'
                }
            }).then(function (result) {
                var data = result.data;
                if (!result.ok || !data || typeof data !== 'object') {
                    setDocStatus(ERROR_COPY[(data && data.error) || 'http_error'], true);
                    return;
                }
                renderJobStatus(data);
                if (data.status === 'pending' || data.status === 'processing') {
                    pollDocumentJob(jobId);
                }
            }).catch(function () {
                setDocStatus(ERROR_COPY.network, true);
                if (docIngestBtn) {
                    docIngestBtn.disabled = false;
                }
            });
        }, 3000);
    }

    function renderJobStatus(job) {
        var status = job.status || 'pending';
        var total = job.document_count || 0;
        var processed = job.processed_count || 0;
        var failed = job.failed_count || 0;
        if (docIngestBtn) {
            docIngestBtn.disabled = status === 'pending' || status === 'processing';
        }
        if (status === 'completed') {
            setDocStatus('Document ingestion complete. New evidence is available in chat.', false);
        } else if (status === 'partial') {
            setDocStatus('Document ingestion partially completed: ' + processed + ' of ' + total + ' processed, ' + failed + ' failed.', true);
        } else if (status === 'failed') {
            setDocStatus('Document ingestion failed for all selected documents.', true);
        } else {
            setDocStatus('Document ingestion ' + status + ': ' + processed + ' of ' + total + ' processed.', false);
        }
    }

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
                ' fact' + (failures.length === 1 ? '' : 's') +
                ' dropped by verifier';
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
        setPending(true);

        var payload = {
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
            if (data.conversation_id) {
                state.conversationId = data.conversation_id;
            }
            state.messages.push({
                role: 'assistant',
                content: data.narrative || ''
            });
            appendAssistantBubble(data.narrative, data.facts, data.verification_failures, data.meta);
        }).catch(function () {
            appendErrorBubble('network', null);
            state.messages.pop();
        }).then(function () {
            setPending(false);
            input.focus();
        });
    }

    form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        send(input.value);
    });
    if (docLoadBtn) {
        docLoadBtn.addEventListener('click', loadRecentDocuments);
    }
    if (docIngestBtn) {
        docIngestBtn.addEventListener('click', startDocumentIngestion);
    }
    loadRecentDocuments();
})();

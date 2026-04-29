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

    function setError(message) {
        content.classList.remove('text-muted');
        content.classList.add('text-danger');
        content.textContent = message;
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

        button.disabled = false;
    }

    button.addEventListener('click', function () {
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
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            return response.json();
        }).then(function (data) {
            if (data && data.error) {
                setError('Brief failed: ' + data.error);
                return;
            }
            renderItems(data);
        }).catch(function (err) {
            setError('Brief failed: ' + (err && err.message ? err.message : 'unknown error'));
        });
    });

    setIdle();
})();

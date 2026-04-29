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
                var li = document.createElement('li');
                li.className = 'mb-1';
                var badge = document.createElement('span');
                badge.className = 'badge badge-secondary mr-2';
                badge.textContent = item.type || 'item';
                li.appendChild(badge);
                li.appendChild(document.createTextNode(item.text || ''));
                ul.appendChild(li);
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

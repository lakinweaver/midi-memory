/* Shared helpers: API access, toasts, formatting, and the live SSE console. */
(function () {
  'use strict';

  /* ------------------------------------------------------------- fetching -- */
  async function api(path, options) {
    const res = await fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    }, options || {}));

    if (res.status === 401) { location.href = '/login'; throw new Error('Not signed in'); }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }

  /* --------------------------------------------------------------- toasts -- */
  function toast(message, kind) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'toast' + (kind === 'error' ? ' err' : '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .3s, transform .3s';
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(() => el.remove(), 320);
    }, 2600);
  }

  /* ----------------------------------------------------------- formatting -- */
  function formatDuration(ms) {
    const total = Math.round((ms || 0) / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);

    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (sameDay) return 'Today ' + time;
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday ' + time;

    const opts = { month: 'short', day: 'numeric' };
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString([], opts) + ' ' + time;
  }

  const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  function noteName(midi) {
    if (midi === null || midi === undefined) return '—';
    return NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
  }

  // Escapes quotes as well as angle brackets: tag names are interpolated into
  // HTML *attributes*, where a bare double quote would break out of the value.
  const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
  }

  /* ------------------------------------------------------ the live console -- */
  // One EventSource per tab, re-dispatched as DOM events so pages can listen.
  // The header shows one readout per capture client; with a single client -- the
  // usual case -- it reads like the single device panel it replaced.
  const clients = new Map();

  function startConsole() {
    const strip = document.getElementById('clients-strip');
    if (!strip) return;

    let source = null;
    let retry = 1000;
    let wasDropped = false;

    function label(client) {
      if (client.state === 'offline') return 'offline';
      if (client.state === 'recording') {
        // The count is for the session in progress, so it holds steady once you
        // stop playing -- the label is what makes that read as a tally, not a timer.
        const n = Number(client.note_count) || 0;
        return n ? 'recording · ' + n + (n === 1 ? ' note' : ' notes') : 'recording';
      }
      return client.connected ? 'idle' : 'no keyboard';
    }

    function paint() {
      const empty = document.getElementById('clients-empty');
      if (clients.size === 0) {
        strip.innerHTML = '<span class="readout muted" id="clients-empty">no capture clients</span>';
        return;
      }
      if (empty) empty.remove();

      const wanted = new Set();
      for (const client of clients.values()) {
        wanted.add(client.id);
        let node = strip.querySelector('[data-client="' + CSS.escape(client.id) + '"]');
        if (!node) {
          node = document.createElement('div');
          node.className = 'readout client';
          node.dataset.client = client.id;
          node.innerHTML = '<span class="lamp"></span><span class="who"></span>'
                         + '<span class="val"></span>';
          strip.appendChild(node);
        }
        const lamp = node.querySelector('.lamp');
        lamp.className = 'lamp'
          + (client.state === 'recording' ? ' rec'
             : client.state === 'offline' ? '' : (client.connected ? ' on' : ''));
        // With one client its name is noise; with several it is the whole point.
        const who = node.querySelector('.who');
        who.textContent = client.name;
        who.hidden = clients.size < 2;
        node.querySelector('.val').textContent = label(client);
        node.title = client.name + (client.port_name ? ' — ' + client.port_name : '');
      }
      strip.querySelectorAll('[data-client]').forEach((node) => {
        if (!wanted.has(node.dataset.client)) node.remove();
      });
    }

    function replaceAll(list) {
      clients.clear();
      (list || []).forEach((c) => clients.set(c.id, c));
      paint();
    }

    function update(msg) {
      const existing = clients.get(msg.client_id) || { id: msg.client_id };
      clients.set(msg.client_id, Object.assign(existing, msg, { id: msg.client_id }));
      paint();
    }

    function connect() {
      source = new EventSource('/api/stream');

      source.onopen = () => {
        retry = 1000;
        // Anything that happened while we were disconnected was missed entirely,
        // so tell the page to resync rather than silently drifting out of date.
        if (wasDropped) {
          wasDropped = false;
          document.dispatchEvent(new CustomEvent('midi:reconnected'));
        }
      };

      source.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }

        if (msg.type === 'status') replaceAll(msg.clients);
        else if (msg.type === 'client_status') update(msg);

        document.dispatchEvent(new CustomEvent('midi:' + msg.type, { detail: msg }));
      };

      source.onerror = () => {
        wasDropped = true;
        source.close();
        strip.querySelectorAll('.lamp').forEach((l) => { l.className = 'lamp'; });
        strip.querySelectorAll('.val').forEach((v) => { v.textContent = 'reconnecting…'; });
        // Back off so a server that is restarting isn't hammered.
        retry = Math.min(retry * 2, 15000);
        setTimeout(connect, retry);
      };
    }

    connect();
  }

  /* -------------------------------------------------------------- settings -- */
  function startSettings() {
    const dialog = document.getElementById('settings-dialog');
    const openBtn = document.getElementById('open-settings');
    if (!dialog || !openBtn) return;

    const f = {
      readonly: document.getElementById('set-readonly'),
      sampleStatus: document.getElementById('sample-status'),
      sampleDownload: document.getElementById('sample-download'),
      clientList: document.getElementById('client-list'),
      newName: document.getElementById('new-client-name'),
      addClient: document.getElementById('add-client'),
      reveal: document.getElementById('secret-reveal'),
      revealFor: document.getElementById('secret-for'),
      revealValue: document.getElementById('secret-value'),
      revealCopy: document.getElementById('secret-copy'),
    };

    const READ_ONLY_LABELS = {
      port: 'Port', host: 'Bind address', data_dir: 'Data directory',
      auth_enabled: 'Password set',
    };

    function fill(payload) {
      const rows = Object.entries(READ_ONLY_LABELS).map(([key, label]) => {
        let value = payload.read_only[key];
        if (typeof value === 'boolean') value = value ? 'yes' : 'no';
        return '<dt>' + label + '</dt><dd>' + escapeHtml(value) + '</dd>';
      });
      f.readonly.innerHTML = rows.join('');
      fillSamples(payload.samples);
      fillClients(payload.clients);
    }

    function fillSamples(state) {
      if (!state) return;
      const complete = state.ready && state.installed >= state.expected;
      f.sampleStatus.className = complete ? 'ok' : 'missing';
      f.sampleStatus.textContent = complete
        ? state.installed + ' of ' + state.expected + ' installed'
        : (state.installed
            ? state.installed + ' of ' + state.expected + ' installed — incomplete'
            : 'not installed — using the built-in tone');
      f.sampleDownload.hidden = complete;
      f.sampleDownload.textContent = state.installed ? 'Finish downloading' : 'Download';
    }

    function stateLabel(client) {
      if (client.revoked) return 'revoked';
      if (client.state === 'recording') return 'recording';
      if (client.state === 'offline') return 'not reporting';
      return client.connected ? 'idle' : 'idle · no keyboard';
    }

    function fillClients(list) {
      if (!list || !list.length) {
        f.clientList.innerHTML =
          '<p class="hint empty">No clients yet. Add one here, then paste its '
          + 'secret into the client\'s own settings page.</p>';
        return;
      }
      f.clientList.innerHTML = list.map((client) => {
        const lamp = client.revoked ? '' :
          (client.state === 'recording' ? ' rec' : client.state === 'offline' ? '' : ' on');
        const count = client.session_count || 0;
        return '<div class="client-row' + (client.revoked ? ' revoked' : '') + '"'
             + ' data-id="' + escapeHtml(client.id) + '">'
             + '<span class="lamp' + lamp + '"></span>'
             + '<div class="client-main">'
               + '<div class="client-name">' + escapeHtml(client.name) + '</div>'
               + '<div class="client-meta">' + escapeHtml(stateLabel(client))
                 + (client.port_name ? ' · ' + escapeHtml(client.port_name) : '')
                 + ' · ' + count + (count === 1 ? ' recording' : ' recordings')
                 + (client.pending_uploads
                     ? ' · ' + client.pending_uploads + ' waiting to upload' : '')
               + '</div>'
             + '</div>'
             + '<div class="client-actions">'
               + '<button class="btn ghost" data-act="secret">New secret</button>'
               + '<button class="btn ghost" data-act="'
                 + (client.revoked ? 'restore">Restore' : 'revoke">Revoke') + '</button>'
               + (count ? '' : '<button class="btn ghost danger" data-act="delete">Remove</button>')
             + '</div>'
             + '</div>';
      }).join('');
    }

    function reveal(name, secret) {
      f.revealFor.textContent = name;
      f.revealValue.textContent = secret;
      f.reveal.hidden = false;
    }

    async function refresh() {
      fill(await api('/api/settings'));
    }

    async function addClient() {
      const name = f.newName.value.trim();
      if (!name) { toast('Give the client a name first', 'error'); return; }
      try {
        const body = await api('/api/clients', {
          method: 'POST', body: JSON.stringify({ name }),
        });
        f.newName.value = '';
        reveal(body.client.name, body.secret);
        await refresh();
      } catch (err) { toast(err.message, 'error'); }
    }

    async function clientAction(event) {
      const button = event.target.closest('button[data-act]');
      if (!button) return;
      const row = button.closest('.client-row');
      const id = row.dataset.id;
      const name = row.querySelector('.client-name').textContent;
      const act = button.dataset.act;

      try {
        if (act === 'secret') {
          if (!confirm('Issue a new secret for “' + name + '”?\n\n'
                       + 'Its current secret stops working immediately, and it will '
                       + 'not be able to upload until you paste the new one into it.')) return;
          const body = await api('/api/clients/' + encodeURIComponent(id) + '/secret',
                                 { method: 'POST' });
          reveal(name, body.secret);
        } else if (act === 'revoke') {
          if (!confirm('Stop accepting uploads from “' + name + '”?\n\n'
                       + 'Its recordings stay in the library.')) return;
          await api('/api/clients/' + encodeURIComponent(id) + '/revoke', { method: 'POST' });
        } else if (act === 'restore') {
          await api('/api/clients/' + encodeURIComponent(id) + '/restore', { method: 'POST' });
        } else if (act === 'delete') {
          if (!confirm('Remove “' + name + '”?')) return;
          await api('/api/clients/' + encodeURIComponent(id), { method: 'DELETE' });
        }
        await refresh();
      } catch (err) { toast(err.message, 'error'); }
    }

    async function downloadSamples() {
      const button = f.sampleDownload;
      button.disabled = true;
      const previous = button.textContent;
      button.textContent = 'Downloading…';
      try {
        fill(await api('/api/settings/samples', { method: 'POST' }));
        toast('Piano samples ready — reload a session to hear them');
      } catch (err) {
        toast('Could not download samples: ' + err.message, 'error');
        button.textContent = previous;
      } finally {
        button.disabled = false;
      }
    }

    async function open() {
      try {
        f.reveal.hidden = true;
        await refresh();
        dialog.showModal();
      } catch (err) { toast(err.message, 'error'); }
    }

    openBtn.addEventListener('click', open);
    f.sampleDownload.addEventListener('click', downloadSamples);
    f.addClient.addEventListener('click', addClient);
    f.clientList.addEventListener('click', clientAction);
    f.newName.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); addClient(); }
    });
    f.revealCopy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(f.revealValue.textContent);
        toast('Secret copied');
      } catch (_) {
        // Clipboard access needs a secure context, which plain HTTP on a LAN is
        // not. Select it instead so it is one keystroke away.
        const range = document.createRange();
        range.selectNodeContents(f.revealValue);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        toast('Select and copy the secret above');
      }
    });
    // Live status should keep the open dialog honest.
    document.addEventListener('midi:client_status', () => {
      if (dialog.open) refresh().catch(() => {});
    });
  }

  window.MM = { api, toast, formatDuration, formatDate, noteName, escapeHtml };
  document.addEventListener('DOMContentLoaded', () => { startConsole(); startSettings(); });
})();

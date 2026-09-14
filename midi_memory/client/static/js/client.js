/* The capture client's single page: poll status, edit settings, test the link. */
(function () {
  'use strict';

  const POLL_MS = 2000;

  const el = (id) => document.getElementById(id);
  const f = {
    server: el('f-server'), secret: el('f-secret'), name: el('f-name'),
    idle: el('f-idle'), minNotes: el('f-min-notes'),
    minSeconds: el('f-min-seconds'), device: el('f-device'),
  };

  // Fields the user is part-way through typing must not be overwritten by the
  // 2-second poll -- nothing is more annoying than a form that fights back.
  let dirty = new Set();
  Object.entries(f).forEach(([key, input]) => {
    input.addEventListener('input', () => dirty.add(key));
  });

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
    return res.json();
  }

  function toast(message, kind) {
    const host = el('toasts');
    const node = document.createElement('div');
    node.className = 'toast' + (kind === 'error' ? ' err' : '');
    node.textContent = message;
    host.appendChild(node);
    setTimeout(() => {
      node.style.transition = 'opacity .3s';
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 320);
    }, 3000);
  }

  const READ_ONLY_LABELS = {
    host: 'Bind address', port: 'Port', data_dir: 'Data directory',
    midi_source: 'Input backend', auth_enabled: 'Password set',
    keep_uploaded_days: 'Keep uploaded copies (days)',
  };

  function paint(payload) {
    const s = payload.settings, st = payload.status, link = payload.link;

    if (!dirty.has('idle')) f.idle.value = s.idle_seconds;
    if (!dirty.has('minNotes')) f.minNotes.value = s.min_notes;
    if (!dirty.has('minSeconds')) f.minSeconds.value = s.min_seconds;
    if (!dirty.has('device')) f.device.value = s.device_match || '';
    if (!dirty.has('server')) f.server.value = s.server_url || '';
    if (!dirty.has('name')) f.name.value = s.client_name || '';

    el('secret-hint').textContent = s.client_secret
      ? 'A secret is saved on this client. Leave blank to keep it, or paste a new one to replace it.'
      : 'Shown once when you add a client on the server. Paste it here.';

    // Keyboard
    el('lamp-device').className = 'lamp' + (st.connected ? ' on' : '');
    el('v-device').textContent = st.connected ? (st.port_name || 'connected') : 'no keyboard';

    // Recorder
    el('lamp-rec').className = 'lamp' + (st.recording ? ' rec' : '');
    el('v-rec').textContent = st.recording
      ? 'recording · ' + st.current_note_count +
        (st.current_note_count === 1 ? ' note' : ' notes')
      : 'idle';

    // Server link
    const lampLink = el('lamp-link');
    const vLink = el('v-link');
    if (!s.server_url || !s.client_secret) {
      lampLink.className = 'lamp'; vLink.textContent = 'not configured';
    } else if (link.authenticated) {
      lampLink.className = 'lamp on'; vLink.textContent = 'connected';
    } else if (link.reachable) {
      lampLink.className = 'lamp warn'; vLink.textContent = 'secret rejected';
    } else {
      lampLink.className = 'lamp warn'; vLink.textContent = 'unreachable';
    }

    el('v-pending').textContent = link.pending === 0
      ? 'nothing waiting'
      : link.pending + (link.pending === 1 ? ' session' : ' sessions');

    const banner = el('error-banner');
    banner.hidden = !link.last_error;
    if (link.last_error) banner.textContent = link.last_error;

    el('setup-banner').hidden = !!(s.server_url && s.client_secret);

    el('readonly').innerHTML = Object.entries(READ_ONLY_LABELS).map(([key, label]) => {
      let value = payload.read_only[key];
      if (typeof value === 'boolean') value = value ? 'yes' : 'no';
      const safe = String(value == null ? '' : value)
        .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                                       '"': '&quot;', "'": '&#39;' }[c]));
      return '<dt>' + label + '</dt><dd>' + safe + '</dd>';
    }).join('');
  }

  async function refresh() {
    try { paint(await api('/api/status')); } catch (_) { /* keep polling */ }
  }

  async function save() {
    const body = {
      idle_seconds: parseFloat(f.idle.value),
      min_notes: parseInt(f.minNotes.value, 10),
      min_seconds: parseFloat(f.minSeconds.value),
      device_match: f.device.value,
      server_url: f.server.value,
      client_name: f.name.value,
    };
    // Blank means "keep what is saved", so it is left out entirely.
    if (f.secret.value.trim()) body.client_secret = f.secret.value.trim();

    for (const [key, value] of Object.entries(body)) {
      if (typeof value === 'number' && Number.isNaN(value)) {
        toast('“' + key.replace(/_/g, ' ') + '” needs a number', 'error');
        return;
      }
    }
    try {
      const payload = await api('/api/settings', {
        method: 'PUT', body: JSON.stringify(body),
      });
      dirty = new Set();
      f.secret.value = '';
      paint(payload);
      toast('Settings saved');
    } catch (err) { toast(err.message, 'error'); }
  }

  async function test() {
    const button = el('test');
    button.disabled = true;
    try {
      const result = await api('/api/test-connection', { method: 'POST' });
      if (result.ok) toast('Connected — the server knows this client as “' + result.client_name + '”');
      else toast(result.error, 'error');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      button.disabled = false;
      refresh();
    }
  }

  async function uploadNow() {
    const button = el('upload-now');
    button.disabled = true;
    try {
      const result = await api('/api/upload-now', { method: 'POST' });
      paint(result);
      toast(result.uploaded
        ? 'Uploaded ' + result.uploaded + (result.uploaded === 1 ? ' session' : ' sessions')
        : 'Nothing was uploaded');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      button.disabled = false;
    }
  }

  el('save').addEventListener('click', save);
  el('test').addEventListener('click', test);
  el('upload-now').addEventListener('click', uploadNow);

  refresh();
  setInterval(refresh, POLL_MS);
})();

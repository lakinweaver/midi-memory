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
  function startConsole() {
    const lampDevice = document.getElementById('lamp-device');
    const lampRec = document.getElementById('lamp-rec');
    const deviceName = document.getElementById('device-name');
    const recState = document.getElementById('rec-state');
    if (!lampDevice) return;

    let source = null;
    let retry = 1000;
    let wasDropped = false;

    function paintDevice(connected, name) {
      lampDevice.classList.toggle('on', !!connected);
      deviceName.textContent = connected ? (name || 'connected') : 'no keyboard';
      deviceName.title = name || '';
    }

    function paintRecording(recording, noteCount) {
      lampRec.classList.toggle('rec', !!recording);
      if (!recording) { recState.textContent = 'idle'; return; }
      // The count is for the session in progress, so it holds steady once you
      // stop playing -- the label is what makes that read as a tally, not a timer.
      const n = Number(noteCount) || 0;
      recState.textContent = n
        ? 'recording · ' + n + (n === 1 ? ' note' : ' notes')
        : 'recording';
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

        switch (msg.type) {
          case 'status':
            paintDevice(msg.connected, msg.port_name);
            paintRecording(msg.recording, msg.current_note_count);
            break;
          case 'device_status':
            paintDevice(msg.connected, msg.port_name);
            break;
          case 'session_started':
            paintRecording(true, 0);
            break;
          case 'activity':
            paintRecording(true, msg.note_count);
            break;
          case 'session_saved':
            paintRecording(false, 0);
            break;
        }
        document.dispatchEvent(new CustomEvent('midi:' + msg.type, { detail: msg }));
      };

      source.onerror = () => {
        wasDropped = true;
        source.close();
        lampDevice.classList.remove('on');
        lampRec.classList.remove('rec');
        deviceName.textContent = 'reconnecting…';
        // Back off so a Pi that is rebooting isn't hammered.
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
      idle: document.getElementById('set-idle'),
      minNotes: document.getElementById('set-min-notes'),
      minSeconds: document.getElementById('set-min-seconds'),
      device: document.getElementById('set-device'),
      readonly: document.getElementById('set-readonly'),
      sampleStatus: document.getElementById('sample-status'),
      sampleDownload: document.getElementById('sample-download'),
      save: document.getElementById('settings-save'),
      reset: document.getElementById('settings-reset'),
    };

    const READ_ONLY_LABELS = {
      port: 'Port', host: 'Bind address', data_dir: 'Data directory',
      midi_source: 'Input backend', auth_enabled: 'Password set',
    };

    function fill(payload) {
      const s = payload.settings;
      f.idle.value = s.idle_seconds;
      f.minNotes.value = s.min_notes;
      f.minSeconds.value = s.min_seconds;
      f.device.value = s.device_match || '';

      const rows = Object.entries(READ_ONLY_LABELS).map(([key, label]) => {
        let value = payload.read_only[key];
        if (typeof value === 'boolean') value = value ? 'yes' : 'no';
        return '<dt>' + label + '</dt><dd>' + escapeHtml(value) + '</dd>';
      });
      // What is actually plugged in matters more than what was configured.
      const input = payload.input.connected ? payload.input.port_name : 'not connected';
      rows.push('<dt>Keyboard</dt><dd>' + escapeHtml(input) + '</dd>');
      f.readonly.innerHTML = rows.join('');
      fillSamples(payload.samples);
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
        fill(await api('/api/settings'));
        dialog.showModal();
      } catch (err) { toast(err.message, 'error'); }
    }

    async function save() {
      const body = {
        idle_seconds: parseFloat(f.idle.value),
        min_notes: parseInt(f.minNotes.value, 10),
        min_seconds: parseFloat(f.minSeconds.value),
        device_match: f.device.value,
      };
      for (const [key, value] of Object.entries(body)) {
        if (typeof value === 'number' && Number.isNaN(value)) {
          toast('“' + key.replace(/_/g, ' ') + '” needs a number', 'error');
          return;
        }
      }
      try {
        fill(await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) }));
        toast('Settings saved');
        dialog.close();
      } catch (err) { toast(err.message, 'error'); }
    }

    async function reset() {
      if (!confirm('Discard these settings and go back to the .env values?\n\n'
                   + 'The .env values take effect after the service restarts.')) return;
      try {
        fill(await api('/api/settings/reset', { method: 'POST' }));
        toast('Reset — restart the service to pick up .env');
      } catch (err) { toast(err.message, 'error'); }
    }

    openBtn.addEventListener('click', open);
    f.save.addEventListener('click', save);
    f.reset.addEventListener('click', reset);
    f.sampleDownload.addEventListener('click', downloadSamples);
    // Enter anywhere in the form should save, not silently dismiss the dialog.
    dialog.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); save(); }
    });
  }

  window.MM = { api, toast, formatDuration, formatDate, noteName, escapeHtml };
  document.addEventListener('DOMContentLoaded', () => { startConsole(); startSettings(); });
})();

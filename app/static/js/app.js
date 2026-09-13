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
    const pianoReadout = document.getElementById('piano-readout');
    if (!lampDevice) return;

    // The instrument is shared, so playback is visible (and stoppable) from any
    // page, not just the session that started it.
    function paintPlayback(playback) {
      if (!pianoReadout) return;
      pianoReadout.hidden = !(playback && playback.playing);
    }

    if (pianoReadout) {
      pianoReadout.addEventListener('click', async (e) => {
        e.preventDefault();
        try {
          paintPlayback(await api('/api/playback/stop', { method: 'POST' }));
        } catch (err) { toast(err.message, 'error'); }
      });
    }

    let source = null;
    let retry = 1000;

    function paintDevice(connected, name) {
      lampDevice.classList.toggle('on', !!connected);
      deviceName.textContent = connected ? (name || 'connected') : 'no keyboard';
      deviceName.title = name || '';
    }

    function paintRecording(recording, noteCount) {
      lampRec.classList.toggle('rec', !!recording);
      recState.textContent = recording
        ? 'recording' + (noteCount ? ' · ' + noteCount : '')
        : 'idle';
    }

    function connect() {
      source = new EventSource('/api/stream');

      source.onopen = () => { retry = 1000; };

      source.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }

        switch (msg.type) {
          case 'status':
            paintDevice(msg.connected, msg.port_name);
            paintRecording(msg.recording, msg.current_note_count);
            paintPlayback(msg.playback);
            break;
          case 'playback':
            paintPlayback(msg.playback);
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
        source.close();
        lampDevice.classList.remove('on');
        lampRec.classList.remove('rec');
        paintPlayback(null);
        deviceName.textContent = 'reconnecting…';
        // Back off so a Pi that is rebooting isn't hammered.
        retry = Math.min(retry * 2, 15000);
        setTimeout(connect, retry);
      };
    }

    connect();
  }

  window.MM = { api, toast, formatDuration, formatDate, noteName, escapeHtml };
  document.addEventListener('DOMContentLoaded', startConsole);
})();

/* Session detail: piano roll, transport, and inline editing. */
(function () {
  'use strict';
  const { api, toast, formatDuration, formatDate, noteName, escapeHtml } = window.MM;

  const data = JSON.parse(document.getElementById('session-data').textContent);
  const engine = new window.PianoEngine();

  const el = {
    canvas: document.getElementById('roll'),
    play: document.getElementById('play'),
    iconPlay: document.getElementById('icon-play'),
    iconPause: document.getElementById('icon-pause'),
    scrub: document.getElementById('scrub'),
    fill: document.getElementById('scrub-fill'),
    knob: document.getElementById('scrub-knob'),
    time: document.getElementById('time'),
    loop: document.getElementById('loop'),
    speed: document.getElementById('speed'),
    title: document.getElementById('title'),
    notes: document.getElementById('notes'),
    fav: document.getElementById('fav'),
    del: document.getElementById('delete'),
    tags: document.getElementById('session-tags'),
    tagInput: document.getElementById('tag-input'),
    mDate: document.getElementById('m-date'),
    mDuration: document.getElementById('m-duration'),
    mRange: document.getElementById('m-range'),
  };

  const state = {
    notes: [],
    pedal: [],          // times the sustain pedal went down
    duration: 0,
    position: 0,
    playing: false,
    loop: false,
    speed: 1,
    anchorCtx: 0,
    anchorPos: 0,
    cursor: 0,          // index of the next note to schedule
    lo: 48, hi: 84,
    tags: data.tags || [],
    favorite: data.favorite,
    warnedSynth: false,
  };

  /* ------------------------------------------------------------ the roll -- */
  const ctx2d = el.canvas.getContext('2d');
  let width = 0, height = 0;

  function resize() {
    const ratio = window.devicePixelRatio || 1;
    width = el.canvas.clientWidth;
    height = el.canvas.clientHeight;
    el.canvas.width = width * ratio;
    el.canvas.height = height * ratio;
    ctx2d.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw();
  }

  const PAD_L = 34, PAD_R = 10, PAD_Y = 10;

  function xOf(seconds) {
    const usable = width - PAD_L - PAD_R;
    return PAD_L + (seconds / Math.max(0.001, state.duration)) * usable;
  }

  function yOf(midi) {
    const usable = height - PAD_Y * 2;
    const span = Math.max(6, state.hi - state.lo);
    return PAD_Y + usable - ((midi - state.lo) / span) * usable;
  }

  function draw() {
    if (!width) return;
    ctx2d.clearRect(0, 0, width, height);

    const rowHeight = Math.max(2.5, (height - PAD_Y * 2) / Math.max(6, state.hi - state.lo));

    // Octave banding + C labels: enough reference to read the shape of a line.
    ctx2d.font = '9px "DM Mono", monospace';
    ctx2d.textBaseline = 'middle';
    for (let midi = Math.ceil(state.lo / 12) * 12; midi <= state.hi; midi += 12) {
      const y = yOf(midi);
      ctx2d.strokeStyle = 'rgba(239,230,216,.07)';
      ctx2d.lineWidth = 1;
      ctx2d.beginPath();
      ctx2d.moveTo(PAD_L, y + 0.5); ctx2d.lineTo(width - PAD_R, y + 0.5);
      ctx2d.stroke();
      ctx2d.fillStyle = 'rgba(239,230,216,.28)';
      ctx2d.fillText(noteName(midi), 4, y);
    }

    // Second ticks, coarsened so a long take doesn't turn into a solid block.
    const step = state.duration > 120 ? 30 : state.duration > 30 ? 10 : 5;
    ctx2d.strokeStyle = 'rgba(239,230,216,.04)';
    for (let t = step; t < state.duration; t += step) {
      const x = xOf(t);
      ctx2d.beginPath();
      ctx2d.moveTo(x + 0.5, PAD_Y); ctx2d.lineTo(x + 0.5, height - PAD_Y);
      ctx2d.stroke();
    }

    // Sustain, marked the way a DAW marks it: a line where the pedal went down,
    // rather than a tail on every note it happened to catch. The tails answered
    // "how long did this ring", which is a question about the sound; the line
    // answers "when did I pedal", which is the one you have while reading a roll.
    // Broad and faint rather than fine and sharp: a pedal mark is background for
    // the notes, so it wants width to be findable and little contrast to stay out
    // of the way. A hairline bright enough to see would read as an event itself.
    // Warmed most of the way from the cream toward the amber, so it belongs to
    // the same performance as the notes rather than to the chrome around them.
    ctx2d.lineWidth = 3;
    for (const t of state.pedal) {
      if (t > state.duration) continue;
      const x = xOf(t);
      ctx2d.strokeStyle = t <= state.position
        ? 'rgba(236,196,155,.15)' : 'rgba(236,196,155,.05)';
      ctx2d.beginPath();
      ctx2d.moveTo(x, PAD_Y); ctx2d.lineTo(x, height - PAD_Y);
      ctx2d.stroke();
    }

    // The notes: the bar is how long the key was held, and nothing else.
    for (const n of state.notes) {
      const x = xOf(n.s);
      const w = Math.max(2, xOf(n.s + n.d) - x);
      const y = yOf(n.n) - rowHeight / 2;
      const h = Math.max(2.5, rowHeight - 1);
      const played = n.s <= state.position;
      const intensity = 0.30 + (n.v / 127) * 0.7;

      ctx2d.fillStyle = played
        ? 'rgba(255,169,77,' + intensity.toFixed(2) + ')'
        : 'rgba(232,145,63,' + (intensity * 0.5).toFixed(2) + ')';

      const r = Math.min(2, h / 2);
      ctx2d.beginPath();
      if (ctx2d.roundRect) ctx2d.roundRect(x, y, w, h, r);
      else ctx2d.rect(x, y, w, h);
      ctx2d.fill();
    }

    // Playhead.
    const px = xOf(state.position);
    ctx2d.strokeStyle = 'rgba(255,169,77,.9)';
    ctx2d.lineWidth = 1.5;
    ctx2d.beginPath();
    ctx2d.moveTo(px, PAD_Y - 4); ctx2d.lineTo(px, height - PAD_Y + 4);
    ctx2d.stroke();
    ctx2d.fillStyle = 'rgba(255,169,77,1)';
    ctx2d.beginPath();
    ctx2d.arc(px, PAD_Y - 4, 2.5, 0, Math.PI * 2);
    ctx2d.fill();
  }

  /* ----------------------------------------------------------- transport -- */
  function currentPosition() {
    if (!state.playing) return state.position;
    return state.anchorPos + (engine.time - state.anchorCtx) * state.speed;
  }

  function reanchor(position) {
    state.position = Math.max(0, Math.min(position, state.duration));
    state.anchorPos = state.position;
    state.anchorCtx = engine.time + 0.06;   // small cushion for scheduling
    state.cursor = 0;
    while (state.cursor < state.notes.length &&
           state.notes[state.cursor].s < state.position) {
      state.cursor++;
    }
  }

  const LOOKAHEAD = 0.4;   // schedule this far ahead of the playhead
  function schedule() {
    if (!state.playing) return;
    const horizon = currentPosition() + LOOKAHEAD;
    while (state.cursor < state.notes.length && state.notes[state.cursor].s <= horizon) {
      const n = state.notes[state.cursor++];
      const when = state.anchorCtx + (n.s - state.anchorPos) / state.speed;
      // `r` is how long the note actually rang, pedal included.
      engine.play(n.n, n.v, Math.max(when, engine.time), (n.r || n.d) / state.speed);
    }
  }

  async function play() {
    try {
      // Wait for the sampled piano. The bytes are prefetched at page load, so
      // this is normally just a decode; the button shows it is working in case
      // a cold cache makes it take a moment.
      if (!engine.samplesReady) el.play.classList.add('loading');
      await engine.unlock();
      el.play.classList.remove('loading');

      if (!engine.beginPlayback() && !state.warnedSynth) {
        state.warnedSynth = true;
        toast(engine.samplesUnavailable
          ? 'Piano samples are missing — run scripts/fetch_samples.py on the server'
          : 'Piano samples are still loading; using the built-in tone', 'error');
      }
    } catch (err) {
      el.play.classList.remove('loading');
      toast(err.message, 'error');
      return;
    }
    if (state.position >= state.duration - 0.05) state.position = 0;
    state.playing = true;
    reanchor(state.position);
    paintTransport();
    tick();
  }

  function pause() {
    state.position = currentPosition();
    state.playing = false;
    engine.panic();
    paintTransport();
    draw();
  }

  let raf = null, scheduleTimer = null;
  function tick() {
    cancelAnimationFrame(raf);
    clearInterval(scheduleTimer);
    scheduleTimer = setInterval(schedule, 60);

    const frame = () => {
      if (!state.playing) return;
      const position = currentPosition();

      if (position >= state.duration) {
        if (state.loop) {
          engine.panic();
          reanchor(0);
        } else {
          state.position = state.duration;
          state.playing = false;
          engine.panic();
          clearInterval(scheduleTimer);
          paintTransport();
          draw();
          paintScrub();
          return;
        }
      }
      state.position = Math.min(position, state.duration);
      draw();
      paintScrub();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
  }

  function paintTransport() {
    // toggleAttribute, not .hidden: these are <svg> elements, and `hidden` is an
    // HTMLElement property. Assigning it on an SVGElement sets a plain JavaScript
    // property that no attribute and no stylesheet ever sees, so the icon never
    // changed and the button claimed "play" the whole way through a recording.
    el.iconPlay.toggleAttribute('hidden', state.playing);
    el.iconPause.toggleAttribute('hidden', !state.playing);
    el.play.setAttribute('aria-label', state.playing ? 'Pause' : 'Play');
  }

  function paintScrub() {
    const pct = state.duration ? (state.position / state.duration) * 100 : 0;
    el.fill.style.width = pct + '%';
    el.knob.style.left = pct + '%';
    el.scrub.setAttribute('aria-valuenow', Math.round(pct));
    el.time.textContent =
      formatDuration(state.position * 1000) + ' / ' + formatDuration(state.duration * 1000);
  }

  function seekTo(position) {
    const target = Math.max(0, Math.min(position, state.duration));
    engine.panic();
    reanchor(target);
    draw();
    paintScrub();
  }

  function seekFromEvent(e) {
    const rect = el.scrub.getBoundingClientRect();
    const clientX = (e.touches ? e.touches[0].clientX : e.clientX);
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    seekTo(ratio * state.duration);
  }

  el.play.addEventListener('click', () => (state.playing ? pause() : play()));

  el.scrub.addEventListener('pointerdown', (e) => {
    el.scrub.setPointerCapture(e.pointerId);
    seekFromEvent(e);
    const move = (ev) => seekFromEvent(ev);
    const up = () => {
      el.scrub.removeEventListener('pointermove', move);
      el.scrub.removeEventListener('pointerup', up);
    };
    el.scrub.addEventListener('pointermove', move);
    el.scrub.addEventListener('pointerup', up);
  });

  el.scrub.addEventListener('keydown', (e) => {
    const jump = e.key === 'ArrowRight' ? 5 : e.key === 'ArrowLeft' ? -5 : 0;
    if (!jump) return;
    e.preventDefault();
    seekTo(state.position + jump);
  });

  el.canvas.addEventListener('click', (e) => {
    const rect = el.canvas.getBoundingClientRect();
    const usable = width - PAD_L - PAD_R;
    const ratio = (e.clientX - rect.left - PAD_L) / usable;
    seekTo(Math.max(0, Math.min(1, ratio)) * state.duration);
  });

  el.loop.addEventListener('click', () => {
    state.loop = !state.loop;
    el.loop.classList.toggle('on', state.loop);
  });

  el.speed.addEventListener('change', () => {
    const wasPlaying = state.playing;
    const position = currentPosition();
    state.speed = parseFloat(el.speed.value);
    if (wasPlaying) { engine.panic(); reanchor(position); }
    else state.position = position;
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea, select')) return;
    if (e.code === 'Space') { e.preventDefault(); state.playing ? pause() : play(); }
  });

  window.addEventListener('beforeunload', () => engine.panic());
  window.addEventListener('resize', resize);

  /* ------------------------------------------------------------- editing -- */
  function debounced(fn, delay) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
  }

  async function patch(fields, successMessage) {
    try {
      const updated = await api('/api/sessions/' + data.id, {
        method: 'PATCH', body: JSON.stringify(fields),
      });
      if (successMessage) toast(successMessage);
      return updated;
    } catch (err) { toast(err.message, 'error'); }
  }

  el.title.addEventListener('input', debounced(() => {
    patch({ name: el.title.value.trim() || 'Untitled' });
  }, 600));
  el.title.addEventListener('blur', () => {
    if (!el.title.value.trim()) el.title.value = 'Untitled';
  });

  el.notes.addEventListener('input', debounced(() => {
    patch({ notes: el.notes.value });
  }, 700));

  el.fav.addEventListener('click', async () => {
    const updated = await patch({ favorite: !state.favorite });
    if (!updated) return;
    state.favorite = updated.favorite;
    el.fav.classList.toggle('on', state.favorite);
    el.fav.innerHTML = '★ ' + (state.favorite ? 'Starred' : 'Star');
  });

  el.del.addEventListener('click', async () => {
    if (!confirm('Delete this recording? This cannot be undone.')) return;
    try {
      await api('/api/sessions/' + data.id, { method: 'DELETE' });
      location.href = '/';
    } catch (err) { toast(err.message, 'error'); }
  });

  /* ---------------------------------------------------------------- tags -- */
  function paintTags() {
    el.tags.innerHTML = state.tags.map(t =>
      '<span class="pip">' + escapeHtml(t) +
      ' <span class="x" data-remove="' + escapeHtml(t) + '" role="button" ' +
      'title="Remove tag">&times;</span></span>').join('') ||
      '<span style="color:var(--faint);font-size:11.5px">No tags yet</span>';
  }

  el.tags.addEventListener('click', async (e) => {
    const target = e.target.closest('[data-remove]');
    if (!target) return;
    try {
      const updated = await api(
        '/api/sessions/' + data.id + '/tags/' + encodeURIComponent(target.dataset.remove),
        { method: 'DELETE' });
      state.tags = updated.tags;
      paintTags();
    } catch (err) { toast(err.message, 'error'); }
  });

  async function commitTag() {
    const value = el.tagInput.value.trim();
    if (!value) return;
    try {
      const updated = await api('/api/sessions/' + data.id + '/tags', {
        method: 'POST', body: JSON.stringify({ tag: value }),
      });
      state.tags = updated.tags;
      el.tagInput.value = '';
      paintTags();
    } catch (err) { toast(err.message, 'error'); }
  }

  el.tagInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    commitTag();
  });
  // When the datalist popup is open the browser gives Enter to the popup, not to
  // us, and picking a suggestion fires `change` instead. Commit on both, and on
  // blur, so a typed tag is never silently lost.
  el.tagInput.addEventListener('change', commitTag);
  el.tagInput.addEventListener('blur', commitTag);

  /* ---------------------------------------------------------------- boot -- */
  async function boot() {
    el.mDate.textContent = formatDate(el.mDate.dataset.iso);
    el.mDuration.textContent = formatDuration(data.duration_ms);
    el.mRange.textContent = data.lowest != null
      ? noteName(data.lowest) + ' – ' + noteName(data.highest) : '—';
    paintTags();

    try {
      const payload = await api('/api/sessions/' + data.id + '/notes');
      state.notes = payload.notes.sort((a, b) => a.s - b.s);
      state.pedal = payload.pedal || [];
      state.duration = Math.max(
        0.5, ...state.notes.map(n => n.s + (n.r || n.d)), (data.duration_ms || 0) / 1000);

      if (state.notes.length) {
        const pitches = state.notes.map(n => n.n);
        state.lo = Math.min(...pitches) - 2;
        state.hi = Math.max(...pitches) + 2;
      }
    } catch (err) {
      toast('Could not load the recording: ' + err.message, 'error');
    }

    // Start pulling the sample bytes now, so pressing play only has to decode.
    // Needs no AudioContext, so it is safe before any user gesture.
    engine.prefetch();

    resize();
    paintScrub();
  }

  boot();
})();

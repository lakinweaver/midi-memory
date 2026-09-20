/* Session detail: piano roll, transport, and inline editing. */
(function () {
  'use strict';
  const { api, toast, formatDuration, formatDate, noteName, escapeHtml } = window.MM;

  const data = JSON.parse(document.getElementById('session-data').textContent);
  const engine = new window.PianoEngine();

  const el = {
    canvas: document.getElementById('roll'),
    scroll: document.getElementById('roll-scroll'),
    track: document.getElementById('roll-track'),
    zoomBar: document.querySelector('.roll-zoom'),
    zoomOut: document.getElementById('zoom-out'),
    zoomIn: document.getElementById('zoom-in'),
    zoomValue: document.getElementById('zoom-value'),
    zoomFit: document.getElementById('zoom-fit'),
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
    fit: false,         // Fit mode: the whole take on screen, nothing to scroll
    window: 16,         // seconds across the roll when not fitted
    rungs: [],          // the windows this take is long enough to offer
    follow: true,       // keep the playhead in view during playback
    tags: data.tags || [],
    favorite: data.favorite,
    warnedSynth: false,
  };

  /* ------------------------------------------------------------ the roll -- */
  const ctx2d = el.canvas.getContext('2d');
  let width = 0, height = 0;

  const PAD_L = 34, PAD_R = 10, PAD_Y = 10;

  // The rungs of the zoom, in seconds across the roll. Any that reach past the
  // end of the take are dropped at boot: they would show dead air after the last
  // note, and Fit already owns that end of the range.
  const WINDOWS = [4, 8, 16, 32, 60, 120, 300];
  // Below about this, a sixteenth note is a sliver and the roll stops being
  // readable -- the threshold that decides how far a take opens zoomed in.
  const READABLE_PX_PER_SEC = 40;
  // Where the playhead rides while it is being followed. Off-centre, because
  // what is coming is worth more of the roll than what has already been played.
  const FOLLOW_BIAS = 0.4;
  const TICK_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 300];

  function measure() {
    const ratio = window.devicePixelRatio || 1;
    width = el.canvas.clientWidth;
    height = el.canvas.clientHeight;
    el.canvas.width = width * ratio;
    el.canvas.height = height * ratio;
    ctx2d.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function usableWidth() { return Math.max(1, width - PAD_L - PAD_R); }

  /* Horizontal scale, always derived rather than stored, so that a resize or a
   * Fit toggle needs nothing kept in sync. */
  function pxPerSec() {
    return usableWidth() / (state.fit ? Math.max(0.001, state.duration) : state.window);
  }

  /* The width the take occupies. In Fit mode this comes out as exactly the
   * width of the roll, so the scroller has nothing to scroll and the geometry
   * collapses back to what it was before any of this existed. */
  function contentWidth() { return PAD_L + state.duration * pxPerSec() + PAD_R; }

  function xOf(seconds) { return PAD_L + seconds * pxPerSec() - el.scroll.scrollLeft; }

  function timeAt(x) { return (x + el.scroll.scrollLeft - PAD_L) / pxPerSec(); }

  function yOf(midi) {
    const usable = height - PAD_Y * 2;
    const span = Math.max(6, state.hi - state.lo);
    return PAD_Y + usable - ((midi - state.lo) / span) * usable;
  }

  function layout() { el.track.style.width = Math.round(contentWidth()) + 'px'; }

  function setScroll(value) {
    const max = Math.max(0, contentWidth() - width);
    const next = Math.max(0, Math.min(value, max));
    if (Math.abs(next - el.scroll.scrollLeft) < 0.5) return;
    el.scroll.scrollLeft = next;
  }

  /** Scroll so that `seconds` lands `fraction` of the way across the roll. */
  function scrollSoThat(seconds, fraction) {
    setScroll(PAD_L + seconds * pxPerSec() - width * fraction);
  }

  function draw() {
    if (!width) return;
    ctx2d.clearRect(0, 0, width, height);

    const scale = pxPerSec();
    const from = timeAt(0), to = timeAt(width);
    const rowHeight = Math.max(2.5, (height - PAD_Y * 2) / Math.max(6, state.hi - state.lo));

    // Octave banding: enough reference to read the shape of a line. The lines run
    // to the edge of the canvas rather than stopping at PAD_R, because mid-scroll
    // there is music out there and a line that stopped short would read as a fault.
    ctx2d.strokeStyle = 'rgba(239,230,216,.07)';
    ctx2d.lineWidth = 1;
    for (let midi = Math.ceil(state.lo / 12) * 12; midi <= state.hi; midi += 12) {
      const y = yOf(midi);
      ctx2d.beginPath();
      ctx2d.moveTo(PAD_L, y + 0.5); ctx2d.lineTo(width, y + 0.5);
      ctx2d.stroke();
    }

    // Second ticks, spaced by the zoom rather than by the length of the take:
    // what has to stay readable is the gap on screen, not the gap in the music.
    const step = TICK_STEPS.find(s => s * scale >= 55) || TICK_STEPS[TICK_STEPS.length - 1];
    ctx2d.strokeStyle = 'rgba(239,230,216,.04)';
    const lastTick = Math.min(state.duration, to);
    for (let t = Math.max(step, Math.ceil(from / step) * step); t < lastTick; t += step) {
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
      if (t > state.duration || t < from || t > to) continue;
      const x = xOf(t);
      ctx2d.strokeStyle = t <= state.position
        ? 'rgba(236,196,155,.15)' : 'rgba(236,196,155,.05)';
      ctx2d.beginPath();
      ctx2d.moveTo(x, PAD_Y); ctx2d.lineTo(x, height - PAD_Y);
      ctx2d.stroke();
    }

    // The notes: the bar is how long the key was held, and nothing else.
    for (const n of state.notes) {
      if (n.s > to || n.s + n.d < from) continue;   // off-screen at this zoom
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

    // The C labels stay pinned while the music scrolls underneath, so the gutter
    // is wiped before they go down -- otherwise notes slide out from behind them.
    // Cleared rather than painted over: what shows through is .roll-wrap's own
    // background, which is the one colour guaranteed to match whatever the rest
    // of the panel is doing.
    ctx2d.clearRect(0, 0, PAD_L, height);
    ctx2d.font = '9px "DM Mono", monospace';
    ctx2d.textBaseline = 'middle';
    ctx2d.fillStyle = 'rgba(239,230,216,.28)';
    for (let midi = Math.ceil(state.lo / 12) * 12; midi <= state.hi; midi += 12) {
      ctx2d.fillText(noteName(midi), 4, yOf(midi));
    }

    // Playhead. Scrolled out of the window it simply isn't drawn; the scrub bar
    // below is what carries position when you have panned away from it.
    const px = xOf(state.position);
    if (px >= PAD_L && px <= width) {
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
  }

  function resize() {
    const anchor = width ? timeAt(width / 2) : 0;
    measure();
    layout();
    scrollSoThat(anchor, 0.5);
    paintZoom();
    draw();
  }

  /* ---------------------------------------------------------------- zoom -- */
  const SCALE_KEY = 'midi-memory.roll-scale';

  function windowLabel(seconds) {
    return seconds < 60 ? seconds + 's' : (seconds / 60) + 'm';
  }

  /* The zoom is a preference, not a property of a recording: someone who reads
   * rolls two bars at a time wants that on the next one too. Kept per browser,
   * and read defensively -- storage throws outright under some privacy settings,
   * and an entry left by an older ladder must not be able to wedge the roll. */
  function storedScale() {
    try {
      const saved = JSON.parse(localStorage.getItem(SCALE_KEY));
      if (saved && Number.isFinite(saved.window)) return saved;
    } catch (_) { /* missing, unreadable or malformed: use the default */ }
    return null;
  }

  function rememberScale() {
    try {
      localStorage.setItem(SCALE_KEY,
                           JSON.stringify({ fit: state.fit, window: state.window }));
    } catch (_) { /* private mode or a full quota; not worth interrupting for */ }
  }

  /** The rung closest to `seconds`, so a preference survives a take too short
   *  to honour it literally: a taste for two minutes at a time becomes the
   *  widest view a thirty second sketch has to offer. */
  function nearestRung(seconds) {
    return state.rungs.reduce(
      (best, w) => Math.abs(w - seconds) < Math.abs(best - seconds) ? w : best);
  }

  /* Pick what the roll opens on.
   *
   * A remembered choice outranks everything below -- that is what remembering it
   * is for. Failing that: Fit when the whole take is already legible end to end,
   * since scrolling a nine second sketch would be motion for its own sake, and
   * otherwise the widest rung that still keeps notes readable. That last rule
   * makes the default width-aware for free, opening the same take further in on
   * a phone than on a desktop.
   */
  function defaultScale() {
    state.window = state.rungs.length ? state.rungs[state.rungs.length - 1] : state.duration;
    if (!state.rungs.length) {
      state.fit = true;
      return;
    }

    const saved = storedScale();
    if (saved) {
      state.fit = saved.fit === true;
      state.window = nearestRung(saved.window);
      return;
    }

    if (usableWidth() / state.duration >= READABLE_PX_PER_SEC) {
      state.fit = true;
      return;
    }
    state.fit = false;
    const legible = state.rungs.filter(w => usableWidth() / w >= READABLE_PX_PER_SEC);
    state.window = legible.length ? legible[legible.length - 1] : state.rungs[0];
  }

  function paintZoom() {
    const index = state.rungs.indexOf(state.window);
    const stuck = !state.rungs.length;          // too short to be worth zooming
    el.zoomBar.classList.toggle('fitted', state.fit);
    el.zoomFit.classList.toggle('on', state.fit);
    el.zoomFit.setAttribute('aria-pressed', String(state.fit));
    el.zoomFit.disabled = stuck;
    el.zoomOut.disabled = state.fit || stuck || index >= state.rungs.length - 1;
    el.zoomIn.disabled = state.fit || stuck || index <= 0;
    el.zoomValue.textContent = stuck ? '—' : windowLabel(state.window);
  }

  function setScale(fit, windowSeconds) {
    // Hold on to whatever was being looked at: the playhead if it is being
    // followed, otherwise whatever sat in the middle of the roll.
    const chasing = state.playing && state.follow;
    const anchor = chasing ? state.position : timeAt(width / 2);
    state.fit = fit;
    if (windowSeconds != null) state.window = windowSeconds;
    layout();
    scrollSoThat(anchor, chasing ? FOLLOW_BIAS : 0.5);
    paintZoom();
    draw();
    rememberScale();      // only here: a choice, never the opening guess
  }

  function stepZoom(direction) {    // +1 closer in, -1 further out
    const next = state.rungs[state.rungs.indexOf(state.window) - direction];
    if (next == null) return;
    setScale(false, next);
  }

  el.zoomIn.addEventListener('click', () => stepZoom(1));
  el.zoomOut.addEventListener('click', () => stepZoom(-1));
  el.zoomFit.addEventListener('click', () => setScale(!state.fit, null));

  /* -------------------------------------------------------------- panning -- */
  // While playing, the frame loop is already redrawing every frame; outside it,
  // a scroll is the only thing that moves the picture.
  el.scroll.addEventListener('scroll', () => { if (!state.playing) draw(); },
                             { passive: true });

  // Follow is dropped on the user's intent to pan, not on the scroll it causes,
  // so that the scrolling this code does itself never looks like a takeover.
  const releaseFollow = () => { state.follow = false; };
  for (const type of ['pointerdown', 'touchstart']) {
    el.scroll.addEventListener(type, releaseFollow, { passive: true });
  }

  // A scroller with only one axis to move on makes the browser redirect a
  // vertical wheel onto it, so rolling the mouse over the roll panned the music
  // and left the page where it was -- the roll became something you had to steer
  // around to get down the page. Vertical intent is handed back to the page, and
  // only a genuinely sideways gesture is left to scroll the roll.
  el.scroll.addEventListener('wheel', (e) => {
    if (e.shiftKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) {
      releaseFollow();
      return;
    }
    e.preventDefault();
    window.scrollBy(0, e.deltaY * (e.deltaMode === 1 ? 16 : 1));
  }, { passive: false });

  /** Continuous follow, used while playing: the playhead is pinned in place. */
  function followPlayhead() {
    if (!state.follow) return;
    scrollSoThat(state.position, FOLLOW_BIAS);
  }

  /** Follow after a seek: chase it only once it has left the roll, since
   *  re-centring on every click somewhere already visible is seasickness. */
  function revealPlayhead() {
    if (!state.follow) return;
    const px = xOf(state.position);
    if (px >= PAD_L && px <= width - PAD_R) return;
    scrollSoThat(state.position, FOLLOW_BIAS);
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
    state.follow = true;        // pressing play is asking to be shown the music
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
      followPlayhead();
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
    state.follow = true;        // going somewhere deliberately rejoins the stream
    revealPlayhead();
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

  // Seeking is on the scroll layer, since that is what the pointer actually hits.
  // A swipe that panned the roll still ends in a click on desktop, so a gesture
  // that moved is treated as a pan and not as a tap on a moment in the music.
  let pressX = null, pressScroll = 0;
  el.scroll.addEventListener('pointerdown', (e) => {
    pressX = e.clientX;
    pressScroll = el.scroll.scrollLeft;
  });
  el.scroll.addEventListener('click', (e) => {
    const panned = pressX === null || Math.abs(e.clientX - pressX) > 4 ||
                   Math.abs(el.scroll.scrollLeft - pressScroll) > 4;
    pressX = null;
    if (panned) return;
    seekTo(timeAt(e.clientX - el.scroll.getBoundingClientRect().left));
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

    state.rungs = WINDOWS.filter(w => w < state.duration);
    measure();
    defaultScale();
    layout();
    paintZoom();
    draw();
    paintScrub();
  }

  boot();
})();

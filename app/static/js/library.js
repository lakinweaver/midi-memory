/* The library: search, filter, and the live-updating list of recordings. */
(function () {
  'use strict';
  const { api, toast, formatDuration, formatDate, noteName, escapeHtml } = window.MM;

  const PAGE_SIZE = 40;

  const el = {
    list: document.getElementById('list'),
    count: document.getElementById('result-count'),
    more: document.getElementById('load-more'),
    q: document.getElementById('f-q'),
    from: document.getElementById('f-from'),
    to: document.getElementById('f-to'),
    duration: document.getElementById('f-duration'),
    sort: document.getElementById('f-sort'),
    fav: document.getElementById('f-fav'),
    clear: document.getElementById('f-clear'),
    tagbar: document.getElementById('tagbar'),
    stats: document.getElementById('library-stats'),
  };

  const state = { tags: new Set(), offset: 0, total: 0, loading: false };

  /* ------------------------------------------------- filters <-> the URL -- */
  function readUrl() {
    const p = new URLSearchParams(location.search);
    el.q.value = p.get('q') || '';
    el.from.value = p.get('from') || '';
    el.to.value = p.get('to') || '';
    el.duration.value = p.get('duration') || '';
    el.sort.value = p.get('sort') || 'date:desc';
    el.fav.classList.toggle('on', p.get('fav') === '1');
    state.tags = new Set(p.getAll('tag'));
    paintTags();
  }

  function writeUrl() {
    const p = new URLSearchParams();
    if (el.q.value.trim()) p.set('q', el.q.value.trim());
    if (el.from.value) p.set('from', el.from.value);
    if (el.to.value) p.set('to', el.to.value);
    if (el.duration.value) p.set('duration', el.duration.value);
    if (el.sort.value !== 'date:desc') p.set('sort', el.sort.value);
    if (el.fav.classList.contains('on')) p.set('fav', '1');
    state.tags.forEach(t => p.append('tag', t));

    const qs = p.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
    el.clear.hidden = !qs;
  }

  function buildQuery(offset) {
    const p = new URLSearchParams();
    if (el.q.value.trim()) p.set('q', el.q.value.trim());
    if (el.from.value) p.set('date_from', el.from.value);
    if (el.to.value) p.set('date_to', el.to.value);
    if (el.fav.classList.contains('on')) p.set('favorite', 'true');

    const [min, max] = (el.duration.value || ':').split(':');
    if (min) p.set('min_duration_ms', min);
    if (max) p.set('max_duration_ms', max);

    const [sort, order] = el.sort.value.split(':');
    p.set('sort', sort); p.set('order', order);
    state.tags.forEach(t => p.append('tag', t));
    p.set('limit', PAGE_SIZE); p.set('offset', offset);
    return p.toString();
  }

  function paintTags() {
    el.tagbar.querySelectorAll('.pip[data-tag]').forEach(pip => {
      pip.classList.toggle('on', state.tags.has(pip.dataset.tag));
    });
  }

  /* ------------------------------------------------------------ rendering -- */
  // A compact fingerprint of the performance: pitch over time, velocity as alpha.
  function drawStrip(canvas, notes, lowest, highest) {
    const ratio = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 96, h = canvas.clientHeight || 40;
    canvas.width = w * ratio; canvas.height = h * ratio;
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, w, h);
    if (!notes || !notes.length) return;

    const lo = Math.min(lowest != null ? lowest : 127, ...notes.map(n => n.n)) - 1;
    const hi = Math.max(highest != null ? highest : 0, ...notes.map(n => n.n)) + 1;
    const span = Math.max(4, hi - lo);
    const end = Math.max(0.001, Math.max(...notes.map(n => n.s + n.d)));

    for (const n of notes) {
      const x = (n.s / end) * w;
      const width = Math.max(1.2, (n.d / end) * w);
      const y = h - ((n.n - lo) / span) * h;
      ctx.fillStyle = 'rgba(232,145,63,' + (0.35 + (n.v / 127) * 0.6).toFixed(2) + ')';
      ctx.fillRect(x, Math.max(0, y - 1.4), width, 2.8);
    }
  }

  function rowHtml(s) {
    const tags = (s.tags || []).map(t =>
      '<span class="pip">' + escapeHtml(t) + '</span>').join('');
    const range = (s.lowest_note != null)
      ? noteName(s.lowest_note) + '–' + noteName(s.highest_note) : '';

    return '' +
      '<div class="strip"><canvas></canvas></div>' +
      '<div class="row-main">' +
        '<div class="row-title">' +
          (s.favorite ? '<span class="star">★</span>' : '') +
          escapeHtml(s.name) +
        '</div>' +
        '<div class="row-meta">' +
          '<span>' + formatDate(s.started_at) + '</span>' +
          '<span class="dot">·</span><span>' + s.note_count + ' notes</span>' +
          (range ? '<span class="dot">·</span><span>' + range + '</span>' : '') +
        '</div>' +
        (tags ? '<div class="row-tags">' + tags + '</div>' : '') +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:10px">' +
        '<span class="dur">' + formatDuration(s.duration_ms) + '</span>' +
        '<div class="row-actions">' +
          '<button class="icon-btn star' + (s.favorite ? ' on' : '') + '" data-act="fav" title="Star">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="' + (s.favorite ? 'currentColor' : 'none') +
            '" stroke="currentColor" stroke-width="1.8"><path d="M12 3l2.6 5.6 6 .8-4.4 4.2 1.1 6-5.3-3-5.3 3 1.1-6L3.4 9.4l6-.8z"/></svg>' +
          '</button>' +
          '<button class="icon-btn" data-act="download" title="Download .mid">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
            '<path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14"/></svg>' +
          '</button>' +
          '<button class="icon-btn danger" data-act="delete" title="Delete">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
            '<path d="M4 7h16M9 7V5h6v2M7 7l1 12h8l1-12"/></svg>' +
          '</button>' +
        '</div>' +
      '</div>';
  }

  function addRow(s, position) {
    const row = document.createElement('div');
    row.className = 'row';
    row.dataset.id = s.id;
    row.innerHTML = rowHtml(s);
    if (position === 'top') el.list.prepend(row); else el.list.appendChild(row);

    row.addEventListener('click', (e) => {
      const action = e.target.closest('[data-act]');
      if (action) { e.stopPropagation(); handleAction(action.dataset.act, s, row); return; }
      location.href = '/sessions/' + s.id;
    });

    // The fingerprint needs the note data, which is a second request per row.
    api('/api/sessions/' + s.id + '/notes')
      .then(data => drawStrip(row.querySelector('canvas'), data.notes, s.lowest_note, s.highest_note))
      .catch(() => {});
    return row;
  }

  async function handleAction(action, s, row) {
    if (action === 'download') { location.href = '/api/sessions/' + s.id + '/download'; return; }

    if (action === 'fav') {
      try {
        const updated = await api('/api/sessions/' + s.id, {
          method: 'PATCH', body: JSON.stringify({ favorite: !s.favorite }),
        });
        s.favorite = updated.favorite;
        row.innerHTML = rowHtml(s);
        api('/api/sessions/' + s.id + '/notes')
          .then(d => drawStrip(row.querySelector('canvas'), d.notes, s.lowest_note, s.highest_note))
          .catch(() => {});
      } catch (err) { toast(err.message, 'error'); }
      return;
    }

    if (action === 'delete') {
      if (!confirm('Delete "' + s.name + '"? This cannot be undone.')) return;
      try {
        await api('/api/sessions/' + s.id, { method: 'DELETE' });
        row.style.transition = 'opacity .2s, transform .2s';
        row.style.opacity = '0'; row.style.transform = 'translateX(-12px)';
        setTimeout(() => row.remove(), 200);
        state.total = Math.max(0, state.total - 1);
        paintCount();
        toast('Deleted');
      } catch (err) { toast(err.message, 'error'); }
    }
  }

  function paintCount() {
    const n = state.total;
    el.count.innerHTML = n === 0
      ? 'No recordings match'
      : '<b>' + n + '</b> recording' + (n === 1 ? '' : 's');
  }

  function emptyState() {
    const filtered = !!location.search;
    el.list.innerHTML =
      '<div class="empty">' +
        '<div class="mark">♪</div>' +
        '<h3>' + (filtered ? 'Nothing matches' : 'Nothing recorded yet') + '</h3>' +
        '<p>' + (filtered
          ? 'Try loosening the filters, or clear them to see everything.'
          : 'Play something on your keyboard. Recording starts by itself, and the take will appear here a few seconds after you stop.') +
        '</p>' +
      '</div>';
  }

  /* -------------------------------------------------------------- loading -- */
  async function load(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) { state.offset = 0; el.list.innerHTML = ''; el.count.textContent = 'Loading…'; }

    try {
      const data = await api('/api/sessions?' + buildQuery(state.offset));
      state.total = data.total;

      if (reset && !data.items.length) { emptyState(); el.count.textContent = 'No recordings match'; }
      else {
        data.items.forEach((s, i) => {
          const row = addRow(s);
          row.style.animationDelay = Math.min(i * 18, 400) + 'ms';
        });
        paintCount();
      }

      state.offset += data.items.length;
      el.more.hidden = !data.has_more;
    } catch (err) {
      toast(err.message, 'error');
      el.count.textContent = 'Could not load recordings';
    } finally {
      state.loading = false;
    }
  }

  async function refreshTags() {
    try {
      const { tags } = await api('/api/tags');
      const label = '<span class="tagbar-label">Tags</span>';
      el.tagbar.innerHTML = label + tags.map(t =>
        '<button class="pip" data-tag="' + escapeHtml(t.name) + '">' +
        escapeHtml(t.name) + ' <span class="n">' + t.count + '</span></button>').join('');
      el.tagbar.hidden = tags.length === 0;
      paintTags();
    } catch (_) {}
  }

  /* --------------------------------------------------------------- events -- */
  let debounce;
  function onFilterChange(immediate) {
    clearTimeout(debounce);
    debounce = setTimeout(() => { writeUrl(); load(true); }, immediate ? 0 : 220);
  }

  el.q.addEventListener('input', () => onFilterChange());
  [el.from, el.to, el.duration, el.sort].forEach(input =>
    input.addEventListener('change', () => onFilterChange(true)));

  el.fav.addEventListener('click', () => {
    el.fav.classList.toggle('on');
    onFilterChange(true);
  });

  el.clear.addEventListener('click', () => {
    el.q.value = ''; el.from.value = ''; el.to.value = '';
    el.duration.value = ''; el.sort.value = 'date:desc';
    el.fav.classList.remove('on');
    state.tags.clear(); paintTags();
    onFilterChange(true);
  });

  el.tagbar.addEventListener('click', (e) => {
    const pip = e.target.closest('.pip[data-tag]');
    if (!pip) return;
    const tag = pip.dataset.tag;
    state.tags.has(tag) ? state.tags.delete(tag) : state.tags.add(tag);
    paintTags();
    onFilterChange(true);
  });

  el.more.addEventListener('click', () => load(false));

  // A take finished while you were looking at the library.
  //
  // When the list is unfiltered and newest-first, the new session belongs at the
  // top and can simply be slid in. Under any other view its position depends on
  // the filters and sort, so re-run the query instead -- otherwise a filtered
  // page silently never updates, and you only find the take by reloading.
  document.addEventListener('midi:session_saved', (e) => {
    const session = e.detail.session;
    if (!session) return;
    refreshTags();

    const unfiltered = !el.q.value.trim() && !el.from.value && !el.to.value
      && !el.duration.value && !el.fav.classList.contains('on')
      && state.tags.size === 0;

    if (!unfiltered || el.sort.value !== 'date:desc') {
      load(true);
      return;
    }

    if (el.list.querySelector('[data-id="' + session.id + '"]')) return;
    el.list.querySelector('.empty')?.remove();

    const row = addRow(session, 'top');
    row.classList.add('fresh');
    state.total += 1;
    paintCount();
    toast('Saved “' + session.name + '”');
  });

  // Catch up on anything missed: the live connection dropped, or the tab was in
  // the background (phones and tablets suspend timers and sockets aggressively).
  document.addEventListener('midi:reconnected', () => { load(true); refreshTags(); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { load(true); refreshTags(); }
  });

  readUrl();
  el.clear.hidden = !location.search;
  load(true);
})();

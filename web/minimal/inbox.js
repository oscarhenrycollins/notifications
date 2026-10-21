'use strict';
(() => {
  const el = id => document.getElementById(id);
  const R = window.NotifRender;
  let filter = 'unread', items = [], selected = null, generation = 0, busy = false, allReadArmed = false, listTop = 0;
  const titleOf = it => String(it.title || String(it.message || '').split('\n')[0] || 'Untitled notification');
  const say = text => { el('status').textContent = text || ''; };
  const disarmAllRead = () => { allReadArmed = false; el('allread').textContent = 'Mark all read'; };
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; }
  // Static, trusted icon paths only (never notification text).
  const ICONS = {
    chat: 'M4 5h16v11H9l-5 4z', read: 'M4 12l5 5L20 6', unread: 'M3 6h18v12H3zM3 6l9 7 9-7',
    flag: 'M6 21V4m0 0h11l-2 4 2 4H6', archive: 'M4 5h16v4H4zM6 9v10h12V9M10 13h4', inbox: 'M4 13l3-8h10l3 8v6H4zM4 13h5l1 2h4l1-2h5',
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', ICONS[name]); svg.append(p); return svg;
  }
  async function api(path, data) {
    const response = await fetch('api/' + path, data ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)} : {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Notifications are not reachable. Try Refresh.');
    return result;
  }
  function visible() { return filter === 'unread' ? items.filter(it => !it.read) : items; }
  function paint() {
    if (strip.busy()) { strip.dirty = true; return; }
    el('list').replaceChildren(...visible().map(it => {
      const li = node('li'), button = node('button', null, 'subject-row' + (it.read ? '' : ' unread'));
      button.type = 'button'; button.dataset.id = it.id;
      button.setAttribute('aria-label', titleOf(it) + (it.read ? '' : ', unread'));
      const dot = node('span', '', 'dot'); dot.setAttribute('aria-hidden', 'true');
      button.append(dot, node('span', titleOf(it), 'title')); li.append(button); return li;
    }));
    el('empty').hidden = visible().length !== 0;
    el('empty').textContent = filter === 'unread' ? 'No unread notifications' : filter === 'flagged' ? 'No flagged notifications' : filter === 'archive' ? 'No archived notifications' : 'No notifications';
    document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === filter)));
    el('overview').hidden = !!selected; el('detail').hidden = !selected;
    // The detail is one quiet page: no filter bar under it.
    el('toolbar').hidden = !!selected; document.documentElement.classList.toggle('nt-detail', !!selected);
    if (selected) paintDetail();
  }

  /* ---- list | detail strip (phone, Miguel's skins only; James owns gestures on his): the same joined strip as Chats' list ⇄ thread ----
   * Numbers are Chats' own (wizard-ux web/core/swipe-strip.js): lock after 10px, horizontal when |dx| > 1.3·|dy|,
   * release goes back past 70px or above 0.45px/ms, 240ms cubic-bezier(.2,.8,.2,1); the outer 24px of the screen
   * is left to the system edge swipe (skin API edgeInset). The page runs in an iframe, so the shell never sees
   * these touches; the gesture lives here. The list next to the detail is an inert copy of the real list. */
  const strip = (() => {
    const LOCK = 10, RATIO = 1.3, GO = 70, VGO = 0.45, MS = 240, EASE = 'cubic-bezier(.2,.8,.2,1)', EDGE = 24;
    const OURS = new Set(['obsidian', 'obsidian-light', 'landscapes']);
    const app = document.querySelector('.wz-app');
    let G = null, ghost = null, anims = [], blockClick = 0;
    const api = {dirty: false};
    function phone() {
      try { const s = parent !== window && parent.document.documentElement.dataset.shell; if (s) return s === 'phone'; } catch (_) {}
      return matchMedia('(pointer: coarse)').matches;
    }
    api.on = () => { const skin = document.documentElement.dataset.skin; return !!skin && OURS.has(skin) && phone() && innerWidth < 768; };
    api.busy = () => !!ghost;
    const W = () => app.getBoundingClientRect().width || innerWidth;
    const put = (x, w) => { ghost.style.transform = 'translate3d(' + (x - w) + 'px,0,0)'; app.style.transform = 'translate3d(' + x + 'px,0,0)'; };
    // An inert copy of the list as it is now (rows, filter bar, scroll position), without ids or the detail.
    api.snapshot = () => {
      const g = app.cloneNode(true);
      g.querySelector('#detail')?.remove(); g.querySelector('#viewer')?.remove(); g.querySelector('#status')?.remove();
      g.querySelector('#overview').hidden = false; g.querySelector('#toolbar').hidden = false; g.querySelector('#options').hidden = true;
      g.querySelectorAll('[id]').forEach(n => n.removeAttribute('id')); g.removeAttribute('id');
      g.querySelectorAll('[tabindex]').forEach(n => n.removeAttribute('tabindex'));
      g.classList.add('nt-ghost'); g.inert = true; g.setAttribute('aria-hidden', 'true');
      return g;
    };
    function mount(g) {
      ghost = g; document.documentElement.classList.add('nt-sliding'); document.body.append(g);
      const sc = g.querySelector('.wz-scroll'); if (sc) sc.scrollTop = listTop;
    }
    function done() {
      anims.forEach(a => a.cancel()); anims = [];
      ghost?.remove(); ghost = null; app.style.transform = ''; document.documentElement.classList.remove('nt-sliding');
      if (api.dirty) { api.dirty = false; if (selected) paintList(); else paint(); }
    }
    // Both panes animate together from where they are to where they go (x = the detail's left edge).
    function slide(from, to, w, then) {
      anims.forEach(a => a.cancel());
      const o = {duration: MS, easing: EASE, fill: 'forwards'}, t = x => 'translate3d(' + x + 'px,0,0)';
      anims = [ghost.animate([{transform: t(from - w)}, {transform: t(to - w)}], o), app.animate([{transform: t(from)}, {transform: t(to)}], o)];
      put(to, w);
      anims[1].finished.then(then, () => {});
    }
    // Tap on a row: the list slides out to the left while the detail comes in from the right.
    api.open = g => { const w = W(); mount(g); put(w, w); slide(w, 0, w, done); };
    // Back: the list comes in from the left, pushing the detail out to the right.
    api.back = (from = 0) => {
      const w = W(); if (!ghost) { mount(api.snapshot()); put(from, w); }
      slide(from, w, w, () => { anims.forEach(a => a.cancel()); anims = []; ghost.remove(); ghost = null; app.style.transform = ''; document.documentElement.classList.remove('nt-sliding'); api.dirty = false; showList(); });
    };
    function sideways(t) {   // content that scrolls sideways (wide code, a wide picture) keeps its own swipe
      for (let n = t; n && n !== app; n = n.parentElement) {
        if (n.scrollWidth > n.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(n).overflowX)) return true;
      }
      return false;
    }
    function frameLeft() { try { return frameElement ? frameElement.getBoundingClientRect().left : 0; } catch (_) { return 0; } }
    document.addEventListener('pointerdown', e => {
      G = null;
      if (!selected || ghost || e.pointerType === 'mouse' || e.button > 0 || !e.isPrimary || !api.on()) return;
      if (!el('viewer').hidden || e.target.closest('input, textarea, select, [contenteditable]') || sideways(e.target)) return;
      if (e.clientX + frameLeft() < EDGE || String(getSelection ? getSelection() : '')) return;
      G = {id: e.pointerId, x: e.clientX, y: e.clientY, lx: e.clientX, lt: performance.now(), v: 0, dx: 0, lock: null, w: W()};
    }, true);
    document.addEventListener('pointermove', e => {
      if (!G || e.pointerId !== G.id) return;
      const dx = e.clientX - G.x, dy = e.clientY - G.y, now = performance.now();
      if (!G.lock) {
        if (Math.hypot(dx, dy) < LOCK) return;
        G.lock = Math.abs(dx) > Math.abs(dy) * RATIO ? 'h' : 'v';
        if (G.lock === 'v') { G = null; return; }   // a vertical drag is the page scrolling
        try { e.target.setPointerCapture(e.pointerId); } catch (_) {}
        mount(api.snapshot());
      }
      e.preventDefault();
      G.v = (e.clientX - G.lx) / Math.max(1, now - G.lt); G.lx = e.clientX; G.lt = now; G.dx = dx;
      put(Math.max(0, Math.min(dx, G.w)), G.w);
    }, {capture: true, passive: false});
    const end = e => {
      if (!G || e.pointerId !== G.id) return; const g = G; G = null;
      if (g.lock !== 'h') return;
      blockClick = Date.now() + 350;
      const dx = e.type === 'pointercancel' ? g.dx : e.clientX - g.x, x = Math.max(0, Math.min(dx, g.w));
      const far = Math.abs(dx) > GO || Math.abs(g.v) > VGO;
      if (e.type !== 'pointercancel' && far && dx > 0) { api.back(x); try { navigator.vibrate && navigator.vibrate(8); } catch (_) {} }
      else slide(x, 0, g.w, done);   // not far enough: both panes snap back
    };
    document.addEventListener('pointerup', end, true);
    document.addEventListener('pointercancel', end, true);
    window.addEventListener('click', e => { if (Date.now() < blockClick) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
    api.state = () => ({active: !!G, lock: G && G.lock, sliding: !!ghost});
    return api;
  })();
  window.__ntStrip = strip;
  // ---- body: parsed blocks → DOM nodes (textContent and createElement only, no HTML parsing) ----
  function linkNode(href, text, dup) {
    const a = node('a', text, 'body-link' + (dup ? ' dup' : '')); a.href = href; a.rel = 'noopener noreferrer';
    // Same-origin Wizard links stay in the shell (it intercepts them); other https links open outside.
    a.target = '_blank'; return a;
  }
  const mediaURL = path => 'media' + path.split('/').map(encodeURIComponent).join('/');
  function mediaNode(block) {
    const url = mediaURL(block.path), name = 'Picture ' + block.index;
    const a = node('a', null, 'thumb'); a.href = url; a.setAttribute('aria-label', 'Open ' + name.toLowerCase());
    const img = node('img'); img.alt = name; img.loading = 'lazy'; img.decoding = 'async'; img.src = url;
    img.onerror = () => { const miss = node('p', 'Picture unavailable', 'thumb-missing'); a.replaceWith(miss); };
    a.append(img);
    a.onclick = e => { if (e.defaultPrevented) return; e.preventDefault(); openViewer(url, name); };
    return a;
  }
  function paintBody(blocks, primaryHref) {
    const out = [];
    for (const b of blocks) {
      if (b.type === 'media') { out.push(mediaNode(b)); continue; }
      const p = node('p');
      b.lines.forEach((runs, i) => {
        if (i) p.append(node('br'));
        for (const r of runs) {
          if (r.t === 'text') p.append(document.createTextNode(r.text));
          else if (r.t === 'link') p.append(linkNode(r.href, r.text, r.href === primaryHref));
          else if (r.t === 'code') p.append(node('code', r.text));
          else if (r.t === 'bold') p.append(node('strong', r.text));
        }
      });
      out.push(p);
    }
    el('body').replaceChildren(...out);
  }
  function openViewer(url, name) {
    const v = el('viewer'), img = el('viewer-img'); img.src = url; img.alt = name; v.hidden = false; el('viewer-close').focus();
  }
  function closeViewer() { if (el('viewer').hidden) return false; el('viewer').hidden = true; el('viewer-img').removeAttribute('src'); return true; }
  // ---- actions ----
  function action(label, iconName, handler, cls) {
    const b = node('button', null, cls || 'act'); b.type = 'button'; b.disabled = busy; b.onclick = handler;
    if (iconName) b.append(icon(iconName)); b.append(node('span', label)); return b;
  }
  function startChat(it) {
    try {
      const receipt = window.WizardApp.nav.openContextDraft({id: String(it.id), title: titleOf(it), message: String(it.message || '')});
      if (receipt?.kind !== 'unsent-draft' || receipt.sent !== false) throw new Error('Could not confirm an unsent draft.');
      say('Reply opened in Chats. Nothing sent.');
    } catch (error) { say(error.message || 'Could not open Chats.'); }
  }
  function primaryLink(it, blocks) {
    const own = R.safeHref(it.link);
    if (own) return {href: own, label: R.firstLinkText(blocks, own) || 'Open link'};
    for (const b of blocks) if (b.type === 'text') for (const l of b.lines) for (const r of l) if (r.t === 'link' && r.text !== r.href) return {href: r.href, label: r.text};
    return null;
  }
  function paintDetail() {
    const it = selected;
    el('subject').textContent = titleOf(it);
    el('meta').textContent = [R.friendlyTime(it.time), it.flag ? 'Flagged' : '', it.archived ? 'Archived' : ''].filter(Boolean).join(' · ');
    // The primary button opens the link, so the body never shows it a second time: a sole inline copy is
    // removed, and when the body has other links too the copy is quieted.
    const parsed = R.parse(it.message, Number(it.time)), link = primaryLink(it, parsed);
    paintBody(link ? R.dropLink(parsed, link.href) : parsed, link ? link.href : '');
    const primary = el('primary'), row = el('actions');
    if (link) {
      const a = node('a', null, 'btn-primary'); a.href = link.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.append(node('span', /^open\b/i.test(link.label) ? link.label : 'Open ' + link.label)); primary.replaceChildren(a);
    } else primary.replaceChildren(action('Reply', 'chat', () => startChat(it), 'btn-primary'));
    const acts = [];
    if (link) acts.push(action('Reply', 'chat', () => startChat(it)));
    acts.push(action(it.read ? 'Mark unread' : 'Mark read', it.read ? 'unread' : 'read', () => mark([it.id], {read: !it.read})));
    acts.push(action(it.flag ? 'Unflag' : 'Flag', 'flag', () => mark([it.id], {flag: !it.flag})));
    acts.push(action(it.archived ? 'Move to inbox' : 'Archive', it.archived ? 'inbox' : 'archive', () => mark([it.id], {archived: !it.archived})));
    if (it.flag) acts.find(b => b.textContent === 'Unflag')?.setAttribute('aria-pressed', 'true');
    row.style.setProperty('--n', String(acts.length)); row.replaceChildren(...acts);
  }
  async function load() {
    const version = ++generation;
    const view = filter === 'unread' ? 'inbox' : filter;
    try {
      const result = await api('items?view=' + encodeURIComponent(view) + '&limit=500');
      if (version !== generation) return;
      if (!Array.isArray(result.items)) throw new Error('Notifications returned an invalid list.');
      items = result.items;
      if (selected) selected = items.find(it => it.id === selected.id) || selected;
      // A background refresh never rebuilds an open detail (pictures and scroll stay put).
      if (selected) paintList(); else paint();
      say('');
      const c = result.counts || {};
      if (parent !== window) parent.postMessage({type: 'wizard:notifications:count', unread: Number(c.unread) || 0, needs: Number(c.needs) || 0}, location.origin);
    } catch (error) { if (version === generation) say(error.message); }
  }
  function paintList() { if (strip.busy()) { strip.dirty = true; return; } const keep = selected; selected = null; const top = el('scroll').scrollTop; paint(); selected = keep; el('overview').hidden = true; el('detail').hidden = false; el('toolbar').hidden = true; document.documentElement.classList.add('nt-detail'); el('scroll').scrollTop = top; }
  async function mark(ids, marks) {
    if (busy || !ids.length) return;
    busy = true; if (selected) paintActionsOnly();
    try {
      await api('mark', {ids, ...marks});
      if (selected && ids.includes(selected.id)) selected = {...selected, ...marks, ...(marks.archived === true ? {read: true} : {})};
      await load();
    } catch (error) { say(error.message); }
    finally { busy = false; if (selected) paintActionsOnly(); }
  }
  function paintActionsOnly() { const top = el('scroll').scrollTop; paintDetail(); el('scroll').scrollTop = top; }
  // Back to the list where it was: same scroll position, focus on the row without scrolling it.
  function showList() {
    if (!selected) return false;
    const id = selected.id; selected = null; paint(); el('scroll').scrollTop = listTop;
    const row = [...document.querySelectorAll('.subject-row')].find(b => b.dataset.id === id); (row || el('scroll')).focus({preventScroll: true}); return true;
  }
  // Back (button, Escape, the shell's Back): on phone the list slides back in from the left, like Chats.
  function closeDetail() {
    if (!selected) return false;
    if (strip.on()) { if (!strip.busy()) strip.back(); return true; }
    return showList();
  }
  el('list').onclick = e => {
    const b = e.target.closest('[data-id]'); if (!b || strip.busy()) return;
    const it = items.find(x => x.id === b.dataset.id); if (!it) return;
    listTop = el('scroll').scrollTop;
    const ghost = strip.on() ? strip.snapshot() : null;
    selected = it; paint(); el('scroll').scrollTop = 0; el('subject').focus({preventScroll: true});
    if (ghost) strip.open(ghost);
  };
  document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
    disarmAllRead();
    filter = b.dataset.filter; selected = null; el('options').hidden = true; el('more').setAttribute('aria-expanded', 'false'); el('scroll').scrollTop = 0;
    // Clear old rows while the new filter loads, never label stale data “All”.
    items = []; paint(); load();
  });
  el('back').onclick = closeDetail;
  el('viewer-close').onclick = closeViewer;
  el('viewer').onclick = e => { if (e.target === el('viewer')) closeViewer(); };
  el('more').onclick = () => { disarmAllRead(); el('options').hidden = !el('options').hidden; el('more').setAttribute('aria-expanded', String(!el('options').hidden)); };
  el('refresh').onclick = load;
  el('allread').onclick = () => {
    const ids = visible().filter(it => !it.read).map(it => it.id);
    if (!ids.length) return;
    if (!allReadArmed) { allReadArmed = true; el('allread').textContent = 'Confirm mark all read'; return; }
    allReadArmed = false; el('allread').textContent = 'Mark all read'; mark(ids, {read: true});
  };
  window.__wizardCloseOverlay = () => {
    if (closeViewer()) return true;
    if (!el('options').hidden) { disarmAllRead(); el('options').hidden = true; el('more').setAttribute('aria-expanded', 'false'); el('more').focus(); return true; }
    return closeDetail();
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') window.__wizardCloseOverlay(); });
  window.WizardApp?.ready({name: 'Notifications'});
  window.WizardApp?.back?.register?.({canGoBack: () => !!selected || !el('options').hidden || !el('viewer').hidden, back: () => window.__wizardCloseOverlay()});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && !busy) load(); }, 60000);
  load();
})();

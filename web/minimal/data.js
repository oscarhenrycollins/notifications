/* Notifications data, one place for both screens (the original inbox and the minimal one).
 * Normal Wizard: same-origin fetches of this app's own api/ (unchanged).
 * Browser trust preview (WIZARD_BROWSER_BOUNDARY, default off): the shell loads this page in an opaque
 * sandbox (origin "null"), so the page has no direct API. It asks the shell for two reviewed, typed
 * operations through WizardApp.data: notifications.inbox and notifications.mark (pictures: WizardApp.media). The shell and the Agent
 * Service check the installed app's grants for each call; this page never names its own identity. */
'use strict';
(() => {
  const opaque = (() => { try { return parent !== window && self.origin === 'null'; } catch (_) { return true; } })();
  const MESSAGES = {
    conflict: 'This changed somewhere else. The list is refreshed; try again.',
    not_allowed: 'Notifications need permission from the owner for this.',
    revoked: 'Permission for Notifications changed. Reopen Notifications.',
    stale_binding: 'Reopen Notifications to continue.',
    outcome_unknown: 'Not sure this was saved. Refresh to check.',
    not_found: 'That notification is gone.',
    quota_exceeded: 'Too many changes at once. Try again in a moment.',
    busy: 'Busy. Try again in a moment.',
    timeout: 'Notifications took too long. Try Refresh.',
  };
  function failure(e) {
    const code = e && typeof e.code === 'string' ? e.code : 'unavailable';
    return Object.assign(new Error(MESSAGES[code] || 'Notifications are not reachable. Try Refresh.'), { code });
  }
  // A v4 UUID for each write. crypto.randomUUID needs a secure context; getRandomValues does not.
  function uuid() {
    try { if (crypto.randomUUID) return crypto.randomUUID(); } catch (_) {}
    const b = crypto.getRandomValues(new Uint8Array(16)); b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }
  let connecting = null;
  function connected() {
    if (window.WizBoundaryClient?.ready) return Promise.resolve();
    if (!window.WizBoundaryClient || !window.WizardApp?.data) return Promise.reject(failure({ code: 'unavailable' }));
    return connecting ||= new Promise((resolve, reject) => {
      const timer = setTimeout(() => { connecting = null; reject(failure({ code: 'timeout' })); }, 8000);
      addEventListener('wizard:boundary:connected', () => { clearTimeout(timer); connecting = null; resolve(); }, { once: true });
    });
  }
  async function direct(path, data) {
    const response = await fetch('api/' + path, data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : { cache: 'no-store' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Notifications are not reachable. Try Refresh.');
    return result;
  }
  /** {view, counts, items}; items carry `revision` (the marks the owner is looking at). */
  async function items(view, limit) {
    if (!opaque) return direct('items?view=' + encodeURIComponent(view) + '&limit=' + limit);
    await connected();
    try { return await window.WizardApp.data.call({ operation: 'notifications.inbox', args: { view, limit } }); }
    catch (e) { throw failure(e); }
  }
  /** Mark ids with {read|flag|archived}. `known` are the items on screen, for their revisions. */
  async function mark(ids, marks, known) {
    if (!opaque) return direct('mark', { ids, ...marks });
    await connected();
    const list = ids.map(id => {
      const it = (known || []).find(x => x.id === id);
      if (!it || typeof it.revision !== 'string') throw failure({ code: 'conflict' });
      return { id, expectedRevision: it.revision };
    });
    const clean = Object.fromEntries(Object.entries(marks).filter(([k, v]) => ['read', 'flag', 'archived'].includes(k) && typeof v === 'boolean'));
    const operationKey = uuid();
    try { return await window.WizardApp.data.call({ operation: 'notifications.mark', args: { items: list, marks: clean }, operationKey }); }
    catch (e) {
      // Never claim a save that cannot be proven: ask once, by this write's key, what happened.
      if (e && (e.code === 'outcome_unknown' || e.code === 'timeout')) {
        const s = await window.WizardApp.data.status({ operation: 'notifications.mark', operationKey }).catch(() => null);
        if (s && s.status === 'succeeded') return s.result;
      }
      throw failure(e);
    }
  }
  /* The unread count for the shell's badge. Same-origin only: under the browser trust preview the shell reads
   * the count itself after each mark, so the page never claims a number. */
  function count(c) {
    if (opaque || parent === window) return;
    try { parent.postMessage({ type: 'wizard:notifications:count', unread: Number(c && c.unread) || 0, needs: Number(c && c.needs) || 0 }, location.origin); } catch (_) {}
  }
  /* One picture of one notification. Same-origin: this app's media/ route, unchanged. Browser trust preview: the
   * page has no route to the bytes; it names the notification and the picture's number and the shell returns a
   * 60 s ticket URL on a separate content origin (never a path, never the owner origin). */
  async function picture(itemId, index, path, purpose) {
    if (!opaque) return 'media' + String(path).split('/').map(encodeURIComponent).join('/');
    await connected();
    try {
      const t = await window.WizardApp.media.open({ resourceId: 'n:' + itemId + ':' + index, purpose: purpose === 'read' ? 'read' : 'thumbnail' });
      if (!t || t.kind !== 'ticket' || typeof t.url !== 'string') throw { code: 'unavailable' };
      return t.url;
    } catch (e) { throw failure(e); }
  }
  window.NotifData = Object.freeze({ opaque, items, mark, count, picture });
})();

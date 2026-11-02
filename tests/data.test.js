'use strict';
// web/minimal/data.js in both modes, in a VM: same-origin uses this app's api/; an opaque frame uses only the
// two reviewed typed operations, sends revisions and a UUID key, and never claims a count or a save it cannot prove.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const SRC = fs.readFileSync(path.join(__dirname, '../web/minimal/data.js'), 'utf8');

function boot({ opaque, call, status, ready = true }) {
  const calls = [], fetches = [], posts = [], listeners = {};
  const parent = { postMessage: (...a) => posts.push(a) };
  const self = { origin: opaque ? 'null' : 'https://wizard.example' };
  const win = {};
  const ctx = {
    self, parent, window: win, location: { origin: self.origin },
    crypto: { getRandomValues: a => crypto.getRandomValues(a) },   // no randomUUID: an insecure context
    fetch: async (url, init) => { fetches.push([url, init]); return { ok: true, json: async () => ({ items: [], counts: {} }) }; },
    addEventListener: (t, fn) => { listeners[t] = fn; }, setTimeout, clearTimeout, Object, Array, Number, Error, Promise, JSON, Uint8Array,
  };
  win.WizBoundaryClient = { ready };
  win.WizardApp = { data: {
    call: async req => { calls.push(['call', req]); return call ? call(req) : { ok: true }; },
    status: async req => { calls.push(['status', req]); return status ? status(req) : { status: 'unknown' }; },
  } };
  ctx.globalThis = ctx;
  vm.runInNewContext(SRC, ctx);
  return { D: win.NotifData, calls, fetches, posts, listeners };
}

test('same-origin keeps the exact existing api/ calls and the count message', async () => {
  const { D, calls, fetches, posts } = boot({ opaque: false });
  assert.equal(D.opaque, false);
  await D.items('flagged', 500);
  await D.mark(['a', 'b'], { read: true }, []);
  D.count({ unread: 3, needs: 1 });
  assert.deepEqual(fetches.map(f => f[0]), ['api/items?view=flagged&limit=500', 'api/mark']);
  assert.deepEqual(JSON.parse(fetches[1][1].body), { ids: ['a', 'b'], read: true });
  assert.equal(calls.length, 0);
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0][1], 'https://wizard.example');
});

test('opaque frame: typed operations only, revisions and a v4 key, no direct fetch, no count claim', async () => {
  const { D, calls, fetches, posts } = boot({ opaque: true, call: req => req.operation === 'notifications.inbox' ? { view: 'inbox', counts: { unread: 1 }, items: [] } : { items: [], operationKey: req.operationKey } });
  assert.equal(D.opaque, true);
  await D.items('inbox', 500);
  const known = [{ id: 'a', revision: 'r-0000000000000001' }, { id: 'b', revision: 'r-0000000000000002' }];
  await D.mark(['a', 'b'], { read: true, flag: undefined, bogus: 1 }, known);
  D.count({ unread: 9 });
  assert.equal(fetches.length, 0);
  assert.equal(posts.length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), ['call', { operation: 'notifications.inbox', args: { view: 'inbox', limit: 500 } }]);
  const write = JSON.parse(JSON.stringify(calls[1][1]));
  assert.match(write.operationKey, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.deepEqual(write, { operation: 'notifications.mark', args: { items: [{ id: 'a', expectedRevision: known[0].revision }, { id: 'b', expectedRevision: known[1].revision }], marks: { read: true } }, operationKey: write.operationKey });
});

test('opaque frame: an item without a known revision is a conflict, never a blind write', async () => {
  const { D, calls } = boot({ opaque: true });
  await assert.rejects(D.mark(['a'], { read: true }, []), e => e.code === 'conflict');
  assert.equal(calls.length, 0);
});

test('opaque frame: an unknown outcome is checked by key once; only a proven success counts as saved', async () => {
  const unknown = () => { throw Object.assign(new Error('x'), { code: 'outcome_unknown' }); };
  let r = boot({ opaque: true, call: unknown, status: () => ({ status: 'succeeded', result: { items: [{ id: 'a' }] } }) });
  assert.deepEqual(JSON.parse(JSON.stringify(await r.D.mark(['a'], { read: true }, [{ id: 'a', revision: 'r-1' }]))), { items: [{ id: 'a' }] });
  assert.equal(r.calls[1][0], 'status');
  assert.equal(r.calls[1][1].operationKey, r.calls[0][1].operationKey);
  r = boot({ opaque: true, call: unknown, status: () => ({ status: 'pending' }) });
  await assert.rejects(r.D.mark(['a'], { read: true }, [{ id: 'a', revision: 'r-1' }]), e => e.code === 'outcome_unknown' && /Not sure/.test(e.message));
});

test('opaque frame: refusals keep their code and get a plain message', async () => {
  const { D } = boot({ opaque: true, call: () => { throw Object.assign(new Error('revoked'), { code: 'revoked' }); } });
  await assert.rejects(D.items('inbox', 50), e => e.code === 'revoked' && /Permission/.test(e.message));
});

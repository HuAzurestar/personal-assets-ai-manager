// Behavioral contract for bounded serial automatic GET search, not source snapshots.
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

globalThis.document = { querySelector: () => null };
globalThis.location = { hash: '#scan-test' };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
async function until(check) {
  for (let i = 0; i < 300; i++) { if (check()) return; await tick(); }
  throw new Error('scan did not settle');
}
const batch = (items, cursor, count = 2) => ({ items, total: null,
  scanned_count: count, has_more: cursor !== null, next_cursor: cursor });
let replies, calls, active, peak;
function fixture(sequence) {
  replies = [...sequence]; calls = []; active = 0; peak = 0;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, signal: options.signal }); peak = Math.max(peak, ++active);
    try {
      let body = replies.shift();
      if (typeof body === 'function') body = await body(options.signal);
      if (body instanceof Error) throw body;
      if (!body) throw new Error('unexpected read');
      return { ok: true, status: 200, json: async () => ({ status: 200, body }) };
    } finally { active--; }
  };
}
function view(scan, name = 'test') {
  const controls = Object.fromEntries(['continue', 'pause', 'cancel'].map(key =>
    [`[data-${name}-${key}]`, { disabled: false, isConnected: true }]));
  const root = { isConnected: true, querySelector: key => controls[key] || null };
  const painted = [];
  const paint = value => { painted.push(value); scan.bind(root, paint); };
  scan.bind(root, paint);
  return { root, painted, controls, latest: () => painted.at(-1) };
}
const query = word => new URLSearchParams({ page_size: '2', query: JSON.stringify([{ key: 'summary', word }]) });

(async () => {
  const { candidateScan, scanControls } = await import(pathToFileURL(path.resolve(__dirname,
    '../frontend/js/util/candidate-scan.js')).href);
  fixture([batch([], 'one'), batch([], 'two'), batch([{ id: 3 }], null)]);
  let scan = candidateScan('/mock', 'test');
  const first = await scan.read(query('sparse'), location.hash);
  assert.equal(first.items.length, 0);
  let ui = view(scan);
  await until(() => ui.latest()?.scan_state === 'complete');
  assert.equal(calls.length, 3); assert.equal(peak, 1);
  assert.equal(ui.latest().scanned_count, 6); assert.equal(ui.latest().matched_count, 1);
  assert.deepEqual(ui.latest().items.map(row => row.id), [3]);
  assert.ok(calls[1].url.includes('cursor=one') && calls[2].url.includes('cursor=two'));
  await ui.controls['[data-test-continue]'].onclick(); assert.equal(calls.length, 3);
  assert.ok(scanControls('test', ui.latest()).includes('本次扫描结束'));

  // A failed GET retains its last committed cursor and hits. User resume retries
  // that GET, never automatically replays a write or restarts from batch one.
  fixture([batch([{ id: 1 }], 'one'), new Error('offline'), batch([{ id: 2 }], null)]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('failure'), location.hash);
  ui = view(scan); await until(() => ui.latest()?.scan_state === 'failed');
  assert.equal(calls.length, 2); assert.equal(ui.latest().next_cursor, 'one');
  assert.deepEqual(ui.latest().items.map(row => row.id), [1]);
  await ui.controls['[data-test-continue]'].onclick();
  assert.equal(calls.length, 3); assert.ok(calls[2].url.includes('cursor=one'));
  assert.deepEqual(ui.latest().items.map(row => row.id), [1, 2]);

  // A phase limit is a pause, not a fabricated end or cumulative cutoff.
  fixture([batch([], 'one'), batch([], 'two'), batch([{ id: 9 }], null)]);
  scan = candidateScan('/mock', 'test', row => row.id, { maxBatches: 1 });
  await scan.read(query('budget'), location.hash); ui = view(scan);
  await until(() => ui.latest()?.scan_state === 'paused');
  assert.equal(calls.length, 2); assert.equal(ui.latest().has_more, true);
  await ui.controls['[data-test-continue]'].onclick();
  assert.equal(ui.latest().scan_state, 'complete'); assert.equal(ui.latest().scanned_count, 6);

  // Result windows remain bounded. Next-window action explicitly replaces the
  // displayed window; nothing is silently sliced from an accepted batch.
  fixture([batch([{ id: 1 }, { id: 2 }], 'one'), new Error('offline'), batch([{ id: 3 }], null)]);
  scan = candidateScan('/mock', 'test', row => row.id, { maxItems: 2 });
  await scan.read(query('window'), location.hash); ui = view(scan);
  await until(() => ui.latest()?.scan_state === 'paused');
  assert.equal(calls.length, 1); assert.equal(ui.latest().scan_replace, true);
  assert.ok(scanControls('test', ui.latest()).includes('替换当前结果'));
  await ui.controls['[data-test-continue]'].onclick();
  assert.equal(ui.latest().scan_state, 'failed'); assert.equal(ui.latest().scan_replace, true);
  assert.deepEqual(ui.latest().items.map(row => row.id), [1, 2]);
  assert.equal(ui.latest().next_cursor, 'one');
  await ui.controls['[data-test-continue]'].onclick();
  assert.deepEqual(ui.latest().items.map(row => row.id), [3]);
  assert.equal(ui.latest().matched_count, 3); assert.equal(ui.latest().scan_window, 2);

  fixture([batch([{ id: 1, text: 'x'.repeat(100) }], 'one'), batch([{ id: 2 }], null)]);
  scan = candidateScan('/mock', 'test', row => row.id, { maxBytes: 50 });
  await scan.read(query('bytes'), location.hash); ui = view(scan);
  await until(() => ui.latest()?.scan_state === 'paused');
  assert.equal(calls.length, 1); assert.equal(ui.latest().items.length, 1);
  assert.equal(ui.latest().scan_replace, true);
  await ui.controls['[data-test-continue]'].onclick(); assert.equal(ui.latest().scan_state, 'complete');

  // Elapsed-time budget is a resumable phase boundary too, not just a batch cap.
  fixture([batch([], 'one'), async () => { await new Promise(resolve => setTimeout(resolve, 25)); return batch([], 'two'); }, batch([{ id: 7 }], null)]);
  scan = candidateScan('/mock', 'test', row => row.id, { maxElapsedMs: 20 });
  await scan.read(query('elapsed'), location.hash); ui = view(scan);
  await until(() => ui.latest()?.scan_state === 'paused');
  assert.equal(calls.length, 2); assert.equal(ui.latest().next_cursor, 'two');
  await ui.controls['[data-test-continue]'].onclick(); assert.equal(ui.latest().scan_state, 'complete');

  // A malformed non-advancing continuation cannot spin indefinitely or consume
  // a new result while losing the only recoverable cursor.
  fixture([batch([{ id: 1 }], 'one'), batch([{ id: 2 }], 'one')]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('bad-cursor'), location.hash);
  ui = view(scan); await until(() => ui.latest()?.scan_state === 'failed');
  assert.equal(calls.length, 2); assert.equal(ui.latest().scanned_count, 2);
  assert.deepEqual(ui.latest().items.map(row => row.id), [1]);

  // Abort and generation guards discard even a deliberately late mock response.
  let late;
  fixture([batch([], 'one'), () => new Promise(resolve => { late = resolve; }), batch([{ id: 4 }], null)]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('pause'), location.hash);
  ui = view(scan); await until(() => !!late);
  assert.equal(scan.busy(), true);
  const before = calls.length; await ui.controls['[data-test-continue]'].onclick();
  assert.equal(calls.length, before);
  ui.controls['[data-test-pause]'].onclick();
  assert.equal(scan.busy(), false); assert.equal(calls[1].signal.aborted, true);
  late(batch([{ id: 999 }], null)); await tick();
  assert.equal(ui.latest().scan_state, 'paused'); assert.equal(ui.latest().scanned_count, 2);
  await ui.controls['[data-test-continue]'].onclick();
  assert.deepEqual(ui.latest().items.map(row => row.id), [4]);

  let cancelled;
  // Foreground refresh may preserve the mounted DOM. Its visible stop must
  // publish a resumable state, reject a late response and retain the cursor.
  let refreshed;
  fixture([batch([{id:1}], 'one'), () => new Promise(resolve => {refreshed = resolve;}), batch([{id:2}], null)]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('retained-refresh'), location.hash);
  ui = view(scan); await until(() => !!refreshed);
  scan.stop(true);
  assert.equal(ui.latest().scan_state, 'paused');
  assert.equal(ui.latest().next_cursor, 'one');
  assert.equal(ui.controls['[data-test-continue]'].disabled, false);
  refreshed(batch([{id:999}], null)); await tick();
  assert.deepEqual(ui.latest().items.map(row => row.id), [1]);
  await ui.controls['[data-test-continue]'].onclick();
  assert.ok(calls[2].url.includes('cursor=one'));
  assert.deepEqual(ui.latest().items.map(row => row.id), [1,2]);

  fixture([batch([], 'one'), () => new Promise(resolve => { cancelled = resolve; }), batch([{ id: 8 }], null)]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('cancel'), location.hash);
  ui = view(scan); await until(() => !!cancelled);
  ui.controls['[data-test-cancel]'].onclick();
  cancelled(batch([{ id: 999 }], null)); await tick();
  assert.equal(ui.latest().scan_state, 'cancelled'); assert.equal(ui.latest().items.length, 0);
  assert.equal(calls.length, 2);
  // Changed conditions invalidate a cancelled scan and its cursor/cache.
  location.hash = '#scan-new'; await scan.read(query('new'), location.hash);
  ui = view(scan); assert.equal(calls.length, 3);
  assert.ok(!calls[2].url.includes('cursor='));

  let routed;
  fixture([batch([], 'one'), () => new Promise(resolve => { routed = resolve; })]);
  scan = candidateScan('/mock', 'test'); await scan.read(query('route'), location.hash);
  ui = view(scan); await until(() => !!routed);
  const paints = ui.painted.length;
  location.hash = '#another-page'; scan.stop(); ui.root.isConnected = false;
  routed(batch([{ id: 999 }], null)); await tick();
  assert.equal(ui.painted.length, paints); assert.equal(scan.busy(), false);
  assert.ok(calls.every(call => !call.url.includes('/command')));
  console.log('PASS serial automatic scan: empty batches, phase/item/byte bounds, pause/cancel, retry cursor, stale routes; GET only');
})().catch(error => { console.error(error); process.exitCode = 1; });

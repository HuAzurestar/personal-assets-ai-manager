"use strict";
// Execute mount -> verify -> finally -> refresh -> click, not a source snapshot.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/js/view/import-batch.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

async function scenario(expired = false) {
  const nodes = new Map();
  const element = () => ({ disabled: false, value: '', textContent: '', innerHTML: '' });
  const verification = element();
  Object.defineProperty(verification, 'innerHTML', { set(value) {
    nodes.delete('[data-batch-observed]');
    if (value.includes('data-batch-observed')) nodes.set('[data-batch-observed]', element());
  } });
  nodes.set('[data-batch-verification]', verification);
  const host = { isConnected: true, innerHTML: '', querySelector(selector) {
    if (selector === '[data-batch-observed]') return nodes.get(selector) || null;
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  }, querySelectorAll(selector) {
    return selector === 'button,input,select' ? [...nodes.values()] : [];
  } };
  const plan = { token: 'mock-token', status: 'PENDING', files: [],
    counts: { new: 0, existing: 0, processed: 0 }, issue_count: 0, preview_digest: 'mock-digest' };
  const retained = JSON.stringify({ token: plan.token, rows: [
    { file_id: 1, source_row_number: 1 }, { file_id: 1, source_row_number: 2 },
  ] });
  let stored = retained, confirming = true, fail = false, writes = 0;
  const context = vm.createContext({ AbortController, URLSearchParams, esc: String,
    localStorage: { getItem: () => stored, removeItem: () => { stored = null; } },
    jsonRequest: async () => { writes++; throw Error('unexpected financial write'); },
    request: async url => {
      if (url.includes('/row/relations')) return { items: [] };
      if (url.includes('/import_file/')) return { items: [
        { id: 1, source_row_number: 1, row_status: 2, transaction_id: 0 },
        { id: 2, source_row_number: 2, row_status: 3, transaction_id: 0 },
      ], total: 2, page_size: 100 };
      if (url.includes('/row/list')) return { items: [], total: 0, page_size: 20 };
      if (fail) throw Error('mock read failed');
      if (expired) throw Object.assign(Error('mock expired'), { status: 410 });
      return { ...plan, status: confirming ? 'CONFIRMING' : 'PENDING' };
    },
  });
  vm.runInContext(source + '\nglobalThis.mount = mountImportBatch;', context);
  await context.mount(host, plan, () => {});
  const find = selector => host.querySelector(selector);
  await find('[data-batch-verify]').onclick();
  let observed = find('[data-batch-observed]');
  assert.ok(observed);
  assert.equal(observed.disabled, !expired); // Includes verify's finally/update.
  if (!expired) {
    await observed.onclick(); // Programmatic invocation must not bypass disabled.
    assert.equal(stored, retained);
    assert.equal(find('[data-batch-confirm]').disabled, true);
    await find('[data-batch-refresh]').onclick();
    assert.equal(observed.disabled, true);
    confirming = false;
    await find('[data-batch-refresh]').onclick();
    assert.equal(observed.disabled, true); // A refresh is not successful verification.
    await find('[data-batch-verify]').onclick();
    observed = find('[data-batch-observed]');
    assert.equal(observed.disabled, false);
    fail = true;
    await find('[data-batch-verify]').onclick();
    assert.equal(observed.disabled, true); // Failed re-verification revokes old permission.
    await observed.onclick();
    assert.equal(stored, retained);
    fail = false;
    await find('[data-batch-verify]').onclick();
    observed = find('[data-batch-observed]');
  }
  assert.equal(observed.disabled, false);
  await observed.onclick();
  assert.equal(stored, null);
  assert.equal(writes, 0);
}
async function retiredRefresh(reuseHost) {
  const nodes = new Map();
  let retired = false, rejectRead, writes = 0, metadataReads = 0;
  const element = () => ({ disabled: false, value: '', textContent: '', innerHTML: '' });
  const host = { isConnected: true, innerHTML: '', querySelector(selector) {
    if (retired && !reuseHost) return null;
    if (selector === '[data-batch-observed]') return null;
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  }, querySelectorAll(selector) {
    return selector === 'button,input,select' ? [...nodes.values()] : [];
  } };
  const plan = { token: 'retired-token', status: 'READY', files: [],
    counts: { new: 0, existing: 0, processed: 0 }, issue_count: 0, preview_digest: 'mock-digest' };
  const sandbox = vm.createContext({ AbortController, URLSearchParams, esc: String,
    localStorage: { getItem: () => null },
    jsonRequest: async () => { writes++; throw Error('unexpected financial write'); },
    request: async url => url.includes('/row/list') ? { items: [], total: 0, page_size: 20 }
      : new Promise((resolve, reject) => { metadataReads++; rejectRead = reject; }),
  });
  vm.runInContext(source + '\nglobalThis.mount = mountImportBatch; globalThis.stop = stopImportRead;', sandbox);
  await sandbox.mount(host, plan, () => {});
  const replacement = host.querySelector('[data-batch-status]');
  const refresh = host.querySelector('[data-batch-refresh]').onclick();
  assert.equal(typeof rejectRead, 'function');
  assert.equal(host.querySelector('[data-batch-refresh]').disabled, true);
  assert.equal(host.querySelector('[data-batch-save]').disabled, true);
  // Even a programmatic second invocation cannot race the first metadata read.
  await host.querySelector('[data-batch-refresh]').onclick();
  assert.equal(metadataReads, 1);
  sandbox.stop();
  retired = true;
  replacement.textContent = 'replacement view untouched';
  rejectRead(Object.assign(Error('late read failure after route change'), { name: 'AbortError' }));
  await assert.doesNotReject(refresh);
  assert.equal(replacement.textContent, 'replacement view untouched');
  assert.equal(writes, 0);
}
Promise.resolve().then(() => scenario()).then(() => scenario(true))
  .then(() => retiredRefresh(false)).then(() => retiredRefresh(true))
  .then(() => console.log('PASS CONFIRMING_BLOCKED=1 CLICK_GUARD=1 FAILED_REVERIFY_BLOCKED=1 TERMINAL_OBSERVED=1 NO_POST_RETRY=1'))
  .catch(error => { console.error(error); process.exitCode = 1; });

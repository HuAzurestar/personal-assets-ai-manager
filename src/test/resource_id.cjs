"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const moduleUrl = file => pathToFileURL(path.join(__dirname, '../frontend/js', file)).href;

async function main() {
  const { resourceId, assertExactResourceIds, esc } = await import(moduleUrl('util/core.js'));
  const { jsonRequest, isUnknownWrite } = await import(moduleUrl('api/client.js'));
  const { writeFailure } = await import(moduleUrl('component/workbench.js'));
  const { financialStateLabel } = await import(moduleUrl('util/financial-copy.js'));
  const dictionary = await import(moduleUrl('util/unit-dictionary.js'));
  dictionary.installUnitDictionary(require('./unit_fixture.cjs').unitFixture());
  const invalid = ['', ' ', '-1', '1.0', '1e3', 'Infinity', 'NaN', '9223372036854775808',
    '9007199254740993', 9007199254740992, -1, 1.5, true, false, null, undefined];
  for (const value of invalid) {
    assert.throws(() => resourceId(value, { allowZero: true }), error => {
      assert.equal(error.code, 'INVALID_ID'); assert.equal(error.status, 422);
      assert.equal(isUnknownWrite(error), false); return true;
    });
  }
  assert.equal(resourceId('0007'), 7);
  assert.equal(resourceId(' 7 '), 7);
  assert.equal(resourceId(Number.MAX_SAFE_INTEGER), Number.MAX_SAFE_INTEGER);
  assert.equal(resourceId('0', { allowZero: true }), 0);
  assert.throws(() => resourceId(0));
  assertExactResourceIds({ new_reviews: [{ allocations: [{ transaction_id: 7, account_ref_id: 0 }],
    new_positions: [{ party_id: 1 }], legs: [{ source: 0 }], new_position_index: 0 }],
    choices: [{ file_id: 1, source_row_number: 1, account_ref_id: null }], activate_review_ids: [7] });

  let calls = 0;
  globalThis.document = { querySelector: () => null, dispatchEvent() {} };
  globalThis.CustomEvent = class {};
  globalThis.fetch = async () => {
    calls++; return { ok: true, status: 200, json: async () => ({ status: 200, message: 'ok', body: {} }) };
  };
  for (const body of [
    { account_id: 9223372036854775808 },
    { new_reviews: [{ parameters: { allocations: [{ transaction_id: 9007199254740992 }] } }] },
    { new_reviews: [], expected_reviews: [{ review_id: 9007199254740992 }] },
    { activate_review_ids: [9007199254740992] },
    { selected_rows: [{ file_id: 1, source_row_number: false }] },
    { new_reviews: [{ legs: [{ source: 9007199254740992 }] }] },
  ]) assert.throws(() => jsonRequest('/paam/ledger/v1/review/command', 'POST', body), { code: 'INVALID_ID' });
  assert.equal(calls, 0);
  await jsonRequest('/paam/import/v1/preview/mock', 'PUT', { choices: [{ file_id: 1, source_row_number: 1, account_ref_id: null }] });
  assert.equal(calls, 1);

  // Run the actual Position submit handler: local input failure must not stick
  // in busy/unknown, and a later valid input must be usable. Unknown stays locked.
  const nodes = new Map(), values = { title: 'Mock', party_id: '9007199254740993', unit_code: 'CNY' };
  const form = { querySelector(selector) {
    if (selector === '[data-pick-party]' || selector === '[data-named-choice="party_id"]') return null;
    if (!nodes.has(selector)) nodes.set(selector, { disabled: false, textContent: '' });
    return nodes.get(selector);
  } };
  const button = { disabled: false, isConnected: true, dataset: {} };
  const host = { isConnected: true, querySelector: () => ({}) };
  const source = fs.readFileSync(path.join(__dirname, '../frontend/js/view/position.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  let writes = 0, unknown = false;
  const context = vm.createContext({ resourceId, writeFailure, esc, financialStateLabel, location: { hash: '#workbench/position' },
    unitChoices: dictionary.unitChoices, unitLabel: dictionary.unitLabel,
    candidateScan: () => ({bind() {}, reset() {}, stop() {}}),
    input: () => '', select: () => '', namedChoice: () => '', FormData: class { [Symbol.iterator]() { return Object.entries(values)[Symbol.iterator](); } },
    workbenchDialog: () => ({ querySelector: () => form, close() {} }),
    jsonRequest: async () => { writes++; if (unknown) throw Object.assign(Error('Mock'), { status: 503, code: 'RESULT_UNKNOWN' }); return {}; },
  });
  vm.runInContext(source + '\ncontroller = {signal: undefined}; globalThis.bind = bindPosition;', context);
  context.bind({ querySelector: () => host }, async () => {});
  await host.onclick({ target: { closest: () => button } });
  await form.onsubmit({ preventDefault() {} });
  assert.equal(writes, 0);
  assert.match(form.querySelector('[role=status]').textContent, /INVALID_ID/);
  assert.equal(form.querySelector('[type=submit]').disabled, false);
  values.party_id = '1'; await form.onsubmit({ preventDefault() {} }); assert.equal(writes, 1);
  unknown = true; await form.onsubmit({ preventDefault() {} }); assert.equal(writes, 2);
  await form.onsubmit({ preventDefault() {} }); assert.equal(writes, 2);
  assert.equal(form.querySelector('[type=submit]').disabled, true);
  console.log('PASS EXACT_IDS=1 LOCAL_REJECTION_NO_HTTP=1 FORM_RECOVERS=1 UNKNOWN_NO_REPLAY=1');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

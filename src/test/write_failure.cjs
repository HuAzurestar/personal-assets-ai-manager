"use strict";
// Execute the shared classifier and the real publication event handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

async function main() {
  const moduleUrl = file => pathToFileURL(path.join(__dirname, '../frontend/js', file)).href;
  const { isUnknownWrite } = await import(moduleUrl('api/client.js'));
  const { writeFailure } = await import(moduleUrl('component/workbench.js'));
  const cases = [
    [{ status: 503, code: 'WRITE_BUSY' }, false],
    [{ status: 409, code: 'WRITE_BUSY' }, false],
    [{ status: 409, code: 'ENTITY_CHANGED' }, false],
    [{ status: 422, code: 'VALIDATION_ERROR' }, false],
    [{ status: 503, code: 'RESULT_UNKNOWN' }, true],
    [{ status: 409, code: 'RESULT_UNKNOWN' }, true],
    [{ status: 500, code: 'INTERNAL_SERVER_ERROR' }, true],
    [{ status: 503 }, true],
    [{ code: 'WRITE_BUSY' }, true],
    [{}, true],
  ];
  const source = fs.readFileSync(path.join(__dirname, '../frontend/js/view/review-workbench.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  for (const [failure, unknown] of cases) {
    assert.equal(isUnknownWrite(failure), unknown);
    const nodes = new Map();
    const form = { isConnected: true, addEventListener() {}, querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { disabled: false, textContent: '', innerHTML: '', value: 'retained draft' });
      return nodes.get(selector);
    }, querySelectorAll() { return [...nodes.values()]; } };
    form.querySelector('input');
    let previews = 0, writes = 0;
    const plan = { impact: { conflicting_review_ids: [], restored_default_review_ids: [],
      affected_account_ref_ids: [], affected_position_ids: [], dependent_position_leg_ids: [], tag_ledger_ids: [] },
      tag_effect: {}, new_reviews: [], position_changes: [], coverage: [],
      blocking_issues: [], expected_reviews: [], preview_digest: 'mock-digest' };
    const context = vm.createContext({ writeFailure, esc: String,
      jsonRequest: async url => {
        if (url.endsWith('/preview')) { previews++; return plan; }
        writes++; throw Object.assign(Error('mock failure'), failure);
      },
    });
    vm.runInContext(source + '\nglobalThis.bind = bindPublication;', context);
    context.bind(form, () => ({ new_reviews: [] }), new Map(), async () => {});
    const preview = form.querySelector('[data-review-preview]');
    const submit = form.querySelector('[data-review-command]');
    await preview.onclick();
    await form.onsubmit({ preventDefault() {} });
    assert.equal(writes, 1);
    assert.equal(form.querySelector('input').value, 'retained draft');
    assert.equal(form.querySelector('input').disabled, unknown);
    assert.equal(submit.disabled, true);
    await form.onsubmit({ preventDefault() {} });
    assert.equal(writes, 1); // No old-plan replay, even on programmatic submit.
    await preview.onclick();
    assert.equal(previews, unknown ? 1 : 2);
    if (!unknown) {
      await form.onsubmit({ preventDefault() {} });
      assert.equal(writes, 2); // Only explicit re-preview + explicit confirm.
    }
  }
  console.log('PASS KNOWN_FAILURE_RECOVERS=1 UNKNOWN_STAYS_LOCKED=1 NO_AUTOMATIC_REPLAY=1');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

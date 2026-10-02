"use strict";
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function main() {
  const { mountTagImpact } = await import(pathToFileURL(path.join(__dirname, '../frontend/js/component/tag-impact.js')).href);
  for (const total of [119, 1001]) {
    const effect = { mappings: Array.from({ length: total }, (_, index) => ({
      old_ledger_id: index + 1, new_output: { review_index: 0, allocation_index: index },
      view_id: index % 2 + 1, tag_id: index % 2 + 1, disposition: index % 2 ? 'KEEP' : 'REVIEW_REQUIRED',
    })), mapping_labels: [{ view_id: 1, tag_id: 1, view_name: 'Mock view', tag_name: 'Mock <tag>' },
      { view_id: 2, tag_id: 2, view_name: 'Mock other', tag_name: 'Mock kept' }] };
    const original = JSON.stringify(effect);
    const nodes = new Map(), listeners = new Map();
    const host = { innerHTML: '', addEventListener(name, handler) { listeners.set(name, handler); },
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, { disabled: false, innerHTML: '', textContent: '', value: '' });
        return nodes.get(selector);
      } };
    mountTagImpact(host, effect);
    const rows = host.querySelector('[data-impact-rows]');
    const next = host.querySelector('[data-impact-next]');
    const seen = new Set();
    while (true) {
      for (const match of rows.innerHTML.matchAll(/data-tag-row-index="(\d+)"/g)) seen.add(Number(match[1]));
      if (next.disabled) break;
      next.onclick();
    }
    assert.equal(seen.size, total);
    assert.ok(seen.has(total - 1));
    host.querySelector('[data-impact-kind]').onchange({ target: { value: 'KEEP' } });
    assert.ok(host.querySelector('[data-impact-count]').textContent.includes(`当前筛选 ${Math.floor(total / 2)} 项`));
    host.querySelector('[data-impact-kind]').onchange({ target: { value: '' } });
    assert.ok(rows.innerHTML.includes('Mock &lt;tag&gt;'));
    let stopped = 0;
    for (const name of ['input', 'change']) listeners.get(name)({ stopPropagation() { stopped++; } });
    assert.equal(stopped, 2); // Read filters do not invalidate financial approval.
    assert.equal(JSON.stringify(effect), original);
  }
  console.log('PASS ALL_MAPPINGS_REACHABLE=1 LABELS_ESCAPED=1 FROZEN_PREVIEW_UNCHANGED=1');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

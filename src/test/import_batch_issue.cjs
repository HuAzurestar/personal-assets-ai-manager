"use strict";
// Run the actual import renderer, including its read-page/finally refresh.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const source = fs.readFileSync(path.join(__dirname, '../frontend/js/view/import-batch.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

async function render(plan, esc) {
  const nodes = new Map();
  const host = { isConnected: true, innerHTML: '', querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, { value: '', disabled: false, textContent: '', innerHTML: '' });
    return nodes.get(selector);
  }, querySelectorAll(selector) { return selector === 'button,input,select' ? [...nodes.values()] : []; } };
  let writes = 0;
  const context = vm.createContext({ AbortController, URLSearchParams, esc,
    localStorage: { getItem: () => null },
    request: async () => ({ items: [], total: 0, page_size: 20 }),
    jsonRequest: async () => { writes++; throw Error('unexpected write'); },
  });
  vm.runInContext(source + '\nglobalThis.mount = mountImportBatch;', context);
  await context.mount(host, plan, () => {});
  assert.equal(writes, 0);
  assert.equal(host.querySelector('[data-batch-confirm]').disabled, true);
  return { html: host.querySelector('[data-batch-files]').innerHTML,
    summary: host.querySelector('[data-batch-summary]').textContent };
}

(async () => {
  const { esc } = await import(pathToFileURL(path.join(__dirname, '../frontend/js/util/core.js')).href);
  const file = { file_id: 1, filename: 'Mock.csv', accepted: 0, skipped: 0,
    invalid: 0, remaining: 0, parsed_row_count: 0, activity_range: { start: null, end: null },
    parse_issue_code: '', parse_issue_message: '', parse_recovery: '' };
  const plan = { token: 'mock-issues', status: 'READY', preview_digest: 'mock-digest',
    counts: { new: 0, existing: 0, processed: 0 }, issue_count: 1, issues: [], files: [
      { ...file, filename: '<Mock broken.csv>', parse_status: 'FAILED',
        parse_issue_code: 'HEADER_NOT_FOUND', parse_issue_message: '未找到可识别的交易表头。',
        parse_recovery: '请保留完整明细表头，选择正确来源后重新导出。' },
      { ...file, file_id: 2, filename: 'Mock empty.csv', parse_status: 'EMPTY' },
      { ...file, file_id: 3, filename: 'Mock ready.csv', parse_status: 'READY', parsed_row_count: 24, remaining: 24 },
    ] };
  const mixed = await render(plan, esc);
  assert.match(mixed.html, /role="alert" data-file-parse-error/);
  assert.match(mixed.html, /HEADER_NOT_FOUND/);
  assert.match(mixed.html, /重新导出/);
  assert.match(mixed.html, /有效空文件/);
  assert.match(mixed.html, /解析成功：24 行/);
  assert.ok(!mixed.html.includes('<Mock broken.csv>'));
  assert.match(mixed.summary, /文件解析失败 1 · 行问题 0 · 总问题 1/);
  // File-level failures remain reachable even when row issue hints are bounded.
  const large = await render({ ...plan, token: 'mock-many-issues', issue_count: 150, has_more_issues: true,
    files: Array.from({ length: 99 }, (_, index) => ({ ...plan.files[0], file_id: index + 1 })),
    issues: [{ file_id: 1, source_row_number: 8, code: '<ROW_INVALID>' }] }, esc);
  assert.equal((large.html.match(/data-file-parse-error/g) || []).length, 99);
  assert.match(large.summary, /文件解析失败 99 · 行问题 51 · 总问题 150/);
  assert.match(large.html, /不能把本提示当作完整行列表/);
  assert.ok(!large.html.includes('<ROW_INVALID>'));
  console.log('PASS FILE_ERROR_VISIBLE=1 EMPTY_DISTINCT=1 COMPLETE_FILE_FAILURES=1 SAFE_TEXT=1 NO_WRITE=1');
})().catch(error => { console.error(error); process.exitCode = 1; });

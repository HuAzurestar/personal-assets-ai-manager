'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the production page and refresh handlers, not a copy of their query logic.
const source = fs.readFileSync(path.join(__dirname, '../frontend/js/view/ledger.js'), 'utf8');
const start = source.indexOf('function historySummaryMarkup(');
const end = source.indexOf('function scheduleHistoryRefresh(', start);
assert.ok(start >= 0 && end > start);
const result = total => ({items: [], total, page_index: 1, page_size: 10});
const summary = count => ({import_file_count: count, imported_file_count: count, success_count: count * 2});
const form = {
  dataset: {page: '2'}, isConnected: true,
  elements: {source_type: {value: '102'}, status: {value: '1'}},
  classList: {add() {}, remove() {}},
};
const nodes = {
  '[data-form="history-filter"]': form,
  '[data-history-results]': {innerHTML: '', setAttribute() {}, removeAttribute() {}},
  '[data-history-summary]': {innerHTML: ''},
  '[data-history-updating]': {textContent: ''},
  '#page-content': {},
};
let calls = [];
const countFor = url => new URL(url, 'http://fictional.test').searchParams.has('filter') ? 1 : 6;
const context = vm.createContext({
  URLSearchParams, AbortController, clearTimeout,
  state: {page: 'import-history', params:new URLSearchParams({source_type:'102',status:'1',page:'2'}), historyRequestVersion: 0}, interactionVersion: 0,
  resourceId:value => {const number = Number(value); if (!Number.isSafeInteger(number) || number < 1) throw Error('invalid page'); return number;},
  canonicalHash:(_page, params) => `workbench/import/history${params.size ? '?'+params : ''}`,
  history: {state:{}, replaceState(_state, _title, route) {this.route = route;}},
  navigationView:{remember(){},mounted(){}}, renderedRoute:null,
  $: selector => nodes[selector],
  sourceLabels: {101: '支付宝', 102: '微信'}, statusLabels: {1: '已导入'},
  esc: value => String(value), preserveView: (_root, render) => render(),
  bindPage() {}, canRefreshPage: () => true, toast: message => {throw new Error(message);},
  request: async url => {
    calls.push(url);
    return url.includes('/summary') ? summary(countFor(url)) : result(countFor(url));
  },
});
vm.runInContext(source.slice(start, end), context);
// Isolate the query/refresh behavior from the unrelated row rendering helpers.
context.historyResultsMarkup = value => `ROWS:${value.total}`;
function assertSameFilter(requests, expected) {
  assert.equal(requests.length, 2);
  const list = new URL(requests.find(url => url.includes('/list?')), 'http://fictional.test');
  const aggregate = new URL(requests.find(url => url.includes('/summary')), 'http://fictional.test');
  assert.equal(aggregate.searchParams.get('filter'), list.searchParams.get('filter'));
  assert.deepEqual(JSON.parse(aggregate.searchParams.get('filter')), expected);
  assert.deepEqual([...aggregate.searchParams.keys()], expected ? ['filter'] : []);
}

(async () => {
  const both = {op: 'AND', expression: [
    {key: 'source_type', op: '=', val: 102}, {key: 'status', op: '=', val: 1},
  ]};
  const markup = await context.importHistoryPage();
  assertSameFilter(calls, both);
  assert.equal(new URL(calls[0], 'http://fictional.test').searchParams.get('page_index'), '2');
  assert.match(markup, /data-page="2"/);
  assert.match(markup, /value="102" selected/);
  assert.match(markup, /<strong>1<\/strong>/);
  assert.doesNotMatch(markup, /<strong>6<\/strong>/);
  for (const [sourceType, status, expected] of [
    ['101', '', {key: 'source_type', op: '=', val: 101}],
    ['', '0', {key: 'status', op: '=', val: 0}],
    ['', '', null],
  ]) {
    form.elements.source_type.value = sourceType;
    form.elements.status.value = status;
    calls = [];
    await context.refreshHistoryResults(form, 1);
    assertSameFilter(calls, expected);
    assert.equal(context.state.params.get('source_type'), sourceType || null);
    assert.equal(context.state.params.get('status'), status || null);
    assert.match(nodes['[data-history-summary]'].innerHTML, expected ? /<strong>1<\/strong>/ : /<strong>6<\/strong>/);
  }
  // Zero matches must clear both list and statistics, including background refresh.
  calls = [];
  context.request = async url => {
    calls.push(url);
    return url.includes('/summary') ? summary(0) : result(0);
  };
  await context.refreshHistoryResults(form, 1, true);
  assertSameFilter(calls, null);
  assert.equal(nodes['[data-history-results]'].innerHTML, 'ROWS:0');
  assert.match(nodes['[data-history-summary]'].innerHTML, /<strong>0<\/strong>/);
  // A late response from an obsolete filter must not replace the newer pair.
  const waiting = [];
  context.request = url => new Promise(resolve => waiting.push({url, resolve}));
  form.elements.source_type.value = '102';
  const older = context.refreshHistoryResults(form, 1);
  form.elements.source_type.value = '101';
  const newer = context.refreshHistoryResults(form, 1);
  waiting.slice(2).forEach(item => item.resolve(item.url.includes('/summary') ? summary(3) : result(3)));
  await newer;
  waiting.slice(0, 2).forEach(item => item.resolve(item.url.includes('/summary') ? summary(9) : result(9)));
  await older;
  assert.equal(nodes['[data-history-results]'].innerHTML, 'ROWS:3');
  assert.match(nodes['[data-history-summary]'].innerHTML, /<strong>3<\/strong>/);
  assert.doesNotMatch(nodes['[data-history-summary]'].innerHTML, /<strong>9<\/strong>/);
  console.log('PASS production import history initial/filter/clear/zero/background/late-response summary scope');
})().catch(error => {console.error(error); process.exitCode = 1;});

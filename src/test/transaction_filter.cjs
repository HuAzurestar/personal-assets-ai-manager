const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const file = pathToFileURL(path.resolve(__dirname, '../frontend/js/component/transaction-filter.js')).href;
  const {transactionFilter, transactionScopeFilters, transactionFilterDirty} = await import(file);
  const scopes = new URLSearchParams({party_id: '42', account_id: '0', account_ref_id: '0', tag_id: '45'});
  assert.deepEqual(transactionScopeFilters(scopes), [
    {key: 'party_id', op: '=', val: 42}, {key: 'account_id', op: '=', val: 0}, {key: 'account_ref_id', op: '=', val: 0}]);
  assert.equal(transactionScopeFilters(scopes, true).at(-1).key, 'tag_id');
  for (const name of ['party_id', 'account_id', 'account_ref_id', 'tag_id']) {
    for (const value of ['', '1e2', '-1', '1.5', 'true', '9007199254740993'])
      assert.throws(() => transactionScopeFilters(new URLSearchParams({[name]: value}), true), {code: 'INVALID_ID'});
  }
  for (const name of ['party_id', 'tag_id'])
    assert.throws(() => transactionScopeFilters(new URLSearchParams({[name]: '0'}), true), {code: 'INVALID_ID'});
  const params = new URLSearchParams({word: '<script>Mock</script>', account_id: '0', account_ref_id: '0'});
  const markup = transactionFilter({params, currency: '<select name="currency_code"></select>', sort: '<select name="sort"></select>'});
  assert.ok(markup.includes('data-transaction-filter'));
  assert.ok(markup.includes('name="account_id" value="0"'));
  assert.ok(markup.includes('data-filter-zero="account_ref_id"'));
  assert.ok(markup.includes('来源未识别') && markup.includes('已识别未分组'));
  assert.ok(!markup.includes('<script>') && !markup.includes('type="number"'));
  assert.ok(!markup.includes('data-named-choice="tag_id"'));
  assert.ok(!markup.includes('data-filter-more open'));
  const main = markup.split('<details id="transaction-filter-more"')[0];
  const more = markup.split('<details id="transaction-filter-more"')[1];
  assert.ok(main.includes('transaction-filter-currency'), 'currency must be a common-row condition');
  assert.ok(!main.includes('name="sort"') && !main.includes('name="search_field"'));
  assert.ok(more.includes('name="sort"') && more.includes('name="search_field"'));
  assert.ok(transactionFilter({params: scopes, flow: true, currency: '', sort: ''}).includes('data-named-choice="tag_id"'));
  globalThis.FormData = class { constructor(form) {this.values = form.values;} [Symbol.iterator]() {return this.values[Symbol.iterator]();} };
  const form = {values: [['word', ''], ['search_field', 'summary'], ['sort', 'occurred_time.desc'], ['party_id', ''], ['currency_code', ''], ['cash_direction', '']]};
  assert.equal(transactionFilterDirty(form, new URLSearchParams()), false);
  form.values[0][1] = ' unsent ';
  assert.equal(transactionFilterDirty(form, new URLSearchParams()), true);
  assert.equal(transactionFilterDirty(form, new URLSearchParams({word: 'unsent'})), false);
  form.values = [['account_id', '0']];
  assert.equal(transactionFilterDirty(form, new URLSearchParams()), true);
  assert.equal(transactionFilterDirty(form, new URLSearchParams({account_id: '0'})), false);
  form.values = [['date_from', '2026-09-01T00:00'], ['date_to', '2026-09-01T23:59'], ['cash_direction', 'IN'], ['currency_code', 'CNY']];
  assert.equal(transactionFilterDirty(form, new URLSearchParams({date_from: '2026-09-01', date_to: '2026-09-01', cash_direction: '1', currency_code: ' cny '})), false);
  assert.equal(transactionFilterDirty(null, scopes), false);
  const {flowVisibility} = await import(pathToFileURL(path.resolve(__dirname, '../frontend/js/util/flow-visibility.js')).href);
  for (const value of [undefined, '']) assert.equal(flowVisibility(new URLSearchParams(value === undefined ? {} : {active:value})), 'true');
  for (const value of ['true', 'false', 'all']) assert.equal(flowVisibility(new URLSearchParams({active:value})), value);
  for (const value of ['invalid','0','TRUE']) assert.throws(() => flowVisibility(new URLSearchParams({active:value})));
  form.values = [['active', 'true']];
  assert.equal(transactionFilterDirty(form, new URLSearchParams()), false);
  assert.equal(transactionFilterDirty(form, new URLSearchParams({active:''})), false);
  assert.equal(transactionFilterDirty(form, new URLSearchParams({active:'all'})), true);
  form.values = [['active', 'all']];
  assert.equal(transactionFilterDirty(form, new URLSearchParams({active:'all'})), false);
  assert.ok(transactionFilter({params:scopes,flow:true,currency:'',sort:'',secondary:'Mock visible mode'}).includes('Mock visible mode'));
  console.log('PASS actual transaction filter markup, exact scopes/zero, escaped query and unsent-condition refresh guard');
})().catch(error => {console.error(error); process.exitCode = 1;});

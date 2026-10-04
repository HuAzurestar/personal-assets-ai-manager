'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {unitFixture} = require('./unit_fixture.cjs');

(async () => {
  const {installUnitDictionary} = await import(pathToFileURL(path.resolve(__dirname, '../frontend/js/util/unit-dictionary.js')).href);
  installUnitDictionary(unitFixture());
  const {accountCorrectionIntent, accountCorrectionMarkup} = await import(pathToFileURL(path.resolve(__dirname, '../frontend/js/util/account-correction.js')).href);
  const keepers = new Map([[7, {transaction_id:8}]]);
  const intent = accountCorrectionIntent(11, '2', 1, [7], keepers);
  assert.deepEqual(intent, {account_corrections:[{ledger_id:11,account_ref_id:2}],correction_duplicates:[{transaction_id:7,kept_transaction_id:8}]});
  keepers.set(7,{transaction_id:9});
  assert.equal(intent.correction_duplicates[0].kept_transaction_id,8,'frozen intent does not retain mutable selection');
  assert.deepEqual(accountCorrectionIntent(11,'0',1,[],new Map()), {account_corrections:[{ledger_id:11,account_ref_id:0}],correction_duplicates:[]});
  for (const args of [[11,'',1,[],new Map()], [11,'1',1,[],new Map()], [11,'9007199254740993',1,[],new Map()], [11,'2',1,[7],new Map()]]) assert.throws(() => accountCorrectionIntent(...args));
  const original = {id:3,status:'CONFIRMED',updated_time:'2024-01-01T00:00:00Z',title:'Mock <group>',type:'OTHER_MANUAL',
    ledger_entries:[{id:11,economic_type:'TRANSACTION',cash_amount:103000,cash_currency_code:'CNY',cash_direction:'OUT',account_ref_id:1,occurred_time:'2024-01-01T00:00:00Z'},
      {id:12,economic_type:'TRANSACTION',cash_amount:1000,cash_currency_code:'KRW',cash_direction:'OUT',account_ref_id:1,occurred_time:'2024-01-01T00:00:00Z'}],
    allocations:[{ledger_id:11,transaction_id:7},{ledger_id:12,transaction_id:8}],position_legs:[],position_allocations:[],positions:[]};
  const draft = {case_code:'ACCOUNT_CORRECTION',source_review_id:3,copied_ledger_ids:[11,12],account_changes:[{ledger_id:11,allocation_index:0,before_account_ref_id:1,after_account_ref_id:2}],
    allocations:[{transaction_id:7,economic_type:'TRANSACTION',cash_amount:103000,account_ref_id:2},{transaction_id:8,economic_type:'TRANSACTION',cash_amount:1000,account_ref_id:1}],legs:[],position_allocations:[]};
  const plan = {new_reviews:[draft],expected_reviews:[{review_id:3,status:'CONFIRMED',updated_time:original.updated_time}],blocking_issues:[],position_changes:[],tag_effect:{mappings:[]}};
  const originals = new Map([[3,original]]), labels = new Map([[1,'Mock old'],[2,'Mock <new>']]);
  const markup = accountCorrectionMarkup(plan,originals,labels);
  assert.ok(markup.includes('Mock &lt;new&gt;') && markup.includes('Mock &lt;group&gt;'));
  assert.ok(markup.includes('CNY') && markup.includes('KRW') && !markup.includes('最小单位'));
  assert.ok(markup.indexOf('现金金额、方向、币种和时间保持') < markup.indexOf('技术关系'));
  assert.ok(markup.includes('未更正') && !markup.includes('<details open'));
  for (const broken of [{...draft,copied_ledger_ids:[11]}, {...draft,allocations:[{...draft.allocations[0],cash_amount:1},draft.allocations[1]]}, {...draft,legs:[{}]}]) {
    assert.throws(() => accountCorrectionMarkup({...plan,new_reviews:[broken]},originals,labels), /完整|一致|改变/);
  }
  assert.throws(() => accountCorrectionMarkup(plan,new Map(),labels), /完整/);
  assert.throws(() => accountCorrectionMarkup(plan,new Map([[3,{...original,status:'REVOKED'}]]),labels), /改变/);
  console.log('PASS account-only intent, exact keeper scope, immutable frozen selection and complete business preview with original units');
})().catch(error => {console.error(error);process.exitCode=1;});

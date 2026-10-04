'use strict';
// Execute the actual preview formatter with the existing exact-unit catalog.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pathToFileURL} = require('node:url');
const {unitFixture} = require('./unit_fixture.cjs');

async function main() {
  const url = file => pathToFileURL(path.join(__dirname,'../frontend/js',file)).href;
  const core = await import(url('util/core.js'));
  const copy = await import(url('util/financial-copy.js'));
  const {installUnitDictionary} = await import(url('util/unit-dictionary.js'));
  installUnitDictionary(unitFixture());
  const source = fs.readFileSync(path.join(__dirname,'../frontend/js/view/review-workbench.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  const context = vm.createContext({...core,...copy});
  vm.runInContext(source+'\nglobalThis.preview = previewMarkup;',context);
  const fact = (cash_currency_code,cash_direction,summary) => ({cash_currency_code,cash_direction,
    summary,occurred_time:'2024-01-01T00:00:00Z'});
  const facts = new Map([[3,fact('CNY','OUT','Mock cash <img src=x>')],
    [4,fact('KRW','IN','Mock won')],[5,fact('CNY','OUT','Mock duplicate')]]);
  const position = {title:'Mock quantity',type:'ASSET',usage_scenario:'PERSONAL-LENDING',
    party_id:1,counterparty:'Mock counterparty',unit_code:'KG_3'};
  const draft = {case_code:'BORROW_REPAY',type:'ASSET_LIABILITY',title:'Mock business result',
    allocations:[{transaction_id:3,cash_amount:12345,account_ref_id:2,economic_type:'TRANSACTION'},
      {transaction_id:4,cash_amount:400,account_ref_id:0,economic_type:'TRANSACTION'},
      {transaction_id:5,cash_amount:12345,account_ref_id:2,economic_type:'DUPLICATE'}],
    new_positions:[position],legs:[{new_position_index:0,existing_position_id:0,type:'ASSET',
      leg_direction:'IN',leg_amount:1,source:0,basis:'Mock basis',occurred_time:'2024-01-01T00:00:00Z'}],
    position_allocations:[{allocation_index:0,leg_index:0,cash_amount:12345,cash_currency_code:'CNY'}]};
  const plan = {impact:{conflicting_review_ids:[7],restored_default_review_ids:[8],affected_account_ref_ids:[2],
    affected_position_ids:[],dependent_position_leg_ids:[9],tag_ledger_ids:[10]},blocking_issues:[],
    reviews:[],new_reviews:[draft],position_changes:[{new_review_index:0,new_position_index:0,
      before:{quantity_state:'UNKNOWN',quantity:null},after:{quantity_state:'KNOWN',quantity:1}}],
    coverage:[{transaction_id:3,cash_amount:12345,effective_cash_amount:12345}],
    tag_effect:{mappings:[{disposition:'REVIEW_REQUIRED'}],affected_views:[1],affected_rule_ids:[2],
      invalidated_request_count:1,scan_state:'PENDING'}};
  const frozen = JSON.stringify(plan);
  const html = context.preview(plan,facts,new Map(),new Map([['account_ref_id:2','Mock named source']]));
  assert.match(html,/data-review-business/);
  assert.ok(html.indexOf('data-review-cash-result') < html.indexOf('data-review-technical'));
  assert.match(html,/<details data-review-technical>/); // Closed by default, not removed.
  assert.match(html,/完整技术关系与标签影响/);
  assert.match(html,/123\.45 CNY/); assert.match(html,/400 KRW/);
  assert.match(html,/支出/); assert.match(html,/收入/); assert.match(html,/Mock named source/);
  assert.match(html,/来源未识别/); assert.doesNotMatch(html,/来源卡 #0/);
  assert.match(html,/重复证据[^<]*不增加现金/);
  assert.match(html,/0\.001 KG_3/); assert.match(html,/数量未知/);
  assert.match(html,/金额覆盖核对/); assert.match(html,/不代表业务已核对正确/);
  assert.match(html,/data-tag-impact/); assert.match(html,/可能失效的后续腿：#9/);
  assert.match(html,/现金行 1 → 数量腿 1/);
  assert.match(html,/&lt;img src=x&gt;/); assert.doesNotMatch(html,/<img src=x>/);
  assert.equal(JSON.stringify(plan),frozen,'presentation must not alter frozen server plan');
  const unknown = context.preview({...plan,position_changes:[{position_id:99,
    before:{quantity_state:'NEEDS_REVIEW',quantity:99},after:{quantity_state:'FUTURE',quantity:0}}]},facts);
  const change = unknown.match(/<p data-review-quantity-change>[\s\S]*?<\/p>/)[0];
  assert.match(change,/数量待核对/); assert.match(change,/状态未知/); assert.doesNotMatch(change,/0\.00/);
  const unread = context.preview(plan,new Map());
  const unreadCash = unread.match(/<li data-review-cash-result>[\s\S]*?<\/li>/)[0];
  assert.match(unreadCash,/单位信息未读取/); assert.doesNotMatch(unreadCash,/123\.45 CNY/);
  // Attribution has its own explicit currency; it must retain exact formatting
  // even when Fact display metadata was not supplied to this formatter.
  assert.match(unread,/现金行 1 → 数量腿 1：[^<]*123\.45 CNY/);
  const transition = context.preview({...plan,new_reviews:[],position_changes:[],reviews:[{id:7}]});
  assert.match(transition,/不新建现金输出/); assert.match(transition,/金额覆盖核对/);
  console.log('PASS business-first exact money/quantity, scoped unknowns, complete folded relations and immutable preview');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

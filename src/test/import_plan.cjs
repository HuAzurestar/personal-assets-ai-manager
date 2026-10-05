'use strict';
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');

async function run() {
  const module = name => pathToFileURL(path.join(__dirname,'../frontend/js',name)).href;
  const {completeImportScope} = await import(module('util/import-scope.js'));
  const {validateImportPlan, mountImportPlan} = await import(module('component/import-plan.js'));
  const {installUnitDictionary} = await import(module('util/unit-dictionary.js'));
  installUnitDictionary(require('./unit_fixture.cjs').unitFixture());
  const rows = Array.from({length:2500},(_,i) => ({file_id:1,source_row_number:i + 1}));
  const policy = {max_batch_rows:1000,serial:true,retain_committed:true,automatic_post_replay:false,
    stop_on:['CANCEL','FAILURE','STALE_PREVIEW','RESULT_UNKNOWN']};
  const batch = (start,count,index) => ({batch_index:index,
    row_ranges:[{file_id:1,row_start:start + 1,row_end:start + count,row_count:count}],
    preview:{selected_rows:rows.slice(start,start + count),budget:{selected_rows:count,review_groups:count,
      facts:count,outputs:count,position_links:count,tag_changes:0},can_confirm:true},duplicate_revoke_scope:[]});
  const plan = {selected_count:2500,selected_rows:rows,batches:[batch(0,1000,0),batch(1000,1000,1),batch(2000,500,2)],
    blocked:[],can_confirm:true,cross_batch_atomic:false,execution_policy:policy};
  assert.equal(validateImportPlan(plan,rows),plan);
  const clone = () => structuredClone(plan);
  for (const mutate of [
    value => value.selected_rows.pop(), value => value.selected_rows[0] = value.selected_rows[1],
    value => value.batches[0].preview.selected_rows.pop(), value => value.batches[2].preview.selected_rows.push(rows[0]),
    value => value.batches.pop(), value => value.batches[1].preview.can_confirm = false,
    value => value.cross_batch_atomic = true, value => value.execution_policy.automatic_post_replay = true,
    value => value.execution_policy.stop_on.pop(), value => value.selected_rows[0].file_id = true,
  ]) {const broken = clone(); mutate(broken); assert.throws(() => validateImportPlan(broken,rows));}
  const blocked = clone();
  blocked.batches.pop(); blocked.can_confirm = false;
  blocked.blocked.push({selected_rows:rows.slice(2000),row_ranges:[{file_id:1,row_start:2001,row_end:2500,row_count:500}],
    budget:batch(2000,500,2).preview.budget,issue:{code:'DETAIL_LIMIT',dimension:'facts',count:2001,limit:2000}});
  assert.equal(validateImportPlan(blocked,rows),blocked);
  let calls = 0;
  const page = query => {
    calls++; const index = Number(query.get('page_index'));
    assert.equal(query.get('preview_digest'),'frozen'); assert.equal(query.get('page_size'),'100');
    return {total:2500,page_index:index,page_size:100,items:rows.slice((index - 1) * 100,index * 100)};
  };
  assert.deepEqual(await completeImportScope(async query => page(query),{preview_digest:'frozen'}),rows);
  assert.equal(calls,25);
  for (const change of [
    value => value.total = 20001, value => value.page_size = 20, value => value.page_index++,
    value => value.items.pop(), value => value.total--, value => value.items[0] = rows[0],
  ]) {
    let count = 0;
    await assert.rejects(completeImportScope(async query => {
      const value = page(query); if (++count === 2) change(value); return value;
    },{preview_digest:'frozen'}));
    assert.equal(count,2);
  }
  let count = 0;
  await assert.rejects(completeImportScope(async query => {if (++count === 3) throw Error('page three failed'); return page(query);},{preview_digest:'frozen'}));
  assert.equal(count,3);
  const controller = new AbortController();
  await assert.rejects(completeImportScope(async query => {controller.abort(); return page(query);},
    {preview_digest:'frozen'},{signal:controller.signal}),error => error.name === 'AbortError');
  let valid = true;
  await assert.rejects(completeImportScope(async query => {valid = false; return page(query);},
    {preview_digest:'frozen'},{valid:() => valid}),error => error.name === 'AbortError');
  let now = 0;
  await assert.rejects(completeImportScope(async query => {now = 30001; return page(query);},
    {preview_digest:'frozen'},{now:() => now}),/30秒/);

  // A read that never settles must still obey the whole-scope budget. A late
  // response or an ignored AbortSignal must not return an accumulated prefix.
  for (const stop of ['TIMEOUT','CANCEL']) {
    let timeout, cleared = 0, issued = 0, late, readSignal;
    const parent = new AbortController();
    const timers = {setTimeout(callback, delay) {assert.equal(delay,30000); timeout = callback; return 7;},
      clearTimeout(id) {assert.equal(id,7); cleared++;}};
    const scope = completeImportScope(async (query, options) => {
      issued++; readSignal = options?.signal;
      if (issued === 1) return page(query);
      return new Promise(resolve => {late = () => resolve(page(query));});
    },{preview_digest:'frozen'},{signal:parent.signal,timers});
    // Allow the first page to settle and the second to start.
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(issued,2); assert.equal(typeof timeout,'function');
    if (stop === 'TIMEOUT') timeout(); else parent.abort();
    await assert.rejects(scope, error => stop === 'TIMEOUT' ? /30秒/.test(error.message) : error.name === 'AbortError');
    assert.equal(readSignal.aborted,true); assert.equal(cleared,1);
    late(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(issued,2); // No page-three request after a stopped read.
  }

  // Exercise actual local pager handlers, not a source-string assertion.
  class Node {
    constructor() {this.isConnected = true; this.innerHTML = ''; this.nodes = new Map();}
    querySelector(key) {if (!this.nodes.has(key)) this.nodes.set(key,new Node()); return this.nodes.get(key);}
    querySelectorAll(key) {return key === '[data-plan-batch]' && this.batchButton ? [this.batchButton] : [];}
  }
  const host = new Node();
  mountImportPlan(host,blocked);
  assert.match(host.innerHTML,/此前成功批保留/); assert.match(host.innerHTML,/不自动重发 POST/);
  assert.match(host.querySelector('[data-plan-blocked]').innerHTML,/2001–2500/);
  const many = clone();
  many.batches = Array.from({length:50},(_,i) => batch(i * 50,50,i));
  validateImportPlan(many,rows);
  mountImportPlan(host,many);
  const list = host.querySelector('[data-plan-batches]');
  assert.match(list.innerHTML,/1–20 \/ 50/);
  list.querySelector('[data-plan-next]').onclick();
  assert.match(list.innerHTML,/21–40 \/ 50/);
  list.querySelector('[data-plan-next]').onclick();
  assert.match(list.innerHTML,/第 50 批/); assert.match(list.innerHTML,/41–50 \/ 50/);
  list.querySelector('[data-plan-next]').onclick(); assert.match(list.innerHTML,/41–50 \/ 50/);
  host.querySelector('[data-plan-batches]').isConnected = false;
  list.querySelector('[data-plan-prev]').onclick(); assert.match(list.innerHTML,/41–50 \/ 50/);
  // Execute the actual detail handler, retaining codes and escaping labels.
  const display = clone();
  display.selected_count = 1000; display.selected_rows = rows.slice(0,1000); display.batches = [display.batches[0]];
  const preview = display.batches[0].preview;
  preview.counts = {new_real_fact:1000,new_duplicate_fact:0,evidence_only:0,skipped:0,invalid:0,unresolved:0};
  preview.pairs = ['NONE_IN_SCOPE','SUSPECTED','UNCHECKED'].map((state,index)=>({row:rows[index],target:null,resolution:'AUTO',
    comparison:{amount:100,currency_code:'CNY',cash_direction:'OUT',occurred_time:null,exact_match:false},
    source_labels_masked:['Mock <source>'],duplicate_hint:{state,candidate_count:index === 2 ? null : index},reason_codes:[]}));
  preview.effects = {by_currency:[],tag_effect:{new_output_count:1000,projected_assignment_count:0,affected_view_ids:[],affected_rule_ids:[],default_assignments:[]},
    new_original_defaults:[{row:rows[0],output_index:0,amount:100,currency_code:'CNY',cash_direction:'OUT',occurred_time:'2024-01-01T00:00:00',account_ref_id:null,source_label_masked:'Mock',after_status:'CONFIRMED'}],
    new_duplicate_reviews:[],before_after_review_states:[{before:{id:7,title:'Mock review',status:'CONFIRMED',type:'OTHER_MANUAL',updated_time:'2024-01-01'},after_status:'REVOKED'}]};
  preview.issues = [];
  const detailHost = new Node(), button = new Node(); button.dataset = {planBatch:'0'};
  detailHost.querySelector('[data-plan-batches]').querySelector('[data-plan-list-items]').batchButton = button;
  mountImportPlan(detailHost,validateImportPlan(display,rows.slice(0,1000)));
  assert.match(detailHost.innerHTML,/规则校验通过；不代表业务已核对正确/);
  button.onclick();
  const detail = detailHost.querySelector('[data-plan-selected-detail]');
  assert.match(detail.innerHTML,/事务内重验.*明确确认/);
  const pairsHtml = detail.querySelector('[data-plan-pairs]').innerHTML;
  assert.match(pairsHtml,/本次核验范围内未发现候选（不代表全库无重复）/);
  assert.match(pairsHtml,/疑似重复（未认定重复）（SUSPECTED）/);
  assert.match(pairsHtml,/风险未核验（不能按零候选处理）（UNCHECKED）.*候选数未知/s);
  assert.match(pairsHtml,/Mock &lt;source&gt;/); assert.doesNotMatch(pairsHtml,/<source>/);
  assert.match(detail.querySelector('[data-plan-defaults]').innerHTML,/拟生效（尚未写入）（CONFIRMED）/);
  assert.match(detail.querySelector('[data-plan-reviews]').innerHTML,/解释已生效（CONFIRMED） → 拟停用（尚未写入）（REVOKED）/);
  display.can_confirm = false; preview.can_confirm = false;
  mountImportPlan(detailHost,validateImportPlan(display,rows.slice(0,1000))); button.onclick();
  assert.doesNotMatch(detailHost.innerHTML,/规则校验通过/);
  assert.match(detail.innerHTML,/本批被阻断，不能提交/);
  console.log('PASS COMPLETE_SCOPE=1 NO_PARTIAL=1 CONTEXT_GUARDS=1 FROZEN_COVERAGE=1 ALL_BATCH_PAGES=1 NO_FINANCIAL_WRITES=1');
}
run().catch(error => {console.error(error); process.exitCode = 1;});

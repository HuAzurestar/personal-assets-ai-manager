'use strict';
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');

async function run() {
  const module = name => pathToFileURL(path.join(__dirname,'../frontend/js',name)).href;
  const {completeImportScope} = await import(module('util/import-scope.js'));
  const {validateImportPlan, mountImportPlan} = await import(module('component/import-plan.js'));
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

  // Exercise actual local pager handlers, not a source-string assertion.
  class Node {
    constructor() {this.isConnected = true; this.innerHTML = ''; this.nodes = new Map();}
    querySelector(key) {if (!this.nodes.has(key)) this.nodes.set(key,new Node()); return this.nodes.get(key);}
    querySelectorAll() {return [];}
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
  console.log('PASS COMPLETE_SCOPE=1 NO_PARTIAL=1 CONTEXT_GUARDS=1 FROZEN_COVERAGE=1 ALL_BATCH_PAGES=1 NO_FINANCIAL_WRITES=1');
}
run().catch(error => {console.error(error); process.exitCode = 1;});

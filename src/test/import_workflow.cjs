'use strict';
// Execute the actual UI handlers with complete fictional scope; no snapshots
// pretending source strings prove submit gating or saved-choice semantics.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pathToFileURL} = require('node:url');

(async () => {
  const module = name => pathToFileURL(path.join(__dirname,'../frontend/js',name)).href;
  const decisions = await import(module('util/import-decision.js'));
  const {validateImportPlan,mountImportPlan} = await import(module('component/import-plan.js'));
  const {mountImportDraftRows} = await import(module('component/import-bulk.js'));
  const {isUnknownWrite} = await import(module('api/client.js'));
  const {importKnownFailureMessage} = await import(module('component/import-execution.js'));
  class Node {
    constructor() {this.isConnected=true;this.open=true;this.nodes=new Map();this.innerHTML='';this.textContent='';this.value='';this.checked=false;}
    querySelector(key) {if (!this.nodes.has(key)) this.nodes.set(key,new Node());return this.nodes.get(key);}
    querySelectorAll(key) {return key === 'button,input,select' ? [...this.nodes.values()] : [];}
    close() {this.isConnected=false;this.open=false;}
    addEventListener() {}
  }
  const rows = Array.from({length:811},(_,i)=>({file_id:Math.floor(i/150)+1,source_row_number:i%150+1,
    classification:i<9 ? 'INVALID' : 'NEW',issue_codes:i<9 ? ['ROW_INVALID'] : [],
    duplicate_hint:i>=9 && i<33 ? {state:'SUSPECTED',candidate_count:1,scope:{source_known:true}}
      : {state:'NONE_IN_SCOPE',candidate_count:0,scope:{source_known:true}}}));
  let current={token:'workflow-fiction',status:'READY',updated_time:'2026-10-04T00:00:00.000001Z',
    preview_digest:'a'.repeat(64),files:[],counts:{new:802,invalid:9,existing:0,processed:0},issue_count:9};
  let context,financial=0,puts=0,reads=0,errorCode='IMPORT_REVIEW_REQUIRED',planFailure=false,dialog;
  const storage=new Map(),host=new Node();
  const makePlan = selected => {
    const issues=selected.filter(row=>{
      const item=context.selected.get(`${row.file_id}:${row.source_row_number}`);
      return item.choice.decision === 'ACCEPT' && (item.row.issue_codes.length || decisions.importRiskUnresolved(item.row,item.choice));
    }).map(row=>({...row,code:'IMPORT_REVIEW_REQUIRED'}));
    const budget={selected_rows:selected.length,review_groups:1,facts:778,outputs:778,position_links:778,tag_changes:0};
    return {source_preview_digest:current.preview_digest,selected_count:selected.length,selected_rows:selected,
      operation_preview_digest:'c'.repeat(64),can_confirm:!issues.length,blocked:[],cross_batch_atomic:false,
      execution_policy:{max_batch_rows:1000,serial:true,retain_committed:true,automatic_post_replay:false,
        stop_on:['CANCEL','FAILURE','STALE_PREVIEW','RESULT_UNKNOWN']},
      batches:[{batch_index:0,row_ranges:[],duplicate_revoke_scope:[],preview:{source_preview_digest:current.preview_digest,
        batch_preview_digest:'b'.repeat(64),selected_rows:selected,budget,can_confirm:!issues.length,issues,
        pairs:selected.map(row=>({...row,row,duplicate_hint:context.selected.get(`${row.file_id}:${row.source_row_number}`).row.duplicate_hint}))}}]};
  };
  const sandbox=vm.createContext({AbortController,URLSearchParams,TextEncoder,...decisions,validateImportPlan,mountImportPlan,
    importKnownFailureMessage,
    mountImportDraftRows,isUnknownWrite,esc:String,
    workbenchDialog:()=>{dialog=new Node();return dialog;},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    request:async(url,options)=>{
      if (url.includes('/row/list')) return {items:[],total:0,page_size:20};
      if (url.endsWith('/operation-preview')) {
        reads++;if (planFailure) throw Object.assign(Error('fictional deadline'),{status:413,code:'READ_BUDGET_EXCEEDED'});
        return makePlan(JSON.parse(options.body).selected_rows);
      }
      return current;
    },
    jsonRequest:async(url,method,body)=>{
      if (method === 'PUT') {puts++;assert.equal(body.choices.length,811);return current;}
      assert.equal(method,'POST');assert.ok(url.endsWith('/confirm'));
      assert.equal(body.batch_preview_digest,'b'.repeat(64));financial++;
      throw Object.assign(Error('fictional rejection'),{status:errorCode === 'RESULT_UNKNOWN' ? 503 : 422,code:errorCode});
    }});
  const source=fs.readFileSync(path.join(__dirname,'../frontend/js/view/import-batch.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  vm.runInContext(source+'\nglobalThis.mount=mountImportBatch;globalThis.testContexts=contexts;',sandbox);
  await sandbox.mount(host,current,()=>{});
  context=sandbox.testContexts.get(current.token);
  context.selected=new Map(rows.map(row=>[`${row.file_id}:${row.source_row_number}`,{row,
    choice:{...decisions.importRowChoice(row),file_id:row.file_id,source_row_number:row.source_row_number}}]));
  context.dirty=true;
  const find=selector=>host.querySelector(selector);
  await find('[data-batch-confirm]').onclick();assert.equal(financial,0);
  await find('[data-batch-save]').onclick();
  assert.equal(puts,1);assert.equal(reads,1);assert.equal(financial,0);
  assert.equal(find('[data-batch-confirm]').disabled,false);
  assert.equal([...context.selected.values()].filter(item=>item.choice.decision==='ACCEPT').length,778);
  assert.match(find('[data-batch-risk-summary]').textContent,/疑似重复 24/);
  assert.match(find('[data-batch-risk-summary]').textContent,/已跳过 24，接受但未解决 0/);
  // A user's explicit ordinary ACCEPT remains authoritative, but does not
  // grant hidden NEW risk consent. All 24 blockers appear before any write.
  for (const item of context.selected.values()) if (decisions.importRiskState(item.row)==='SUSPECTED') item.choice.decision='ACCEPT';
  context.dirty=true;
  await find('[data-batch-save]').onclick();
  assert.equal(context.dirty,false);assert.equal(find('[data-batch-confirm]').disabled,true);
  assert.match(find('[data-batch-selection]').textContent,/选择已保存.*有阻断/);
  assert.match(find('[data-batch-status]').textContent,/未解决 24 行/);
  const issues=find('[data-batch-operation]').querySelector('[data-plan-all-issues]');
  assert.match(issues.innerHTML,/1–20 \/ 24/);
  issues.querySelector('[data-plan-next]').onclick();assert.match(issues.innerHTML,/21–24 \/ 24/);
  assert.match(issues.innerHTML,/核对候选/);
  await find('[data-batch-confirm]').onclick();assert.equal(financial,0);
  // Batch skip requires acknowledgement; it is draft-only, not hidden dropping.
  find('[data-batch-risk-skip]').onclick();
  dialog.querySelector('[data-risk-skip-apply]').onclick();assert.equal(context.dirty,false);
  dialog.querySelector('[data-risk-skip-ack]').checked=true;
  dialog.querySelector('[data-risk-skip-apply]').onclick();
  assert.equal(context.dirty,true);assert.equal(context.disclosure,null);assert.equal(financial,0);
  await new Promise(resolve=>setImmediate(resolve)); // Actual handler refreshes the visible page.
  await find('[data-batch-save]').onclick();assert.equal(find('[data-batch-confirm]').disabled,false);
  // Final authoritative rejection does not imply previously saved choices
  // were lost. No automatic retry, for risk, stale preview, or a busy writer.
  const saved=JSON.stringify([...context.selected.values()].map(item=>item.choice));
  for (const code of ['IMPORT_REVIEW_REQUIRED','STALE_PREVIEW','WRITE_BUSY']) {
    errorCode=code;
    await find('[data-batch-save]').onclick();const before=financial;
    await find('[data-batch-confirm]').onclick();
    assert.equal(financial,before+1);assert.equal(context.dirty,false);assert.equal(context.unknown,false);
    assert.equal(JSON.stringify([...context.selected.values()].map(item=>item.choice)),saved);
    assert.match(find('[data-batch-selection]').textContent,/选择已保存.*重新核验/);
    assert.equal(find('[data-batch-confirm]').disabled,true);
    assert.ok(!storage.has('paam.import.pending.v1'));
    await find('[data-batch-confirm]').onclick();assert.equal(financial,before+1);
  }
  planFailure=true;await find('[data-batch-save]').onclick();
  assert.equal(context.dirty,false);assert.equal(find('[data-batch-confirm]').disabled,true);
  assert.match(find('[data-batch-status]').textContent,/尚未取得可提交计划/);
  planFailure=false;await find('[data-batch-save]').onclick();
  errorCode='RESULT_UNKNOWN';await find('[data-batch-confirm]').onclick();
  assert.equal(context.unknown,true);assert.equal(find('[data-batch-confirm]').disabled,true);
  assert.ok(storage.has('paam.import.pending.v1'));
  const before=financial;await find('[data-batch-save]').onclick();await find('[data-batch-confirm]').onclick();assert.equal(financial,before);
  // Serial failure and changed server premises also do not mean local choices
  // became unsaved. Revalidation is separately required before another plan.
  context.unknown=false;storage.clear();await find('[data-batch-save]').onclick();
  find('[data-batch-consent]').checked=true;
  sandbox.createImportExecution=options=>({state:{stop_requested:false},stop:async()=>{},run:async()=>{
    options.failed(Object.assign(Error('fictional busy'),{status:503,code:'WRITE_BUSY'}),{unknown:false,stage:'CONFIRM'});
    return {phase:'FAILED'};
  }});
  await find('[data-batch-execute]').onclick();
  assert.equal(context.dirty,false);assert.equal(context.revalidationRequired,true);
  assert.equal(financial,before);assert.equal(JSON.stringify([...context.selected.values()].map(item=>item.choice)),saved);
  assert.match(find('[data-batch-status]').textContent,/核验导入/);
  assert.match(find('[data-batch-selection]').textContent,/已保存.*重新核验/);
  await find('[data-batch-plan]').onclick();assert.equal(context.disclosure,null);
  await find('[data-batch-save]').onclick();assert.equal(context.revalidationRequired,false);
  current={...current,preview_digest:'d'.repeat(64)};
  await find('[data-batch-refresh]').onclick();
  assert.equal(context.dirty,false);assert.equal(context.revalidationRequired,true);
  assert.equal(context.disclosure,null);assert.equal(financial,before);
  console.log('PASS 811/9/24 complete-risk defaults, mandatory preflight, full risk paging, acknowledged skip, saved/blocked/stale/unknown separation, no replay');
})().catch(error=>{console.error(error);process.exitCode=1;});

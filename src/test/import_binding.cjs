'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  const decisions = await import(pathToFileURL(path.join(__dirname,'../frontend/js/util/import-decision.js')).href);
  const {validateImportBinding,projectImportBinding,readImportBinding} = await import(pathToFileURL(
    path.join(__dirname,'../frontend/js/component/import-binding.js')).href);
  const time='2026-10-03T00:00:00.123456Z', digest='a'.repeat(64);
  const selected=new Map([
    ['1:1',{row:{file_id:1,source_row_number:1,classification:'NEW',persisted_row_status:0},refLabel:'old',
      targetLabel:'Named keeper',choice:{decision:'ACCEPT',recheck:false,resolution:'DUPLICATE',
        target:{kind:'ROW',file_id:2,source_row_number:3},account_ref_id:7,acknowledge_new_risk:false}}],
    ['1:2',{row:{file_id:1,source_row_number:2,classification:'NEW',persisted_row_status:2},
      choice:{decision:'SKIP',recheck:false,resolution:'AUTO',account_ref_id:null}}],
    ['1:3',{row:{file_id:1,source_row_number:3,classification:'NEW',persisted_row_status:0},
      choice:{decision:'ACCEPT',resolution:'NEW',acknowledge_new_risk:true,recheck:true,account_ref_id:null}}],
  ]);
  const result={source_preview_digest:digest,expected_updated_time:time,account_ref_id:9,selected_count:3,
    items:[...selected.values()].map((item,i)=>({row:{file_id:1,source_row_number:i+1},
      source_state:i===2?'UNKNOWN':'RELIABLE',applicable:i!==1,reason_codes:i===1?['ROW_RECHECK_REQUIRED']:[]}))};
  const before=JSON.stringify([...selected]);
  assert.equal(validateImportBinding(result,selected,9,digest,time),result);
  const projected=projectImportBinding(selected,result,9,'Named card',digest,time);
  assert.equal(projected.get('1:2'),selected.get('1:2')); // Exceptions remain selected, not finance-excluded.
  for (const key of ['1:1','1:3']) {
    assert.deepEqual(projected.get(key).choice,{...selected.get(key).choice,account_ref_id:9});
    assert.equal(projected.get(key).refLabel,'Named card');
  }
  assert.notEqual(projected.get('1:1').choice.target,selected.get('1:1').choice.target);
  assert.equal(projected.get('1:1').targetLabel,'Named keeper');
  assert.equal(JSON.stringify([...selected]),before);
  for (const ref of [null,0]) {
    const updates=projectImportBinding(selected,{...result,account_ref_id:ref},ref,'',digest,time);
    assert.equal(updates.get('1:1').choice.account_ref_id,ref);
    assert.equal(updates.get('1:1').refLabel,null);
  }
  for (const ref of [true,'9',1.5,-1,undefined,Number.MAX_SAFE_INTEGER+1])
    assert.throws(()=>validateImportBinding({...result,account_ref_id:ref},selected,ref,digest,time));
  for (const change of [
    {source_preview_digest:'b'.repeat(64)}, {expected_updated_time:'2026-10-03T00:00:00.123457Z'},
    {account_ref_id:8}, {selected_count:2}, {items:result.items.slice(0,2)},
    {items:[result.items[0],result.items[0],result.items[2]]},
    {items:result.items.map((item,i)=>i===2?{...item,row:{file_id:99,source_row_number:1}}:item)},
    {items:result.items.map((item,i)=>i===2?{...item,applicable:true,reason_codes:['ACCOUNT_BINDING_CONFLICT']}:item)},
    {items:result.items.map((item,i)=>i===2?{...item,source_state:'MASKED'}:item)},
    {items:result.items.map((item,i)=>i===2?{...item,row:{file_id:'1',source_row_number:3}}:item)},
  ]) assert.throws(()=>projectImportBinding(selected,{...result,...change},9,'Named',digest,time));
  assert.throws(()=>validateImportBinding(result,selected,9,digest));
  for (const change of [{classification:'PROCESSED'},{classification:'EXISTING'},{persisted_row_status:1},
    {existing_transaction_id:27}]) {
    const guarded=new Map(selected);
    guarded.set('1:1',{...selected.get('1:1'),row:{...selected.get('1:1').row,...change}});
    assert.throws(()=>projectImportBinding(guarded,result,9,'Named',digest,time),/不能覆盖来源/);
  }
  const linking=new Map(selected);
  linking.set('1:1',{...selected.get('1:1'),choice:{...selected.get('1:1').choice,resolution:'LINK_EXISTING'}});
  assert.throws(()=>projectImportBinding(linking,result,9,'Named',digest,time),/不能覆盖来源/);
  const large=new Map(Array.from({length:20000},(_,i)=>[`1:${i+1}`,{
    row:{file_id:1,source_row_number:i+1,classification:'NEW'},choice:{decision:'ACCEPT',resolution:'AUTO'}}]));
  const largeResult={...result,selected_count:20000,items:[...large.values()].map(item=>({row:item.row,
    applicable:true,source_state:'RELIABLE',reason_codes:[]}))};
  assert.equal(projectImportBinding(large,largeResult,9,'Named',digest,time).size,20000);
  large.set('1:20001',{row:{file_id:1,source_row_number:20001},choice:{decision:'ACCEPT'}});
  assert.throws(()=>validateImportBinding(largeResult,large,9,digest,time));

  let timeout,cleared=false,readSignal;
  const timers={setTimeout(fn,ms){assert.equal(ms,30000);timeout=fn;return 1;},
    clearTimeout(id){assert.equal(id,1);cleared=true;}};
  const good=await readImportBinding(async signal=>{readSignal=signal;return result;},{timers});
  assert.equal(good,result);assert.ok(cleared && readSignal.aborted);
  let release;
  const hung=readImportBinding(signal=>{readSignal=signal;return new Promise(resolve=>release=resolve);},{timers});
  const timedOut=assert.rejects(hung,/超过30秒/);
  await Promise.resolve();timeout();await timedOut;
  assert.ok(readSignal.aborted);release(result); // Late whole response cannot become an applied projection.
  const controller=new AbortController();
  const stopped=readImportBinding(()=>new Promise(()=>{}),{signal:controller.signal,timers});
  const cancelled=assert.rejects(stopped,{name:'AbortError'});controller.abort();await cancelled;
  let valid=true;
  await assert.rejects(readImportBinding(async()=>{valid=false;return result;},{valid:()=>valid,timers}),{name:'AbortError'});

  // Execute the real toolbar handler. Dialog snapshots clone every item;
  // counting reference inequality would falsely count unchanged exceptions.
  const source=fs.readFileSync(path.join(__dirname,'../frontend/js/view/import-batch.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  const nodes=new Map(),host={isConnected:true,innerHTML:'',querySelector(key){
    if(!nodes.has(key))nodes.set(key,{value:'',textContent:'',innerHTML:'',disabled:false});return nodes.get(key);
  },querySelectorAll(key){return key==='button,input,select'?[...nodes.values()]:[];}};
  const plan={token:'binding-node',status:'READY',files:[],counts:{new:0,existing:0,processed:0},
    preview_digest:digest,updated_time:time,issue_count:0};
  let options,writes=0;
  const sandbox=vm.createContext({AbortController,URLSearchParams,esc:String,...decisions,
    localStorage:{getItem:()=>null},openImportBinding:input=>{options=input;},
    request:async()=>({items:[],total:0,page_size:20}),jsonRequest:async()=>{writes++;}});
  vm.runInContext(source+'\nglobalThis.mount=mountImportBatch;globalThis.contexts=contexts;',sandbox);
  await sandbox.mount(host,plan,()=>{});
  const context=sandbox.contexts.get(plan.token);
  context.selected=selected;
  host.querySelector('[data-batch-bind]').onclick();
  assert.ok(options.valid());
  const cloned=projectImportBinding(new Map([...selected].map(([key,item])=>[key,{...item,choice:{...item.choice}}])),
    result,9,'Named',digest,time);
  options.apply(cloned,{modified:2,exceptions:1});
  assert.match(host.querySelector('[data-batch-status]').textContent,/已应用 2 行来源草稿；1 行例外/);
  assert.equal(context.selected.size,3);assert.ok(context.dirty);assert.equal(writes,0);
  console.log('PASS complete exact-time binding scope, strict source policy, immutable intent/risk/targets, selected exceptions, 20k projection, deadline/cancel/late/stale guards');
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const fs = require('node:fs');
const vm = require('node:vm');

(async()=>{
  const {validateImportPairing,projectImportPairing,sameImportPairingScope} = await import(pathToFileURL(
    path.join(__dirname,'../frontend/js/component/import-pairing.js')).href);
  const {readImportBinding} = await import(pathToFileURL(path.join(__dirname,'../frontend/js/component/import-binding.js')).href);
  const time='2026-10-03T00:00:00.123456Z',digest='a'.repeat(64);
  const core={amount:100,currency_code:'CNY',cash_direction:'OUT',occurred_time:'2024-03-14T16:00:00Z',summary:'Mock original'};
  const entry=(file,row,choice={})=>({row:{file_id:file,source_row_number:row,classification:'NEW',persisted_row_status:0,
    parsed:{...core},issue_codes:[],duplicate_hint:{state:'SUSPECTED'}},refLabel:'Named card',
    choice:{file_id:file,source_row_number:row,decision:'ACCEPT',resolution:'AUTO',recheck:false,account_ref_id:9,acknowledge_new_risk:false,...choice}});
  const selected=new Map([['1:1',entry(1,1)],['1:2',entry(1,2)],['2:1',entry(2,1,{resolution:'NEW',acknowledge_new_risk:true})]]);
  const suggested=(file,row,target,kind='SAME_SOURCE')=>({row:{file_id:file,source_row_number:row},state:'SUGGESTED',candidate_count:1,
    source_label_masked:'ccb ****1234',reason_codes:[],suggestion:{target,resolution:kind==='SAME_SOURCE'?'LINK_EXISTING':'DUPLICATE',
      ...core,occurred_time:'2024-03-14T16:00:00.000000+00:00',summary_masked:'Named keeper',source_label_masked:'ccb ****5678',
      current_review_summaries:[{title:'Current real cash'}]}});
  const result={source_preview_digest:digest,expected_updated_time:time,kind:'SAME_SOURCE',selected_count:3,items:[
    suggested(1,1,{kind:'FACT',transaction_id:41}),suggested(1,2,{kind:'ROW',file_id:2,source_row_number:1}),
    {row:{file_id:2,source_row_number:1},state:'EXCEPTION',candidate_count:null,source_label_masked:'ccb ****5678',suggestion:null,reason_codes:['USER_INTENT_RETAINED']}]};
  const before=JSON.stringify([...selected]);
  assert.equal(validateImportPairing(result,selected,'SAME_SOURCE',digest,time),result);
  const unchanged=projectImportPairing(selected,result,new Set(),'SAME_SOURCE',digest,time);
  assert.equal(unchanged.modified,0); // Unique suggestions are never consent.
  assert.deepEqual([...unchanged.selected],[...selected]);
  const projected=projectImportPairing(selected,result,new Set(['1:1','1:2']),'SAME_SOURCE',digest,time);
  assert.equal(projected.modified,2);assert.equal(projected.unchanged,1);
  assert.deepEqual(projected.selected.get('1:1').choice.target,{kind:'FACT',transaction_id:41});
  assert.deepEqual(projected.selected.get('1:2').choice.target,{kind:'ROW',file_id:2,source_row_number:1});
  assert.equal(projected.selected.get('1:1').choice.account_ref_id,undefined);
  assert.equal(projected.selected.get('1:1').refLabel,null);
  assert.equal(projected.selected.get('1:1').choice.acknowledge_new_risk,false);
  assert.equal(projected.selected.get('2:1'),selected.get('2:1'));
  assert.equal(JSON.stringify([...selected]),before);
  const cross={...result,kind:'CROSS_SOURCE',items:result.items.map(item=>item.suggestion ? {...item,
    suggestion:{...item.suggestion,resolution:'DUPLICATE'}}:item)};
  assert.equal(projectImportPairing(selected,cross,new Set(['1:1']),'CROSS_SOURCE',digest,time).selected.get('1:1').choice.account_ref_id,9);
  assert.throws(()=>projectImportPairing(selected,result,new Set(['2:1']),'SAME_SOURCE',digest,time));
  for (const changed of [
    {source_preview_digest:'b'.repeat(64)}, {expected_updated_time:'2026-10-03T00:00:00.123457Z'}, {kind:'CROSS_SOURCE'},
    {selected_count:2}, {items:result.items.slice(0,2)}, {items:[result.items[0],result.items[0],result.items[2]]},
    {items:result.items.map((item,i)=>i ? item:{...item,row:{file_id:'1',source_row_number:1}})},
    {items:result.items.map((item,i)=>i ? item:{...item,candidate_count:true})},
    {items:result.items.map((item,i)=>i ? item:{...item,reason_codes:['SOURCE_IDENTITY_REQUIRED']})},
    {items:result.items.map((item,i)=>i ? item:{...item,suggestion:{...item.suggestion,amount:101}})},
    {items:result.items.map((item,i)=>i ? item:{...item,suggestion:{...item.suggestion,occurred_time:'2024-03-14T16:00:00.000001Z'}})},
    {items:result.items.map((item,i)=>i ? item:{...item,suggestion:{...item.suggestion,target:{kind:'FACT',transaction_id:'41'}}})},
    {items:result.items.map((item,i)=>i ? item:{...item,suggestion:{...item.suggestion,target:{kind:'ROW',file_id:1,source_row_number:1}}})},
    {items:result.items.map((item,i)=>i ? item:{...item,suggestion:{...item.suggestion,current_review_summaries:[{title:null}]}})},
    {items:result.items.map((item,i)=>i===2 ? {...item,state:'AMBIGUOUS',candidate_count:1}:item)},
  ]) assert.throws(()=>projectImportPairing(selected,{...result,...changed},new Set(['1:1']),'SAME_SOURCE',digest,time));
  for (const change of [{classification:'EXISTING'},{classification:'PROCESSED'},{persisted_row_status:1},{existing_transaction_id:41}]) {
    const altered=new Map(selected);altered.set('1:1',{...selected.get('1:1'),row:{...selected.get('1:1').row,...change}});
    assert.throws(()=>validateImportPairing(result,altered,'SAME_SOURCE',digest,time));
  }
  for(const change of [{decision:'SKIP'},{resolution:'DUPLICATE',target:{kind:'FACT',transaction_id:99}}]) {
    const altered=new Map(selected);altered.set('2:1',{...selected.get('2:1'),choice:{...selected.get('2:1').choice,...change}});
    assert.throws(()=>projectImportPairing(altered,result,new Set(['1:2']),'SAME_SOURCE',digest,time));
  }
  const chain={...result,items:result.items.map((item,i)=>i===0 ? {...item,suggestion:{...item.suggestion,target:{kind:'ROW',file_id:1,source_row_number:2}}}:item)};
  assert.throws(()=>projectImportPairing(selected,chain,new Set(['1:1','1:2']),'SAME_SOURCE',digest,time),/锚点/);
  const manual=new Map([['1:1',{...selected.get('1:1'),choice:{...selected.get('1:1').choice,resolution:'NEW',acknowledge_new_risk:true}}]]);
  const manualRead={...result,items:result.items.map((item,i)=>i===0 ? {...result.items[2],row:{file_id:1,source_row_number:1}}:item)};
  const manually=projectImportPairing(selected,manualRead,new Set(['1:2']),'SAME_SOURCE',digest,time,manual);
  assert.equal(manually.modified,2);assert.equal(manually.selected.get('1:1').choice.acknowledge_new_risk,true);
  manual.get('1:1').choice.acknowledge_new_risk=false;
  assert.throws(()=>projectImportPairing(selected,manualRead,new Set(),'SAME_SOURCE',digest,time,manual),/风险确认/);
  const frozen=new Map([...selected].map(([key,item])=>[key,structuredClone(item)]));
  assert.ok(sameImportPairingScope(frozen,selected));
  selected.get('2:1').choice.account_ref_id=10;
  assert.equal(sameImportPairingScope(frozen,selected),false);selected.get('2:1').choice.account_ref_id=9;
  selected.get('2:1').row.parsed.amount=101;
  assert.equal(sameImportPairingScope(frozen,selected),false);selected.get('2:1').row.parsed.amount=100;
  const large=new Map(Array.from({length:20000},(_,i)=>[`1:${i+1}`,entry(1,i+1)]));
  const largeResult={...result,selected_count:20000,items:[...large.values()].map(item=>suggested(1,item.row.source_row_number,{kind:'FACT',transaction_id:item.row.source_row_number+20000}))};
  assert.equal(projectImportPairing(large,largeResult,new Set(large.keys()),'SAME_SOURCE',digest,time).modified,20000);
  large.set('1:20001',entry(1,20001));assert.throws(()=>validateImportPairing(largeResult,large,'SAME_SOURCE',digest,time));

  let expire,readSignal,release;
  const timers={setTimeout(fn,ms){assert.equal(ms,30000);expire=fn;return 1;},clearTimeout(){}};
  const hung=readImportBinding(signal=>{readSignal=signal;return new Promise(resolve=>release=resolve);},{label:'完整批配对核验',timers});
  const timeout=assert.rejects(hung,/完整批配对核验超过30秒/);await Promise.resolve();expire();await timeout;
  assert.ok(readSignal.aborted);release(result);

  // Execute the real toolbar integration and frozen validity, not a code snapshot.
  const source=fs.readFileSync(path.join(__dirname,'../frontend/js/view/import-batch.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  const nodes=new Map(),host={isConnected:true,innerHTML:'',querySelector(key){
    if(!nodes.has(key)) nodes.set(key,{value:'',textContent:'',innerHTML:'',disabled:false});return nodes.get(key);
  },querySelectorAll(key){return key==='button,input,select'?[...nodes.values()]:[];}};
  const plan={token:'pairing-node',status:'READY',files:[],counts:{new:0,existing:0,processed:0},preview_digest:digest,updated_time:time,issue_count:0};
  let options,writes=0;
  const sandbox=vm.createContext({AbortController,URLSearchParams,esc:String,localStorage:{getItem:()=>null},
    openImportPairing:input=>{options=input;},request:async()=>({items:[],total:0,page_size:20}),jsonRequest:async()=>{writes++;}});
  vm.runInContext(source+'\nglobalThis.mount=mountImportBatch;globalThis.contexts=contexts;',sandbox);
  await sandbox.mount(host,plan,()=>{});
  const context=sandbox.contexts.get(plan.token);context.selected=selected;
  host.querySelector('[data-batch-pair]').onclick();assert.ok(options.valid());
  context.plan.updated_time='2026-10-03T00:00:00.123457Z';assert.equal(options.valid(),false);context.plan.updated_time=time;
  options.apply(projected.selected,{modified:2,unchanged:1});
  assert.match(host.querySelector('[data-batch-status]').textContent,/已应用 2 行具名配对／人工草稿；其余 1 行保留/);
  assert.ok(context.dirty);assert.equal(context.selected.size,3);assert.equal(writes,0);assert.equal(options.valid(),false);
  console.log('PASS complete named pairing projection, no implicit consent, exact microseconds and IDs, exceptions retained, manual risk, real anchors and chains, 20k scope, frozen mutation and readonly deadline, real toolbar');
})().catch(error=>{console.error(error);process.exitCode=1;});

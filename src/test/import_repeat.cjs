'use strict';
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const fs=require('node:fs');
const vm=require('node:vm');

(async()=>{
  const {validateImportRepeat,projectImportRepeat,repeatIssueLabel}=await import(pathToFileURL(
    path.join(__dirname,'../frontend/js/component/import-repeat.js')).href);
  const time='2026-10-05T00:00:00.123456Z',digest='a'.repeat(64);
  const core={amount:100,currency_code:'CNY',cash_direction:'OUT',occurred_time:'2024-03-14T16:00:00.000001Z',summary:'Mock'};
  const locator=file=>({file_id:file,source_row_number:1});
  const entry=file=>({row:{...locator(file),classification:'NEW',parsed:{...core},issue_codes:[],persisted_row_status:0},
    refLabel:'Named card',choice:{...locator(file),decision:'SKIP',resolution:'AUTO',recheck:false,account_ref_id:9,acknowledge_new_risk:false}});
  const selected=new Map([1,2,3,4].map(file=>[`${file}:1`,entry(file)]));
  const result={source_preview_digest:digest,expected_updated_time:time,selected_count:4,
    groups:[{kind:'SUSPECTED_EXPORT',keeper_row:locator(1),repeated_rows:[locator(2),locator(3)],parsed:{...core},source_label_masked:'ccb ****1234'}],
    items:[1,2,3].map(file=>({row:locator(file),state:'GROUPED',reason_codes:[],keeper_row:locator(1)})).concat([
      {row:locator(4),state:'EXCEPTION',reason_codes:['SOURCE_IDENTITY_REQUIRED'],keeper_row:null}])};
  const before=JSON.stringify([...selected]);
  for(const code of ['ROW_INVALID','NON_POSTED_EVIDENCE','NEUTRAL_EVIDENCE']) {
    assert.match(repeatIssueLabel(code),/不能/);assert.ok(repeatIssueLabel(code).includes(code));
    assert.doesNotMatch(repeatIssueLabel(code),/来源身份不足/);
  }
  assert.equal(validateImportRepeat(result,selected,digest,time),result);
  const noConsent=projectImportRepeat(selected,result,new Set(),false,digest,time);
  assert.equal(noConsent.modified,0);assert.deepEqual([...noConsent.selected],[...selected]);
  assert.throws(()=>projectImportRepeat(selected,result,new Set(['1:1']),false,digest,time),/确认/);
  assert.throws(()=>projectImportRepeat(selected,result,new Set(['4:1']),true,digest,time));
  const applied=projectImportRepeat(selected,result,new Set(['1:1']),true,digest,time);
  assert.equal(applied.modified,3);assert.equal(applied.groups,1);assert.equal(applied.unchanged,1);
  assert.equal(applied.selected.get('1:1').choice.decision,'ACCEPT');
  assert.equal(applied.selected.get('1:1').choice.resolution,'NEW');
  assert.equal(applied.selected.get('1:1').choice.acknowledge_new_risk,true);
  assert.equal(applied.selected.get('1:1').choice.account_ref_id,9);
  for (const key of ['2:1','3:1']) {
    assert.equal(applied.selected.get(key).choice.decision,'SKIP');
    assert.equal(applied.selected.get(key).choice.resolution,'AUTO');
    assert.equal(applied.selected.get(key).choice.target,null);
    assert.equal(applied.selected.get(key).choice.acknowledge_new_risk,false);
    assert.equal(applied.selected.get(key).row,selected.get(key).row);
  }
  assert.equal(applied.selected.get('4:1'),selected.get('4:1'));
  assert.equal(JSON.stringify([...selected]),before);
  for (const change of [
    {source_preview_digest:'b'.repeat(64)},{expected_updated_time:'2026-10-05T00:00:00.123457Z'},
    {selected_count:3},{items:result.items.slice(0,3)},{items:[result.items[0],result.items[0],...result.items.slice(2)]},
    {groups:[{...result.groups[0],keeper_row:locator(2),repeated_rows:[locator(1),locator(3)]}]},
    {groups:[{...result.groups[0],repeated_rows:[locator(2),locator(2)]}]},
    {groups:[{...result.groups[0],repeated_rows:[locator(2)]}]},
    {groups:[{...result.groups[0],kind:'CERTAIN'}]},
    {groups:[{...result.groups[0],parsed:{...core,occurred_time:'2024-03-14T16:00:00.000002Z'}}]},
    {groups:[result.groups[0],result.groups[0]]},
    {items:result.items.map((item,i)=>i===1 ? {...item,row:{file_id:'2',source_row_number:1}}:item)},
    {items:result.items.map((item,i)=>i===1 ? {...item,reason_codes:['IDENTITY_AMBIGUOUS']}:item)},
    {items:result.items.map((item,i)=>i===3 ? {...item,state:'GROUPED',reason_codes:[],keeper_row:locator(1)}:item)},
  ]) assert.throws(()=>projectImportRepeat(selected,{...result,...change},new Set(['1:1']),true,digest,time));
  for(const change of [{classification:'EXISTING'},{classification:'PROCESSED'},{persisted_row_status:1},
    {persisted_row_status:2},{existing_transaction_id:5},{issue_codes:['ROW_INVALID']},
    {parsed:{...core,cash_direction:'IN'}},{parsed:{...core,currency_code:'CNY_4'}}]) {
    const altered=new Map(selected);altered.set('2:1',{...selected.get('2:1'),row:{...selected.get('2:1').row,...change}});
    assert.throws(()=>validateImportRepeat(result,altered,digest,time));
  }
  const explicit=new Map(selected);explicit.set('1:1',{...selected.get('1:1'),choice:{...selected.get('1:1').choice,resolution:'NEW',acknowledge_new_risk:true}});
  assert.throws(()=>validateImportRepeat(result,explicit,digest,time));
  const rechecked=new Map(selected);rechecked.set('2:1',{...selected.get('2:1'),row:{...selected.get('2:1').row,persisted_row_status:2},choice:{...selected.get('2:1').choice,recheck:true}});
  assert.equal(validateImportRepeat(result,rechecked,digest,time),result);
  const large=new Map(),groups=[],items=[];
  for(let n=1;n<=10000;n++) {
    const first={file_id:1,source_row_number:n},second={file_id:2,source_row_number:n};
    for(const row of [first,second]) {
      large.set(`${row.file_id}:${n}`,{...entry(row.file_id),row:{...entry(row.file_id).row,...row},choice:{...entry(row.file_id).choice,...row}});
      items.push({row,state:'GROUPED',reason_codes:[],keeper_row:first});
    }
    groups.push({...result.groups[0],keeper_row:first,repeated_rows:[second]});
  }
  const full={...result,selected_count:20000,groups,items};
  assert.equal(projectImportRepeat(large,full,new Set(groups.map(group=>`${group.keeper_row.file_id}:${group.keeper_row.source_row_number}`)),true,digest,time).modified,20000);
  large.set('3:1',entry(3));assert.throws(()=>validateImportRepeat(full,large,digest,time));
  const decisions=await import(pathToFileURL(path.join(__dirname,'../frontend/js/util/import-decision.js')).href);
  const source=fs.readFileSync(path.join(__dirname,'../frontend/js/view/import-batch.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  const nodes=new Map(),host={isConnected:true,innerHTML:'',querySelector(key){
    if(!nodes.has(key)) nodes.set(key,{value:'',textContent:'',innerHTML:'',disabled:false});return nodes.get(key);
  },querySelectorAll(key){return key==='button,input,select'?[...nodes.values()]:[];}};
  const plan={token:'repeat-node',status:'READY',files:[],counts:{new:0,existing:0,processed:0},preview_digest:digest,updated_time:time,issue_count:0};
  let options,writes=0,reads=0,fail=false,release;
  const sandbox=vm.createContext({AbortController,URLSearchParams,esc:String,...decisions,localStorage:{getItem:()=>null},
    openImportRepeat:input=>{options=input;},completeImportScope:async()=>{reads++;if(fail) throw Error('Mock read failed');
      if(release) await new Promise(resolve=>release=resolve);return [...selected.values()].map(item=>item.row);},
    request:async()=>({items:[],total:0,page_size:20}),jsonRequest:async()=>{writes++;}});
  vm.runInContext(source+'\nglobalThis.mount=mountImportBatch;globalThis.contexts=contexts;',sandbox);
  await sandbox.mount(host,plan,()=>{});
  assert.match(host.innerHTML,/data-batch-repeat/);
  const context=sandbox.contexts.get(plan.token),button=host.querySelector('[data-batch-repeat]');
  context.selected=selected;
  for(const flag of ['busy','unknown','restoreRequired','guiding','executor']) {
    context[flag]=true;await button.onclick();assert.equal(options,undefined,flag);context[flag]=false;
  }
  context.plan.status='CONFIRMING';await button.onclick();assert.equal(options,undefined);context.plan.status='READY';
  fail=true;await button.onclick();assert.equal(options,undefined);assert.equal(context.selected,selected);fail=false;
  release=true;const reading=button.onclick();const issuedReads=reads;await button.onclick();assert.equal(reads,issuedReads);release();release=null;await reading;
  assert.ok(options.valid());assert.equal(options.selected.size,4);
  context.plan.updated_time='2026-10-05T00:00:00.123457Z';assert.equal(options.valid(),false);
  assert.throws(()=>options.apply(applied.selected,applied),/变化/);assert.equal(writes,0);context.plan.updated_time=time;
  options.apply(applied.selected,applied);assert.equal(context.selected,applied.selected);assert.ok(context.dirty);
  assert.match(host.querySelector('[data-batch-status]').textContent,/下一步核验导入/);assert.equal(writes,0);
  console.log('PASS explicit complete repeat groups, first NEW and later SKIP, retained exceptions and intent, exact microseconds, no mutation, 20k bounded projection');
})().catch(error=>{console.error(error);process.exitCode=1;});

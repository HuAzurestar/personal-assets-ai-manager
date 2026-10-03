'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {pathToFileURL}=require('node:url');
(async()=>{
  const module=await import(pathToFileURL(path.join(__dirname,'../frontend/js/component/import-reconciliation.js')).href);
  const {readImportBinding}=await import(pathToFileURL(path.join(__dirname,'../frontend/js/component/import-binding.js')).href);
  const {resourceId}=await import(pathToFileURL(path.join(__dirname,'../frontend/js/util/core.js')).href);
  const time='2026-10-03T01:02:03.123456Z';
  const row={file_id:1,source_row_number:2};
  const intent={...row,resolution:'DUPLICATE',decision:'ACCEPT',target:{kind:'FACT',transaction_id:11}};
  const retained={token:'mock-token',rows:[row],files:[{file_id:1,sha256:'a'.repeat(64)}],intents:[intent]};
  const input=module.importReconciliationInput(retained);
  const fact=id=>({id,amount:123,currency_code:'CNY',cash_direction:'OUT',occurred_time:time});
  const output=(id,transaction_id,type,status,ref)=>({transaction_id,allocation_id:id,ledger_id:id,review_id:id,
    review_status:status,economic_type:type,account_ref_id:ref,cash_amount:123,cash_currency_code:'CNY',cash_direction:'OUT',occurred_time:time});
  const result={observed_at:time,current_state_only:true,fully_observed:true,
    items:[{row,resolution:'DUPLICATE',row_id:1,row_status:1,transaction_id:22,target_transaction_id:11,state:'DUPLICATE_EXCLUDED',
      fully_observed:true,reason_codes:['KEEPER_LOCATED_FROM_CLIENT_CONTEXT']}],facts:[fact(11),fact(22)],
    outputs:[output(1,11,'TRANSACTION','CONFIRMED',1),output(2,22,'TRANSACTION','REVOKED',2),output(3,22,'DUPLICATE','CONFIRMED',2)]};
  assert.equal(module.validateImportReconciliation(result,input),result);
  assert.match(module.reconciliationRowLabel(result.items[0]),/不是数据库首次配对回执/);
  const clone=value=>JSON.parse(JSON.stringify(value));
  for (const modify of [value=>value.rows.push(row),value=>value.intents[0].file_id=2,value=>value.files=[],
    value=>value.rows[0].file_id=true,value=>value.files[0].sha256='B'.repeat(64),value=>value.intents[0].resolution='AUTO',
    value=>value.intents[0].target={kind:'ROW',file_id:2,source_row_number:1}]) {
    const value=clone(retained);modify(value);assert.throws(()=>module.importReconciliationInput(value));
  }
  for (const modify of [value=>value.items=[],value=>value.items.push(value.items[0]),value=>value.facts.pop(),
    value=>value.items[0].resolution='AUTO',value=>value.items[0].target_transaction_id=99,
    value=>value.items[0].fully_observed=false,value=>value.outputs.pop(),value=>value.outputs[0].review_status='REVOKED',
    value=>value.outputs[2].account_ref_id=1,value=>value.outputs[2].cash_amount=122,
    value=>value.outputs[2].economic_type='TRANSACTION',value=>value.facts[1].occurred_time='2026-10-03T01:02:03.123457Z',
    value=>value.outputs[2].transaction_id=99,value=>value.items[0].state='UNRESOLVED',
    value=>value.outputs.push(value.outputs[0]),value=>value.outputs[2].cash_direction='IN',
    value=>value.items[0].row_status=0,value=>value.items[0].row_id=0]) {
    const value=clone(result);modify(value);assert.throws(()=>module.validateImportReconciliation(value,input));
  }
  const unresolved=clone(result);unresolved.fully_observed=false;unresolved.items[0].fully_observed=false;
  unresolved.items[0].state='UNRESOLVED';unresolved.items[0].reason_codes=['PAIR_CONTEXT_REQUIRED'];
  assert.equal(module.validateImportReconciliation(unresolved,input),unresolved);
  const changed=clone(result);changed.items[0].state='CURRENT_STATE_CHANGED';changed.outputs[2].economic_type='TRANSACTION';
  assert.equal(module.validateImportReconciliation(changed,input),changed);
  const lostTarget=clone(input);lostTarget.rows[0].target=null;
  assert.throws(()=>module.validateImportReconciliation(changed,lostTarget));
  const ordinary=clone(result);ordinary.items[0].state='ACCEPTED';
  assert.throws(()=>module.validateImportReconciliation(ordinary,input));
  const rowContext=clone(retained);rowContext.intents[0].target={kind:'ROW',file_id:1,source_row_number:1};
  assert.equal(module.validateImportReconciliation(result,module.importReconciliationInput(rowContext)),result);

  // Execute actual mount, readonly POST, two lease checks, finally/update,
  // failed recheck and explicit human acknowledgement; never replay confirm.
  const source=fs.readFileSync(path.join(__dirname,'../frontend/js/view/import-batch.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  for (const mode of ['CONFIRMING_BEFORE','CONFIRMING_AFTER','EXPIRED','UNRESOLVED','COMPLETE','SERIAL_RELOAD']) {
    const nodes=new Map();
    const element=()=>({disabled:false,value:'',textContent:'',innerHTML:'',isConnected:true});
    const panel=element();panel.querySelector=selector=>{if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);};
    Object.defineProperty(panel,'innerHTML',{set(value){nodes.delete('[data-batch-observed]');
      if(value.includes('data-batch-observed'))nodes.set('[data-batch-observed]',element());}});
    nodes.set('[data-batch-verification]',panel);
    const host={isConnected:true,innerHTML:'',querySelector(selector){
      if(selector==='[data-batch-observed]')return nodes.get(selector)||null;
      if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);
    },querySelectorAll(selector){return selector === 'button,input,select' ? [...nodes.values()] : [];}};
    const readonlyPager=element();readonlyPager.closest=selector=>selector==='[data-batch-verification]'?panel:null;
    nodes.set('[data-reconcile-pager]',readonlyPager);
    const serial=mode==='SERIAL_RELOAD',remain={file_id:1,source_row_number:3,decision:'ACCEPT',resolution:'NEW',acknowledge_new_risk:true};
    let stored=JSON.stringify({...retained,...(serial ? {serial:true} : {})}),remaining=serial ? JSON.stringify({token:retained.token,files:retained.files,choices:[intent,remain]}) : null;
    let leases=0,reads=0,writes=0,fail=false;
    const plan={token:serial ? 'new-token' : 'mock-token',status:'PENDING',files:serial ? [{...retained.files[0],filename:'Mock.csv',parse_status:'READY',parsed_row_count:2,
      activity_range:{start:null,end:null},accepted:1,skipped:0,invalid:0,remaining:1}] : [],counts:{new:0,existing:0,processed:0},preview_digest:'a'.repeat(64)};
    const sandbox=vm.createContext({AbortController,URLSearchParams,TextEncoder,resourceId,...module,readImportBinding,
      mountImportDraftRows:(node,rows)=>node.textContent=String(rows.length),esc:String,
      localStorage:{getItem:key=>key==='paam.import.remaining.v1' ? remaining : stored,
        setItem:(key,value)=>{if(key==='paam.import.remaining.v1')remaining=value;else stored=value;},
        removeItem:key=>{if(key==='paam.import.remaining.v1')remaining=null;else stored=null;}},
      jsonRequest:async()=>{writes++;throw Error('unexpected financial replay');},
      request:async(url,options={})=>{
        if(fail)throw Error('Mock read failed');
        if(url.endsWith('/reconcile')){reads++;assert.equal(options.method,'POST');assert.ok(options.signal);
          assert.deepEqual(JSON.parse(options.body),input);return mode==='UNRESOLVED'?unresolved:result;}
        if(url.includes('/row/list'))return {items:[],total:0,page_size:20};
        leases++;if(mode==='EXPIRED')throw Object.assign(Error('expired'),{status:410});
        return {...plan,status:(mode==='CONFIRMING_BEFORE'&&leases%2===1)||(mode==='CONFIRMING_AFTER'&&leases%2===0)?'CONFIRMING':'PENDING'};
      }});
    vm.runInContext(source+'\nglobalThis.mount=mountImportBatch;globalThis.testContexts=contexts;',sandbox);
    await sandbox.mount(host,plan,()=>{});
    await host.querySelector('[data-batch-verify]').onclick();
    assert.equal(reads,1);assert.equal(leases,2);assert.equal(writes,0);
    assert.equal(readonlyPager.disabled,false);assert.equal(panel.inert,false);
    let button=host.querySelector('[data-batch-observed]');
    if(mode==='UNRESOLVED'){assert.equal(button,null);assert.ok(stored);continue;}
    assert.ok(button);assert.equal(button.disabled,mode.startsWith('CONFIRMING'),`${mode}: ${JSON.stringify(sandbox.testContexts.get(plan.token))} ${host.querySelector('[data-batch-status]').textContent}`);
    if(mode.startsWith('CONFIRMING')){await button.onclick();assert.ok(stored);continue;}
    fail=true;await host.querySelector('[data-batch-verify]').onclick();
    assert.match(host.querySelector('[data-batch-status]').textContent,/Mock read failed/);
    assert.equal(button.disabled,true);await button.onclick();assert.ok(stored);
    fail=false;await host.querySelector('[data-batch-verify]').onclick();
    button=host.querySelector('[data-batch-observed]');assert.equal(button.disabled,false);
    await button.onclick();assert.equal(stored,null);assert.equal(writes,0);
    if(serial){
      assert.equal(JSON.parse(remaining).choices.length,1);assert.deepEqual(JSON.parse(remaining).choices[0],remain);
      const actual=sandbox.testContexts.get(plan.token);
      assert.equal(actual.selected.size,1);assert.equal(actual.dirty,true);assert.equal(actual.restoreRequired,true);
      assert.equal(host.querySelector('[data-batch-execute]').disabled,true);
    }
  }
  console.log('PASS exact complete target-aware current observation, no guessed targets, two CONFIRMING checks, failed reverify revokes, no replay, human acknowledgement only');
})().catch(error=>{console.error(error);process.exitCode=1;});

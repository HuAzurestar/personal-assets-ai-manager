'use strict';
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');

async function run() {
  const {createImportExecution,validateImportChild,importKnownFailureMessage}=await import(pathToFileURL(path.join(__dirname,'../frontend/js/component/import-execution.js')).href);
  assert.equal(typeof importKnownFailureMessage,'function');
  for (const [code,reason] of [['STALE_PREVIEW','原计划已失效'],['WRITE_BUSY','数据库正忙']]) {
    const copy=importKnownFailureMessage(Object.assign(Error(code),{code}));
    for (const text of [reason,'本批未提交','已完成批保留','保存选择并重新核验','再次明确批准','不会自动重发',code]) assert.ok(copy.includes(text),copy);
    assert.ok(!copy.includes(`${code}：${code}`),copy);
  }
  assert.ok(importKnownFailureMessage(Object.assign(Error('Mock source conflict'),{code:'IDENTITY_CHANGED'})).includes('Mock source conflict'));
  assert.ok(importKnownFailureMessage(Error('Mock preparation failed')).includes('Mock preparation failed'));
  const policy={max_batch_rows:1000,serial:true,retain_committed:true,automatic_post_replay:false,stop_on:['CANCEL','FAILURE','STALE_PREVIEW','RESULT_UNKNOWN']};
  const rows=Array.from({length:2500},(_,i)=>({file_id:1,source_row_number:i+1}));
  const stamp=i=>`2026-10-03T00:00:00.${String(i).padStart(6,'0')}Z`;
  const hash='a'.repeat(64),op='b'.repeat(64);
  const plan={source_preview_digest:hash,operation_preview_digest:op,selected_rows:rows,selected_count:2500,can_confirm:true,
    cross_batch_atomic:false,execution_policy:policy,blocked:[],batches:[0,1000,2000].map((offset,index)=>({batch_index:index,
      preview:{selected_rows:rows.slice(offset,offset+1000),batch_preview_digest:String(index+1).repeat(64),can_confirm:true,budget:{selected_rows:Math.min(1000,2500-offset)}}}))};
  const current={token:'mock',updated_time:stamp(1),preview_digest:hash,files:[{file_id:1,sha256:hash}]};
  const response=index=>({operation_preview_digest:op,next_batch_index:index+1,complete:index===2,preview_digest:'c'.repeat(64),preview_updated_time:stamp(index+3),
    processed_rows:plan.batches[index].preview.selected_rows.map(row=>({...row,row_id:row.source_row_number,row_status:1,transaction_id:row.source_row_number,
      created_review_id:row.source_row_number,created_ledger_id:row.source_row_number,resolution_effect:'NEW_REAL',effective_review_ids:[row.source_row_number],
      effective_ledger_ids:[row.source_row_number],duplicate_kept_transaction_id:0})),
    files:[{file_id:1,sha256:hash,status:index===2 ? 1 : 2,updated_time:stamp(index+3),accepted:Math.min((index+1)*1000,2500),skipped:0,invalid:0,remaining:Math.max(0,2500-(index+1)*1000)}],
    new_fact_count:Math.min(1000,2500-index*1000),linked_existing_count:0,manual_linked_count:0,duplicate_fact_count:0,skipped_count:0,invalid_count:0,remaining_count:Math.max(0,2500-(index+1)*1000)});
  function setup(overrides={}) {
    const calls=[],prepared=[],known=[],errors=[],stops=[];
    const executor=createImportExecution({plan,current,unknown:error=>!error.status || error.code==='RESULT_UNKNOWN' || (error.status>=500 && error.code!=='WRITE_BUSY'),
      approve:async body=>{calls.push(['approve',body]);return {token:'mock',updated_time:stamp(2),preview_digest:hash,operation_preview_digest:op,batch_count:3,selected_count:2500};},
      confirm:async body=>{calls.push(['confirm',body]);return response(body.batch_index);},
      stop:async body=>stops.push(body),prepare:async scope=>prepared.push(scope),committed:async value=>known.push(value),failed:async (error,state)=>errors.push({error,state}),...overrides});
    return {executor,calls,prepared,known,errors,stops};
  }
  let s=setup(),state=await s.executor.run();
  assert.equal(state.phase,'COMPLETE');assert.equal(state.completed_rows,2500);assert.equal(state.remaining_rows,0);
  assert.deepEqual(s.calls.filter(([kind])=>kind==='confirm').map(([,body])=>body.selected_rows.length),[1000,1000,500]);
  assert.deepEqual(s.calls.slice(1).map(([,body])=>body.expected_updated_time),[stamp(2),stamp(3),stamp(4)]);
  assert.deepEqual(s.calls.slice(1).map(([,body])=>body.batch_preview_digest),['1'.repeat(64),'2'.repeat(64),'3'.repeat(64)]);
  await assert.rejects(s.executor.run(),/不能重复/);assert.equal(s.calls.length,4);assert.equal(s.stops.length,0);
  for (const mutate of [value=>value.processed_rows.pop(),value=>value.processed_rows[0]=value.processed_rows[1],value=>value.next_batch_index++,
    value=>value.complete=true,value=>value.new_fact_count++,value=>value.processed_rows[0].row_id=9007199254740992,
    value=>value.preview_updated_time=stamp(2),value=>value.processed_rows[0].transaction_id=0,value=>value.files=[],
    value=>value.processed_rows[0].effective_ledger_ids=[],value=>value.files[0].remaining=-1,value=>value.files[0].sha256='d'.repeat(64)]) {
    const result=response(0);mutate(result);assert.throws(()=>validateImportChild(result,plan.batches[0],0,3,op,{updated_time:stamp(2)},current.files));
    s=setup({confirm:async()=>result});state=await s.executor.run();
    assert.equal(state.phase,'UNKNOWN');assert.equal(state.completed_rows,0);assert.equal(s.prepared.length,1);assert.equal(s.stops.length,1);
    assert.equal(s.errors[0].state.unknown,true);
  }
  for (const error of [Object.assign(Error('busy'),{status:503,code:'WRITE_BUSY'}),Object.assign(Error('stale'),{status:409,code:'STALE_PREVIEW'}),
      Object.assign(Error('lost'),{status:503,code:'RESULT_UNKNOWN'}),Error('transport')]) {
    let count=0;
    s=setup({confirm:async body=>{if (++count===2) throw error;return response(body.batch_index);}});
    state=await s.executor.run();
    assert.equal(count,2);assert.equal(state.completed_rows,1000);assert.equal(state.remaining_rows,1500);
    assert.equal(state.phase,['busy','stale'].includes(error.message) ? 'FAILED':'UNKNOWN');assert.equal(s.stops.length,1);
  }
  // Stop or route leave while a financial request is actually outstanding.
  for (const leave of [false,true]) {
    let settle,live=true;
    s=setup({valid:()=>live,confirm:()=>new Promise(resolve=>settle=resolve)});
    const pending=s.executor.run();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(s.executor.state.in_flight,true);
    if (leave) live=false;
    await s.executor.stop();assert.equal(s.stops.length,1);assert.equal(s.executor.state.in_flight,true);
    settle(response(0));state=await pending;
    assert.equal(state.phase,'STOPPED');assert.equal(state.completed_rows,1000);assert.equal(state.remaining_rows,1500);assert.equal(s.prepared.length,1);
  }
  let settleApproval;
  s=setup({approve:()=>new Promise(resolve=>settleApproval=resolve)});
  const pending=s.executor.run();await new Promise(resolve=>setImmediate(resolve));await s.executor.stop();
  settleApproval({token:'mock',updated_time:stamp(2),preview_digest:hash,operation_preview_digest:op,batch_count:3,selected_count:2500});
  state=await pending;assert.equal(state.phase,'STOPPED');assert.equal(s.prepared.length,0);assert.equal(s.stops.length,1);
  s=setup({prepare:async()=>{throw Error('quota');}});state=await s.executor.run();
  assert.equal(state.phase,'FAILED');assert.equal(s.calls.length,1);assert.equal(s.errors[0].state.unknown,false);
  s=setup({committed:async()=>{throw Error('cleanup');}});state=await s.executor.run();
  assert.equal(state.completed_rows,1000);assert.equal(state.phase,'FAILED');assert.equal(s.prepared.length,1);assert.equal(s.errors[0].state.stage,'LOCAL_RESULT');
  s=setup({approve:async()=>({})});state=await s.executor.run();assert.equal(state.phase,'FAILED');assert.equal(s.prepared.length,0);
  let expire,late,cleared=0;
  s=setup({timers:{setTimeout(callback,delay){assert.equal(delay,35000);expire=callback;return 7;},clearTimeout(token){assert.equal(token,7);cleared++;}},
    confirm:()=>new Promise(resolve=>late=resolve)});
  const timed=s.executor.run();await new Promise(resolve=>setImmediate(resolve));expire();state=await timed;
  assert.equal(state.phase,'UNKNOWN');assert.equal(state.completed_rows,0);assert.equal(s.prepared.length,1);assert.equal(cleared,1);
  late(response(0));await new Promise(resolve=>setImmediate(resolve));assert.equal(s.known.length,0);assert.equal(s.prepared.length,1);
  const broken=structuredClone(plan);broken.batches[1].batch_index=0;assert.throws(()=>setup({plan:broken}));
  console.log('PASS SERIAL_2500=1 EXACT_SCOPE=1 MICROSECOND_GUARDS=1 NO_REPLAY=1 STOP_IN_FLIGHT=1 ROUTE_STOP=1 REMAINING_REAPPROVAL=1');
}
run().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {unitFixture} = require('./unit_fixture.cjs');

(async () => {
  const moduleURL = name => pathToFileURL(path.resolve(__dirname, `../frontend/js/${name}.js`)).href;
  const {installUnitDictionary} = await import(moduleURL('util/unit-dictionary'));
  installUnitDictionary(unitFixture());
  globalThis.document = {querySelector:()=>null};
  const {sourceCompletionFilter, sourceCompletionPage, sourceCompletionSelection, sourceCompletionIntent,
    sourceCompletionWhole, sourceCompletionPlan, boundedSourceRead, sourceCompletionOriginals} = await import(moduleURL('util/source-completion'));
  const row = id => ({id, active:true, account_ref_id:0, transaction_id:id+100, summary:`Mock ${id}`,
    cash_amount:1000,cash_currency_code:'KRW',cash_direction:'OUT',occurred_time:'2032-01-01T00:00:00Z',
    review:{id:id+200,status:'CONFIRMED',title:'Mock original'}});
  const filter = sourceCompletionFilter({direction:'OUT',currency:'KRW'});
  assert.deepEqual(filter.expression.slice(0,2), [{key:'active',op:'=',val:true},{key:'account_ref_id',op:'=',val:0}]);
  assert.ok(filter.expression.some(item => item.key==='cash_currency_code' && item.val==='KRW'));
  for (const scope of [{direction:'X'}, {currency:'PCS'}, {start:'2032-02-01T00:00:00Z',end:'2032-01-01T00:00:00Z'}, {start:'2032-01-01'}])
    assert.throws(() => sourceCompletionFilter(scope));
  const response = (items,total=items.length,page=1,size=20) => ({items,total,page_index:page,page_size:size});
  assert.deepEqual(sourceCompletionPage(response([row(1),row(2)]),1,20).items.map(item=>item.id),[1,2]);
  for (const result of [response([row(1),row(1)]), response([{...row(1),active:false}]), response([{...row(1),account_ref_id:2}]),
    response([row(1)],2),response([row(1)],1,2),response([row(1)],1,1,100),response([{...row(1),id:9007199254740992}])])
    assert.throws(() => sourceCompletionPage(result,1,20));
  const first = row(1), selected = sourceCompletionSelection(new Map(),[first]);
  first.review.title='changed outside';
  assert.equal(selected.get(1).review.title,'Mock original');
  const next = sourceCompletionSelection(selected,[row(2)]);
  assert.equal(selected.size,1);assert.equal(next.size,2);
  assert.equal(sourceCompletionSelection(next,[row(1)],true).size,1);
  assert.throws(() => sourceCompletionSelection(next,[{...row(1),transaction_id:999}]));
  assert.throws(() => sourceCompletionSelection(new Map(),Array.from({length:101},(_,i)=>row(i+1))));
  const intent = sourceCompletionIntent(next,'3',[],new Map());
  assert.deepEqual(intent,{account_corrections:[{ledger_id:1,account_ref_id:3},{ledger_id:2,account_ref_id:3}],correction_duplicates:[]});
  assert.equal(JSON.stringify(intent).includes('cash_amount'),false);
  const plan={new_reviews:[{account_changes:[{ledger_id:1,before_account_ref_id:0,after_account_ref_id:3},
    {ledger_id:2,before_account_ref_id:0,after_account_ref_id:3}]}]};
  sourceCompletionPlan(plan,next,3);
  for (const changes of [plan.new_reviews[0].account_changes.slice(0,1), [...plan.new_reviews[0].account_changes,{ledger_id:99,before_account_ref_id:0,after_account_ref_id:3}],
    plan.new_reviews[0].account_changes.map(item=>({...item,after_account_ref_id:4}))])
    assert.throws(()=>sourceCompletionPlan({new_reviews:[{account_changes:changes}]},next,3));
  for (const target of ['', '0', '1e2', '9007199254740993']) assert.throws(() => sourceCompletionIntent(next,target,[],new Map()));
  assert.throws(() => sourceCompletionIntent(new Map(),3,[],new Map()));
  assert.throws(() => sourceCompletionIntent(next,3,[7],new Map()));
  assert.deepEqual(sourceCompletionIntent(next,3,[7],new Map([[7,{transaction_id:8}]])).correction_duplicates,
    [{transaction_id:7,kept_transaction_id:8}]);
  assert.throws(() => sourceCompletionWhole(response([row(1)],101,1,100)));
  assert.equal(sourceCompletionWhole(response([row(1)],1,1,100)).length,1);
  const controller = new AbortController();controller.abort();
  let calls=0;
  await assert.rejects(boundedSourceRead(async () => {calls++;},controller.signal), /中止|abort/i);
  assert.equal(calls,0);
  await assert.rejects(boundedSourceRead(() => new Promise(resolve=>setTimeout(resolve,40)),undefined,5), /超时/);
  globalThis.fetch = async url => {calls++; const id=Number(url.split('/').pop());return {ok:true,status:200,headers:new Headers(),
    json:async()=>({status:200,body:{id,status:'CONFIRMED'}})};};
  const originals = await sourceCompletionOriginals({new_reviews:[{source_review_id:3},{source_review_id:4}]});
  assert.equal(originals.size,2);
  assert.throws(() => sourceCompletionOriginals({new_reviews:[{source_review_id:3},{source_review_id:3}]}));
  globalThis.fetch = async () => ({ok:true,status:200,headers:new Headers(),json:async()=>({status:200,body:{id:99}})});
  await assert.rejects(sourceCompletionOriginals({new_reviews:[{source_review_id:3}]}), /完整|不一致/);
  const {bindNamedChoice} = await import(moduleURL('component/workbench'));
  let locked=true,changed=0;
  const input={value:'2',dispatchEvent(){}},label={textContent:'Mock source 2'},pick={},clear={};
  const field={querySelector:selector=>({'input':input,'[data-choice-label]':label,'[data-choice-pick]':pick,'[data-choice-clear]':clear})[selector]};
  const host={isConnected:true,querySelector:()=>field};
  bindNamedChoice(host,'account_ref_id',{url:'/mock/ref',initialize:false,canChange:()=>!locked,changed:()=>changed++});
  assert.doesNotThrow(()=>pick.onclick(),'locked programmatic click cannot open a picker');
  clear.onclick();assert.equal(input.value,'2');assert.equal(changed,0);
  locked=false;clear.onclick();assert.equal(input.value,'');assert.equal(changed,1);
  locked=true;input.value='3';clear.onclick();assert.equal(input.value,'3');
  console.log('PASS explicit historical source scope, atomic selection budget, exact correction intent and bounded complete original reads');
})().catch(error => {console.error(error);process.exitCode=1;});

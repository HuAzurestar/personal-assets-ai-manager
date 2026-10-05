'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const module = name => pathToFileURL(path.join(__dirname, '../frontend/js', name)).href;
  const {projectImportBulk, importRangeFilter} = await import(module('component/import-bulk.js'));
  const row = n => ({file_id:1,source_row_number:n,classification:'NEW',persisted_row_status:0,
    parsed:{amount:100,currency_code:'CNY',cash_direction:'OUT',occurred_time:'2026-10-03T00:00:00.000001Z'},
    duplicate_hint:{state:'UNCHECKED'}});
  const paired = {row:row(1),targetLabel:'Named keeper',choice:{decision:'ACCEPT',resolution:'DUPLICATE',
    target:{kind:'FACT',transaction_id:17},recheck:false,account_ref_id:9}};
  const selection = new Map([['1:1',paired], ['1:2',{row:{...row(2),persisted_row_status:2},
    choice:{decision:'SKIP',resolution:'AUTO',recheck:false}}],
    ['1:3',{row:{...row(3),classification:'PROCESSED',persisted_row_status:1},choice:{decision:'ACCEPT'}}]]);
  const before = JSON.stringify([...selection]);
  const accepting = projectImportBulk(selection,'ACCEPT');
  assert.deepEqual([...accepting.updates.keys()],['1:1']);
  assert.deepEqual(accepting.updates.get('1:1').choice.target,{kind:'FACT',transaction_id:17});
  assert.equal(accepting.updates.get('1:1').targetLabel,'Named keeper');
  assert.equal(accepting.updates.get('1:1').choice.acknowledge_new_risk,undefined);
  assert.equal(accepting.exceptions.length,2);
  assert.equal(JSON.stringify([...selection]),before);
  const rechecking = projectImportBulk(selection,'RECHECK');
  assert.deepEqual([...rechecking.updates.keys()],['1:2']);
  assert.equal(rechecking.updates.get('1:2').choice.recheck,true);
  assert.equal(rechecking.updates.get('1:2').choice.decision,'SKIP'); // Recheck is not automatic acceptance.
  const skipping = projectImportBulk(selection,'SKIP');
  assert.equal(skipping.updates.get('1:1').choice.resolution,'AUTO');
  assert.equal(skipping.updates.get('1:1').choice.target,null);
  assert.equal(skipping.updates.get('1:1').choice.acknowledge_new_risk,false);
  assert.equal(skipping.updates.get('1:1').targetLabel,null);
  assert.throws(() => projectImportBulk(selection,'NEW',false),/新增真实现金/);
  assert.throws(() => projectImportBulk(selection,'NEW',1),/新增真实现金/);
  const fresh = projectImportBulk(selection,'NEW',true);
  assert.deepEqual([...fresh.updates.keys()],['1:1']);
  assert.equal(fresh.updates.get('1:1').choice.target,null);
  assert.equal(fresh.updates.get('1:1').choice.account_ref_id,9);
  assert.equal(fresh.updates.get('1:1').choice.acknowledge_new_risk,true);
  const incomplete = new Map([['1:4',{row:{...row(4),parsed:{...row(4).parsed,amount:null}},choice:{decision:'ACCEPT'}}]]);
  assert.equal(projectImportBulk(incomplete,'NEW',true).updates.size,0);
  for (const action of ['LINK_EXISTING','DUPLICATE','bogus']) assert.throws(() => projectImportBulk(selection,action));
  const reset = projectImportBulk(new Map([['1:1',{...paired,choice:{...paired.choice,resolution:'NEW',acknowledge_new_risk:true}}]]),'AUTO');
  assert.equal(reset.updates.get('1:1').choice.acknowledge_new_risk,false);
  assert.equal(reset.updates.get('1:1').choice.target,null);
  assert.throws(() => projectImportBulk(new Map(),'ACCEPT'));
  assert.throws(() => projectImportBulk(new Map([['wrong',paired]]),'ACCEPT'));
  const large = new Map(Array.from({length:20000},(_,i) => [`1:${i+1}`,{row:row(i+1),choice:{decision:'ACCEPT'}}]));
  assert.equal(projectImportBulk(large,'SKIP').updates.size,20000);
  large.set('1:20001',{row:row(20001),choice:{decision:'ACCEPT'}});
  assert.throws(() => projectImportBulk(large,'SKIP'));
  assert.deepEqual(importRangeFilter('1','6','107'),{key:'source_row_number',op:'between',val:{start:6,end:107}});
  for (const [file,start,end] of [['','6','7'],['1','0','7'],['1','6','6'],['1','7','6'],
    ['1','1.5','7'],['1','1e3','2000'],['1','6','9007199254740992']])
    assert.throws(() => importRangeFilter(file,start,end));
  console.log('PASS bounded bulk drafts, explicit cash consent, paired target preservation, immutable accepted rows, half-open source ranges');
})().catch(error => {console.error(error);process.exitCode=1;});

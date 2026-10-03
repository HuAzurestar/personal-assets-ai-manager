'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const module = name => pathToFileURL(path.join(__dirname, '../frontend/js', name)).href;
  const {installUnitDictionary} = await import(module('util/unit-dictionary.js'));
  installUnitDictionary(require('./unit_fixture.cjs').unitFixture());
  const {importIntentChoice, importAnchorChoices, importRowLabel} = await import(module('component/import-choice.js'));
  const row = {file_id:2,source_row_number:4,classification:'NEW',
    parsed:{occurred_time:'2026-10-03T00:00:00.123456Z',cash_direction:'OUT',amount:40000,currency_code:'CNY',summary:'Fictional purchase'},
    duplicate_hint:{state:'UNCHECKED'}};
  const previous = {decision:'ACCEPT',recheck:true,account_ref_id:9};
  assert.throws(() => importIntentChoice(row,previous,'NEW',null,false), /风险确认/);
  assert.throws(() => importIntentChoice(row,previous,'NEW',null,1), /风险确认/);
  const fresh = importIntentChoice(row,previous,'NEW',null,true);
  assert.equal(fresh.acknowledge_new_risk,true);
  assert.equal(fresh.account_ref_id,9);
  assert.equal(fresh.recheck,true);
  assert.equal(importIntentChoice({...row,duplicate_hint:{state:'NONE_IN_SCOPE'}},previous,'NEW',null,false).acknowledge_new_risk,false);
  const automatic = importIntentChoice(row,previous,'AUTO',null,true);
  assert.equal(automatic.acknowledge_new_risk,false);
  const target = {kind:'FACT',transaction_id:17};
  const link = importIntentChoice(row,previous,'LINK_EXISTING',target,false);
  assert.equal(link.target.transaction_id,17);
  assert.equal(Object.hasOwn(link,'account_ref_id'),false);
  assert.equal(previous.account_ref_id,9);
  assert.notEqual(link.target,target);
  const duplicate = importIntentChoice(row,previous,'DUPLICATE',target,true);
  assert.equal(duplicate.account_ref_id,9);
  assert.equal(duplicate.acknowledge_new_risk,false);
  for (const [mode,value] of [['AUTO',target], ['NEW',target], ['LINK_EXISTING',null], ['DUPLICATE',null],
    ['DUPLICATE',{kind:'FACT',transaction_id:true}], ['DUPLICATE',{kind:'FACT',transaction_id:0}],
    ['DUPLICATE',{kind:'FACT',transaction_id:'17'}], ['DUPLICATE',{kind:'ROW',file_id:'1',source_row_number:24}],
    ['DUPLICATE',{kind:'FACT',transaction_id:17,file_id:1}], ['DUPLICATE',{kind:'unknown'}],
    ['DUPLICATE',{kind:'ROW',file_id:2,source_row_number:4}], ['bogus',null]])
    assert.throws(() => importIntentChoice(row,previous,mode,value,false));
  const anchor = {...row,file_id:1,source_row_number:24};
  const selected = new Map([['1:24',{row:anchor,choice:{decision:'ACCEPT',resolution:'NEW'}}],
    ['2:4',{row,choice:previous}], ['3:1',{row:{...anchor,file_id:3},choice:{decision:'SKIP'}}],
    ['4:1',{row:{...anchor,file_id:4},choice:{decision:'ACCEPT',resolution:'DUPLICATE'}}],
    ['5:1',{row:{...anchor,file_id:5,parsed:{...anchor.parsed,currency_code:'CNY_4'}},choice:{decision:'ACCEPT'}}],
    ['6:1',{row:{...anchor,file_id:6,classification:'PROCESSED'},choice:{decision:'ACCEPT'}}]]);
  const files = [{file_id:1,filename:'Fictional anchor.csv'}];
  const choices = importAnchorChoices(selected,row,files);
  assert.deepEqual([...choices.keys()],['1:24']);
  assert.match(choices.get('1:24'),/Fictional anchor.csv 第 24 行/);
  assert.match(importRowLabel(row,files),/Fictional purchase/);
  selected.delete('1:24');
  assert.equal(importAnchorChoices(selected,row,files).size,0);
  assert.equal(importIntentChoice(row,previous,'LINK_EXISTING',{kind:'ROW',file_id:1,source_row_number:24},false).target.kind,'ROW');
  console.log('PASS explicit named import intents, strict targets, risk not auto-acknowledged, LINK clears account override, exact live ROW anchor scope');
})().catch(error => {console.error(error); process.exitCode = 1;});

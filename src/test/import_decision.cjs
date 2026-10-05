'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const {importRowChoice} = await import(pathToFileURL(path.join(__dirname,
    '../frontend/js/util/import-decision.js')).href);
  const row = {file_id:1, source_row_number:6, classification:'NEW', issue_codes:[], choice:null,
    duplicate_hint:{state:'NONE_IN_SCOPE', candidate_count:0, scope:{source_known:true}}};
  for (const classification of ['NEW', 'EXISTING', 'PROCESSED']) {
    assert.equal(importRowChoice({...row, classification}).decision, 'ACCEPT');
  }
  for (const classification of ['INVALID', 'AMBIGUOUS', 'UNKNOWN', undefined]) {
    assert.equal(importRowChoice({...row, classification}).decision, 'SKIP');
  }
  for (const code of ['ROW_INVALID', 'NON_POSTED_EVIDENCE', 'NEUTRAL_EVIDENCE',
    'FACT_CONFLICT', 'IDENTITY_AMBIGUOUS', 'ACCOUNT_NOT_ACTIVE', 'RESTORE_REQUIRED']) {
    assert.equal(importRowChoice({...row, issue_codes:[code]}).decision, 'SKIP');
  }
  const explicit = {decision:'ACCEPT', recheck:true, resolution:'DUPLICATE',
    target:{kind:'FACT', transaction_id:17}, account_ref_id:9};
  const invalid = {...row, classification:'INVALID', issue_codes:['ROW_INVALID']};
  assert.equal(importRowChoice(invalid, explicit), explicit);
  assert.equal(importRowChoice({...invalid, choice:explicit}), explicit);
  const skipped = {...explicit, decision:'SKIP', resolution:'AUTO', target:null};
  assert.equal(importRowChoice(row, skipped), skipped);
  assert.equal(importRowChoice({...row, choice:explicit}, skipped), skipped);
  const original = JSON.stringify(row);
  const first = importRowChoice(row), second = importRowChoice(row);
  first.decision = 'SKIP';
  assert.equal(second.decision, 'ACCEPT');
  assert.equal(JSON.stringify(row), original);
  assert.equal(second.recheck, false);
  assert.equal(importRowChoice({...row, persisted_row_status:3}).recheck, false);
  assert.equal(second.account_ref_id, null);
  assert.equal(second.acknowledge_new_risk, undefined);
  // Validity inference is not duplicate-risk consent or financial confirmation.
  assert.equal(importRowChoice({...row, duplicate_hint:{state:'SUSPECTED'}}).acknowledge_new_risk, undefined);
  for (const hint of [null, {state:'SUSPECTED',candidate_count:1},
    {state:'UNCHECKED',candidate_count:null}, {state:'NONE_IN_SCOPE',candidate_count:null},
    {state:'NONE_IN_SCOPE',candidate_count:0,scope:{source_known:false}}]) {
    const risky = {...row,duplicate_hint:hint};
    assert.equal(importRowChoice(risky).decision,'SKIP');
    assert.equal(importRowChoice(risky,explicit),explicit);
    assert.equal(importRowChoice({...risky,choice:explicit}),explicit);
  }
  // Known evidence is not new cash and must not acquire an irrelevant risk gate.
  assert.equal(importRowChoice({...row,classification:'EXISTING',duplicate_hint:{state:'UNCHECKED'}}).decision,'ACCEPT');
  // Certain canonical repetitions use the whole-preview recommendation;
  // saved/user choices win, and a server recommendation is not risk consent.
  const repeat = {...row, default_decision:'SKIP', canonical_duplicate:{kind:'SOURCE_REFERENCE',
    keeper_row:{file_id:1,source_row_number:1}, member_count:25,is_keeper:false}};
  assert.equal(importRowChoice(repeat).decision,'SKIP');
  assert.equal(importRowChoice(repeat,explicit),explicit);
  assert.equal(importRowChoice({...repeat,choice:explicit}),explicit);
  assert.equal(importRowChoice({...repeat, default_decision:'ACCEPT',duplicate_hint:{state:'SUSPECTED',candidate_count:1}}).decision,'SKIP');
  console.log('PASS normal ACCEPT, invalid/ambiguous/issue SKIP, explicit precedence, no mutation or risk consent');
})().catch(error => {console.error(error); process.exitCode = 1;});

const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  const load = file => import(pathToFileURL(path.resolve(__dirname, '../frontend/js/' + file)).href);
  const copy = await load('util/financial-copy.js');
  assert.match(copy.financialStateLabel('coverage','FULL'), /金额已完整分配/);
  assert.doesNotMatch(copy.financialStateLabel('coverage','FULL'), /已核对正确/);
  assert.match(copy.financialStateLabel('default','KNOWN'), /原系统默认唯一/);
  assert.match(copy.financialStateLabel('default','MISSING'), /缺失/);
  assert.match(copy.financialStateLabel('default','AMBIGUOUS'), /不唯一/);
  assert.match(copy.financialStateLabel('review','CONFIRMED'), /解释已生效/);
  assert.match(copy.financialStateLabel('review','REVOKED'), /解释已停用/);
  assert.match(copy.financialScopeNote, /不代表业务已核对正确/);
  assert.match(copy.financialStateLabel('quantity','UNKNOWN'), /没有数量证据，不是零/);
  assert.match(copy.financialStateLabel('quantity','NEEDS_REVIEW'), /来源失效.*不是零/);
  assert.match(copy.financialStateLabel('position','SETTLED'), /标记结清.*核对当前数量/);
  for (const group of ['coverage','default','review','quantity','position','identity','cost','__proto__']) {
    for (const code of [null,undefined,'BAD','__proto__','<img>']) {
      assert.match(copy.financialStateLabel(group,code), /状态未知/);
      assert.doesNotMatch(copy.financialStateLabel(group,code), /已分配|已生效|已结清/);
    }
  }
  for (const code of ['ROW_INVALID','FACT_COVERAGE_REQUIRED','INVALID_PRINCIPAL','POSITION_SOURCE_INVALID',
    'ACCOUNT_CORRECTION_KEEPER_REQUIRED','UNIT_MISMATCH','RELATION_BROKEN','STALE_PREVIEW']) {
    const message = copy.financialIssueMessage({code,message:'PRIVATE English diagnostic'});
    assert.ok(/[\u4e00-\u9fff]/.test(message));
    assert.equal(message.split(code).length,2);
    assert.doesNotMatch(message, /PRIVATE/);
  }
  assert.match(copy.financialIssueMessage({code:'NOT_YET_MAPPED',message:'PRIVATE English diagnostic'}), /未通过校验/);
  assert.doesNotMatch(copy.financialIssueMessage({code:'<script>',message:'PRIVATE diagnostic'}), /<script>|PRIVATE/);
  assert.match(copy.financialIssueMessage(Error('请先选择完整来源范围')), /请先选择完整来源范围/);
  const {importIssueMessage} = await load('util/import-decision.js');
  assert.equal(importIssueMessage('ROW_INVALID'),copy.financialIssueReason('ROW_INVALID'));
  const {statusNames} = await load('util/core.js');
  assert.equal(statusNames.CONFIRMED,copy.financialStateLabel('review','CONFIRMED',false));
  // Existing uncertainty classification remains authoritative, even for a
  // recognizable validation code delivered with an unexplained server failure.
  const {writeFailure} = await load('component/workbench.js');
  const status = {textContent:''}, host={querySelector:()=>status};
  assert.equal(writeFailure(host,{status:422,code:'INVALID_PRINCIPAL',message:'PRIVATE'}),false);
  assert.match(status.textContent,/本金/); assert.doesNotMatch(status.textContent,/PRIVATE/);
  assert.equal(writeFailure(host,{status:503,code:'INVALID_PRINCIPAL'}),true);
  assert.match(status.textContent,/结果未知.*不要重发/);
  const {request,isUnknownWrite} = await load('api/client.js');
  global.document={querySelector:()=>null};
  for (const [statusCode,code] of [[422,'INVALID_PRINCIPAL'],[503,'INVALID_PRINCIPAL'],[503,'WRITE_BUSY'],[409,'RESULT_UNKNOWN'],[409,'NEW_SERVER_CODE']]) {
    global.fetch=async () => ({ok:false,status:statusCode,headers:new Headers(),
      json:async () => ({message:'PRIVATE server diagnostic',body:{code,details:{safe_locator:7}}})});
    await assert.rejects(request('/paam/ledger/v1/review/3'), error => {
      assert.equal(error.status,statusCode); assert.equal(error.code,code);
      assert.equal(error.details.safe_locator,7);
      assert.ok(/[\u4e00-\u9fff]/.test(error.message)); assert.doesNotMatch(error.message,/PRIVATE/);
      assert.equal(isUnknownWrite(error),code === 'RESULT_UNKNOWN' || statusCode >= 500 && code !== 'WRITE_BUSY');
      return true;
    });
  }
  console.log('PASS truthful coverage/publication/quantity states, shared Chinese financial reasons, safe unknowns and unchanged write uncertainty');
})().catch(error=>{console.error(error);process.exitCode=1;});

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
  assert.equal(copy.financialStateLabel('account','ACTIVE',false),'使用中');
  assert.match(copy.financialStateLabel('account','CLOSED'), /已关闭.*历史现金保留.*CLOSED/);
  assert.match(copy.financialStateLabel('importRisk','NONE_IN_SCOPE'), /本次核验范围.*不代表全库无重复/);
  assert.match(copy.financialStateLabel('importRisk','SUSPECTED'), /疑似重复.*未认定重复/);
  assert.match(copy.financialStateLabel('importRisk','UNCHECKED'), /风险未核验.*不能按零候选处理/);
  assert.match(copy.financialStateLabel('plannedReview','CONFIRMED'), /拟生效.*尚未写入.*CONFIRMED/);
  assert.match(copy.financialStateLabel('plannedReview','REVOKED'), /拟停用.*尚未写入.*REVOKED/);
  assert.doesNotMatch(copy.financialStateLabel('plannedReview','CONFIRMED'), /已生效|已核对/);
  assert.match(copy.importPlanScopeNote, /规则校验通过.*不代表业务已核对正确.*事务内重验.*明确确认/);
  assert.equal(copy.financialStateLabel('positionType','LIABILITY'),'负债／债务（LIABILITY）');
  assert.equal(copy.financialStateLabel('positionUsage','GENERAL'),'通用数量对象（GENERAL）');
  const {usages} = await load('view/position.js');
  for (const code of usages) assert.doesNotMatch(copy.financialStateLabel('positionUsage',code),/状态未知/);
  for (const group of ['coverage','default','review','quantity','position','positionType','positionUsage','identity','cost','account','metadata','plannedReview','importRisk','__proto__']) {
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
  const {writeFailure,metadataLabel} = await load('component/workbench.js');
  assert.equal(metadataLabel({name:'Mock source',status:'ACTIVE',id:7}), 'Mock source · 使用中（ACTIVE） · #7');
  assert.match(metadataLabel({name:'Mock source',status:'CLOSED',id:7}), /已关闭.*历史内容保留.*CLOSED.*#7/);
  assert.match(metadataLabel({name:'Mock source',status:'FUTURE',id:7}), /状态未知.*FUTURE.*#7/);
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
      if(code === 'NEW_SERVER_CODE') assert.match(error.message,/读取失败.*重新读取/);
      assert.equal(isUnknownWrite(error),code === 'RESULT_UNKNOWN' || statusCode >= 500 && code !== 'WRITE_BUSY');
      return true;
    });
  }
  global.document.dispatchEvent=()=>{};
  global.CustomEvent=class {};
  for (const [statusCode,phrase] of [[503,'读取当前状态核对'],[422,'未通过校验']]) {
    global.fetch=async () => ({ok:false,status:statusCode,headers:new Headers(),
      json:async () => ({message:'PRIVATE unknown write diagnostic',body:{code:'NEW_SERVER_CODE'}})});
    await assert.rejects(request('/paam/ledger/v1/review/command',{method:'POST'}), error => {
      assert.ok(error.message.includes(phrase)); assert.doesNotMatch(error.message,/PRIVATE/);
      assert.equal(isUnknownWrite(error),statusCode === 503); return true;
    });
  }
  // Check the actual page template as well as the shared formatter: a fixed
  // note must not offer Position as an account-balance or valuation feature.
  global.location={hash:'#workbench/account'};
  const accountReads=[];
  global.fetch=async (url,options={}) => {
    assert.match(url,/^\/paam\/ledger\/v1\/account-ref\/list\?/);
    assert.equal(options.method || 'GET','GET'); accountReads.push(url);
    return {ok:true,status:200,headers:new Headers(),json:async()=>({status:200,
      body:{items:[],total:0,page_index:1,page_size:20}})};
  };
  const {accountManagementPage}=await load('view/account-management.js');
  const accountMarkup=await accountManagementPage(new URLSearchParams());
  assert.match(accountMarkup,/有据数量另见/);
  assert.match(accountMarkup,/不是账户余额或市场估值/);
  assert.doesNotMatch(accountMarkup,/数量和余额另见/);
  assert.equal(accountReads.length,1);
  console.log('PASS truthful page and formatter quantity states, shared Chinese financial reasons, safe unknowns and unchanged write uncertainty');
})().catch(error=>{console.error(error);process.exitCode=1;});

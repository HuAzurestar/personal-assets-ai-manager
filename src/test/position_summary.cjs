const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  global.document = {querySelector: () => null};
  const module = file => import(pathToFileURL(path.resolve(__dirname, '../frontend/js/' + file)).href);
  const {installUnitDictionary} = await module('util/unit-dictionary.js');
  const cash = precision => ({code:`CNY_${precision}`, label:'Mock exact CNY', dimension:'CURRENCY',
    quantum:precision ? `1E-${precision}` : '1', precision, is_default:false});
  installUnitDictionary([{...cash(2),code:'CNY',is_default:true}, ...Array.from({length:9}, (_,i)=>cash(i)),
    {code:'KG_3',label:'Mock kg',dimension:'MASS',quantum:'0.001',precision:3,is_default:false},
    {code:'PCS',label:'Mock pieces',dimension:'COUNT',quantum:'1',precision:0,is_default:false}]);
  const {positionQuantityLabel,positionRows,positionFields,positionPage} = await module('view/position.js');
  const fields = positionFields({status:'ACTIVE',usage_scenario:'GENERAL'},true);
  assert.match(fields, /value="ACTIVE"[^>]*>使用中（ACTIVE）/);
  assert.match(fields, /value="ARCHIVED"[^>]*>已归档（证据保留）（ARCHIVED）/);
  assert.match(fields, /value="SETTLED"[^>]*>已标记结清（仍需核对当前数量）（SETTLED）/);
  assert.match(fields, /value="PERSONAL-LENDING"[^>]*>个人借还（PERSONAL-LENDING）/);
  assert.equal(positionQuantityLabel({quantity_state:'KNOWN',quantity:10000,unit_code:'CNY'}),'100.00 CNY');
  assert.equal(positionQuantityLabel({quantity_state:'KNOWN',quantity:0,unit_code:'KG_3'}),'0.000 KG_3');
  assert.equal(positionQuantityLabel({quantity_state:'KNOWN',quantity:4,unit_code:'PCS'}),'4 PCS');
  assert.ok(positionQuantityLabel({quantity_state:'UNKNOWN',quantity:null}).includes('不是零'));
  assert.ok(positionQuantityLabel({quantity_state:'NEEDS_REVIEW',quantity:null}).includes('需核对'));
  for (const value of [null,1.5,NaN,Number.MAX_SAFE_INTEGER+1]) {
    assert.throws(()=>positionQuantityLabel({quantity_state:'KNOWN',quantity:value,unit_code:'CNY'}));
  }
  assert.throws(()=>positionQuantityLabel({quantity_state:'BAD'}));
  const rows = positionRows([{id:1,title:'Mock <img src=x>',type:'ASSET',usage_scenario:'GENERAL',
    party_name:'Mock <person>',counterparty:'Mock & borrower',unit_code:'CNY',status:'ARCHIVED',
    quantity_state:'KNOWN',quantity:10000}],new URLSearchParams());
  assert.ok(rows[0].includes('100.00 CNY') && rows[0].includes('ARCHIVED'));
  assert.ok(rows[0].includes('Mock &lt;person&gt;') && rows[0].includes('Mock &amp; borrower'));
  assert.ok(!rows[0].includes('<img src=x>'));
  assert.ok(rows[0].includes('资产／债权（ASSET）') && rows[0].includes('通用数量对象（GENERAL）'));
  assert.ok(positionRows([],new URLSearchParams(),true)[0].includes('空批次不代表扫描结束'));
  global.location = {hash:'#workbench/position?id=1'};
  const detail = {id:1,title:'Mock <position>',type:'ASSET',usage_scenario:'PERSONAL-LENDING',status:'ACTIVE',
    party_id:1,counterparty:'Mock borrower',unit_code:'CNY',quantity_state:'KNOWN',quantity:10000,cost_state:'UNKNOWN'};
  const evidence = {id:2,type:'MOVEMENT',review_id:3,review:{status:'CONFIRMED'},leg_direction:'IN',leg_amount:10000,
    unit_code:'CNY',source_position_leg_id:0,occurred_time:'2024-01-01T00:00:00Z',basis:'Mock evidence',position_allocations:[]};
  const calls = [];
  global.fetch = async url => {
    calls.push(String(url));
    const body = String(url).includes('/leg/list') ? {items:[evidence],total:1,page_size:20,page_index:1}
      : String(url).includes('/list?') ? {items:[detail],total:1,page_size:20,page_index:1} : detail;
    return {ok:true,status:200,headers:new Headers(),json:async()=>({status:200,body})};
  };
  let html = await positionPage(new URLSearchParams('id=1'));
  assert.match(html,/资产／债权（ASSET）.*个人借还（PERSONAL-LENDING）.*使用中（ACTIVE）/);
  assert.match(html,/Review #3 解释已生效（CONFIRMED）/);
  assert.match(html,/100.00 CNY/);
  assert.match(html,/Mock &lt;position&gt;/);
  evidence.review.status = 'FUTURE'; detail.status = 'FUTURE';
  html = await positionPage(new URLSearchParams('id=1'));
  assert.match(html,/Review #3 状态未知（FUTURE）/);
  assert.doesNotMatch(html.slice(html.indexOf('<section class="panel" data-position-detail')),/解释已停用|已标记结清|使用中（FUTURE）/);
  assert.equal(calls.length,6); // List, detail and legs are still three reads.
  console.log('PASS exact quantity summary, zero versus unknown, invalid response guard and identity escaping');
})().catch(error=>{console.error(error);process.exitCode=1;});

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
  const {positionQuantityLabel,positionRows} = await module('view/position.js');
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
  assert.ok(positionRows([],new URLSearchParams(),true)[0].includes('空批次不代表扫描结束'));
  console.log('PASS exact quantity summary, zero versus unknown, invalid response guard and identity escaping');
})().catch(error=>{console.error(error);process.exitCode=1;});

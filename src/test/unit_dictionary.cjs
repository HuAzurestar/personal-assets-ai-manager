'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {unitFixture, installFixture} = require('./unit_fixture.cjs');
const vm = require('node:vm');
const fs = require('node:fs');

async function main() {
  const units = unitFixture();
  const dictionary = await import(pathToFileURL(path.join(__dirname, '../frontend/js/util/unit-dictionary.js')).href);
  const core = await import(pathToFileURL(path.join(__dirname, '../frontend/js/util/core.js')).href);
  assert.throws(() => core.decimalAmount('1', 'CNY'), {code: 'UNIT_DICTIONARY_UNAVAILABLE'});
  let calls = 0;
  globalThis.document = {querySelector: () => null};
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url, '/paam/ledger/v1/unit');
    assert.equal(options.method || 'GET', 'GET');
    assert.ok(options.signal instanceof AbortSignal);
    return calls === 1
      ? {ok:false, status:503, headers:new Headers(), json:async () => ({message:'Mock read failure', body:{code:'QUERY_BUSY'}})}
      : {ok:true, status:200, json:async () => ({status:200,message:'ok',body:{items:units}})};
  };
  await assert.rejects(dictionary.loadUnitDictionary(), {code:'QUERY_BUSY'});
  await Promise.all([dictionary.loadUnitDictionary(), dictionary.loadUnitDictionary()]);
  assert.equal(calls, 2, 'failed GET is user-retryable; parallel consumers share one read');
  await dictionary.loadUnitDictionary(); assert.equal(calls, 2);
  assert.equal(dictionary.unitChoices().length, units.length);
  assert.equal(dictionary.unitChoices('CURRENCY').length, 70);
  for (const unit of units) {
    assert.equal(dictionary.unitPrecision(unit.code), unit.precision);
    assert.equal(dictionary.unitDefinition(unit.code).quantum, unit.quantum);
    assert.ok(Object.isFrozen(dictionary.unitDefinition(unit.code)));
    if (unit.dimension === 'CURRENCY') {
      assert.equal(core.decimalAmount(unit.precision ? `0.${'0'.repeat(unit.precision-1)}1` : '1', unit.code), 1);
    } else assert.throws(() => dictionary.currencyPrecision(unit.code), {code:'INVALID_CURRENCY'});
  }
  assert.equal(dictionary.currencyPrecision(' krw '), 0);
  assert.equal(core.quantityAmount('1.00', ' cny '), 100, 'preserve backend currency normalization');
  assert.equal(core.quantityDecimal(1, ' krw '), '1');
  assert.throws(() => core.quantityAmount('1', 'pcs'), {code:'INVALID_CURRENCY'});
  assert.throws(() => dictionary.currencyPrecision('KRW_9'), {code:'INVALID_UNIT'});
  assert.throws(() => dictionary.installUnitDictionary([]));
  assert.throws(() => dictionary.installUnitDictionary(units.slice(0, -1)));
  assert.throws(() => dictionary.installUnitDictionary([...units, units[0]]));
  assert.throws(() => dictionary.installUnitDictionary(units.map((item, i) => i ? item : {...item, quantum:'0.001'})));
  assert.throws(() => dictionary.installUnitDictionary(units.map((item, i) => i ? item : {...item, precision:true})));
  assert.equal(dictionary.unitChoices().length, 72, 'invalid replacement must be atomic');
  const context = vm.createContext({});
  installFixture(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/js/view/disclosure.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, ''), context);
  assert.equal(context.decimalBoundary('400', 'KRW'), 400);
  assert.equal(context.decimalBoundary('0.00000001', 'KRW_8'), 1);
  assert.equal(context.boundaryDecimal(1, 'KRW_8'), '0.00000001');
  assert.equal(context.parseDisclosure('{"KRW":[0,400]}', 'DAY').amount_bands.KRW.boundaries[1], 400);
  for (const code of ['PCS','KG_3','krw',' KRW','KRW_9','XYZ']) {
    assert.throws(() => context.parseDisclosure(JSON.stringify({[code]:[0,1]}), 'DAY'));
  }
  console.log('PASS authoritative unit catalog: shared GET/retry, exact currencies/quantities, atomic validation and disclosure');
}
main().catch(error => {console.error(error); process.exitCode = 1;});

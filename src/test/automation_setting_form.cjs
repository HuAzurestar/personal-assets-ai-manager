"use strict";
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const context = vm.createContext({ registerInspection: () => {} });
for (const name of ['disclosure', 'automation']) {
  const source = fs.readFileSync(path.join(__dirname, `../frontend/js/view/${name}.js`),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
  vm.runInContext(source,context);
}
for (const [code, value, expected] of [['CNY','30.01',3001], ['CNY_4','0.3000',3000], ['JPY','3000',3000], ['USD_8','0.00000001',1]]) {
  assert.equal(context.decimalBoundary(value,code),expected);
  assert.equal(context.decimalBoundary(context.boundaryDecimal(expected,code),code),expected);
}
assert.equal(context.decimalBoundary('90000000000.00','CNY'),9000000000000);
for (const [value,code] of [['1.001','CNY'], ['1.1','JPY'], ['-1','CNY'], ['1e2','CNY'], ['90000000000.01','CNY']]) assert.throws(() => context.decimalBoundary(value,code));
for (const cron of ['*/5 * * * *','0 * * * *','0 9 * * *','59 23 * * *','*/1 * * * * *','0 20 * * mon-fri','']) {
  const preset = context.cronPreset(cron);
  assert.equal(context.frequencyCron(preset.frequency,preset.time,cron),cron);
}
assert.throws(() => context.frequencyCron('day','25:00',''));
assert.throws(() => vm.runInContext('numberOrNull("abc", true)', context), /数值参数格式不正确/);
assert.throws(() => vm.runInContext('numberOrNull("3.9", true)', context), /整数/);
context.invalidModelData = { get: (key) => key === 'extras' ? '{bad-json' : '' };
assert.throws(() => vm.runInContext('modelParameters(invalidModelData)', context), /有效的 JSON 对象/);
const slot = { innerHTML: '' };
context.$ = () => slot;
context.esc = (value) => value;
vm.runInContext('showFormError({}, { code: "SETTING_VERSION_CONFLICT", message: "raw backend text" })', context);
assert.match(slot.innerHTML, /设置已被其他操作更新/);
assert.doesNotMatch(slot.innerHTML, /raw backend text/);
console.log('PASS exact currency units, precision rejection, legacy/custom CRON roundtrip');

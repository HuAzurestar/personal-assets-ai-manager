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
for (const cron of ['*/5 * * * *','0 * * * *','0 9 * * *','59 23 * * *','*/1 * * * * *','0 20 * * mon-fri','0 9 * * 1-5','']) {
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
vm.runInContext('showFormError({}, { code: "VALIDATION_ERROR", message: "raw backend text", details: [{loc:["body","cron"]}] })', context);
assert.match(slot.innerHTML, /星期可用 0\/7=周日/);
vm.runInContext('showFormError({}, { code: "AUTO_TAG_RULE_CRON_NO_RUN", message: "raw backend text" })', context);
assert.match(slot.innerHTML, /没有下一次执行时间/);
assert.doesNotMatch(slot.innerHTML, /raw backend text/);
vm.runInContext('showFormError({}, { code: "MODEL_KEY_REQUIRED", details: { model_ids: [1, 3] }, message: "raw backend text" })', context);
assert.match(slot.innerHTML, /模型 #1、#3 没有服务端密钥/);
vm.runInContext('showFormError({}, { code: "PROTECTED_SECRET_STORE_ERROR", message: "raw backend text" })', context);
assert.match(slot.innerHTML, /没有可用的受保护凭据库/);
const form = {
  dataset: { id: '3' },
  settingSnapshot: { models: [
    { id: 1, enabled: true, key_configured: false, litellm_params: { model: 'test/one', api_base: 'https://example.test/v1' } },
    { id: 3, enabled: false, key_configured: false, litellm_params: { model: 'test/three', api_base: 'https://example.test/v1' } },
  ], updated_time: null },
};
const button = { disabled: false };
let writes = 0;
let secretInput = 'fake-only';
context.$ = (selector) => selector === '.form-error-slot' ? slot : button;
context.jsonRequest = async () => { writes++; };
context.structuredClone = structuredClone;
context.FormData = class {
  get(key) { return { name: 'Test model', model: 'test/three', api_base: 'https://example.test/v1', secret: secretInput }[key] || ''; }
  has(key) { return key === 'enabled'; }
};
context.formUnderTest = form;
const submit = () => vm.runInContext('submitModel({ preventDefault() {}, currentTarget: formUnderTest })', context);
async function testMissingModelKeys() {
  await submit();
  assert.equal(writes, 0);
  assert.match(slot.innerHTML, /模型 #1 没有服务端密钥/);
  assert.equal(button.disabled, false);
  form.settingSnapshot.models[0].enabled = false;
  secretInput = '';
  await submit();
  assert.equal(writes, 0);
  assert.match(slot.innerHTML, /模型 #3 未配置服务端密钥/);
  const clientContext = vm.createContext({
    fetch: async () => ({ ok: false, status: 422, json: async () => ({
      status: 422, message: 'enabled models require a configured secret',
      body: { code: 'MODEL_KEY_REQUIRED', details: { model_ids: [1] } },
    }) }),
    document: { querySelector: () => null },
  });
  const clientSource = fs.readFileSync(path.join(__dirname, '../frontend/js/api/client.js'), 'utf8').replace(/^export /gm, '');
  vm.runInContext(clientSource, clientContext);
  await assert.rejects(vm.runInContext('request("/fixture")', clientContext), (error) => {
    assert.equal(error.code, 'MODEL_KEY_REQUIRED');
    assert.equal(error.details.model_ids[0], 1);
    return true;
  });
  console.log('PASS exact currency units, model-key diagnostics, legacy/custom CRON roundtrip');
}
testMissingModelKeys().catch((error) => { throw error; });

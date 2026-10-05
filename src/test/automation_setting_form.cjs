"use strict";
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const context = vm.createContext({ registerInspection: () => {} });
require('./unit_fixture.cjs').installFixture(context);
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
assert.equal(vm.runInContext('providerPresets.deepseek.base', context), 'https://api.deepseek.com');
assert.equal(vm.runInContext('providerPresets.opencode_console.base', context), 'https://opencode.ai/inference/openai/v1');
assert.equal(vm.runInContext('modelParameterSignature({model:"openai/example",api_base:"https://example.test/v1",proxy_url:"http://a.test:7890"}) === modelParameterSignature({model:"openai/example",api_base:"https://example.test/v1",proxy_url:"http://b.test:7890"})', context), true);
assert.equal(vm.runInContext('modelParameterSignature({model:"openai/one",api_base:"https://example.test/v1"}) === modelParameterSignature({model:"openai/two",api_base:"https://example.test/v1"})', context), false);
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
async function testSavedRuleSnapshot() {
  const original = { id: 7, cron: '*/5 * * * *', updated_time: '2026-10-03T01:02:03.123456Z' };
  const other = { id: 8, cron: '0 * * * *' };
  const saved = { ...original, name: 'Returned name', cron: '30 9 * * *', updated_time: '2026-10-03T01:02:03.123457Z' };
  context.originalRule = original;
  context.otherRule = other;
  vm.runInContext('rules = [originalRule, otherRule]', context);
  const sent = [], events = [];
  let closed = 0;
  context.semanticRuleChange = () => false;
  context.FormData = class {
    constructor() { this.entries = new Map(Object.entries({ name: 'Submitted name', model_id: '1', prompt: 'Fictional', frequency: 'day', daily_time: '09:30', cron: '*/5 * * * *', amount_mode: '1', view_id: '2' })); }
    get(key) { return this.entries.get(key); }
    has(key) { return this.entries.has(key); }
    [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
  };
  context.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
  context.window = { dispatchEvent: (event) => events.push(event) };
  context.jsonRequest = async (...args) => { sent.push(args); return { body: saved, warnings: [{code: 'REGISTER_FAILED'}] }; };
  const ruleForm = { dataset: { id: '7' }, ruleSnapshot: original, closest: () => ({ close: () => {
    closed++;
    // Immediately re-edit, before any list request can finish.
    assert.equal(vm.runInContext('rules[0]', context), saved);
    assert.equal(vm.runInContext('rules[1]', context), other);
    assert.equal(context.cronPreset(vm.runInContext('rules[0].cron', context)).frequency, 'day');
  } }) };
  context.formUnderTest = ruleForm;
  await vm.runInContext('submitRule({ preventDefault() {}, currentTarget: formUnderTest })', context);
  assert.equal(closed, 1);
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], '/paam/tag/v1/auto_rule/7');
  assert.equal(sent[0][1], 'PUT');
  assert.equal(sent[0][2].expected_updated_time, original.updated_time);
  assert.equal(vm.runInContext('rules[0].updated_time', context), saved.updated_time);
  assert.match(events[0].detail.message, /调度注册失败/);
  ruleForm.ruleSnapshot = saved;
  context.jsonRequest = async (...args) => { sent.push(args); throw Object.assign(new Error('conflict'), {code: 'AUTO_TAG_RULE_WRITE_CONFLICT'}); };
  await vm.runInContext('submitRule({ preventDefault() {}, currentTarget: formUnderTest })', context);
  assert.equal(sent[1][2].expected_updated_time, saved.updated_time);
  assert.equal(closed, 1);
  assert.equal(events.length, 1);
  assert.equal(vm.runInContext('rules[0]', context), saved);
  assert.equal(button.disabled, false);
  // A new rule's success navigates to its page; do not falsify this page's
  // membership, total or sort order by appending it to the current list.
  const created = { ...saved, id: 9 };
  ruleForm.dataset.id = '';
  ruleForm.ruleSnapshot = null;
  ruleForm.closest = () => ({ close: () => { closed++; } });
  context.jsonRequest = async (...args) => { sent.push(args); return {body: created}; };
  await vm.runInContext('submitRule({ preventDefault() {}, currentTarget: formUnderTest })', context);
  assert.equal(sent[2][1], 'POST');
  assert.equal(events[1].detail.createdRuleId, 9);
  assert.equal(vm.runInContext('rules.length', context), 2);
}
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
    fetch: async () => ({ ok: false, status: 422,
      headers: new Headers({ 'X-PAAM-Trace-ID': 'fixture-trace' }),
      json: async () => ({
      status: 422, message: 'enabled models require a configured secret',
      body: { code: 'MODEL_KEY_REQUIRED', details: { model_ids: [1] } },
    }) }),
    document: { querySelector: () => null },
  });
  const clientSource = fs.readFileSync(path.join(__dirname, '../frontend/js/api/client.js'), 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  vm.runInContext(clientSource, clientContext);
  await assert.rejects(vm.runInContext('request("/fixture")', clientContext), (error) => {
    assert.equal(error.code, 'MODEL_KEY_REQUIRED');
    assert.equal(error.details.model_ids[0], 1);
    assert.equal(error.traceId, 'fixture-trace');
    assert.equal(error.status, 422);
    return true;
  });
  await testSavedRuleSnapshot();
  console.log('PASS exact currency units, model-key diagnostics, legacy/custom CRON roundtrip, authoritative saved-rule snapshot before refresh and conflict preservation');
}
testMissingModelKeys().catch((error) => { throw error; });

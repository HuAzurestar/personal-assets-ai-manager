"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = { console, URLSearchParams, AbortController,
  esc: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  registerInspection() {}, helpTip: (_title, text) => text,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../frontend/js/view/automation.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, ''), context);
const config = { scan_available:true, scan_enabled:false, models:[{enabled:true,key_configured:true}] };
const schedule = { scheduler_state:'RUNNING', worker_state:'HEALTHY', tasks:[] };
const markup = context.automationStateMarkup({status:'ok'}, config, schedule);
assert.match(markup, /data-automation-state="service"[^>]*data-state="connected"/);
assert.match(markup, /data-automation-state="analysis"[^>]*data-state="off"/);
assert.match(markup, /1 个配置 · 1 个已启用且已存密钥/);
assert.match(markup, /不代表真实连接已验证/);
assert.match(markup, /调度运行 · 执行器正常/);
assert.match(markup, /已注册 0 · 运行 0 · 排队 0/);
assert.doesNotMatch(markup, /重启服务|扫描已就绪/);
const stopped = context.automationStateMarkup({status:'ok'}, {...config,scan_enabled:true}, {...schedule,scheduler_state:'STOPPED',worker_state:'UNHEALTHY'});
assert.match(stopped, /data-automation-state="service"[^>]*data-state="connected"/);
assert.match(stopped, /data-automation-state="analysis"[^>]*data-state="on"/);
assert.match(stopped, /调度停止 · 执行器异常/);
for (const invalid of [null, {}, {status:'unknown'}]) {
  assert.match(context.automationStateMarkup(invalid, config, schedule), /data-automation-state="service"[^>]*data-state="unknown"/);
}
const unknown = context.automationStateMarkup({status:'ok'}, null, null);
assert.match(unknown, /模型配置未知/);
assert.match(unknown, /自动分析状态未知/);
assert.match(unknown, /任务状态未知/);
assert.doesNotMatch(unknown, /已注册 0|没有模型|已关闭/);
assert.match(context.automationStateMarkup({status:'ok',operational_log:'unavailable'}, config, schedule), /服务在线.*运行日志不可用/s);
assert.match(context.automationStateMarkup({status:'ok'}, {...config,scan_available:false,scan_enabled:true}, schedule), /部署层已关闭/);
assert.match(context.automationStateMarkup({status:'ok'}, {...config,scan_enabled:undefined}, schedule), /自动分析状态未知/);
vm.runInContext('setting = config; scheduleStatus = {tag_scan_guard:"DISABLED",tasks:[]}', Object.assign(context,{config}));
assert.match(context.scheduleNotice(), /设置页.*开启自动分析/);
assert.match(context.scheduleNotice(), /无需重启服务/);
assert.doesNotMatch(context.scheduleNotice(), /并重启服务/);
vm.runInContext('setting = {...config,scan_available:false}', context);
assert.match(context.scheduleNotice(), /部署配置/);
// A failed schedule read cannot erase an independent successful health read.
const requested = [];
context.request = async (url, options) => {
  requested.push(url); assert.equal(options.cache,'no-store');
  if (url === '/api/health') return {status:'ok'};
  if (url.includes('schedule/status')) throw Error('schedule only failure');
  return config;
};
context.diagnosticSnapshot = async () => { throw Error('diagnostics only failure'); };
(async () => {
  const snapshot = await context.loadAutomationSnapshot({dataset:{autoPage:'settings'}}, new AbortController().signal);
  assert.equal(snapshot.health.status,'ok'); assert.equal(snapshot.schedule,null);
  assert.equal(snapshot.currentSetting,config); assert.equal(snapshot.diagnostics,null);
  assert.equal(requested.length,3);
  const aborted = new AbortController(); aborted.abort();
  context.request = async () => { throw Object.assign(Error('cancelled'),{name:'AbortError'}); };
  await assert.rejects(context.loadAutomationSnapshot({dataset:{autoPage:'settings'}},aborted.signal), /cancelled/);
  console.log('PASS independent service/config/analysis/task states, truthful unknowns, deployment guidance, read-only partial failures and abort');
})().catch(error => { console.error(error); process.exitCode=1; });

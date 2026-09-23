"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = path.resolve(__dirname, "../frontend/js/view/automation.js");
const code = fs.readFileSync(source, "utf8")
  .replace(/^import .*;\r?\n/gm, "")
  .replace(/^export /gm, "");
const rule = {
  id: 1, rule_revision: 3, name: "虚构早餐", enabled: true,
  view_id: 1, cron: "*/10 * * * * *", scan_after_ledger_id: 1, scan_epoch: 3,
  analyzed_count: 1, failed_count: 0, suggested_count: 1,
  accepted_count: 0, rejected_count: 0,
  method_config: { model_id: 2, prompt: "只判断虚构早餐" },
};
const setting = {
  models: [{
    id: 2, name: "虚构模型", enabled: true, key_configured: true,
    litellm_params: { model: "openai/Qwen/Qwen3.5-4B", api_base: "test://fixture" },
  }],
  disclosure: {},
};
let schedule = {
  scheduler_state: "RUNNING", worker_state: "HEALTHY", tasks: [],
};
const context = {
  request: async (url) => url.includes("auto_rule/list") ? { items: [rule] }
    : url.includes("schedule/status") ? schedule : setting,
  jsonRequest: async () => {},
  $: () => null,
  $$: () => [],
  esc: (value) => String(value ?? ""),
};
vm.createContext(context);
vm.runInContext(`${code}\nglobalThis.testPanel = autoRulesPanel; globalThis.testSettings = automationSettingsPage;`, context);

(async () => {
  const views = [{ id: 1, name: "分类", status: "ACTIVE" }];
  const idle = await context.testPanel(views);
  assert.match(idle, /自动标签扫描未启用/);
  assert.match(idle, /已保存·未调度/);
  assert.match(idle, /未注册到调度器/);
  assert.doesNotMatch(idle, /CRON 调度运行中/);

  schedule = {
    scheduler_state: "RUNNING", worker_state: "HEALTHY",
    tasks: [{ task_key: "tag-scan:1", next_run_at: "2026-09-23T14:00:00+08:00", last_result: "COMPLETED" }],
  };
  const active = await context.testPanel(views);
  assert.match(active, /CRON 调度运行中 · 1 条规则已注册/);
  assert.match(active, /上次执行：完成/);
  assert.doesNotMatch(active, /已保存·未调度/);

  const settings = await context.testSettings();
  assert.match(settings, /只有显式隔离的合成验收库才会注册 CRON 扫描/);
  console.log("RESULT=PASS DEFAULT_NOT_SCHEDULED=1 SYNTHETIC_REGISTERED=1 SETTINGS_COPY=1");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

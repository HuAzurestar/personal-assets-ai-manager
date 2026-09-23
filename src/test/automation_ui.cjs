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
const suggestion = {
  id: 8, status: 1, created_time: "2026-09-23T14:00:00+08:00",
  proposed_tag_name: "餐饮", view_name: "消费分类", view_system_name: "category",
  reason_summary: "工作日早餐", rule_name: "早餐规则", rule_id: 1,
  rule_revision: 3, ledger_id: 12, ledger_counterparty_name: "虚构咖啡店",
};
const ledger = {
  ledger_entry: { active: true, summary: "买早餐", amount: 2500, currency_code: "CNY",
    occurred_time: "2026-09-23T08:00:00+08:00", entry_direction: 2,
    tags: [{ view_system_name: "category", tag_name: "未分类", source_type: "MANUAL" }] },
  facts: [{ counterparty_name: "虚构咖啡店", summary: "早餐", amount: 2500,
    currency_code: "CNY", occurred_time: "2026-09-23T08:00:00+08:00" }],
};
const context = {
  request: async (url) => url.includes("auto_rule/list") ? { items: [rule] }
    : url.includes("auto_rule/1") ? rule
    : url.includes("assignment_request/list") ? { items: [suggestion] }
    : url.includes("assignment_request/8") ? suggestion
    : url.includes("flow/12") ? ledger
    : url.includes("view/list") ? { items: [{ id: 1, name: "分类", status: "ACTIVE" }] }
    : url.includes("schedule/status") ? schedule : setting,
  jsonRequest: async () => {},
  $: () => null,
  $$: () => [],
  esc: (value) => String(value ?? ""),
  money: ({ amount, currency_code }) => `${currency_code} ${amount}`,
  URLSearchParams,
};
vm.createContext(context);
vm.runInContext(`${code}\nglobalThis.testPanel = autoRulesPanel; globalThis.testSettings = automationSettingsPage; globalThis.testRuleDetail = ruleDetailMarkup; globalThis.testRequestDetail = requestDetailMarkup; globalThis.testScheduleExplanation = scheduleExplanation; globalThis.testRulePage = autoRulesPage; globalThis.testReviewPage = tagReviewPage;`, context);

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
  assert.match(active, /审查详情/);

  const detail = context.testRuleDetail(rule, schedule, { items: [{
    id: 8, ledger_id: 12, ledger_counterparty_name: "虚构商户", proposed_tag_name: "餐饮",
    status: 1, created_time: "2026-09-23T14:00:00+08:00",
  }] });
  assert.match(detail, /业务判断说明/);
  assert.match(detail, /虚构商户/);
  assert.match(detail, /历史模型请求正文与原始响应未被保存/);
  assert.match(context.testScheduleExplanation("ACCEPTANCE_DATABASE_REQUIRED"), /非虚构数据/);

  const rulePage = await context.testRulePage(new URLSearchParams({ rule_id: "1" }));
  assert.match(rulePage, /返回规则列表/);
  assert.match(rulePage, /业务判断说明/);
  const reviewPage = await context.testReviewPage(new URLSearchParams({ request_id: "8" }));
  assert.match(reviewPage, /返回建议列表/);
  assert.match(reviewPage, /虚构咖啡店/);
  assert.match(reviewPage, /通过并打标/);

  const requestDetail = context.testRequestDetail({
    id: 8, status: 1, created_time: "2026-09-23T14:00:00+08:00",
    proposed_tag_name: "餐饮", view_name: "消费分类", view_system_name: "category",
    reason_summary: "工作日早餐", rule_name: "早餐规则", rule_id: 1,
    rule_revision: 3, ledger_id: 12,
  }, {
    ledger_entry: { active: true, summary: "买早餐", amount: 2500, currency_code: "CNY",
      occurred_time: "2026-09-23T08:00:00+08:00",
      tags: [{ view_system_name: "category", tag_name: "未分类", source_type: "MANUAL" }] },
    facts: [{ counterparty_name: "虚构咖啡店", summary: "早餐", amount: 2500,
      currency_code: "CNY", occurred_time: "2026-09-23T08:00:00+08:00" }],
  }, rule);
  assert.match(requestDetail, /虚构咖啡店/);
  assert.match(requestDetail, /当前标签/);
  assert.match(requestDetail, /历史模型输入正文和原始输出未留存/);

  const settings = await context.testSettings();
  assert.match(settings, /只有显式隔离的合成验收库才会注册 CRON 扫描/);
  console.log("RESULT=PASS DEFAULT_NOT_SCHEDULED=1 SYNTHETIC_REGISTERED=1 RULE_DETAIL=1 REQUEST_DETAIL=1");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

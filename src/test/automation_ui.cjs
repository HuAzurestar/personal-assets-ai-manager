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
  scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "DISABLED", tasks: [],
};
const requestedUrls = [];
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
  request: async (url) => (requestedUrls.push(url), url.includes("auto_rule/list") ? { items: [rule] }
    : url.includes("auto_rule/1/summary") ? { ...rule, execution_success_count: "1", execution_success_rate: 1, decision_count: "0", acceptance_rate: null }
    : url.includes("auto_rule/1") ? rule
    : url.includes("assignment_request/list") ? { items: [suggestion], total: 1, page_index: 1, page_size: 20 }
    : url.includes("assignment_request/8") ? suggestion
    : url.includes("flow/12") ? ledger
    : url.includes("view/list") ? { items: [{ id: 1, name: "分类", status: "ACTIVE" }] }
    : url.includes("schedule/status") ? schedule : setting),
  jsonRequest: async () => {},
  $: () => null,
  $$: () => [],
  esc: (value) => String(value ?? ""),
  money: ({ amount, currency_code }) => `${currency_code} ${amount}`,
  URLSearchParams,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../frontend/js/view/automation_feedback.js"), "utf8")
  .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""), context);
vm.runInContext(`${code}\nglobalThis.testPanel = autoRulesPanel; globalThis.testSettings = automationSettingsPage; globalThis.testRuleDetail = ruleDetailMarkup; globalThis.testRequestDetail = requestDetailMarkup; globalThis.testScheduleExplanation = scheduleExplanation; globalThis.testRulePage = autoRulesPage; globalThis.testReviewPage = tagReviewPage; globalThis.testFilterParams = tagReviewFilterParams;`, context);

(async () => {
  const views = [{ id: 1, name: "分类", status: "ACTIVE" }];
  const idle = await context.testPanel(views);
  assert.match(idle, /没有已注册的自动标签任务/);
  assert.match(idle, /等待不会产生新建议/);
  assert.match(idle, /已保存·未调度/);
  assert.match(idle, /未注册到调度器/);
  assert.doesNotMatch(idle, /CRON 调度运行中/);

  schedule = {
    scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "SYNTHETIC_READY",
    tasks: [{ task_key: "tag-scan:1", queue_state: "IDLE", next_run_at: "2026-09-23T14:00:00+08:00", last_result: "COMPLETED" }],
  };
  const active = await context.testPanel(views);
  assert.match(active, /CRON 调度运行中 · 1 条规则已注册/);
  assert.match(active, /上次执行：完成/);
  assert.doesNotMatch(active, /已保存·未调度/);
  assert.match(active, /审查详情/);

  schedule.tag_scan_guard = "REAL_READY";
  const real = await context.testPanel(views);
  assert.match(real, /真实流水自动分析已启用/);
  assert.match(real, /新增 Ledger 将在后续 CRON 中检查/);
  assert.match(real, /批准或拒绝由人处理/);
  assert.match(real, /CRON 调度运行中 · 1 条规则已注册/);
  assert.doesNotMatch(real, /纯虚构验收库/);

  const detail = context.testRuleDetail(rule, schedule, { items: [{
    id: 8, ledger_id: 12, ledger_counterparty_name: "虚构商户", proposed_tag_name: "餐饮",
    status: 1, created_time: "2026-09-23T14:00:00+08:00",
  }] });
  assert.match(detail, /业务判断说明/);
  assert.match(detail, /虚构商户/);
  assert.match(detail, /历史模型请求正文与原始响应未被保存/);
  assert.match(context.testScheduleExplanation("ACCEPTANCE_DATABASE_REQUIRED"), /非虚构数据/);

  schedule = {
    scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "NON_SYNTHETIC_FACT",
    tasks: [{ task_key: "tag-scan:1", queue_state: "PAUSED", next_run_at: null,
      last_result: "FAILED", last_error_code: "ACCEPTANCE_DATABASE_REQUIRED" }],
  };
  const blocked = await context.testPanel(views);
  assert.match(blocked, /当前库不允许自动标签扫描/);
  assert.match(blocked, /安全阻断/);
  assert.match(blocked, /已注册任务会暂停且不再按 CRON 重试/);
  assert.doesNotMatch(blocked, /下一次触发/);
  const blockedDetail = context.testRuleDetail(rule, schedule, { items: [] });
  assert.match(blockedDetail, /安全门禁阻止/);
  assert.match(blockedDetail, /ACCEPTANCE_DATABASE_REQUIRED/);
  schedule.tasks = [];
  const blockedAfterRestart = await context.testPanel(views);
  assert.match(blockedAfterRestart, /当前库不允许自动标签扫描/);
  assert.doesNotMatch(blockedAfterRestart, /CRON 调度运行中 · 1 条规则已注册/);
  schedule = { scheduler_state: "RUNNING", worker_state: "HEALTHY",
    tag_scan_guard: "SYNTHETIC_READY", tasks: [] };

  const rulePage = await context.testRulePage(new URLSearchParams({ rule_id: "1" }));
  assert.match(rulePage, /返回规则列表/);
  assert.match(rulePage, /业务判断说明/);
  const reviewPage = await context.testReviewPage(new URLSearchParams({ request_id: "8" }));
  assert.match(reviewPage, /返回建议列表/);
  assert.match(reviewPage, /虚构咖啡店/);
  assert.match(reviewPage, /通过并打标/);

  const allParams = context.testFilterParams([["view_id", ""], ["rule_id", ""], ["status", ""]]);
  assert.equal(allParams.has("status"), true);
  assert.equal(allParams.get("status"), "");
  assert.equal(allParams.get("page"), "1");
  const allPage = await context.testReviewPage(allParams);
  assert.match(allPage, /全部状态/);
  assert.match(allPage, /data-request-page=/);
  assert.doesNotMatch(allPage, /<button[^>]*data-page=/); // Reserved for global navigation.
  assert.doesNotMatch(requestedUrls.at(-1), /filter=/);
  const pendingParams = context.testFilterParams([["status", "1"]]);
  await context.testReviewPage(pendingParams);
  assert.match(decodeURIComponent(requestedUrls.at(-1)), /"key":"status"/);
  const rejectedParams = context.testFilterParams([["status", "3"]]);
  await context.testReviewPage(rejectedParams);
  assert.match(decodeURIComponent(requestedUrls.at(-1)), /"val":3/);

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
  assert.match(settings, /真实流水自动分析须由服务显式启用/);
  console.log("RESULT=PASS DEFAULT_NOT_SCHEDULED=1 SYNTHETIC_REGISTERED=1 RULE_DETAIL=1 REQUEST_DETAIL=1");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

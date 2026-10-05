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
  view_id: 1, amount_mode: 1, cron: "*/10 * * * * *", scan_after_ledger_id: 1, scan_epoch: 3,
  analyzed_count: 1, failed_count: 0, suggested_count: 1,
  accepted_count: 0, rejected_count: 0,
  method_config: { model_id: 2, prompt: "只判断虚构早餐" },
};
const setting = {
  scan_enabled: true, scan_available: true,
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
  proposed_tag_name: "餐饮", view_id: 1, view_name: "消费分类", view_system_name: "category",
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
  request: async (url) => {
    requestedUrls.push(url);
    if (url.includes("auto_rule/list")) return { items: [rule] };
    if (url.includes("auto_rule/1/summary")) return { ...rule, execution_success_count: "1", execution_success_rate: 1, decision_count: "0", acceptance_rate: null };
    if (url.includes("auto_rule/1")) return rule;
    if (url.includes("assignment_request/list")) return { items: [suggestion], total: 1, page_index: 1, page_size: 20 };
    if (url.includes("assignment_request/8")) return suggestion;
    if (url.includes("flow/12")) return ledger;
    if (url.includes("view/list")) return { items: [{ id: 1, name: "分类", status: "ACTIVE" }] };
    if (url.includes("schedule/status")) return schedule;
    return setting;
  },
  jsonRequest: async () => {},
  $: () => null,
  $$: () => [],
  esc: (value) => String(value ?? ""),
  money: ({ amount, currency_code }) => `${currency_code} ${amount}`,
  date: (value) => String(value || "—"),
  URLSearchParams,
  registerInspection: () => {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../frontend/js/view/automation_explain.js"), "utf8")
  .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""), context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../frontend/js/view/automation_feedback.js"), "utf8")
  .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""), context);
vm.runInContext(`${code}\nglobalThis.testPanel = autoRulesPanel; globalThis.testSettings = automationSettingsPage; globalThis.testScanControl = scanControlMarkup; globalThis.testRuleDetail = ruleDetailMarkup; globalThis.testRequestDetail = requestDetailMarkup; globalThis.testScheduleExplanation = scheduleExplanation; globalThis.testRulePage = autoRulesPage; globalThis.testReviewPage = tagReviewPage; globalThis.testFilterParams = tagReviewFilterParams;`, context);

(async () => {
  // Two already-open confirmations must not submit overlapping commands.
  const dialogs = [];
  context.testOpenDialog = () => {
    const dialog = { close() {}, addEventListener() {} };
    dialogs.push(dialog);
    return dialog;
  };
  context.$ = (_selector, dialog) => dialog?.addEventListener ? {
    addEventListener: (_event, callback) => { dialog.confirm = callback; },
  } : null;
  vm.runInContext('openDialog = testOpenDialog', context);
  let batchCalls = 0;
  let rejectBatch;
  context.jsonRequest = () => { batchCalls++; return new Promise((_resolve, reject) => { rejectBatch = reject; }); };
  const root = { dataset: {} };
  const transition = vm.runInContext('transitionRequests', context);
  await transition(root, { dataset: { id: "1", operation: "approve" } }, async () => {});
  await transition(root, { dataset: { id: "2", operation: "approve" } }, async () => {});
  const firstBatch = dialogs[0].confirm({ currentTarget: {} });
  await dialogs[1].confirm({ currentTarget: {} });
  assert.equal(batchCalls, 1);
  rejectBatch(new Error("offline"));
  await firstBatch;
  assert.equal(root.dataset.commandPending, "false");
  context.$ = () => null;
  const views = [{ id: 1, name: "分类", status: "ACTIVE" }];
  const idle = await context.testPanel(views);
  assert.match(context.testScanControl(), /data-action="scan-toggle"/);
  assert.match(context.testScanControl(), /自动分析：已开启/);
  setting.scan_available = false;
  assert.match(context.testScanControl(), /自动分析：部署层已关闭/);
  assert.match(context.testScanControl(), /data-action="scan-toggle" disabled/);
  setting.scan_available = true;
  assert.match(idle, /自动分析已关闭/);
  assert.doesNotMatch(idle, /部署者|PAAM_AUTOTAG/);
  assert.match(idle, /配置已启用/);
  assert.doesNotMatch(idle, /data-preserve="schedule-help"/);
  assert.doesNotMatch(idle, /验收库混入/);
  assert.doesNotMatch(idle, /CRON 调度运行中/);

  schedule = {
    scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "SYNTHETIC_READY",
    tasks: [{ task_key: "tag-scan:1", queue_state: "IDLE", next_run_at: "2026-09-23T14:00:00+08:00", last_result: "COMPLETED" }],
  };
  const active = await context.testPanel(views);
  assert.match(active, /自动扫描已就绪 · 1 条规则/);
  assert.match(active, /上次执行：完成/);
  assert.doesNotMatch(active, /已保存·未调度/);
  assert.match(active, /规则详情/);
  assert.match(active, /href="#details\/auto-rule\?rule_id=1"/);
  assert.match(active, /查看待分析账目/);
  assert.match(active, /编辑规则/);
  assert.match(active, /累计通过/);
  assert.match(active, /aria-label="历史累计说明"/);
  assert.doesNotMatch(active, /data-preserve="lifecycle"/);

  schedule.tag_scan_guard = "REAL_READY";
  const real = await context.testPanel(views);
  assert.match(real, /自动扫描已就绪 · 1 条规则/);
  assert.match(real, /下次触发/);
  assert.match(real, /等待定时触发/);
  assert.doesNotMatch(real, /纯虚构验收库/);

  const detail = context.testRuleDetail(rule, schedule, { items: [{
    id: 8, ledger_id: 12, ledger_counterparty_name: "虚构商户", proposed_tag_name: "餐饮",
    status: 1, created_time: "2026-09-23T14:00:00+08:00",
  }] });
  assert.match(detail, /业务判断说明/);
  assert.match(detail, /虚构商户/);
  assert.match(detail, /data-action="disclosure-preview"/);
  assert.match(context.testScheduleExplanation("ACCEPTANCE_DATABASE_REQUIRED"), /非虚构数据/);
  const adapter = fs.readFileSync(path.resolve(__dirname, "../backend/service/llm_adapter.py"), "utf8");
  const backendCodes = [...adapter.matchAll(/_error\(\s*"([A-Z_]+)"/g)].map(match => match[1]);
  const scan = fs.readFileSync(path.resolve(__dirname, "../backend/service/auto_tag_scan_service.py"), "utf8");
  backendCodes.push(...[...scan.matchAll(/, "((?:RULE_|VIEW_|MODEL_)[A-Z_]+|NO_ACTIVE_TARGETS)"\)/g)].map(match => match[1]));
  backendCodes.push(...[...scan.matchAll(/return report\("([A-Z_]+)"\)/g)].map(match => match[1]).filter(code => !["NO_DATA", "PAGE_COMPLETE", "RETRY_DEFERRED", "RULE_TOKEN_CHANGED"].includes(code)));
  for (const errorCode of new Set([...backendCodes, "RULE_NOT_FOUND", "RULE_DISABLED", "VIEW_INACTIVE", "SOFT_BUDGET_EXHAUSTED", "COUNTER_EXHAUSTED", "COMMIT_FAILED", "RULE_TOKEN_CHANGED", "CANCELLED", "AUDIT_STORAGE_ERROR", "AUDIT_FINISH_FAILED", "OPERATION_BLOCKED"])) {
    assert.doesNotMatch(context.testScheduleExplanation(errorCode), /执行失败，请查看服务端日志/, errorCode);
  }
  assert.doesNotMatch(code, /RATE_LIMITED:|REGISTRATION_FAILED:/);
  assert.equal(vm.runInContext('numberOrNull("3", true)', context), 3);
  assert.equal(vm.runInContext('numberOrNull("", true)', context), null);
  assert.throws(() => vm.runInContext('numberOrNull("3.9", true)', context), /整数/);

  schedule = {
    scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "NON_SYNTHETIC_FACT",
    tasks: [{ task_key: "tag-scan:1", queue_state: "PAUSED", next_run_at: null,
      last_result: "FAILED", last_error_code: "ACCEPTANCE_DATABASE_REQUIRED" }],
  };
  const blocked = await context.testPanel(views);
  assert.match(blocked, /当前库不允许自动标签扫描/);
  assert.match(blocked, /安全阻断/);
  assert.match(blocked, /扫描已暂停/);
  assert.doesNotMatch(blocked, /下一次触发/);
  const blockedDetail = context.testRuleDetail(rule, schedule, { items: [] });
  assert.match(blockedDetail, /安全阻断/);
  assert.match(blockedDetail, /ACCEPTANCE_DATABASE_REQUIRED/);
  schedule.tasks = [];
  const blockedAfterRestart = await context.testPanel(views);
  assert.match(blockedAfterRestart, /当前库不允许自动标签扫描/);
  assert.doesNotMatch(blockedAfterRestart, /CRON 调度运行中 · 1 条规则已注册/);
  schedule = { scheduler_state: "RUNNING", worker_state: "HEALTHY",
    tag_scan_guard: "SYNTHETIC_READY", tasks: [] };

  const rulePage = await context.testRulePage(new URLSearchParams({ rule_id: "1" }));
  assert.match(rulePage, /data-open-rule="1"/);
  assert.match(rulePage, /data-auto-rule-rows/);
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
  assert.doesNotMatch(allPage, /data-request-page=/);
  assert.match(context.requestPagerMarkup({total:21,page_size:20,page_index:1}), /data-request-page="2"/);
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
  assert.doesNotMatch(requestDetail, /MANUAL|AUTO_RULE/);
  for (const relative of ["view/automation.js", "view/disclosure.js", "view/ledger.js", "component/inspection.js"]) {
    const source = fs.readFileSync(path.resolve(__dirname, "../frontend/js", relative), "utf8");
    assert.match(source, /from "\.\.\/api\/client\.js"/);
    assert.doesNotMatch(source, /client\.js\?/);
  }
  assert.match(requestDetail, /aria-label="建议依据"/);
  assert.match(requestDetail, /原始响应未保存/);

  const settings = await context.testSettings();
  assert.match(settings, /data-auto-notice/);
  assert.match(settings, /data-auto-diagnostics/);
  assert.doesNotMatch(settings, /<details/);
  assert.match(settings, /测试真实连接/);
  assert.match(settings, /可能计费/);
  assert.equal((settings.match(/class="automation-section"/g) || []).length, 5);
  assert.match(settings, /公共 Prompt/);
  assert.match(settings, /data-action="prompt-library"/);
  assert.equal((settings.match(/data-auto-notice/g) || []).length, 1);
  assert.doesNotMatch(settings, /interaction-demo|M2 交互演示|automation-hero/);

  // Current execution is distinct from the configuration switch and last outcome.
  const model = setting.models[0];
  for (const [queue_state, label] of Object.entries({ RUNNING: "分析中", QUEUED: "排队中", IDLE: "等待定时触发", PAUSED: "已暂停", BLOCKED: "执行受阻" })) {
    const snapshot = { scheduler_state: "RUNNING", worker_state: "HEALTHY", tag_scan_guard: "REAL_READY",
      tasks: [{ task_key: "tag-scan:1", queue_state, queue_position: 3, last_result: "FAILED" }] };
    assert.equal(context.ruleExecution(rule, snapshot, model, views[0]).label, label);
    if (queue_state === "QUEUED") assert.match(context.ruleExecution(rule, snapshot, model, views[0]).reason, /第 3 位/);
  }
  assert.equal(context.ruleExecution(rule, null, model, views[0]).label, "状态未知");
  assert.equal(context.ruleExecution({ ...rule, enabled: false }, null, model, views[0]).label, "已停用");
  assert.equal(context.ruleExecution(rule, schedule, { ...model, enabled: false }, views[0]).label, "模型不可用");

  for (const enabled of [true, false]) {
    context.overrideSetting = { ...setting, scan_enabled: !enabled, config_state: {
      sections: [{ section: "scan-control", effective_value: enabled, overridden: true }],
      apply: { status: "APPLIED", available: true },
    } };
    vm.runInContext('setting = overrideSetting', context);
    const control = vm.runInContext('scanControlMarkup()', context);
    assert.match(control, new RegExp(`自动分析：${enabled ? "已开启" : "已关闭"}`));
    assert.match(control, /有效值由部署覆盖/);
    assert.match(control, /data-action="scan-toggle" disabled/);
  }
  context.overrideSetting = setting;
  vm.runInContext('setting = overrideSetting', context);

  const draft = { prompt: rule.method_config.prompt, model_id: "2", amount_mode: "1", name: "仅改名", cron: "* * * * *", enabled: false };
  assert.equal(context.semanticRuleChange(rule, draft), false);
  assert.equal(context.semanticRuleChange(rule, { ...draft, prompt: `  ${draft.prompt}\n` }), false);
  for (const change of [{ prompt: "新判断" }, { model_id: 3 }, { amount_mode: 2 }]) {
    const changed = { ...draft, ...change };
    assert.equal(context.semanticRuleChange(rule, changed), true);
    assert.match(context.ruleEditImpact(rule, changed, 7), /当前 7 条待确认建议将被取消/);
    assert.match(context.ruleEditImpact(rule, changed, null), /数量尚未读取/);
  }
  assert.match(context.ruleEditImpact(rule, draft, 7), /不取消已有建议/);

  const revised = { ...rule, rule_revision: 4 };
  assert.match(context.requestVersionCopy(suggestion, revised), /版本已过期，不能通过/);
  for (const status of [2, 3, 4, 5]) {
    const historical = context.testRequestDetail({ ...suggestion, status }, ledger, revised);
    assert.match(historical, /仅供追溯/);
    assert.match(historical, /当前判断说明（非历史快照）/);
    assert.doesNotMatch(historical, /通过时会因版本变化而拒绝|data-operation="approve"/);
  }
  const related = { pending: { total: 2, items: [suggestion, { ...suggestion, id: 9, proposed_tag_name: "交通" }] }, approved: { total: 0, items: [] } };
  const decision = context.testRequestDetail(suggestion, ledger, rule, related);
  assert.match(decision, /其他待确认 1 条/);
  assert.match(decision, /其他待确认建议.*取消/);
  assert.match(decision, /原已通过建议.*替换/);
  assert.match(decision, /data-id="9"/);
  const staleDecision = context.testRequestDetail(suggestion, ledger, revised, related);
  assert.match(staleDecision, /data-operation="approve"[^>]*disabled/);
  const missingScope = context.testRequestDetail(suggestion, ledger, rule);
  assert.match(missingScope, /影响数量未知/);
  assert.match(missingScope, /data-operation="approve"[^>]*disabled/);

  await context.testReviewPage(new URLSearchParams({ ledger_id: "12", view_id: "1", status: "" }));
  assert.match(decodeURIComponent(requestedUrls.at(-1)), /"key":"ledger_id","op":"=","val":12/);
  assert.match(decodeURIComponent(requestedUrls.at(-1)), /"key":"view_id","op":"=","val":1/);
  const preview = context.candidatePreviewMarkup({ scan_after_ledger_id: 42, inspected_count: 100, eligible_count: 21,
    reason_counts: { ELIGIBLE: 21, ALREADY_CLASSIFIED: 79 }, samples: [{ ledger_id: 43, amount: 2500, currency_code: "CNY", occurred_time: suggestion.created_time, counterparty_name: "合成早餐店", summary: "测试午餐" }] });
  assert.match(preview, /已扫描至 #42/);
  assert.match(preview, /最多 100 条/);
  assert.match(preview, /最多 20 条/);
  assert.match(preview, /满足本地筛选条件/);
  assert.match(preview, /合成早餐店/);
  assert.match(preview, /data-preview-ledger="43"/);
  assert.doesNotMatch(preview, /SIMULATED_LOCAL|ELIGIBLE/);
  let optionPages = 0;
  context.request = async () => ({ items: [{ id: ++optionPages }], total: optionPages === 1 ? 2 : 1000 });
  const options = await vm.runInContext('readListOptions("/test", undefined)', context);
  assert.equal(optionPages, 2);
  assert.equal(options.items.length, 2);
  console.log("RESULT=PASS DEFAULT_NOT_SCHEDULED=1 SYNTHETIC_REGISTERED=1 RULE_DETAIL=1 REQUEST_DETAIL=1");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

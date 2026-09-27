import { request, jsonRequest } from "../api/client.js?v=20260927.1";
import { $, $$, esc, money } from "../util/core.js";
import { startVisiblePoll } from "../util/visible_poll.js?v=20260927.1";
import { openDisclosureEditor, openDisclosurePreview } from "./disclosure.js?v=20260927.1";
import { batchResults, batchResultMarkup, batchFailureMarkup, selectionConflict, ruleStatistics, runtimeMarkup, diagnosticsMarkup, interactionScenarios, interactionMarkup } from "./automation_feedback.js?v=20260927.1";

let setting = null;
let rules = [];
let views = [];
let scheduleStatus = null;
let stopPolling = () => {};
let requestItems = [];
let batchFeedback = "";
const boundRoots = new WeakSet();
const ruleListUrl = "/paam/tag/v1/auto_rule/list?page_index=1&page_size=100&sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D";
const freshnessMarkup = '<p class="automation-freshness" data-auto-freshness role="status" aria-live="polite">已读取页面数据；可见页每 5 秒刷新业务状态，编辑或确认期间暂停更新。</p>';

export function stopAutomationPolling() { stopPolling(); stopPolling = () => {}; }

const amountModeNames = { 1: "金额区间（BAND）", 2: "精确金额（EXACT）", 3: "不发送金额（NONE）" };

function statusPill(enabled, available = true, unavailableCopy = "缺少密钥") {
  const active = enabled && available;
  const copy = !available ? unavailableCopy : enabled ? "已启用" : "已停用";
  return `<span class="automation-status ${active ? "active" : "inactive"}"><i></i>${copy}</span>`;
}

function modelSummary(model) {
  const params = model.litellm_params;
  const details = [
    ["模型", params.model],
    ["API 地址", params.api_base],
    ["温度", params.temperature ?? "默认"],
    ["最大输出", params.max_tokens ?? "默认"],
    ["超时", params.timeout == null ? "默认 60 秒" : `${params.timeout} 秒`],
  ].map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("");
  return `<article class="automation-card" data-model-card="${model.id}">
    <header><div><span class="eyebrow">MODEL #${model.id}</span><h3>${esc(model.name)}</h3></div>${statusPill(model.enabled, model.key_configured)}</header>
    <dl class="automation-definition">${details}</dl>
    <div class="automation-card-note"><span>API Key</span><strong>${model.key_configured ? "已安全配置，不会回显" : "尚未配置"}</strong></div>
    <div class="automation-actions">
      <button type="button" class="quiet" data-action="model-test" data-id="${model.id}">配置检查（模拟）</button>
      ${model.key_configured ? `<button type="button" class="quiet danger-text" data-action="model-secret-delete" data-id="${model.id}">清除密钥</button>` : ""}
      <button type="button" data-action="model-edit" data-id="${model.id}">编辑</button>
    </div>
    <div class="automation-result" data-model-result="${model.id}" aria-live="polite" hidden></div>
  </article>`;
}

export async function automationSettingsPage() {
  setting = await request("/paam/system/v1/setting/automation");
  const modelCards = setting.models.map(modelSummary).join("");
  const bands = Object.entries(setting.disclosure?.amount_bands || {}).map(([currency, config]) =>
    `<div class="disclosure-row"><strong>${esc(currency)}</strong><span>${esc((config.boundaries || []).join(" → "))}</span></div>`,
  ).join("");
  scheduleStatus = await request("/paam/system/v1/schedule/status").catch(() => null);
  const diagnostics = await readScheduleDiagnostics().catch(() => null);
  return `<div class="automation-page" data-auto-page="settings">
    <section class="automation-hero">
      <div><span class="eyebrow">SETTINGS · AUTOMATION</span><h2>自动化设置</h2><p>模型连接保存在本地设置中；密钥进入系统凭据存储，普通读取永不回显。</p></div>
      <button type="button" class="primary" data-action="model-new">＋ 添加模型</button>
    </section>
    <div class="automation-notice" role="note"><strong>模型安全边界</strong><span>真实流水自动分析须由服务显式启用；请在自动规则页查看实际调度状态。启用后会按规则调用真实模型并可能产生费用，只生成待人工处理的打标申请。API Key 只从系统凭据存储读取，不写入 SQLite，也不会回显。</span></div>
    <section class="automation-section" aria-labelledby="automation-model-title">
      <div class="automation-section-head"><div><h3 id="automation-model-title">模型连接</h3><p>${setting.models.length ? `共 ${setting.models.length} 个本地配置` : "添加首个模型后，规则才可选择模型"}</p></div></div>
      <div class="automation-grid">${modelCards || '<div class="panel empty-state"><strong>尚未配置模型</strong><p>添加一个 LiteLLM 兼容配置；启用前必须保存 API Key。</p><button type="button" data-action="model-new">添加模型</button></div>'}</div>
    </section>
    <section class="automation-section" aria-labelledby="automation-disclosure-title">
      <div class="automation-section-head"><div><h3 id="automation-disclosure-title">金额披露</h3><p>全局按币种分档；每条规则独立选择 BAND / EXACT / NONE。</p></div><div class="automation-actions"><button type="button" class="quiet" data-action="disclosure-preview">虚构披露预览</button><button type="button" data-action="disclosure-edit">编辑披露策略</button></div></div>
      <div class="disclosure-card"><div><span>时间粒度</span><strong>${esc(setting.disclosure?.date_granularity || "未配置")}</strong></div><div><span>按币种区间（最小货币单位）</span>${bands || "<strong>尚未配置</strong>"}</div></div>
    </section>
    <section class="automation-section"><h3>调度运行状态</h3>${freshnessMarkup}<div data-auto-runtime>${runtimeMarkup(scheduleStatus)}</div><div data-auto-diagnostics>${diagnosticsMarkup(diagnostics)}</div><button type="button" class="quiet" data-action="interaction-demo">M2 交互演示（虚构，不写数据）</button></section>
  </div>`;
}

function ruleRow(rule) {
  const view = views.find((item) => item.id === rule.view_id);
  const model = setting?.models.find((item) => item.id === rule.method_config.model_id);
  const modelAvailable = Boolean(model?.enabled && model?.key_configured);
  const unavailableCopy = !model ? "模型不可用" : !model.enabled ? "模型已停用" : "缺少密钥";
  const counts = [
    ["已分析", rule.analyzed_count], ["失败", rule.failed_count], ["建议", rule.suggested_count],
    ["已启用", rule.accepted_count], ["已拒绝", rule.rejected_count],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${esc(value)}</strong></div>`).join("");
  const scheduleTask = scheduleStatus?.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  const schedulerRunning = scheduleStatus?.scheduler_state === "RUNNING" && scheduleStatus?.worker_state === "HEALTHY";
  const ruleStatus = !rule.enabled ? statusPill(false)
    : !modelAvailable ? statusPill(true, false, unavailableCopy)
    : scheduleStatus?.tag_scan_guard === "DISABLED" ? statusPill(true, false, "已保存·服务未开启扫描")
    : scheduleStatus?.tag_scan_guard === "NON_SYNTHETIC_FACT" ? statusPill(true, false, "安全阻断")
    : !scheduleTask ? statusPill(true, false, "已保存·未调度")
    : scheduleTask.queue_state === "PAUSED" ? statusPill(true, false, "扫描已暂停")
    : scheduleTask.queue_state === "BLOCKED" ? statusPill(true, false, "调度阻塞·查看诊断")
    : !schedulerRunning ? statusPill(true, false, "调度已停止")
    : statusPill(true);
  const resultNames = { COMPLETED: "完成", PARTIAL_FAILURE: "部分失败", FAILED: "失败", CANCELLED: "已取消" };
  const lastRun = scheduleTask?.last_result
    ? `上次执行：${resultNames[scheduleTask.last_result] || scheduleTask.last_result}${scheduleTask.last_error_code ? ` · ${scheduleTask.last_error_code === "ACCEPTANCE_DATABASE_REQUIRED" ? "验收库含非虚构记录" : scheduleTask.last_error_code}` : ""}`
    : scheduleTask ? "尚未执行"
    : scheduleStatus?.tag_scan_guard === "DISABLED" ? "服务扫描开关关闭，规则未注册"
    : "未注册到调度器";
  return `<tr data-rule-row="${rule.id}">
    <td class="rule-identity"><span class="eyebrow">RULE #${rule.id} · REV ${rule.rule_revision}</span><strong>${esc(rule.name)}</strong><small>${esc(rule.method_config.prompt)}</small></td>
    <td><strong>${esc(view?.name || `View #${rule.view_id}`)}</strong><small>${esc(model?.name || `Model #${rule.method_config.model_id}`)}</small></td>
    <td class="rule-schedule"><code>${esc(rule.cron || "未设置")}</code><small>Ledger #${rule.scan_after_ledger_id} · Epoch ${rule.scan_epoch}</small><small>${esc(lastRun)}</small></td>
    <td><div class="rule-counts">${counts}</div></td>
    <td class="rule-operation">${ruleStatus}<div class="automation-actions"><button type="button" class="quiet" data-action="rule-detail" data-id="${rule.id}">审查详情</button><button type="button" class="quiet" data-action="rule-preview" data-id="${rule.id}">资格预览</button><button type="button" data-action="rule-edit" data-id="${rule.id}">编辑</button></div></td>
  </tr>`;
}

function scheduleNotice() {
  const tasks = (scheduleStatus?.tasks || []).filter((item) => item.task_key.startsWith("tag-scan:"));
  const disabled = scheduleStatus?.tag_scan_guard === "DISABLED";
  const blockedByData = scheduleStatus?.tag_scan_guard === "NON_SYNTHETIC_FACT";
  const realAnalysis = scheduleStatus?.tag_scan_guard === "REAL_READY";
  const running = scheduleStatus?.scheduler_state === "RUNNING" && scheduleStatus?.worker_state === "HEALTHY";
  const nextRuns = tasks.map((item) => item.next_run_at).filter(Boolean).sort();
  const failed = tasks.filter((item) => ["FAILED", "PARTIAL_FAILURE"].includes(item.last_result)).length;
  const title = !scheduleStatus ? "暂时无法读取调度状态"
    : disabled ? "服务未启用自动标签扫描"
    : blockedByData ? "当前库不允许自动标签扫描"
    : !tasks.length ? "没有已注册的自动标签任务"
    : running ? `CRON 调度运行中 · ${tasks.length} 条规则已注册` : "CRON 调度未运行";
  const next = !scheduleStatus ? "请检查服务连接并刷新；不能据此判断规则已停用"
    : disabled ? "等待不会产生新建议。服务级扫描开关关闭，保存规则或模型密钥不会自动开启。启用真实账单分析需设置 PAAM_AUTOTAG_REAL_ANALYSIS=1 并重启服务；请先确认外发授权和模型费用。无需重新导入数据"
    : blockedByData
    ? "验收库含非虚构记录；安全门禁在模型调用前阻断扫描，已注册任务会暂停且不再按 CRON 重试。请改用独立的纯虚构验收库"
    : !tasks.length
    ? realAnalysis ? "真实流水分析已启用，但没有启用的规则；请配置模型和自动规则" : "等待不会产生新建议。默认模式不扫描账单；纯虚构验收库混入普通记录后也不会注册标签任务。规则已启用不等于已调度"
    : nextRuns.length ? `下一次触发：${new Date(nextRuns[0]).toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong" })}` : "当前没有待触发的启用规则";
  const failure = failed && !blockedByData ? `；${failed} 条规则上次执行失败，请打开规则详情查看具体原因` : "";
  const mode = realAnalysis ? "真实流水自动分析已启用：新增 Ledger 将在后续 CRON 中检查；真实模型调用可能计费，只生成待审申请，批准或拒绝由人处理。" : "";
  return `<div class="automation-notice compact" role="status" aria-live="polite"><strong>${esc(title)}</strong><span>${esc(mode + next + failure)}。CRON 触发进入共享 FIFO；本页仍不提供手动执行或重扫入口。</span></div>`;
}

export async function autoRulesPanel(tagViews) {
  views = tagViews;
  const [rulePage, currentSetting, currentSchedule] = await Promise.all([
    request(ruleListUrl),
    request("/paam/system/v1/setting/automation"),
    request("/paam/system/v1/schedule/status"),
  ]);
  rules = rulePage.items;
  setting = currentSetting;
  scheduleStatus = currentSchedule;
  const canCreate = views.some((item) => item.status === "ACTIVE") && setting.models.some((item) => item.enabled && item.key_configured);
  return `<section class="tag-manager automation-rules" aria-labelledby="auto-rule-title">
    <div class="tag-manager-head"><div><h2 id="auto-rule-title">自动打标签规则</h2><p>同一标签维度可配置多条独立规则；配置和进度从 SQLite 实时读取。</p></div><button type="button" class="primary" data-action="rule-new" ${canCreate ? "" : 'disabled title="需要启用中的标签维度和模型"'}>＋ 新建规则</button></div>
    ${freshnessMarkup}<div data-auto-notice>${scheduleNotice()}</div>
    <div class="automation-table-wrap"><table class="automation-table rule-table"><thead><tr><th>规则</th><th>View / 模型</th><th>调度 / 进度</th><th>累计统计</th><th>状态 / 操作</th></tr></thead><tbody data-auto-rule-rows>${rules.map(ruleRow).join("") || '<tr><td colspan="5" class="table-empty"><strong>尚未创建自动规则</strong><span>启用模型并准备标签维度后即可保存第一条规则。</span></td></tr>'}</tbody></table></div>
  </section>`;
}

export async function autoRulesPage(params = new URLSearchParams()) {
  const ruleId = Number(params.get("rule_id"));
  if (Number.isSafeInteger(ruleId) && ruleId > 0) {
    const query = new URLSearchParams({
      page_index: "1", page_size: "5",
      filter: JSON.stringify({ key: "rule_id", op: "=", val: ruleId }),
      sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
    });
    const [rule, viewPage, currentSetting, schedule, recent, summary] = await Promise.all([
      request(`/paam/tag/v1/auto_rule/${ruleId}`),
      request("/paam/tag/v1/view/list?page_index=1&page_size=100"),
      request("/paam/system/v1/setting/automation"),
      request("/paam/system/v1/schedule/status"),
      request(`/paam/tag/v1/assignment_request/list?${query}`),
      request(`/paam/tag/v1/auto_rule/${ruleId}/summary`),
    ]);
    views = viewPage.items;
    setting = currentSetting;
    const diagnosticOptions = { taskKey: `tag-scan:${ruleId}` };
    const diagnostics = await readScheduleDiagnostics(diagnosticOptions).catch(() => null);
    return `<div class="auto-rules-page automation-detail-page" data-auto-page="rule" data-rule-id="${ruleId}" data-recent-query="${esc(query.toString())}"><button type="button" class="quiet" data-action="rule-detail-back">← 返回规则列表</button><h2>规则 #${ruleId} · ${esc(rule.name)}</h2>${freshnessMarkup}<div data-auto-rule-detail>${ruleDetailMarkup(rule, schedule, recent, summary)}</div><div data-auto-diagnostics>${diagnosticsMarkup(diagnostics, diagnosticOptions)}</div></div>`;
  }
  const viewPage = await request("/paam/tag/v1/view/list?page_index=1&page_size=100");
  return `<div class="auto-rules-page" data-auto-page="rules">${await autoRulesPanel(viewPage.items)}</div>`;
}

const requestStatusNames = {
  1: "待确认", 2: "已通过", 3: "已拒绝", 4: "已取消", 5: "已替换",
};

function requestStatusPill(status) {
  const active = Number(status) === 2;
  const pending = Number(status) === 1;
  return `<span class="automation-status ${active ? "active" : pending ? "pending" : "inactive"}"><i></i>${esc(requestStatusNames[status] || status)}</span>`;
}

function requestRowsMarkup(items) {
  return items.map((item) => `<tr data-tag-request-row="${item.id}">
    <td><label class="request-selector"><input type="checkbox" data-tag-request-select value="${item.id}" ${item.status === 1 ? "" : "disabled"}><span><strong>Request #${item.id} · ${esc(item.ledger_counterparty_name || "未记录交易对方")}</strong><small>${esc(item.ledger_summary || "无账目摘要；请查看详情")}</small></span></label></td>
    <td><strong>${item.ledger_amount == null ? `Ledger #${item.ledger_id}` : esc(money({ amount: item.ledger_amount, currency_code: item.ledger_currency_code }))}</strong><small>Ledger #${item.ledger_id} · ${esc(item.view_name)} · ${item.ledger_active ? "当前有效" : "Ledger 已失效"}</small></td>
    <td><strong>${esc(item.proposed_tag_name)}</strong><small>${esc(item.proposed_tag_system_name)}</small></td>
    <td><strong>${esc(item.rule_name)}</strong><small>Rule #${item.rule_id} · Revision ${item.rule_revision}</small></td>
    <td>${requestStatusPill(item.status)}</td>
    <td><div class="automation-actions request-actions"><button type="button" class="quiet" data-action="tag-request-detail" data-id="${item.id}">查看依据</button>${requestActionsMarkup(item)}</div></td>
  </tr>`).join("") || '<tr><td colspan="6" class="table-empty"><strong>没有符合条件的建议请求</strong><span>可选择「全部状态」查看已处理建议；只有已注册的规则扫描任务才会自动产生新建议。</span></td></tr>';
}

function requestActionsMarkup(item) {
  return item.status === 1 ? `<button type="button" class="primary" data-action="tag-request-transition" data-operation="approve" data-id="${item.id}">通过并打标</button><button type="button" class="quiet" data-action="tag-request-transition" data-operation="reject" data-id="${item.id}">拒绝</button>` : "";
}

function requestPagerMarkup(result) {
  const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
  const page = result.page_index;
  return `<div class="request-pager"><span>共 ${result.total} 条 · 第 ${page}/${lastPage} 页</span><div><button type="button" class="quiet" data-action="tag-request-page" data-request-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><button type="button" class="quiet" data-action="tag-request-page" data-request-page="${page + 1}" ${page >= lastPage ? "disabled" : ""}>下一页</button></div></div>`;
}

export async function tagReviewPage(params = new URLSearchParams()) {
  const requestId = Number(params.get("request_id"));
  if (Number.isSafeInteger(requestId) && requestId > 0) {
    const item = await request(`/paam/tag/v1/assignment_request/${requestId}`);
    const [ledger, rule] = await Promise.all([
      request(`/paam/ledger/v1/flow/${item.ledger_id}`),
      request(`/paam/tag/v1/auto_rule/${item.rule_id}`),
    ]);
    requestItems = [item];
    return `<div class="tag-review-page automation-detail-page" data-auto-page="request" data-request-id="${requestId}"><button type="button" class="quiet" data-action="tag-request-detail-back">← 返回建议列表</button><h2>建议请求 #${requestId} · 判断依据</h2>${freshnessMarkup}<div data-batch-feedback>${batchFeedback}</div><div data-auto-request-detail>${requestDetailMarkup(item, ledger, rule)}<div class="automation-actions">${requestActionsMarkup(item)}</div></div></div>`;
  }
  const page = Math.max(1, Number(params.get("page") || 1));
  const pageSize = Math.min(100, Math.max(1, Number(params.get("page_size") || 20)));
  const viewId = params.get("view_id") || "";
  const ruleId = params.get("rule_id") || "";
  const status = params.has("status") ? params.get("status") : "1";
  const expressions = [];
  if (viewId) expressions.push({ key: "view_id", op: "=", val: Number(viewId) });
  if (ruleId) expressions.push({ key: "rule_id", op: "=", val: Number(ruleId) });
  if (status) expressions.push({ key: "status", op: "=", val: Number(status) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: String(page), page_size: String(pageSize),
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  const [viewPage, rulePage] = await Promise.all([
    request("/paam/tag/v1/view/list?page_index=1&page_size=100"),
    request("/paam/tag/v1/auto_rule/list?page_index=1&page_size=100&sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D"),
  ]);
  const result = await request(`/paam/tag/v1/assignment_request/list?${query}`);
  requestItems = result.items;
  const viewOptions = viewPage.items.map((item) => `<option value="${item.id}" ${String(item.id) === viewId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  const ruleOptions = rulePage.items.map((item) => `<option value="${item.id}" ${String(item.id) === ruleId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  return `<div class="tag-review-page" data-auto-page="requests" data-request-query="${esc(query.toString())}">
    <section class="tag-manager automation-requests" aria-labelledby="tag-request-title">
      <div class="tag-manager-head"><div><h2 id="tag-request-title">标签建议请求</h2><p>列表展示交易对方、摘要和金额；点击「查看依据」核对账目、现有标签与建议理由后再决定。</p></div><div class="request-batch-actions"><button type="button" class="primary" data-action="tag-request-batch" data-operation="approve" disabled>批量通过</button><button type="button" class="quiet" data-action="tag-request-batch" data-operation="reject" disabled>批量拒绝</button></div></div>
      <div class="automation-notice compact" role="status" aria-live="polite"><strong>人工确认边界</strong><span>通过时会重新核对规则版本、Ledger、View、候选标签和当前人工标签；冲突不会强制覆盖。请求超时请刷新核对状态。</span></div>
      ${freshnessMarkup}<div data-batch-feedback>${batchFeedback}</div>
      <div class="review-filter-skeleton" aria-labelledby="tag-review-filter-title">
        <form class="form-grid three" data-form="tag-request-filter">
          <label>标签维度<select name="view_id"><option value="">全部 View</option>${viewOptions}</select></label>
          <label>来源规则<select name="rule_id"><option value="">全部规则</option>${ruleOptions}</select></label>
          <label>建议状态<select name="status"><option value="" ${status === "" ? "selected" : ""}>全部状态</option>${Object.entries(requestStatusNames).map(([value, label]) => `<option value="${value}" ${status === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        </form>
      </div>
      <div class="automation-table-wrap"><table class="automation-table request-table" aria-labelledby="tag-review-list-title"><thead><tr><th id="tag-review-list-title"><label class="request-select-all"><input type="checkbox" data-tag-request-select-all> 请求</label></th><th>Ledger / View</th><th>建议标签</th><th>来源规则</th><th>状态</th><th>操作</th></tr></thead><tbody data-auto-request-rows>${requestRowsMarkup(result.items)}</tbody></table></div>
      <div data-auto-request-pager>${requestPagerMarkup(result)}</div>
    </section>
  </div>`;
}

function openDialog(title, body) {
  const dialog = document.createElement("dialog");
  dialog.className = "wide automation-dialog";
  dialog.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button type="button" data-close aria-label="关闭">关闭</button></div><div class="dialog-body">${body}</div>`;
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
  });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function extrasFor(model) {
  const { model: _model, api_base: _base, temperature: _temperature, max_tokens: _tokens, timeout: _timeout, ...extra } = model?.litellm_params || {};
  return Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
}

function modelDialog(model = null) {
  const nextId = Math.max(0, ...(setting?.models || []).map((item) => item.id)) + 1;
  const value = model || { id: nextId, name: "", enabled: false, key_configured: false, litellm_params: {} };
  const params = value.litellm_params;
  const dialog = openDialog(model ? `编辑模型 #${model.id}` : "添加模型", `<form data-form="automation-model" data-id="${value.id}" class="stack automation-form">
    <div class="form-error-slot" aria-live="assertive"></div>
    <div class="form-grid"><label>显示名称<input name="name" required maxlength="120" value="${esc(value.name)}" placeholder="例如：本地 Qwen"></label><label>模型 ID<input value="${value.id}" disabled><small>稳定 ID 不会因改名改变。</small></label></div>
    <label>LiteLLM 模型名<input name="model" required maxlength="512" value="${esc(params.model || "")}" placeholder="openai/Qwen/Qwen3-8B"></label>
    <label>HTTPS API 地址<input name="api_base" type="url" required maxlength="2048" pattern="https://.*" value="${esc(params.api_base || "")}" placeholder="https://api.example.test/v1"></label>
    <div class="form-grid three"><label>Temperature<input name="temperature" type="number" step="any" value="${params.temperature ?? ""}" placeholder="供应商默认"></label><label>Max tokens<input name="max_tokens" type="number" min="1" step="1" value="${params.max_tokens ?? ""}" placeholder="供应商默认"></label><label>Timeout（秒）<input name="timeout" type="number" min="0.001" step="any" value="${params.timeout ?? ""}" placeholder="供应商默认"></label></div>
    <label>其他供应商参数（JSON 对象）<textarea name="extras" rows="5" placeholder='{"extra_body":{"enable_thinking":false}}'>${esc(extrasFor(value))}</textarea><small>保留 0 / false；禁止在这里写 api_key、password 或 secret。</small></label>
    <label>API Key（可选）<input name="secret" type="password" autocomplete="new-password" placeholder="${value.key_configured ? "留空则保持现有密钥" : "保存到系统凭据存储，不写入 SQLite"}"></label>
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用此模型</label>
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存并回读</button></div>
  </form>`);
  $("[data-form='automation-model']", dialog).addEventListener("submit", submitModel);
}

function numberOrNull(value, integer = false) {
  if (String(value).trim() === "") return null;
  const parsed = integer ? Number.parseInt(value, 10) : Number(value);
  if (!Number.isFinite(parsed)) throw new Error("数值参数格式不正确");
  return parsed;
}

function showFormError(form, error) {
  const slot = $(".form-error-slot", form);
  if (slot) slot.innerHTML = `<div class="error" role="alert">${esc(error.message)}</div>`;
}

async function saveModels(models, expectedUpdatedTime) {
  return jsonRequest("/paam/system/v1/setting/automation", "PUT", { expected_updated_time: expectedUpdatedTime, models });
}

async function submitModel(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type='submit']", form);
  button.disabled = true;
  try {
    const data = new FormData(form);
    const id = Number(form.dataset.id);
    const existing = setting.models.find((item) => item.id === id);
    const extra = data.get("extras") ? JSON.parse(data.get("extras")) : {};
    if (!extra || Array.isArray(extra) || typeof extra !== "object") throw new Error("其他供应商参数必须是 JSON 对象");
    const litellmParams = {
      ...extra,
      model: String(data.get("model")), api_base: String(data.get("api_base")),
      temperature: numberOrNull(data.get("temperature")),
      max_tokens: numberOrNull(data.get("max_tokens"), true),
      timeout: numberOrNull(data.get("timeout")),
    };
    const desired = { id, name: String(data.get("name")), enabled: data.has("enabled"), litellm_params: litellmParams };
    const secret = String(data.get("secret") || "");
    if (!existing && desired.enabled && !secret.trim()) throw new Error("新模型启用前必须填写 API Key");
    let models = setting.models.filter((item) => item.id !== id).map(({ key_configured: _key, ...item }) => item);
    models.push(existing || !desired.enabled ? desired : { ...desired, enabled: false });
    let saved;
    if (existing && secret) await jsonRequest(`/paam/system/v1/setting/automation/model/${id}/secret`, "PUT", { secret });
    saved = await saveModels(models, setting.updated_time);
    if (!existing && secret) await jsonRequest(`/paam/system/v1/setting/automation/model/${id}/secret`, "PUT", { secret });
    if (!existing && desired.enabled) {
      models = saved.models.map(({ key_configured: _key, ...item }) => item.id === id ? desired : item);
      saved = await saveModels(models, saved.updated_time);
    }
    setting = saved;
    form.closest("dialog").close();
    window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { message: "模型配置已保存并从 SQLite 回读" } }));
  } catch (error) {
    button.disabled = false;
    showFormError(form, error);
  }
}

function ruleDialog(rule = null) {
  const enabledViews = views.filter((item) => item.status === "ACTIVE" || item.id === rule?.view_id);
  const enabledModels = setting.models.filter((item) => (item.enabled && item.key_configured) || item.id === rule?.method_config.model_id);
  const value = rule || { name: "", view_id: enabledViews[0]?.id, enabled: false, cron: "*/5 * * * *", amount_mode: 1, method_config: { model_id: enabledModels[0]?.id, prompt: "" } };
  const dialog = openDialog(rule ? `编辑规则 #${rule.id}` : "新建自动打标签规则", `<form data-form="automation-rule" data-id="${rule?.id || ""}" class="stack automation-form">
    <div class="form-error-slot" aria-live="assertive"></div>
    <div class="form-grid"><label>规则名称<input name="name" required maxlength="120" value="${esc(value.name)}"></label><label>标签维度<select name="view_id" ${rule ? "disabled" : ""}>${enabledViews.map((item) => `<option value="${item.id}" ${item.id === value.view_id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label></div>
    <label>使用模型<select name="model_id">${enabledModels.map((item) => `<option value="${item.id}" ${item.id === value.method_config.model_id ? "selected" : ""}>${esc(item.name)}${item.enabled ? "" : "（已停用）"}</option>`).join("")}</select></label>
    <label>业务判断说明<textarea name="prompt" rows="5" required>${esc(value.method_config.prompt)}</textarea><small>这是业务 Prompt；后续执行时仍会经过统一脱敏和 system Prompt 边界。</small></label>
    <div class="form-grid"><label>CRON（5 或 6 段）<input name="cron" value="${esc(value.cron)}" ${value.enabled ? "required" : ""} placeholder="*/5 * * * *"><small data-cron-copy>保存时严格校验；是否注册请以规则列表中的调度状态为准。</small></label><label>金额披露<select name="amount_mode">${Object.entries(amountModeNames).map(([id, label]) => `<option value="${id}" ${Number(id) === value.amount_mode ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用规则配置</label>
    <div class="automation-notice compact"><strong>保存规则配置</strong><span>服务显式启用自动分析后，启用的规则会注册 CRON；是否正在调度请以列表状态为准。模型只生成待人工确认的建议，不会直接修改标签。修改 Prompt、模型或金额模式会从头重新扫描。</span></div>
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存并回读</button></div>
  </form>`);
  const form = $("[data-form='automation-rule']", dialog);
  $("[name='enabled']", form).addEventListener("change", (event) => {
    $("[name='cron']", form).required = event.currentTarget.checked;
  });
  form.addEventListener("submit", submitRule);
}

async function submitRule(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type='submit']", form);
  button.disabled = true;
  try {
    const data = new FormData(form);
    const id = Number(form.dataset.id || 0);
    const existing = rules.find((item) => item.id === id);
    const payload = {
      name: String(data.get("name")), method: 1,
      method_config: { schema_version: 1, model_id: Number(data.get("model_id")), prompt: String(data.get("prompt")) },
      enabled: data.has("enabled"), cron: String(data.get("cron")), amount_mode: Number(data.get("amount_mode")),
    };
    if (existing) payload.expected_updated_time = existing.updated_time;
    else payload.view_id = Number(data.get("view_id"));
    const saved = await jsonRequest(existing ? `/paam/tag/v1/auto_rule/${id}` : "/paam/tag/v1/auto_rule", existing ? "PUT" : "POST", payload, true);
    form.closest("dialog").close();
    const warning = saved.warnings?.some((item) => item.code === "REGISTER_FAILED");
    window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { message: warning ? "规则已保存，但调度注册失败；请查看安全诊断并重新保存" : "规则已保存并从 SQLite 回读" } }));
  } catch (error) {
    button.disabled = false;
    showFormError(form, error);
  }
}

async function testModel(button) {
  const result = $(`[data-model-result="${button.dataset.id}"]`);
  button.disabled = true;
  result.hidden = false;
  result.textContent = "正在执行安全检查…";
  try {
    const body = await jsonRequest(`/paam/system/v1/setting/automation/model/${button.dataset.id}/test`, "POST", {});
    result.className = "automation-result simulated";
    result.innerHTML = `<strong>${esc(body.mode)} · 未连接供应商</strong><span>${esc(body.message)}</span>`;
  } catch (error) {
    result.className = "automation-result error";
    result.textContent = error.message;
  } finally { button.disabled = false; }
}

async function previewRule(button) {
  const dialog = openDialog("候选资格预览", '<div class="automation-result" aria-live="polite">正在本地检查候选资格…</div>');
  const result = $(".automation-result", dialog);
  button.disabled = true;
  try {
    const body = await jsonRequest(`/paam/tag/v1/auto_rule/${button.dataset.id}/candidate_preview`, "POST", {});
    const reasons = Object.entries(body.reason_counts).map(([name, count]) => `<span>${esc(name)} <strong>${count}</strong></span>`).join("");
    const samples = body.samples.map((item) => `Ledger #${item.ledger_id}`).join("、") || "没有可用样例";
    result.className = "automation-result simulated";
    result.innerHTML = `<strong>${esc(body.mode)} · 只读预览</strong><span>预期模式 SIMULATED_LOCAL；检查 ${body.inspected_count} 条，可候选 ${body.eligible_count} 条；${esc(samples)}</span><div class="preview-reasons">${reasons}</div><small>${esc(body.message)}</small>`;
  } catch (error) {
    result.className = "automation-result error";
    result.textContent = error.message;
  } finally { button.disabled = false; }
}

function detailFields(fields) {
  return `<dl class="automation-definition">${fields.map(([label, value]) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></div>`).join("")}</dl>`;
}

function displayTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong" }) : "—";
}

function scheduleExplanation(code) {
  const explanations = {
    ACCEPTANCE_DATABASE_REQUIRED: "验收库含有非虚构数据；安全保护已阻止扫描，未向模型发送这些账目。请改用独立的纯虚构验收库。",
    MODEL_DISABLED: "所选模型已停用或不可用；请核对模型启用状态及密钥。",
    AUTH_ERROR: "模型供应商鉴权失败；请核对 API Key。",
    CONFIG_ERROR: "模型或金额披露配置有误；请核对设置。",
    NO_ACTIVE_TARGETS: "目标标签维度没有可用的候选标签。",
    ITEM_FAILURE: "至少一条账目分析失败；请查看下方安全诊断记录。",
    JOB_CALLBACK_FAILED: "调度回调异常；请查看安全诊断记录并核对业务结果。",
    OUTPUT_JSON_INVALID: "模型输出不是合法 JSON；本项没有生成申请。请检查模型兼容性，不会自动单项重扫。",
    OUTPUT_SCHEMA_INVALID: "模型 JSON 结构不符合打标协议；本项没有生成申请。请检查字段及模型兼容性。",
    OUTPUT_SEMANTIC_INVALID: "输出未通过业务或隐私校验；本项没有生成申请。新诊断记录包含固定细分原因；旧记录不能补造字段。",
    PROVIDER_UNAVAILABLE: "模型供应商暂时不可用；本轮未完成。请检查供应商状态及连接，后续由 CRON 按策略处理。",
    REQUEST_TIMEOUT: "模型调用超时；本轮未完成。请检查超时配置或供应商响应。",
    RATE_LIMITED: "模型供应商限流；请检查额度及调用频率。",
    RATE_LIMIT: "模型供应商限流；按 Retry-After 和本轮预算处理。",
    COMMIT_FAILED: "本条写入已回滚，游标与计数未提交；请查看诊断。",
    REGISTER_FAILED: "配置已保存但注册失败；请检查并重新保存规则。",
    REGISTRATION_FAILED: "规则调度注册失败，不会触发；请检查 CRON 和时区配置。",
  };
  return code ? `${explanations[code] || "执行失败，请查看服务端日志。"}（${code}）` : "无错误码记录";
}

function ruleDetailMarkup(rule, schedule, recent, summary = null) {
  const view = views.find((item) => item.id === rule.view_id);
  const model = setting?.models.find((item) => item.id === rule.method_config.model_id);
  const task = schedule.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  const running = schedule.scheduler_state === "RUNNING" && schedule.worker_state === "HEALTHY";
  const scheduling = !rule.enabled ? "规则已停用，不会调度"
    : schedule.tag_scan_guard === "DISABLED" ? "服务扫描开关关闭；保存规则或密钥不会自动开启，无需重新导入"
    : schedule.tag_scan_guard === "NON_SYNTHETIC_FACT" ? "安全门禁阻止：验收库含非虚构记录；不会继续扫描，也不会发送这些账目给模型"
    : !task ? "未注册 CRON 任务；当前环境可能未启用自动扫描"
    : task.queue_state === "PAUSED" ? "任务已暂停；不会按 CRON 触发"
    : !running ? "调度器未运行" : `${task.queue_state}；下一次触发 ${displayTime(task.next_run_at)}`;
  const latest = !task ? "无调度执行记录" : !task.last_result ? "尚无执行结果" : `${task.last_result}；${task.last_error_code ? scheduleExplanation(task.last_error_code) : "无错误码"}`;
  return `<div class="automation-detail stack">
    <section><h3>规则配置</h3>${detailFields([
      ["规则", `${rule.name} · #${rule.id} · 修订 ${rule.rule_revision}`],
      ["状态", rule.enabled ? "已启用" : "已停用"],
      ["标签维度", `${view?.name || `View #${rule.view_id}`} (#${rule.view_id})`],
      ["模型", `${model?.name || `Model #${rule.method_config.model_id}`} (#${rule.method_config.model_id})`],
      ["CRON", rule.cron || "未设置"], ["金额披露", amountModeNames[rule.amount_mode]],
      ["业务判断说明", rule.method_config.prompt], ["最后修改", displayTime(rule.updated_time)],
    ])}</section>
    <section><h3>调度与最近执行</h3>${detailFields([
      ["调度状态", scheduling], ["最近结果", latest],
      ["扫描游标", `Ledger #${rule.scan_after_ledger_id} · Epoch ${rule.scan_epoch}`],
      ["累计", `分析 ${rule.analyzed_count} · 失败 ${rule.failed_count} · 建议 ${rule.suggested_count} · 通过 ${rule.accepted_count} · 拒绝 ${rule.rejected_count}`],
    ])}${task ? runtimeMarkup({ ...schedule, tasks: [task] }) : ""}<p class="automation-detail-note">上方进度属于当前进程；下方为有限保留的安全诊断，不是完整模型请求历史。累计计数从数据库读取。资格预览不会请求模型，也不会推进游标。</p></section>
    <section><h3>最近建议 / 审查结果</h3>${recent.items.length ? `<div class="automation-detail-list">${recent.items.map((item) => `<div><button type="button" class="quiet" data-action="tag-request-detail" data-id="${item.id}">Request #${item.id} · ${esc(item.ledger_counterparty_name || item.ledger_summary || `Ledger #${item.ledger_id}`)}</button><span>${esc(item.proposed_tag_name)} · ${esc(requestStatusNames[item.status])} · ${esc(displayTime(item.created_time))}</span></div>`).join("")}</div>` : "<p>尚无建议请求；失败的调度也可能未生成请求。</p>"}</section>
    <section><h3>统计口径与采纳反馈</h3>${ruleStatistics(summary)}</section>
    <section><h3>模型输入与输出</h3><p class="automation-detail-note">历史模型请求正文与原始响应未被保存，不能准确回放；当前只保留建议标签和清洗后的理由。此处不会将资格预览冒充模型审查结果。</p><button type="button" class="quiet" data-action="disclosure-preview" data-mode="${rule.amount_mode}">查看系统约束与虚构披露预览</button></section>
  </div>`;
}

function requestDetailMarkup(item, ledger, rule) {
  const facts = ledger.facts || [];
  const currentTags = (ledger.ledger_entry.tags || []).filter((tag) => tag.view_system_name === item.view_system_name);
  const revisionChanged = rule.rule_revision !== item.rule_revision;
  return `<div class="automation-detail stack">
    <section><h3>建议与来源</h3>${detailFields([
      ["请求", `#${item.id} · ${requestStatusNames[item.status]} · ${displayTime(item.created_time)}`],
      ["建议标签", `${item.view_name} → ${item.proposed_tag_name}`],
      ["建议理由", item.reason_summary || "模型未提供可展示的理由"],
      ["当前标签", currentTags.length ? currentTags.map((tag) => `${tag.tag_name} (${tag.source_type})`).join("、") : "目标维度尚无标签"],
      ["来源规则", `${item.rule_name} · #${item.rule_id} · 请求修订 ${item.rule_revision}`],
      ["业务判断说明", rule.method_config.prompt],
      ["版本核对", revisionChanged ? `规则现为修订 ${rule.rule_revision}；通过时会因版本变化而拒绝` : "与当前规则修订一致"],
    ])}</section>
    <section><h3>被审查的账目</h3>${detailFields([
      ["账目", `Ledger #${item.ledger_id} · ${ledger.ledger_entry.active ? "当前有效" : "已失效"}`],
      ["账目摘要", ledger.ledger_entry.summary || "无摘要"],
      ["方向与金额", `${ledger.ledger_entry.entry_direction === 2 ? "支出" : ledger.ledger_entry.entry_direction === 1 ? "收入" : "其他"} · ${money(ledger.ledger_entry)}`], ["账目时间", displayTime(ledger.ledger_entry.occurred_time)],
    ])}${facts.length ? `<div class="automation-detail-list">${facts.map((fact) => `<div><strong>${esc(fact.counterparty_name || "未记录交易对方")} · ${esc(money(fact))}</strong><span>${esc(fact.summary || "无交易摘要")} · ${esc(displayTime(fact.occurred_time))}</span></div>`).join("")}</div>` : "<p>未找到关联交易事实；无法仅凭本页核对该建议。</p>"}</section>
    <section><h3>判断边界</h3><p class="automation-detail-note">此页展示当前账目与交易事实，可能不同于生成建议时的状态。历史模型输入正文和原始输出未留存，无法证明当时逐字传输了什么；建议理由仅是保存的清洗摘要。通过时服务端会再次校验规则版本、账目及现有标签。</p></section>
  </div>`;
}

function tagReviewFilterParams(entries) {
  const params = new URLSearchParams();
  for (const [name, value] of entries) {
    if (value || name === "status") params.set(name, value);
  }
  params.set("page", "1");
  return params;
}

function updateRequestActions(root) {
  const selection = $$('[data-tag-request-select]:not(:disabled)', root);
  const selected = selection.filter((item) => item.checked).length;
  $$('[data-action="tag-request-batch"]', root).forEach((button) => {
    button.disabled = selected === 0 || root.dataset.commandPending === "true" || root.dataset.autoStale === "true";
  });
  const all = $('[data-tag-request-select-all]', root);
  if (all) { all.checked = selected > 0 && selected === selection.length; all.indeterminate = selected > 0 && selected < selection.length; }
}

function diagnosticOptions(root) {
  const form = $('[data-form="schedule-diagnostics"]', root);
  return { taskKey: $('[name="task_key"]', form)?.value || "", severity: $('[name="severity"]', form)?.value ?? "ERROR", code: $('[name="code"]', form)?.value || "" };
}

async function readScheduleDiagnostics({ taskKey = "", severity = "ERROR", code = "" } = {}, page = 1, signal) {
  const fields = [["task_key", taskKey], ["severity", severity], ["code", code]].filter(([, value]) => value)
    .map(([key, val]) => ({ key, op: "=", val }));
  const query = new URLSearchParams({ page_index: String(page), page_size: "10" });
  if (fields.length) query.set("filter", JSON.stringify(fields.length === 1 ? fields[0] : { op: "AND", expression: fields }));
  const result = await request(`/paam/system/v1/schedule/event/list?${query}`, { signal, cache: "no-store" });
  if (!Array.isArray(result?.items)) throw new Error("安全诊断响应不可用");
  return result;
}

async function diagnosticSnapshot(root, signal) {
  const options = diagnosticOptions(root);
  const pageIndex = Number($('[data-diagnostic-state]', root)?.dataset.page || 1);
  return { options, pageIndex, events: await readScheduleDiagnostics(options, pageIndex, signal) };
}

async function refreshDiagnostics(root, pageIndex = 1) {
  if (root.dataset.commandPending === "true") return;
  root.dataset.commandPending = "true";
  const target = $('[data-auto-diagnostics]', root);
  const options = diagnosticOptions(root);
  try {
    const events = await readScheduleDiagnostics(options, pageIndex);
    if (target.isConnected) target.innerHTML = diagnosticsMarkup(events, options);
  } finally { root.dataset.commandPending = "false"; }
}

async function loadAutomationSnapshot(page, signal) {
  const read = (url) => request(url, { signal, cache: "no-store" });
  const kind = page.dataset.autoPage;
  if (kind === "settings") {
    const [schedule, diagnostics] = await Promise.all([read("/paam/system/v1/schedule/status"), diagnosticSnapshot(page, signal)]);
    return { schedule, diagnostics };
  }
  if (kind === "requests") return { requests: await read(`/paam/tag/v1/assignment_request/list?${page.dataset.requestQuery}`) };
  if (kind === "request") {
    const item = await read(`/paam/tag/v1/assignment_request/${page.dataset.requestId}`);
    const [ledger, rule] = await Promise.all([read(`/paam/ledger/v1/flow/${item.ledger_id}`), read(`/paam/tag/v1/auto_rule/${item.rule_id}`)]);
    return { item, ledger, rule };
  }
  const [schedule, currentSetting, viewPage] = await Promise.all([
    read("/paam/system/v1/schedule/status"), read("/paam/system/v1/setting/automation"), read("/paam/tag/v1/view/list?page_index=1&page_size=100"),
  ]);
  if (kind === "rules") return { schedule, currentSetting, viewPage, rulePage: await read(ruleListUrl) };
  const [rule, summary, recent] = await Promise.all([
    read(`/paam/tag/v1/auto_rule/${page.dataset.ruleId}`), read(`/paam/tag/v1/auto_rule/${page.dataset.ruleId}/summary`),
    read(`/paam/tag/v1/assignment_request/list?${page.dataset.recentQuery}`),
  ]);
  return { schedule, currentSetting, viewPage, rule, summary, recent, diagnostics: await diagnosticSnapshot(page, signal) };
}

function applyAutomationSnapshot(root, page, data) {
  const focused = root.contains(document.activeElement) ? document.activeElement : null;
  const focusMatch = focused?.hasAttribute("data-tag-request-select") ? (node) => node.value === focused.value
    : focused?.dataset.action ? (node) => node.dataset.action === focused.dataset.action && node.dataset.id === focused.dataset.id : null;
  if (data.schedule) scheduleStatus = data.schedule;
  if (data.currentSetting) setting = data.currentSetting;
  if (data.viewPage) views = data.viewPage.items;
  root.dataset.autoStale = "false";
  if (page.dataset.autoPage === "settings") $('[data-auto-runtime]', root).innerHTML = runtimeMarkup(data.schedule);
  if (data.rulePage) {
    rules = data.rulePage.items;
    $('[data-auto-notice]', root).innerHTML = scheduleNotice();
    $('[data-auto-rule-rows]', root).innerHTML = rules.map(ruleRow).join("") || '<tr><td colspan="5">尚无规则</td></tr>';
    const create = $('[data-action="rule-new"]', root);
    if (create) create.disabled = !views.some((item) => item.status === "ACTIVE") || !setting.models.some((item) => item.enabled && item.key_configured);
  }
  if (data.summary) $('[data-auto-rule-detail]', root).innerHTML = ruleDetailMarkup(data.rule, data.schedule, data.recent, data.summary);
  if (data.diagnostics && JSON.stringify(data.diagnostics.options) === JSON.stringify(diagnosticOptions(root))
    && data.diagnostics.pageIndex === Number($('[data-diagnostic-state]', root)?.dataset.page || 1)) {
    $('[data-auto-diagnostics]', root).innerHTML = diagnosticsMarkup(data.diagnostics.events, data.diagnostics.options);
  }
  if (data.requests) {
    const selected = new Set($$('[data-tag-request-select]:checked', root).map((item) => item.value));
    requestItems = data.requests.items;
    $('[data-auto-request-rows]', root).innerHTML = requestRowsMarkup(requestItems);
    $('[data-auto-request-pager]', root).innerHTML = requestPagerMarkup(data.requests);
    $$('[data-tag-request-select]:not(:disabled)', root).forEach((item) => { item.checked = selected.has(item.value); });
    updateRequestActions(root);
  }
  if (data.item) {
    requestItems = [data.item];
    $('[data-auto-request-detail]', root).innerHTML = `${requestDetailMarkup(data.item, data.ledger, data.rule)}<div class="automation-actions">${requestActionsMarkup(data.item)}</div>`;
  }
  if (focusMatch && !focused.isConnected) $$('[data-tag-request-select], [data-action]', root).find(focusMatch)?.focus({ preventScroll: true });
}

function startAutomationRefresh(root) {
  const page = $('[data-auto-page]', root);
  if (!page) return;
  stopAutomationPolling();
  root.dataset.autoStale = "false";
  const route = location.hash;
  stopPolling = startVisiblePoll({
    isAlive: () => page.isConnected && location.hash === route,
    canPoll: () => !document.querySelector("dialog[open]") && root.dataset.commandPending !== "true"
      && !(root.contains(document.activeElement) && document.activeElement.matches("input:not([type='checkbox']), select, textarea")),
    load: (signal) => loadAutomationSnapshot(page, signal),
    apply: (data) => applyAutomationSnapshot(root, page, data),
    onState: ({ state, lastSuccess }) => {
      const feedback = $('[data-auto-freshness]', root);
      if (!feedback) return;
      const last = lastSuccess ? displayTime(lastSuccess) : "本页初次读取";
      feedback.textContent = state === "UNKNOWN" ? `当前状态未知：业务接口连接失败或超时。下方是旧快照，不能证明正在分析；最后成功刷新：${last}。可见页会继续尝试只读刷新。`
        : state === "PAUSED" ? `页面隐藏，已停止轮询；最后成功刷新：${last}。`
        : state === "CURRENT" ? `业务状态已更新：${last}。可见页每 5 秒刷新；编辑或确认期间暂停更新。`
        : "等待业务状态刷新；可见页每 5 秒读取，页面隐藏后停止。";
      if (state === "UNKNOWN") {
        root.dataset.autoStale = "true";
        $$('[data-action="tag-request-transition"]', root).forEach((button) => { button.disabled = true; });
      }
      updateRequestActions(root);
    },
  });
}

function showBatchFeedback(root, markup) {
  batchFeedback = markup;
  const slot = $('[data-batch-feedback]', root);
  if (slot) { slot.innerHTML = markup; slot.scrollIntoView({ block: "nearest" }); }
}

async function transitionRequests(root, button, rerender) {
  if (root.dataset.commandPending === "true" || root.dataset.autoStale === "true") return;
  const ids = button.dataset.id ? [Number(button.dataset.id)]
    : $$('[data-tag-request-select]:checked:not(:disabled)', root).map((item) => Number(item.value));
  if (!ids.length) return;
  const approve = button.dataset.operation === "approve";
  const conflict = approve && selectionConflict(requestItems.filter((item) => ids.includes(item.id)));
  const dialog = openDialog(approve ? "确认通过标签建议" : "确认拒绝标签建议", `<p>本次选择 ${ids.length} 项：${ids.map((id) => `#${id}`).join("、")}。</p><p>${approve ? "同一账目 + 维度只能选一项；服务端将重新核对版本、账目和人工标签，冲突不会强制覆盖。" : "只将这些申请标为已拒绝，不修改现有标签。"}</p>${conflict ? '<p class="error">选择中有同范围冲突项，这些项不会通过；其他有效范围仍可处理。可取消后调整选择。</p>' : ""}<p>返回逐项结果；有效项会提交，失败项不改变标签。重复已处理项不重复计数；超时结果未知时请先刷新核对。</p><div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="button" class="primary" data-confirm-batch>确认${approve ? "通过" : "拒绝"}</button></div>`);
  $('[data-confirm-batch]', dialog).addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    root.dataset.commandPending = "true";
    $$('[data-action="tag-request-transition"], [data-action="tag-request-batch"]', root).forEach((item) => { item.disabled = true; });
    dialog.close();
    try {
      const body = await jsonRequest(`/paam/tag/v1/assignment_request/batch_${approve ? "approve" : "reject"}`, "POST", { request_ids: ids });
      showBatchFeedback(root, batchResultMarkup(batchResults(body, ids, approve ? "approve" : "reject")));
      await rerender();
    } catch (error) {
      showBatchFeedback(root, batchFailureMarkup(error, ids));
      // Do not automatically resubmit an ambiguous command. The next read refreshes state.
    } finally {
      root.dataset.commandPending = "false";
      updateRequestActions(root);
    }
  }, { once: true });
}

export function bindAutomation(root, rerender, notify, navigate) {
  startAutomationRefresh(root);
  if (boundRoots.has(root)) return;
  boundRoots.add(root);
  root.addEventListener("submit", async (event) => {
    if (!event.target.matches('[data-form="schedule-diagnostics"]')) return;
    event.preventDefault();
    try { await refreshDiagnostics(root); } catch (error) { notify(error.message, true); }
  });
  root.addEventListener("change", (event) => {
    const filter = event.target.closest('[data-form="tag-request-filter"]');
    if (filter) { batchFeedback = ""; navigate?.("tag-review", tagReviewFilterParams(new FormData(filter))); return; }
    if (event.target.matches('[data-tag-request-select-all]')) {
      $$('[data-tag-request-select]:not(:disabled)', root).forEach((item) => { item.checked = event.target.checked; });
    }
    updateRequestActions(root);
  });
  root.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button || !root.contains(button) || button.disabled) return;
    const action = button.dataset.action;
    const id = Number(button.dataset.id);
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    try {
      if (action === "diagnostic-page") await refreshDiagnostics(root, Number(button.dataset.diagnosticPage));
      if (action === "copy-diagnostic") { await navigator.clipboard.writeText(button.dataset.runId); notify("诊断编号已复制"); }
      if (action === "model-new") modelDialog();
      if (action === "model-edit") modelDialog(setting.models.find((item) => item.id === id));
      if (action === "model-test") await testModel(button);
      if (action === "model-secret-delete") {
        if (!window.confirm("清除该模型的本地 API Key？启用状态可能因此不可用。")) return;
        button.disabled = true;
        await request(`/paam/system/v1/setting/automation/model/${id}/secret`, { method: "DELETE" });
        notify("密钥已从系统凭据存储清除"); await rerender();
      }
      if (action === "disclosure-edit") openDisclosureEditor(setting, openDialog, (saved) => {
        setting = saved;
        window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { message: "披露策略已保存并回读；受影响规则的待审申请已失效" } }));
      });
      if (action === "disclosure-preview") openDisclosurePreview(openDialog, Number(button.dataset.mode || 1));
      if (action === "interaction-demo") {
        const dialog = openDialog("M2 虚构交互演示", `<label>虚构场景<select data-interaction-scenario>${Object.entries(interactionScenarios).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label><div data-interaction-result>${interactionMarkup("PARTIAL")}</div>`);
        $('[data-interaction-scenario]', dialog).addEventListener("change", (event) => {
          $('[data-interaction-result]', dialog).innerHTML = interactionMarkup(event.target.value);
        });
      }
      if (action === "rule-new") ruleDialog();
      if (action === "rule-edit") ruleDialog(rules.find((item) => item.id === id));
      if (action === "rule-preview") await previewRule(button);
      if (action === "rule-detail") navigate?.("auto-rules", new URLSearchParams({ rule_id: String(id) }));
      if (action === "rule-detail-back") navigate?.("auto-rules");
      if (action === "tag-request-detail") { batchFeedback = ""; params.set("request_id", String(id)); navigate?.("tag-review", params); }
      if (action === "tag-request-detail-back") { params.delete("request_id"); navigate?.("tag-review", params); }
      if (action === "tag-request-page") { batchFeedback = ""; params.set("page", button.dataset.requestPage); navigate?.("tag-review", params); }
      if (["tag-request-transition", "tag-request-batch"].includes(action)) await transitionRequests(root, button, rerender);
    } catch (error) { button.disabled = false; notify(error.message, true); }
  });
}

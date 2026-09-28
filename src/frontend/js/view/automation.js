import { request, jsonRequest } from "../api/client.js?v=20260928.2";
import { preserveView, patchMarkup } from "../util/view_state.js?v=20260928.2";
import { openInspection, registerInspection } from "../component/inspection.js?v=20260928.4";
import { $, $$, esc, money, date } from "../util/core.js";
import { startVisiblePoll } from "../util/visible_poll.js?v=20260928.2";
import { openDisclosureEditor, openDisclosurePreview } from "./disclosure.js?v=20260928.4";
import { helpTip, batchResults, batchResultMarkup, batchFailureMarkup, selectionConflict, ruleStatistics, runtimeMarkup, diagnosticsMarkup } from "./automation_feedback.js?v=20260928.5";
import { executionResultNames, ruleExecution, semanticRuleChange, ruleEditImpact, candidatePreviewMarkup, requestVersionCopy, scopeImpactMarkup } from "./automation_explain.js?v=20260928.5";

let setting = null;
let rules = [];
let views = [];
let scheduleStatus = null;
let stopPolling = () => {};
let requestItems = [];
let batchFeedback = "";
const boundRoots = new WeakSet();
const ruleListUrl = "/paam/tag/v1/auto_rule/list?page_index=1&page_size=100&sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D";
const viewListUrl = "/paam/tag/v1/view/list?sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D";
const freshnessMarkup = '<p class="automation-freshness" data-auto-freshness role="status" aria-live="polite">状态自动更新</p>';

export function stopAutomationPolling() { stopPolling(); stopPolling = () => {}; }

// Option lists use bounded API pages too; never silently truncate at 100.
async function readListOptions(url, signal) {
  const [path, search] = url.split("?");
  const query = new URLSearchParams(search);
  query.set("page_size", "100");
  const items = [];
  let page = 1;
  let result;
  do {
    query.set("page_index", String(page++));
    result = await request(`${path}?${query}`, { signal, cache: "no-store" });
    items.push(...result.items);
  } while (result.items.length && items.length < result.total);
  return { ...result, items };
}

function ruleListQuery(params = new URLSearchParams()) {
  const rawPage = Number(params.get("page") || 1);
  const query = new URLSearchParams({ page_index: String(Number.isSafeInteger(rawPage) && rawPage > 0 ? rawPage : 1), page_size: "20",
    sorter: JSON.stringify([{ key: "id", direction: params.get("sort") === "desc" ? "desc" : "asc" }]) });
  if (params.get("q")?.trim()) query.set("query", JSON.stringify([{ key: "name", word: params.get("q").trim().slice(0, 200) }]));
  if (["true", "false"].includes(params.get("enabled"))) query.set("filter", JSON.stringify({ key: "enabled", op: "=", val: params.get("enabled") === "true" }));
  return query;
}

const amountModeNames = { 1: "仅发送金额区间", 2: "发送精确金额", 3: "不发送金额" };

function statusPill(enabled, available = true, unavailableCopy = "缺少密钥") {
  const active = enabled && available;
  const copy = !available ? unavailableCopy : enabled ? "已启用" : "已停用";
  return `<span class="automation-status ${active ? "active" : "inactive"}"><i></i>${copy}</span>`;
}

function modelSummary(model) {
  const params = model.litellm_params;
  return `<article class="automation-card" data-model-card="${model.id}">
    <header><div><h3>${esc(model.name)}</h3><small>${esc(params.model)}</small></div>${statusPill(model.enabled, model.key_configured)}</header>
    <div class="automation-actions">
      <button type="button" class="quiet" data-action="model-test" data-id="${model.id}">测试真实连接</button>
      <button type="button" data-action="model-edit" data-id="${model.id}">编辑</button>
    </div>
    <div class="automation-result" data-model-result="${model.id}" aria-live="polite" hidden></div>
  </article>`;
}

function disclosureSummaryMarkup() {
  const bands = Object.entries(setting.disclosure?.amount_bands || {}).map(([currency, config]) =>
    `<div class="disclosure-row"><strong>${esc(currency)}</strong><span>${esc((config.boundaries || []).map((amount) => money({ amount, currency_code: currency })).join(" / "))}</span></div>`,
  ).join("");
  return `<div><span>时间策略 ${helpTip("时间策略", "当前发送内容省略交易时间，此项仅保存配置。")}</span><strong>${esc(({ DAY: "精确到日", MONTH: "精确到月", NONE: "不发送时间" })[setting.disclosure?.date_granularity] || "未配置")}</strong></div><div><span>金额分档 ${helpTip("金额分档", "按金额绝对值分档，以下为换算后的日常金额。")}</span>${bands || "<strong>尚未配置</strong>"}</div>`;
}

export async function automationSettingsPage() {
  setting = await request("/paam/system/v1/setting/automation");
  const modelCards = setting.models.map(modelSummary).join("");
  scheduleStatus = await request("/paam/system/v1/schedule/status").catch(() => null);
  const diagnostics = await readScheduleDiagnostics().catch(() => null);
  return `<div class="automation-page automation-settings" data-auto-page="settings">
    <section class="automation-section" aria-labelledby="automation-model-title">
      <div class="automation-section-head"><div><h3 id="automation-model-title">模型连接 ${helpTip("模型连接说明", "密钥保存在本机系统凭据存储。连接测试会调用模型，可能计费。")}</h3></div><button type="button" class="primary" data-action="model-new">＋ 添加模型</button></div>
      <div class="automation-grid" data-auto-models>${modelCards || '<p>尚未配置模型。添加连接并保存密钥后，规则才可选择模型。</p>'}</div>
    </section>
    <section class="automation-section" aria-labelledby="automation-disclosure-title">
      <div class="automation-section-head"><div><h3 id="automation-disclosure-title">数据披露 ${helpTip("数据披露说明", "每条规则可选择发送金额区间、精确金额或省略金额。")}</h3></div><div class="automation-actions"><button type="button" class="quiet" data-action="disclosure-preview">查看发送示例</button><button type="button" data-action="disclosure-edit">编辑披露策略</button></div></div>
      <div class="disclosure-card" data-auto-disclosure>${disclosureSummaryMarkup()}</div>
    </section>
    <section class="automation-section"><h3>运行概况</h3><div data-auto-notice>${scheduleNotice()}</div>${freshnessMarkup}
      <nav class="automation-links"><a href="#details/auto-rule">管理规则 →</a><a href="#workbench/tag-review">查看待审建议 →</a></nav>
      <section class="automation-runtime"><h3>任务队列</h3><div data-auto-runtime>${runtimeMarkup(scheduleStatus)}</div><div data-auto-diagnostics>${diagnosticsMarkup(diagnostics)}</div></section></section>
  </div>`;
}

function ruleRow(rule) {
  const view = views.find((item) => item.id === rule.view_id);
  const model = setting?.models.find((item) => item.id === rule.method_config.model_id);
  const counts = [
    ["分析", rule.analyzed_count], ["失败", rule.failed_count], ["建议", rule.suggested_count],
    ["累计通过", rule.accepted_count], ["拒绝", rule.rejected_count],
  ].map(([label, value]) => `<span>${label} <strong>${esc(value)}</strong></span>`).join("");
  const scheduleTask = scheduleStatus?.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  const execution = ruleExecution(rule, scheduleStatus, model, view);
  const lastRun = scheduleTask?.last_result
    ? `上次执行：${executionResultNames[scheduleTask.last_result] || "结果未知"}` : "尚无执行结果";
  return `<tr data-rule-row="${rule.id}">
    <td class="rule-identity"><strong><a class="detail-primary" data-action="rule-detail" data-id="${rule.id}" href="#details/auto-rule?rule_id=${rule.id}">${esc(rule.name)}</a></strong><small class="rule-purpose">${esc(rule.method_config.prompt.trim().slice(0, 80))}${rule.method_config.prompt.trim().length > 80 ? "…" : ""}</small><small>#${rule.id} · 修订 ${rule.rule_revision} · 配置${rule.enabled ? "已启用" : "已停用"}</small></td>
    <td><strong>${esc(view?.name || `维度 #${rule.view_id}`)}</strong><small>${esc(model?.name || `模型 #${rule.method_config.model_id}`)}</small></td>
    <td class="rule-schedule"><span class="automation-status ${execution.tone}"><i></i>${esc(execution.label)}</span>${execution.reason ? helpTip("执行状态说明", execution.reason) : ""}<small>${esc(lastRun)}${scheduleTask?.last_error_code ? " · 详见规则详情" : ""}</small></td>
    <td><div class="rule-counts">${counts}</div></td>
    <td class="rule-operation"><div class="automation-actions"><button type="button" class="quiet" data-action="rule-detail" data-id="${rule.id}" title="查看当前配置、执行记录及产生的建议">规则详情</button><button type="button" class="quiet" data-action="rule-preview" data-id="${rule.id}" title="只读检查后续最多 100 条账目，不调用模型">查看待分析账目</button><button type="button" data-action="rule-edit" data-id="${rule.id}" title="修改配置；判断内容变更会取消旧的待确认建议">编辑规则</button></div></td>
  </tr>`;
}

function scheduleNotice() {
  const tasks = (scheduleStatus?.tasks || []).filter((item) => item.task_key.startsWith("tag-scan:"));
  const disabled = scheduleStatus?.tag_scan_guard === "DISABLED";
  const blockedByData = scheduleStatus?.tag_scan_guard === "NON_SYNTHETIC_FACT";
  const running = scheduleStatus?.scheduler_state === "RUNNING" && scheduleStatus?.worker_state === "HEALTHY";
  const nextRuns = tasks.map((item) => item.next_run_at).filter(Boolean).sort();
  const failed = tasks.filter((item) => ["FAILED", "PARTIAL_FAILURE"].includes(item.last_result)).length;
  const title = !scheduleStatus ? "暂时无法读取调度状态"
    : disabled ? "自动分析已关闭"
    : blockedByData ? "当前库不允许自动标签扫描"
    : !tasks.length ? "没有已注册的自动标签任务"
    : running ? `自动扫描已就绪 · ${tasks.length} 条规则` : "自动扫描已停止";
  const explanation = !scheduleStatus ? "请检查服务连接。"
    : disabled ? "需在服务配置中开启自动分析并重启服务；仅启用模型或规则不会开始扫描。开启后可能产生模型费用。"
    : blockedByData ? "验收模式仅支持纯虚构数据，请使用独立验收库。"
    : !tasks.length ? "请检查模型和规则是否已启用。"
    : !running ? "请查看运行概况中的调度器与执行器状态。"
    : nextRuns.length ? `下次触发：${date(nextRuns[0])}` : "当前没有待触发时间。";
  const activity = running && tasks.length && !disabled && !blockedByData
    ? `${tasks.filter((task) => task.queue_state === "RUNNING").length} 条分析中 · ${tasks.filter((task) => task.queue_state === "QUEUED").length} 条排队` : "";
  return `<div class="automation-notice compact" role="status"><strong>${esc(title)} ${explanation ? helpTip("扫描状态说明", explanation) : ""}</strong>${activity ? `<span>${activity}</span>` : ""}${failed && !blockedByData ? `<span>${failed} 条规则上次执行失败，请查看规则详情。</span>` : ""}</div>`;
}

export async function autoRulesPanel(tagViews, params = new URLSearchParams()) {
  views = tagViews;
  const [rulePage, currentSetting, currentSchedule] = await Promise.all([
    request(`/paam/tag/v1/auto_rule/list?${ruleListQuery(params)}`),
    request("/paam/system/v1/setting/automation"),
    request("/paam/system/v1/schedule/status").catch(() => null),
  ]);
  rules = rulePage.items;
  setting = currentSetting;
  scheduleStatus = currentSchedule;
  const canCreate = views.some((item) => item.status === "ACTIVE") && setting.models.some((item) => item.enabled && item.key_configured);
  return `<section class="tag-manager automation-rules" aria-labelledby="auto-rule-title">
    <div class="tag-manager-head"><div><h2 id="auto-rule-title">自动打标签规则</h2></div><button type="button" class="primary" data-action="rule-new" ${canCreate ? "" : 'disabled title="需要启用中的标签维度和模型"'}>＋ 新建规则</button></div>
    ${freshnessMarkup}<div data-auto-notice>${scheduleNotice()}</div>
    <form class="form-grid three" data-form="auto-rule-filter"><label>搜索规则<input name="q" type="search" maxlength="200" value="${esc(params.get("q") || "")}" placeholder="输入规则名称"></label><label>配置状态<select name="enabled"><option value="">全部状态</option><option value="true" ${params.get("enabled") === "true" ? "selected" : ""}>已启用</option><option value="false" ${params.get("enabled") === "false" ? "selected" : ""}>已停用</option></select></label><button type="submit" class="quiet">筛选规则</button></form>
    <div class="automation-table-wrap"><table class="automation-table rule-table"><thead><tr><th>规则 / 用途</th><th>标签维度 / 模型</th><th>当前执行 / 上次结果</th><th>历史累计 ${helpTip("历史累计说明", "累计通过包含后来被替换的建议。")}</th><th>操作</th></tr></thead><tbody data-auto-rule-rows>${rules.map(ruleRow).join("") || '<tr><td colspan="5" class="table-empty"><strong>尚未创建自动规则</strong><span>启用模型并准备标签维度后即可保存第一条规则。</span></td></tr>'}</tbody></table></div>
    <div data-auto-rule-pager>${requestPagerMarkup(rulePage, "auto-rule-page")}</div>
  </section>`;
}

export async function autoRulesPage(params = new URLSearchParams(), { inspection = false } = {}) {
  const ruleId = Number(params.get("rule_id"));
  if (inspection && Number.isSafeInteger(ruleId) && ruleId > 0) {
    const query = new URLSearchParams({
      page_index: "1", page_size: "5",
      filter: JSON.stringify({ key: "rule_id", op: "=", val: ruleId }),
      sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
    });
    const [rule, viewPage, currentSetting, schedule, recent, summary] = await Promise.all([
      request(`/paam/tag/v1/auto_rule/${ruleId}`),
      readListOptions(viewListUrl),
      request("/paam/system/v1/setting/automation"),
      request("/paam/system/v1/schedule/status"),
      request(`/paam/tag/v1/assignment_request/list?${query}`),
      request(`/paam/tag/v1/auto_rule/${ruleId}/summary`),
    ]);
    views = viewPage.items;
    setting = currentSetting;
    const diagnosticOptions = { taskKey: `tag-scan:${ruleId}` };
    const diagnostics = await readScheduleDiagnostics(diagnosticOptions).catch(() => null);
    return { title: rule.name, body: `<div class="auto-rules-page automation-detail-page" data-auto-page="rule" data-rule-id="${ruleId}" data-recent-query="${esc(query.toString())}">${freshnessMarkup}<div data-auto-rule-detail>${ruleDetailMarkup(rule, schedule, recent, summary)}</div><div data-auto-diagnostics>${diagnosticsMarkup(diagnostics, diagnosticOptions)}</div></div>` };
  }
  const viewPage = await readListOptions(viewListUrl);
  return `<div class="auto-rules-page" data-auto-page="rules" data-rule-query="${esc(ruleListQuery(params).toString())}" ${Number.isSafeInteger(ruleId) && ruleId > 0 ? `data-open-rule="${ruleId}"` : ""}>${await autoRulesPanel(viewPage.items, params)}</div>`;
}

registerInspection("rule", {
  name: "自动规则", action: "rule-detail",
  load: (id) => autoRulesPage(new URLSearchParams({ rule_id: String(id) }), { inspection: true }),
  describe: (data) => ({ ...data, kind: "rule", subtitle: "", hero: "", actions: "",
    presentation: { mount: (body) => bindAutomation(body, inspectionCallbacks.rerender, inspectionCallbacks.notify, inspectionCallbacks.navigate) } }),
});
let inspectionCallbacks = {};

async function openRuleInspection(id, root, rerender, notify, navigate) {
  inspectionCallbacks = { rerender, notify, navigate };
  await openInspection("rule", id, () => {});
  const dialog = document.querySelector('.inspection-workspace[open]');
  if (!dialog || dialog.dataset.automationBound) return;
  dialog.classList.add('automation-dialog');
  dialog.dataset.automationBound = "true";
  dialog.addEventListener('close', () => { if (root.isConnected) startAutomationRefresh(root); }, { once: true });
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
    <td><label class="request-selector"><input type="checkbox" data-tag-request-select value="${item.id}" ${item.status === 1 ? "" : "disabled"}><span><a href="#workbench/tag-review?request_id=${item.id}" data-action="tag-request-detail" data-id="${item.id}">${esc(item.ledger_counterparty_name || "未记录交易对方")} · 查看依据</a><small>${esc(item.ledger_summary || "无账目摘要；请查看详情")}</small></span></label></td>
    <td><strong>${item.ledger_amount == null ? `Ledger #${item.ledger_id}` : esc(money({ amount: item.ledger_amount, currency_code: item.ledger_currency_code }))}</strong><small>Ledger #${item.ledger_id} · ${esc(item.view_name)} · ${item.ledger_active ? "当前有效" : "Ledger 已失效"}</small></td>
    <td><strong>${esc(item.proposed_tag_name)}</strong><small>${esc(item.proposed_tag_system_name)}</small></td>
    <td><strong>${esc(item.rule_name)}</strong><small>Rule #${item.rule_id} · Revision ${item.rule_revision}</small></td>
    <td>${requestStatusPill(item.status)}</td>
    <td><div class="automation-actions request-actions">${requestActionsMarkup(item)}</div></td>
  </tr>`).join("") || '<tr><td colspan="6" class="table-empty"><strong>没有符合条件的建议请求</strong><span>可选择「全部状态」查看已处理建议；只有已注册的规则扫描任务才会自动产生新建议。</span></td></tr>';
}

function requestActionsMarkup(item, blockedReason = "") {
  return item.status === 1 ? `<button type="button" class="primary" data-action="tag-request-transition" data-operation="approve" data-id="${item.id}" ${blockedReason ? `disabled title="${esc(blockedReason)}"` : ""}>通过并打标</button><button type="button" class="quiet" data-action="tag-request-transition" data-operation="reject" data-id="${item.id}">拒绝</button>` : "";
}

function assignmentQuery(filters, pageSize = 20) {
  const expression = Object.entries(filters).map(([key, val]) => ({ key, op: "=", val }));
  return new URLSearchParams({ page_index: "1", page_size: String(pageSize),
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
    filter: JSON.stringify(expression.length === 1 ? expression[0] : { op: "AND", expression }) });
}

async function readRequestScope(item, signal) {
  const filters = { ledger_id: item.ledger_id, view_id: item.view_id };
  const read = (status) => request(`/paam/tag/v1/assignment_request/list?${assignmentQuery({ ...filters, status })}`, { signal, cache: "no-store" });
  const [pending, approved] = await Promise.all([read(1), read(2)]);
  return { pending, approved };
}

function requestPagerMarkup(result, action = "tag-request-page") {
  const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
  const page = result.page_index;
  if (lastPage === 1 && page === 1) return `<p class="request-pager">共 ${result.total} 条</p>`;
  return `<div class="request-pager"><span>共 ${result.total} 条 · 第 ${page}/${lastPage} 页</span><div><button type="button" class="quiet" data-action="${action}" data-request-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><button type="button" class="quiet" data-action="${action}" data-request-page="${page + 1}" ${page >= lastPage ? "disabled" : ""}>下一页</button></div></div>`;
}

export async function tagReviewPage(params = new URLSearchParams()) {
  const requestId = Number(params.get("request_id"));
  if (Number.isSafeInteger(requestId) && requestId > 0) {
    const item = await request(`/paam/tag/v1/assignment_request/${requestId}`);
    const [ledger, rule, scope] = await Promise.all([
      request(`/paam/ledger/v1/flow/${item.ledger_id}`),
      request(`/paam/tag/v1/auto_rule/${item.rule_id}`),
      readRequestScope(item).catch(() => null),
    ]);
    requestItems = [item];
    return `<div class="tag-review-page automation-detail-page" data-auto-page="request" data-request-id="${requestId}"><button type="button" class="quiet" data-action="tag-request-detail-back">← 返回建议列表</button><h2>标签建议 #${requestId}</h2>${freshnessMarkup}<div data-batch-feedback>${batchFeedback}</div><div data-auto-request-detail>${requestDetailMarkup(item, ledger, rule, scope)}</div></div>`;
  }
  const page = Math.max(1, Number(params.get("page") || 1));
  const pageSize = Math.min(100, Math.max(1, Number(params.get("page_size") || 20)));
  const viewId = params.get("view_id") || "";
  const ruleId = params.get("rule_id") || "";
  const ledgerId = params.get("ledger_id") || "";
  const status = params.has("status") ? params.get("status") : "1";
  const expressions = [];
  if (viewId) expressions.push({ key: "view_id", op: "=", val: Number(viewId) });
  if (ruleId) expressions.push({ key: "rule_id", op: "=", val: Number(ruleId) });
  if (ledgerId) expressions.push({ key: "ledger_id", op: "=", val: Number(ledgerId) });
  if (status) expressions.push({ key: "status", op: "=", val: Number(status) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: String(page), page_size: String(pageSize),
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  const [viewPage, rulePage] = await Promise.all([
    readListOptions(viewListUrl),
    readListOptions(ruleListUrl),
  ]);
  const result = await request(`/paam/tag/v1/assignment_request/list?${query}`);
  requestItems = result.items;
  const viewOptions = viewPage.items.map((item) => `<option value="${item.id}" ${String(item.id) === viewId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  const ruleOptions = rulePage.items.map((item) => `<option value="${item.id}" ${String(item.id) === ruleId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  return `<div class="tag-review-page" data-auto-page="requests" data-request-query="${esc(query.toString())}">
    <section class="tag-manager automation-requests" aria-labelledby="tag-request-title">
      <div class="tag-manager-head"><div><h2 id="tag-request-title">标签建议请求 ${helpTip("建议审查说明", "通过前会核对账目与规则版本；冲突时保留人工标签。")}</h2><p>通过后应用标签，拒绝则保留原标签。</p></div><div class="request-batch-actions"><button type="button" class="primary" data-action="tag-request-batch" data-operation="approve" disabled>批量通过</button><button type="button" class="quiet" data-action="tag-request-batch" data-operation="reject" disabled>批量拒绝</button></div></div>
      ${freshnessMarkup}<div data-batch-feedback>${batchFeedback}</div>
      ${ledgerId ? `<p class="automation-detail-note">当前仅查看账目 #${esc(ledgerId)} 的建议。<button type="button" class="quiet" data-action="tag-request-clear-scope">查看全部账目</button></p>` : ""}
      <div class="review-filter-skeleton" aria-labelledby="tag-review-filter-title">
        <form class="form-grid three" data-form="tag-request-filter">
          ${ledgerId ? `<input type="hidden" name="ledger_id" value="${esc(ledgerId)}">` : ""}
          <label>标签维度<select name="view_id"><option value="">全部 View</option>${viewOptions}</select></label>
          <label>来源规则<select name="rule_id"><option value="">全部规则</option>${ruleOptions}</select></label>
          <label>建议状态<select name="status"><option value="" ${status === "" ? "selected" : ""}>全部状态</option>${Object.entries(requestStatusNames).map(([value, label]) => `<option value="${value}" ${status === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        </form>
      </div>
      <label class="request-select-all"><input type="checkbox" data-tag-request-select-all> 全选本页可处理请求</label>
      <div class="automation-table-wrap"><table class="automation-table request-table" aria-labelledby="tag-review-list-title"><thead><tr><th id="tag-review-list-title">请求</th><th>Ledger / View</th><th>建议标签</th><th>来源规则</th><th>状态</th><th>操作</th></tr></thead><tbody data-auto-request-rows>${requestRowsMarkup(result.items)}</tbody></table></div>
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
    <label>显示名称<input name="name" required maxlength="120" value="${esc(value.name)}" placeholder="例如：我的 Qwen"></label>
    <label>LiteLLM 模型名<input name="model" required maxlength="512" value="${esc(params.model || "")}" placeholder="openai/Qwen/Qwen3-8B"></label>
    <label>HTTPS API 地址<input name="api_base" type="url" required maxlength="2048" pattern="https://.*" value="${esc(params.api_base || "")}" placeholder="https://api.example.test/v1"></label>
    <details class="model-advanced"><summary>高级参数</summary><div class="form-grid three"><label>温度<input name="temperature" type="number" step="any" value="${params.temperature ?? ""}" placeholder="供应商默认"></label><label>最大输出长度<input name="max_tokens" type="number" min="1" step="1" value="${params.max_tokens ?? ""}" placeholder="供应商默认"></label><label>超时（秒）<input name="timeout" type="number" min="0.001" step="any" value="${params.timeout ?? ""}" placeholder="供应商默认"></label></div>
    <label>其他供应商参数（JSON 对象）<textarea name="extras" rows="5" placeholder='{"extra_body":{"enable_thinking":false}}'>${esc(extrasFor(value))}</textarea><small>保留 0 / false；禁止在这里写 api_key、password 或 secret。</small></label>
    </details><label>API Key<input name="secret" type="password" autocomplete="new-password" placeholder="${value.key_configured ? "留空则保持现有密钥" : "密钥仅保存到系统凭据库"}"></label>
    ${value.key_configured ? '<label class="check-row"><input name="delete_secret" type="checkbox">保存时清除密钥并停用模型</label>' : ""}
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用此模型</label>
    <div data-model-impact hidden><p data-model-impact-copy role="status"></p><label class="check-row"><input type="checkbox" name="acknowledged">我了解旧的待审建议将取消，重新扫描可能产生模型费用</label></div>
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存</button></div>
  </form>`);
  const form = $("[data-form='automation-model']", dialog);
  form.settingSnapshot = structuredClone(setting);
  form.addEventListener("input", (event) => {
    if (event.target.name === "acknowledged") return;
    form.impactSignature = null;
    $('[name="acknowledged"]', form).checked = false;
    $('[name="acknowledged"]', form).required = false;
    $('[data-model-impact]', form).hidden = true;
    $('button[type="submit"]', form).textContent = "保存";
  });
  form.addEventListener("submit", submitModel);
}

function modelParameters(data) {
  const extra = data.get("extras") ? JSON.parse(data.get("extras")) : {};
  if (!extra || Array.isArray(extra) || typeof extra !== "object") throw new Error("其他供应商参数必须是 JSON 对象");
  return { ...extra, model: String(data.get("model")), api_base: String(data.get("api_base")),
    temperature: numberOrNull(data.get("temperature")), max_tokens: numberOrNull(data.get("max_tokens"), true), timeout: numberOrNull(data.get("timeout")) };
}

function modelParameterSignature(params) {
  const sorted = (value) => Array.isArray(value) ? value.map(sorted)
    : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
  const effective = Object.fromEntries(Object.entries(params).filter(([, value]) => value != null));
  effective.timeout = params.timeout ?? 60;
  return JSON.stringify(sorted(effective));
}

function numberOrNull(value, integer = false) {
  if (String(value).trim() === "") return null;
  const parsed = integer ? Number.parseInt(value, 10) : Number(value);
  if (!Number.isFinite(parsed)) throw new Error("数值参数格式不正确");
  return parsed;
}

function showFormError(form, error) {
  const slot = $(".form-error-slot", form);
  const message = error.code === "AUTO_TAG_RULE_VERSION_CONFLICT"
    ? "规则或其统计已被其他操作更新，本次未保存。请保留草稿，关闭编辑器并刷新后重新核对；不会自动覆盖其他修改。"
    : error.message;
  if (slot) slot.innerHTML = `<div class="error" role="alert">${esc(message)}</div>`;
}

async function saveModels(models, expectedUpdatedTime) {
  return jsonRequest("/paam/system/v1/setting/automation", "PUT", { expected_updated_time: expectedUpdatedTime, models });
}

async function submitModel(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type='submit']", form);
  button.disabled = true;
  let configurationSaved = false;
  try {
    const data = new FormData(form);
    const id = Number(form.dataset.id);
    const snapshot = form.settingSnapshot;
    const existing = snapshot.models.find((item) => item.id === id);
    let litellmParams = modelParameters(data);
    const signature = modelParameterSignature(litellmParams);
    const changed = existing && signature !== modelParameterSignature(existing.litellm_params);
    if (changed) {
      if (form.impactSignature !== signature) {
        const [allRules, pending] = await Promise.all([
          readListOptions(ruleListUrl),
          readListOptions(`/paam/tag/v1/assignment_request/list?${assignmentQuery({ status: 1 }, 100)}`),
        ]);
        if (!form.isConnected) return;
        if (signature !== modelParameterSignature(modelParameters(new FormData(form)))) throw new Error("参数已改变，请重新查看保存影响");
        const affected = allRules.items.filter(rule => rule.method_config.model_id === id);
        const ids = new Set(affected.map(rule => rule.id));
        const count = pending.items.filter(item => ids.has(item.rule_id)).length;
        $('[data-model-impact-copy]', form).textContent = `将更新 ${affected.length} 条规则的判断版本、重置扫描进度，取消当前 ${count} 条待审建议。规则：${affected.map(rule => rule.name).join("、") || "无"}。数量为读取时快照；保存时以实际影响为准。已通过标签保留。`;
        $('[data-model-impact]', form).hidden = false;
        $('[name="acknowledged"]', form).required = true;
        button.textContent = "确认影响并保存";
        form.impactSignature = signature;
      }
      if (!data.has("acknowledged")) throw new Error("请核对模型参数变更影响，并勾选确认后保存");
    } else if (existing) {
      litellmParams = structuredClone(existing.litellm_params);
    }
    const desired = { id, name: String(data.get("name")), enabled: data.has("enabled"), litellm_params: litellmParams };
    const secret = String(data.get("secret") || "");
    if (data.has("delete_secret") && secret) throw new Error("清除密钥与填写新密钥不能同时选择");
    if (data.has("delete_secret")) desired.enabled = false;
    if (!existing && desired.enabled && !secret.trim()) throw new Error("新模型启用前必须填写 API Key");
    let models = snapshot.models.filter((item) => item.id !== id).map(({ key_configured: _key, ...item }) => item);
    models.push(secret ? { ...desired, enabled: false } : desired);
    let saved;
    saved = await saveModels(models, snapshot.updated_time);
    // Validate and version-check configuration before changing any credential.
    setting = saved;
    form.settingSnapshot = structuredClone(saved);
    configurationSaved = true;
    if (existing && data.has("delete_secret")) await jsonRequest(`/paam/system/v1/setting/automation/model/${id}/secret`, "DELETE");
    if (secret) await jsonRequest(`/paam/system/v1/setting/automation/model/${id}/secret`, "PUT", { secret });
    if (secret && desired.enabled) {
      models = saved.models.map(({ key_configured: _key, ...item }) => item.id === id ? desired : item);
      saved = await saveModels(models, saved.updated_time);
      form.settingSnapshot = structuredClone(saved);
    }
    setting = await request('/paam/system/v1/setting/automation');
    form.closest("dialog").close();
    window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { message: "模型配置已保存" } }));
  } catch (error) {
    showFormError(form, configurationSaved ? { message: `配置已保存，但后续步骤未完成：${error.message}。请核对密钥和启用状态。` } : error);
  } finally { button.disabled = false; }
}

function ruleDialog(rule = null) {
  const enabledViews = views.filter((item) => item.status === "ACTIVE" || item.id === rule?.view_id);
  const enabledModels = setting.models.filter((item) => (item.enabled && item.key_configured) || item.id === rule?.method_config.model_id);
  const value = rule || { name: "", view_id: enabledViews[0]?.id, enabled: false, cron: "*/5 * * * *", amount_mode: 1, method_config: { model_id: enabledModels[0]?.id, prompt: "" } };
  const dialog = openDialog(rule ? `编辑规则 #${rule.id}` : "新建自动打标签规则", `<form data-form="automation-rule" data-id="${rule?.id || ""}" class="stack automation-form">
    <div class="form-error-slot" aria-live="assertive"></div>
    <div class="form-grid"><label>规则名称<input name="name" required maxlength="120" value="${esc(value.name)}"></label><label>标签维度<select name="view_id" ${rule ? "disabled" : ""}>${enabledViews.map((item) => `<option value="${item.id}" ${item.id === value.view_id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label></div>
    <label>使用模型<select name="model_id">${enabledModels.map((item) => `<option value="${item.id}" ${item.id === value.method_config.model_id ? "selected" : ""}>${esc(item.name)}${item.enabled ? "" : "（已停用）"}</option>`).join("")}</select></label>
    <label>判断模板<select name="prompt_template"><option value="">自定义说明</option><option value="purpose">消费用途</option><option value="channel">购买渠道</option></select></label>
    <label>业务判断说明<textarea name="prompt" rows="3" required>${esc(value.method_config.prompt)}</textarea></label>
    <div class="form-grid"><label>执行频率<select name="frequency">${[["five", "每 5 分钟"], ["hour", "每小时"], ["day", "每天"], ["custom", "自定义 CRON"]].map(([id,label]) => `<option value="${id}" ${cronPreset(value.cron).frequency === id ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>金额发送方式<select name="amount_mode">${Object.entries(amountModeNames).map(([id, label]) => `<option value="${id}" ${Number(id) === value.amount_mode ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
    <label data-daily-time>每天的时间<input name="daily_time" type="time" value="${cronPreset(value.cron).time}"></label>
    <label data-custom-cron>CRON（5 或 6 段）<input name="cron" value="${esc(value.cron)}" placeholder="*/5 * * * *"></label>
    <small data-cron-copy>执行时区：香港（UTC+8）。保存后可在规则列表查看下次执行时间。</small>
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用规则配置</label>
    ${rule ? '<div class="automation-notice compact"><strong>本次保存的影响</strong><span data-rule-edit-impact role="status" aria-live="polite"></span></div><p data-rule-pending-count>正在读取待确认建议数量…</p><button type="button" class="quiet" data-rule-impact-retry hidden>重新读取影响数量</button><label class="check-row" data-rule-impact-ack hidden><input name="acknowledged" type="checkbox">我了解旧的待确认建议将取消，重新扫描可能产生模型费用</label>' : ""}
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存并回读</button></div>
  </form>`);
  const form = $("[data-form='automation-rule']", dialog);
  // Keep the version token bound to this exact form, not a later background read.
  form.ruleSnapshot = rule ? structuredClone(rule) : null;
  form.pendingCount = null;
  if (rule) {
    const refreshImpact = (event) => {
      const draft = Object.fromEntries(new FormData(form));
      const changed = semanticRuleChange(form.ruleSnapshot, draft);
      const acknowledgment = $('[name="acknowledged"]', form);
      if (event && ["model_id", "prompt", "amount_mode"].includes(event.target.name)) acknowledgment.checked = false;
      $('[data-rule-edit-impact]', form).textContent = ruleEditImpact(form.ruleSnapshot, draft, form.pendingCount);
      $('[data-rule-impact-ack]', form).hidden = !changed;
      acknowledgment.required = changed;
      const submit = $('button[type="submit"]', form);
      submit.textContent = changed ? "确认影响并保存" : "保存规则";
      submit.disabled = changed && form.pendingCount == null;
    };
    const readImpact = async () => {
      const retry = $('[data-rule-impact-retry]', form);
      retry.hidden = true;
      try {
        const pending = await request(`/paam/tag/v1/assignment_request/list?${assignmentQuery({ rule_id: rule.id, status: 1 }, 1)}`, { cache: "no-store" });
        form.pendingCount = pending.total;
        $('[data-rule-pending-count]', form).textContent = `当前有 ${pending.total} 条待确认建议（保存时会再次校验）。`;
      } catch (_error) {
        $('[data-rule-pending-count]', form).textContent = "待确认数量读取失败。修改判断内容前请重新读取；不将未知数量视为零。";
        retry.hidden = false;
      }
      refreshImpact();
    };
    form.addEventListener("input", refreshImpact);
    form.addEventListener("change", refreshImpact);
    $('[data-rule-impact-retry]', form).addEventListener("click", readImpact);
    refreshImpact();
    void readImpact();
  }
  const syncFrequency = () => {
    const frequency = $('[name="frequency"]', form).value;
    $('[data-daily-time]', form).hidden = frequency !== "day";
    $('[data-custom-cron]', form).hidden = frequency !== "custom";
    $('[name="daily_time"]', form).required = frequency === "day";
    $('[name="cron"]', form).required = frequency === "custom" && $('[name="enabled"]', form).checked;
  };
  $('[name="frequency"]', form).addEventListener("change", syncFrequency);
  $('[name="enabled"]', form).addEventListener("change", syncFrequency);
  $('[name="prompt_template"]', form).addEventListener("change", async (event) => {
    const template = event.currentTarget.value;
    if (!template) return;
    const prompt = $('[name="prompt"]', form);
    const previous = prompt.value;
    try {
      const templates = await request("/asset/tag/classification.json");
      const preset = templates.views?.find((view) => view.system_name === ({ purpose: "expense_purpose", channel: "purchase_channel" })[template]);
      if (!preset?.rule?.prompt) throw new Error("模板格式不正确");
      if (!form.isConnected || $('[name="prompt_template"]', form).value !== template || prompt.value !== previous) return;
      prompt.value = preset.rule.prompt;
      // Programmatic updates must run the same invalidation/acknowledgment logic as typing.
      prompt.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (error) { showFormError(form, error); }
  });
  syncFrequency();
  form.addEventListener("submit", submitRule);
}

export function cronPreset(cron) {
  if (cron === "*/5 * * * *") return { frequency: "five", time: "09:00" };
  if (cron === "0 * * * *") return { frequency: "hour", time: "09:00" };
  const daily = /^(\d|[1-5]\d) (\d|1\d|2[0-3]) \* \* \*$/.exec(cron);
  return daily ? { frequency: "day", time: `${daily[2].padStart(2,"0")}:${daily[1].padStart(2,"0")}` } : { frequency: "custom", time: "09:00" };
}

export function frequencyCron(frequency, time, original) {
  if (frequency === "five") return "*/5 * * * *";
  if (frequency === "hour") return "0 * * * *";
  if (frequency === "custom") return original;
  if (frequency !== "day" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error("请选择每天执行的时间");
  const [hour, minute] = time.split(":").map(Number);
  return `${minute} ${hour} * * *`;
}

async function submitRule(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type='submit']", form);
  button.disabled = true;
  try {
    const data = new FormData(form);
    const id = Number(form.dataset.id || 0);
    const existing = form.ruleSnapshot;
    if (semanticRuleChange(existing, Object.fromEntries(data)) && (form.pendingCount == null || !data.has("acknowledged"))) {
      throw new Error("请先读取影响数量，并确认待确认建议失效和重新扫描的影响");
    }
    const payload = {
      name: String(data.get("name")), method: 1,
      method_config: { schema_version: 1, model_id: Number(data.get("model_id")), prompt: String(data.get("prompt")) },
      enabled: data.has("enabled"), cron: frequencyCron(data.get("frequency"), data.get("daily_time"), String(data.get("cron"))), amount_mode: Number(data.get("amount_mode")),
    };
    if (existing) payload.expected_updated_time = existing.updated_time;
    else payload.view_id = Number(data.get("view_id"));
    const saved = await jsonRequest(existing ? `/paam/tag/v1/auto_rule/${id}` : "/paam/tag/v1/auto_rule", existing ? "PUT" : "POST", payload, true);
    form.closest("dialog").close();
    const warning = saved.warnings?.some((item) => item.code === "REGISTER_FAILED");
    window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { createdRuleId: existing ? null : saved.body.id, message: warning ? "规则已保存，但调度注册失败；请查看诊断" : scheduleStatus?.tag_scan_guard === "DISABLED" ? "规则已保存；服务尚未开启扫描" : "规则已保存，请查看实际调度状态" } }));
  } catch (error) {
    button.disabled = false;
    showFormError(form, error);
  }
}

async function testModel(button) {
  const result = $(`[data-model-result="${button.dataset.id}"]`);
  const expectedUpdatedTime = setting.updated_time;
  const dialog = openDialog("检查模型连接", '<p>将使用已保存的配置发送一条固定测试消息，不包含账目。最多一次请求、32 个输出 token，可能产生少量费用。不会开启自动扫描。</p><div data-connection-feedback role="status"></div><div class="actions"><button type="button" data-close>取消</button><button type="button" class="primary" data-confirm-connection>开始检查</button></div>');
  const confirm = $('[data-confirm-connection]', dialog);
  confirm.addEventListener('click', async () => {
    if (confirm.disabled) return;
    confirm.disabled = true;
    button.disabled = true;
    result.hidden = false;
    result.textContent = '正在检查连接…';
    $('[data-connection-feedback]', dialog).textContent = '正在检查连接…';
    try {
      const body = await jsonRequest(`/paam/system/v1/setting/automation/model/${button.dataset.id}/connection_check`, "POST", {confirmed:true, expected_updated_time:expectedUpdatedTime});
      const current = await request('/paam/system/v1/setting/automation');
      const stale = current.updated_time !== body.configuration_updated_time;
      const message = stale ? '配置已改变，本次检查结果已过期。请刷新后重试。' : `${body.connected ? '连接成功' : '连接未通过'} · ${body.message}（${displayTime(body.checked_at)}）`;
      result.className = 'automation-result';
      result.textContent = message;
      $('[data-connection-feedback]', dialog).textContent = message;
    } catch (error) {
      result.textContent = error.message;
      $('[data-connection-feedback]', dialog).textContent = error.message;
    } finally { button.disabled = false; confirm.textContent = '检查已结束'; }
  });
}

async function previewRule(button) {
  const dialog = openDialog("查看待分析账目", '<div class="automation-result" aria-live="polite">正在本地检查筛选条件…</div>');
  const result = $(".automation-result", dialog);
  button.disabled = true;
  try {
    const body = await jsonRequest(`/paam/tag/v1/auto_rule/${button.dataset.id}/candidate_preview`, "POST", {});
    result.className = "automation-result simulated";
    result.innerHTML = candidatePreviewMarkup(body);
    result.addEventListener("click", async (event) => {
      const sample = event.target.closest('[data-preview-ledger]');
      if (!sample || sample.disabled) return;
      sample.disabled = true;
      try {
        const ledger = await request(`/paam/ledger/v1/flow/${sample.dataset.previewLedger}`);
        if (!dialog.isConnected) return;
        openDialog(`账目 #${sample.dataset.previewLedger} · 本地只读详情`, `${detailFields([
          ["摘要", ledger.ledger_entry.summary || "无摘要"], ["金额", money(ledger.ledger_entry)],
          ["时间", displayTime(ledger.ledger_entry.occurred_time)], ["状态", ledger.ledger_entry.active ? "当前有效" : "已失效"],
        ])}<h3>关联交易事实</h3><ul class="candidate-samples">${(ledger.facts || []).map((fact) => `<li><strong>${esc(fact.counterparty_name || "未记录交易对方")} · ${esc(money(fact))}</strong><span>${esc(fact.summary || "无摘要")}</span></li>`).join("") || "<li>未找到关联交易事实</li>"}</ul><p>仅在本机展示，不发送给模型，不修改账目。</p>`);
      } catch (error) {
        if (dialog.isConnected) openDialog("账目读取失败", `<p role="alert">${esc(error.message)}</p>`);
      } finally { sample.disabled = false; }
    });
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
  return value ? date(value) : "—";
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
  const task = schedule?.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  const execution = ruleExecution(rule, schedule, model, view);
  const scheduling = execution.reason ? `${execution.label}；${execution.reason}` : execution.label;
  const latest = !task ? "无调度执行记录" : !task.last_result ? "尚无执行结果" : `${executionResultNames[task.last_result] || "结果未知"}；${task.last_error_code ? scheduleExplanation(task.last_error_code) : "无错误码"}`;
  return `<div class="automation-detail stack">
    <section class="inspection-card"><h3>规则配置</h3>${detailFields([
      ["规则", `${rule.name} · #${rule.id} · 修订 ${rule.rule_revision}`],
      ["配置开关", rule.enabled ? "已启用" : "已停用"],
      ["标签维度", `${view?.name || `View #${rule.view_id}`} (#${rule.view_id})`],
      ["模型", `${model?.name || `Model #${rule.method_config.model_id}`} (#${rule.method_config.model_id})`],
      ["CRON", rule.cron || "未设置"], ["金额披露", amountModeNames[rule.amount_mode]],
      ["业务判断说明", rule.method_config.prompt], ["最后修改", displayTime(rule.updated_time)],
    ])}</section>
    <section class="inspection-card"><h3>调度与最近执行</h3>${detailFields([
      ["调度状态", scheduling], ["最近结果", latest],
      ["扫描游标", `Ledger #${rule.scan_after_ledger_id} · Epoch ${rule.scan_epoch}`],
    ])}${task ? runtimeMarkup({ ...schedule, tasks: [task] }) : ""}</section>
    <section><h3>最近建议 / 审查结果</h3>${recent.items.length ? `<div class="automation-detail-list">${recent.items.map((item) => `<div><button type="button" class="quiet" data-action="tag-request-detail" data-id="${item.id}">Request #${item.id} · ${esc(item.ledger_counterparty_name || item.ledger_summary || `Ledger #${item.ledger_id}`)}</button><span>${esc(item.proposed_tag_name)} · ${esc(requestStatusNames[item.status])} · ${esc(displayTime(item.created_time))}</span></div>`).join("")}</div>` : "<p>尚无建议请求；失败的调度也可能未生成请求。</p>"}</section>
    <button type="button" class="quiet" data-action="rule-requests" data-id="${rule.id}">查看这条规则的全部建议</button>
    <section class="inspection-card"><h3>采纳统计</h3>${ruleStatistics(summary)}</section>
    <section><button type="button" class="quiet" data-action="disclosure-preview" data-mode="${rule.amount_mode}">查看发送示例</button></section>
  </div>`;
}

function requestDetailMarkup(item, ledger, rule, scope = null) {
  const facts = ledger.facts || [];
  const currentTags = (ledger.ledger_entry.tags || []).filter((tag) => tag.view_system_name === item.view_system_name);
  const revisionChanged = rule.rule_revision !== item.rule_revision;
  const sourceNames = { MANUAL: "人工设置", AUTO_RULE: "自动建议" };
  const blockedReason = revisionChanged ? "规则版本已过期" : !ledger.ledger_entry.active ? "账目已失效" : !scope ? "同范围建议未读取，请刷新核对" : "";
  const currentLabel = currentTags.length ? currentTags.map((tag) => tag.tag_system_name === "unclassified" ? tag.tag_name : `${tag.tag_name}（${sourceNames[tag.source_type] || "来源未知"}）`).join("、") : "目标维度尚无标签";
  return `<div class="automation-detail stack">
    <section class="request-decision"><div class="automation-section-head"><div><h3>${esc(item.ledger_counterparty_name || facts[0]?.counterparty_name || "未记录交易对方")} · ${esc(money(ledger.ledger_entry))}</h3><p>${esc(ledger.ledger_entry.summary || item.ledger_summary || "无账目摘要")} · ${esc(displayTime(ledger.ledger_entry.occurred_time))}</p></div>${requestStatusPill(item.status)}</div>
      <div class="request-comparison"><div><small>当前标签 · ${esc(item.view_name)}</small><strong>${esc(currentLabel)}</strong></div><span aria-hidden="true">→</span><div><small>${item.status === 1 ? "本次建议" : "当时建议"}</small><strong>${esc(item.proposed_tag_name)}</strong></div></div>
      <h3>建议理由 ${helpTip("建议依据", "保留的是理由摘要，历史模型输入与原始响应未保存。")}</h3><p class="request-reason">${esc(item.reason_summary || "模型未提供可展示的理由")}</p>
      <p class="automation-detail-note">${esc(requestVersionCopy(item, rule))}</p>
      <div class="request-decision-actions"><span>${item.status === 1 ? esc(blockedReason || "通过后才会修改标签；请核对下方影响范围") : "此建议已处理，不能再次通过或拒绝"}</span><div class="automation-actions">${requestActionsMarkup(item, blockedReason)}</div></div></section>
    <section><h3>同范围建议与操作影响</h3>${scopeImpactMarkup(item, scope)}</section>
    <section><h3>当前账目 ${helpTip("账目时间范围", "展示当前账目与交易事实，可能与生成建议时不同。")}</h3>${detailFields([
      ["账目", `#${item.ledger_id} · ${ledger.ledger_entry.active ? "当前有效" : "已失效"}`],
      ["方向与金额", `${ledger.ledger_entry.entry_direction === 2 ? "支出" : ledger.ledger_entry.entry_direction === 1 ? "收入" : "其他"} · ${money(ledger.ledger_entry)}`], ["账目时间", displayTime(ledger.ledger_entry.occurred_time)],
    ])}${facts.length ? `<div class="automation-detail-list">${facts.map((fact) => `<div><strong>${esc(fact.counterparty_name || "未记录交易对方")} · ${esc(money(fact))}</strong><span>${esc(fact.summary || "无交易摘要")} · ${esc(displayTime(fact.occurred_time))}</span></div>`).join("")}</div>` : "<p>未找到关联交易事实；无法仅凭本页核对该建议。</p>"}</section>
    <section><h3>来源规则</h3>${detailFields([
      ["来源规则", `${item.rule_name} · #${item.rule_id} · 建议修订 ${item.rule_revision}`],
      ["生成时间", displayTime(item.created_time)], ["当前规则修订", rule.rule_revision],
      ["当前判断说明（非历史快照）", rule.method_config.prompt],
    ])}</section>
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
  const toolbar = $('.request-batch-actions', root);
  if (toolbar) toolbar.hidden = selected === 0;
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
    if (target.isConnected) preserveView(root, () => patchMarkup(target, diagnosticsMarkup(events, options)));
  } finally { root.dataset.commandPending = "false"; }
}

async function loadAutomationSnapshot(page, signal) {
  const read = (url) => request(url, { signal, cache: "no-store" });
  const kind = page.dataset.autoPage;
  if (kind === "settings") {
    const [schedule, diagnostics, currentSetting] = await Promise.all([read("/paam/system/v1/schedule/status"), diagnosticSnapshot(page, signal), read('/paam/system/v1/setting/automation')]);
    return { schedule, diagnostics, currentSetting };
  }
  if (kind === "requests") return { requests: await read(`/paam/tag/v1/assignment_request/list?${page.dataset.requestQuery}`) };
  if (kind === "request") {
    const item = await read(`/paam/tag/v1/assignment_request/${page.dataset.requestId}`);
    const [ledger, rule, scope] = await Promise.all([read(`/paam/ledger/v1/flow/${item.ledger_id}`), read(`/paam/tag/v1/auto_rule/${item.rule_id}`), readRequestScope(item, signal)]);
    return { item, ledger, rule, scope };
  }
  const [schedule, currentSetting, viewPage] = await Promise.all([
    read("/paam/system/v1/schedule/status"), read("/paam/system/v1/setting/automation"), readListOptions(viewListUrl, signal),
  ]);
  if (kind === "rules") return { schedule, currentSetting, viewPage, rulePage: await read(`/paam/tag/v1/auto_rule/list?${page.dataset.ruleQuery}`) };
  const [rule, summary, recent] = await Promise.all([
    read(`/paam/tag/v1/auto_rule/${page.dataset.ruleId}`), read(`/paam/tag/v1/auto_rule/${page.dataset.ruleId}/summary`),
    read(`/paam/tag/v1/assignment_request/list?${page.dataset.recentQuery}`),
  ]);
  return { schedule, currentSetting, viewPage, rule, summary, recent, diagnostics: await diagnosticSnapshot(page, signal) };
}

function applyAutomationSnapshot(root, page, data) {
  preserveView(root, () => applyAutomationData(root, page, data));
}

function applyAutomationData(root, page, data) {
  const settingChanged = data.currentSetting && JSON.stringify(data.currentSetting) !== JSON.stringify(setting);
  const focused = root.contains(document.activeElement) ? document.activeElement : null;
  const focusMatch = focused?.hasAttribute("data-tag-request-select") ? (node) => node.value === focused.value
    : focused?.dataset.action ? (node) => node.dataset.action === focused.dataset.action && node.dataset.id === focused.dataset.id : null;
  if (data.schedule) scheduleStatus = data.schedule;
  if (data.currentSetting) setting = data.currentSetting;
  if (data.viewPage) views = data.viewPage.items;
  root.dataset.autoStale = "false";
  root.querySelector('[data-refresh-error]')?.remove();
  if (page.dataset.autoPage === "settings") {
    if (settingChanged) {
      patchMarkup($('[data-auto-models]', root), setting.models.map(modelSummary).join('') || '<p>尚未配置模型。添加连接并保存密钥后，规则才可选择模型。</p>');
      patchMarkup($('[data-auto-disclosure]', root), disclosureSummaryMarkup());
    }
    patchMarkup($('[data-auto-runtime]', root), runtimeMarkup(data.schedule));
    patchMarkup($('[data-auto-notice]', root), scheduleNotice());
  }
  if (data.rulePage) {
    rules = data.rulePage.items;
    patchMarkup($('[data-auto-notice]', root), scheduleNotice());
    patchMarkup($('[data-auto-rule-rows]', root), rules.map(ruleRow).join("") || '<tr><td colspan="5">尚无规则</td></tr>');
    patchMarkup($('[data-auto-rule-pager]', root), requestPagerMarkup(data.rulePage, "auto-rule-page"));
    const create = $('[data-action="rule-new"]', root);
    if (create) create.disabled = !views.some((item) => item.status === "ACTIVE") || !setting.models.some((item) => item.enabled && item.key_configured);
  }
  if (data.summary) patchMarkup($('[data-auto-rule-detail]', root), ruleDetailMarkup(data.rule, data.schedule, data.recent, data.summary));
  if (data.diagnostics && JSON.stringify(data.diagnostics.options) === JSON.stringify(diagnosticOptions(root))
    && data.diagnostics.pageIndex === Number($('[data-diagnostic-state]', root)?.dataset.page || 1)) {
    patchMarkup($('[data-auto-diagnostics]', root), diagnosticsMarkup(data.diagnostics.events, data.diagnostics.options));
  }
  if (data.requests) {
    const selected = new Set($$('[data-tag-request-select]:checked', root).map((item) => item.value));
    requestItems = data.requests.items;
    patchMarkup($('[data-auto-request-rows]', root), requestRowsMarkup(requestItems));
    patchMarkup($('[data-auto-request-pager]', root), requestPagerMarkup(data.requests));
    $$('[data-tag-request-select]:not(:disabled)', root).forEach((item) => { item.checked = selected.has(item.value); });
    updateRequestActions(root);
  }
  if (data.item) {
    requestItems = [data.item];
    patchMarkup($('[data-auto-request-detail]', root), requestDetailMarkup(data.item, data.ledger, data.rule, data.scope));
  }
  if (focusMatch && !focused.isConnected) $$('[data-tag-request-select], [data-action]', root).find(focusMatch)?.focus({ preventScroll: true });
}

export function startAutomationRefresh(root, { stale = false } = {}) {
  const page = $('[data-auto-page]', root);
  if (!page) return;
  stopAutomationPolling();
  root.dataset.autoStale = String(stale);
  const route = location.hash;
  stopPolling = startVisiblePoll({
    isAlive: () => page.isConnected && location.hash === route,
    canPoll: () => ![...document.querySelectorAll("dialog[open]")].some(dialog => !dialog.contains(root)) && root.dataset.commandPending !== "true"
      && !(root.contains(document.activeElement) && document.activeElement.matches("input:not([type='checkbox']), select, textarea")),
    load: (signal) => loadAutomationSnapshot(page, signal),
    apply: (data) => applyAutomationSnapshot(root, page, data),
    onState: ({ state, lastSuccess }) => {
      const feedback = $('[data-auto-freshness]', root);
      if (!feedback) return;
      const last = lastSuccess ? displayTime(lastSuccess) : "本页初次读取";
      const unknown = state === "UNKNOWN" || (state === "REFRESHING" && root.dataset.autoStale === "true");
      feedback.textContent = unknown ? `连接中断，当前状态未知 · 上次更新：${last}`
        : state === "PAUSED" ? `已暂停刷新 · ${last}`
        : state === "CURRENT" ? `已更新 · ${last}`
        : "状态自动更新";
      if (unknown) {
        root.dataset.autoStale = "true";
        const notice = $('[data-auto-notice]', root);
        if (notice) notice.textContent = "连接中断，扫描状态未知。";
        $$('[data-action="tag-request-transition"]', root).forEach((button) => { button.disabled = true; });
      }
      updateRequestActions(root);
    },
  });
}

function showBatchFeedback(root, markup) {
  batchFeedback = markup;
  const slot = $('[data-batch-feedback]', root);
  if (slot) preserveView(root, () => patchMarkup(slot, markup));
}

async function transitionRequests(root, button, rerender) {
  if (root.dataset.commandPending === "true" || root.dataset.autoStale === "true") return;
  const ids = button.dataset.id ? [Number(button.dataset.id)]
    : $$('[data-tag-request-select]:checked:not(:disabled)', root).map((item) => Number(item.value));
  if (!ids.length) return;
  const approve = button.dataset.operation === "approve";
  const selected = requestItems.filter((item) => ids.includes(item.id));
  const conflict = approve && selectionConflict(selected);
  const selectionSummary = selected.map((item) => `<li><strong>#${item.id} · ${esc(item.ledger_counterparty_name || item.ledger_summary || `账目 #${item.ledger_id}`)}</strong><span>${esc(item.view_name)} → ${esc(item.proposed_tag_name)}</span></li>`).join("");
  const dialog = openDialog(approve ? "确认通过标签建议" : "确认拒绝标签建议", `<p>本次选择 ${ids.length} 项。</p><ul class="candidate-samples">${selectionSummary}</ul><p>${approve ? "通过后修改对应账目与维度的标签；同范围其他待确认建议会被取消，原已通过建议会被标为已替换。其他账目和维度不变。同一账目 + 维度只能选一项；服务端将重新核对版本、账目和人工标签，冲突不会强制覆盖。" : "只将这些申请标为已拒绝，不修改现有标签或其他建议。"}</p>${conflict ? '<p class="error">选择中有同范围冲突项，这些项不会通过；其他有效范围仍可处理。可取消后调整选择。</p>' : ""}<p>返回逐项结果；有效项会提交，失败项不改变标签。重复已处理项不重复计数；超时结果未知时请先刷新核对。</p><div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="button" class="primary" data-confirm-batch>确认${approve ? "通过" : "拒绝"}</button></div>`);
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
  const deepLink = $('[data-open-rule]', root);
  if (deepLink) {
    const id = Number(deepLink.dataset.openRule);
    delete deepLink.dataset.openRule;
    openRuleInspection(id, root, rerender, notify, navigate).catch(error => notify(error.message, true));
  }
  if (boundRoots.has(root)) return;
  boundRoots.add(root);
  root.addEventListener("submit", async (event) => {
    if (event.target.matches('[data-form="auto-rule-filter"]')) {
      event.preventDefault();
      const params = new URLSearchParams(new FormData(event.target));
      params.set("page", "1");
      navigate?.("auto-rules", params);
      return;
    }
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
      if (action === "rule-new") ruleDialog();
      if (action === "auto-rule-page") { params.delete("rule_id"); params.set("page", button.dataset.requestPage); navigate?.("auto-rules", params); }
      if (action === "rule-edit") ruleDialog(rules.find((item) => item.id === id));
      if (action === "rule-preview") await previewRule(button);
      if (action === "rule-detail") { event.preventDefault(); await openRuleInspection(id, root, rerender, notify, navigate); }
      if (action === "rule-detail-back") navigate?.("auto-rules");
      if (action === "rule-requests") { batchFeedback = ""; navigate?.("tag-review", new URLSearchParams({ rule_id: String(id), status: "" })); }
      if (action === "tag-request-detail") { event.preventDefault(); batchFeedback = ""; params.set("request_id", String(id)); navigate?.("tag-review", params); }
      if (action === "tag-request-detail-back") { params.delete("request_id"); navigate?.("tag-review", params); }
      if (action === "tag-request-scope") { batchFeedback = ""; navigate?.("tag-review", new URLSearchParams({ ledger_id: button.dataset.ledgerId, view_id: button.dataset.viewId, status: "" })); }
      if (action === "tag-request-clear-scope") { params.delete("ledger_id"); params.set("page", "1"); navigate?.("tag-review", params); }
      if (action === "tag-request-page") { batchFeedback = ""; params.set("page", button.dataset.requestPage); navigate?.("tag-review", params); }
      if (["tag-request-transition", "tag-request-batch"].includes(action)) await transitionRequests(root, button, rerender);
    } catch (error) { button.disabled = false; notify(error.message, true); }
  });
}

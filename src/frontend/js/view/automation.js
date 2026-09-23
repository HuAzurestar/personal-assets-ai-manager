import { request, jsonRequest } from "../api/client.js?v=20260922.3";
import { $, $$, esc, money } from "../util/core.js";

let setting = null;
let rules = [];
let views = [];
let scheduleStatus = null;

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
    ["超时", params.timeout == null ? "默认" : `${params.timeout} 秒`],
  ].map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("");
  return `<article class="automation-card" data-model-card="${model.id}">
    <header><div><span class="eyebrow">MODEL #${model.id}</span><h3>${esc(model.name)}</h3></div>${statusPill(model.enabled, model.key_configured)}</header>
    <dl class="automation-definition">${details}</dl>
    <div class="automation-card-note"><span>API Key</span><strong>${model.key_configured ? "已安全配置，不会回显" : "尚未配置"}</strong></div>
    <div class="automation-actions">
      <button type="button" class="quiet" data-action="model-test" data-id="${model.id}">虚构数据测试</button>
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
  return `<div class="automation-page">
    <section class="automation-hero">
      <div><span class="eyebrow">SETTINGS · AUTOMATION</span><h2>自动化设置</h2><p>模型连接保存在本地设置中；密钥进入系统凭据存储，普通读取永不回显。</p></div>
      <button type="button" class="primary" data-action="model-new">＋ 添加模型</button>
    </section>
    <div class="automation-notice" role="note"><strong>模型安全边界</strong><span>规则配置可以保存；本阶段只有显式隔离的合成验收库才会注册 CRON 扫描。API Key 只从系统凭据存储读取，不写入 SQLite，也不会回显。</span></div>
    <section class="automation-section" aria-labelledby="automation-model-title">
      <div class="automation-section-head"><div><h3 id="automation-model-title">模型连接</h3><p>${setting.models.length ? `共 ${setting.models.length} 个本地配置` : "添加首个模型后，规则才可选择模型"}</p></div></div>
      <div class="automation-grid">${modelCards || '<div class="panel empty-state"><strong>尚未配置模型</strong><p>添加一个 LiteLLM 兼容配置；启用前必须保存 API Key。</p><button type="button" data-action="model-new">添加模型</button></div>'}</div>
    </section>
    <section class="automation-section disclosure-readonly" aria-labelledby="automation-disclosure-title">
      <div class="automation-section-head"><div><h3 id="automation-disclosure-title">金额披露</h3><p>本阶段只读展示；编辑与安全预览属于 DEV-013。</p></div><span class="automation-status inactive"><i></i>尚未接通编辑</span></div>
      <div class="disclosure-card"><div><span>时间粒度</span><strong>${esc(setting.disclosure?.date_granularity || "未配置")}</strong></div><div><span>按币种区间（最小货币单位）</span>${bands || "<strong>尚未配置</strong>"}</div></div>
    </section>
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
    : !scheduleTask ? statusPill(true, false, "已保存·未调度")
    : !schedulerRunning ? statusPill(true, false, "调度已停止")
    : statusPill(true);
  const resultNames = { COMPLETED: "完成", PARTIAL_FAILURE: "部分失败", FAILED: "失败", CANCELLED: "已取消" };
  const lastRun = scheduleTask?.last_result
    ? `上次执行：${resultNames[scheduleTask.last_result] || scheduleTask.last_result}${scheduleTask.last_error_code ? ` · ${scheduleTask.last_error_code}` : ""}`
    : scheduleTask ? "尚未执行" : "未注册到调度器";
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
  const running = scheduleStatus?.scheduler_state === "RUNNING" && scheduleStatus?.worker_state === "HEALTHY";
  const nextRuns = tasks.map((item) => item.next_run_at).filter(Boolean).sort();
  const failed = tasks.filter((item) => ["FAILED", "PARTIAL_FAILURE"].includes(item.last_result)).length;
  const title = !tasks.length ? "自动标签扫描未启用"
    : running ? `CRON 调度运行中 · ${tasks.length} 条规则已注册` : "CRON 调度未运行";
  const next = !tasks.length
    ? "规则配置可保存；默认模式不扫描账单。只有显式合成验收环境会注册自动扫描"
    : nextRuns.length ? `下一次触发：${new Date(nextRuns[0]).toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong" })}` : "当前没有待触发的启用规则";
  const failure = failed ? `；${failed} 条规则上次执行失败，请打开规则详情查看具体原因` : "";
  return `<div class="automation-notice compact" role="status" aria-live="polite"><strong>${esc(title)}</strong><span>${esc(next + failure)}。CRON 触发进入共享 FIFO；本页仍不提供手动执行或重扫入口。</span></div>`;
}

export async function autoRulesPanel(tagViews) {
  views = tagViews;
  const [rulePage, currentSetting, currentSchedule] = await Promise.all([
    request("/paam/tag/v1/auto_rule/list?page_index=1&page_size=100&sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D"),
    request("/paam/system/v1/setting/automation"),
    request("/paam/system/v1/schedule/status"),
  ]);
  rules = rulePage.items;
  setting = currentSetting;
  scheduleStatus = currentSchedule;
  const canCreate = views.some((item) => item.status === "ACTIVE") && setting.models.some((item) => item.enabled && item.key_configured);
  return `<section class="tag-manager automation-rules" aria-labelledby="auto-rule-title">
    <div class="tag-manager-head"><div><h2 id="auto-rule-title">自动打标签规则</h2><p>同一标签维度可配置多条独立规则；配置和进度从 SQLite 实时读取。</p></div><button type="button" class="primary" data-action="rule-new" ${canCreate ? "" : 'disabled title="需要启用中的标签维度和模型"'}>＋ 新建规则</button></div>
    ${scheduleNotice()}
    <div class="automation-table-wrap"><table class="automation-table rule-table"><thead><tr><th>规则</th><th>View / 模型</th><th>调度 / 进度</th><th>累计统计</th><th>状态 / 操作</th></tr></thead><tbody>${rules.map(ruleRow).join("") || '<tr><td colspan="5" class="table-empty"><strong>尚未创建自动规则</strong><span>启用模型并准备标签维度后即可保存第一条规则。</span></td></tr>'}</tbody></table></div>
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
    const [rule, viewPage, currentSetting, schedule, recent] = await Promise.all([
      request(`/paam/tag/v1/auto_rule/${ruleId}`),
      request("/paam/tag/v1/view/list?page_index=1&page_size=100"),
      request("/paam/system/v1/setting/automation"),
      request("/paam/system/v1/schedule/status"),
      request(`/paam/tag/v1/assignment_request/list?${query}`),
    ]);
    views = viewPage.items;
    setting = currentSetting;
    return `<div class="auto-rules-page automation-detail-page"><button type="button" class="quiet" data-action="rule-detail-back">← 返回规则列表</button><h2>规则 #${ruleId} · ${esc(rule.name)}</h2>${ruleDetailMarkup(rule, schedule, recent)}</div>`;
  }
  const viewPage = await request("/paam/tag/v1/view/list?page_index=1&page_size=100");
  return `<div class="auto-rules-page">${await autoRulesPanel(viewPage.items)}</div>`;
}

const requestStatusNames = {
  1: "待确认", 2: "已通过", 3: "已拒绝", 4: "已取消", 5: "已替换",
};

function requestStatusPill(status) {
  const active = Number(status) === 2;
  const pending = Number(status) === 1;
  return `<span class="automation-status ${active ? "active" : pending ? "pending" : "inactive"}"><i></i>${esc(requestStatusNames[status] || status)}</span>`;
}

export async function tagReviewPage(params = new URLSearchParams()) {
  const requestId = Number(params.get("request_id"));
  if (Number.isSafeInteger(requestId) && requestId > 0) {
    const item = await request(`/paam/tag/v1/assignment_request/${requestId}`);
    const [ledger, rule] = await Promise.all([
      request(`/paam/ledger/v1/flow/${item.ledger_id}`),
      request(`/paam/tag/v1/auto_rule/${item.rule_id}`),
    ]);
    return `<div class="tag-review-page automation-detail-page"><button type="button" class="quiet" data-action="tag-request-detail-back">← 返回建议列表</button><h2>建议请求 #${requestId} · 判断依据</h2>${requestDetailMarkup(item, ledger, rule)}${item.status === 1 ? `<div class="automation-actions"><button type="button" class="primary" data-action="tag-request-transition" data-operation="approve" data-id="${item.id}">通过并打标</button><button type="button" class="quiet" data-action="tag-request-transition" data-operation="reject" data-id="${item.id}">拒绝</button></div>` : ""}</div>`;
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
  const viewOptions = viewPage.items.map((item) => `<option value="${item.id}" ${String(item.id) === viewId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  const ruleOptions = rulePage.items.map((item) => `<option value="${item.id}" ${String(item.id) === ruleId ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  const rows = result.items.map((item) => `<tr data-tag-request-row="${item.id}">
    <td><label class="request-selector"><input type="checkbox" data-tag-request-select value="${item.id}" ${item.status === 1 ? "" : "disabled"}><span><strong>Request #${item.id} · ${esc(item.ledger_counterparty_name || "未记录交易对方")}</strong><small>${esc(item.ledger_summary || "无账目摘要；请查看详情")}</small></span></label></td>
    <td><strong>${item.ledger_amount == null ? `Ledger #${item.ledger_id}` : esc(money({ amount: item.ledger_amount, currency_code: item.ledger_currency_code }))}</strong><small>Ledger #${item.ledger_id} · ${esc(item.view_name)} · ${item.ledger_active ? "当前有效" : "Ledger 已失效"}</small></td>
    <td><strong>${esc(item.proposed_tag_name)}</strong><small>${esc(item.proposed_tag_system_name)}</small></td>
    <td><strong>${esc(item.rule_name)}</strong><small>Rule #${item.rule_id} · Revision ${item.rule_revision}</small></td>
    <td>${requestStatusPill(item.status)}</td>
    <td><div class="automation-actions request-actions"><button type="button" class="quiet" data-action="tag-request-detail" data-id="${item.id}">查看依据</button>${item.status === 1 ? `<button type="button" class="primary" data-action="tag-request-transition" data-operation="approve" data-id="${item.id}">通过并打标</button><button type="button" class="quiet" data-action="tag-request-transition" data-operation="reject" data-id="${item.id}">拒绝</button>` : ""}</div></td>
  </tr>`).join("");
  const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
  const pager = `<div class="request-pager"><span>共 ${result.total} 条 · 第 ${result.page_index}/${lastPage} 页</span><div><button type="button" class="quiet" data-action="tag-request-page" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><button type="button" class="quiet" data-action="tag-request-page" data-page="${page + 1}" ${page >= lastPage ? "disabled" : ""}>下一页</button></div></div>`;
  return `<div class="tag-review-page">
    <section class="tag-manager automation-requests" aria-labelledby="tag-request-title">
      <div class="tag-manager-head"><div><h2 id="tag-request-title">标签建议请求</h2><p>列表展示交易对方、摘要和金额；点击「查看依据」核对账目、现有标签与建议理由后再决定。</p></div><div class="request-batch-actions"><button type="button" class="primary" data-action="tag-request-batch" data-operation="approve" disabled>批量通过</button><button type="button" class="quiet" data-action="tag-request-batch" data-operation="reject" disabled>批量拒绝</button></div></div>
      <div class="automation-notice compact" role="status" aria-live="polite"><strong>人工确认边界</strong><span>通过时会重新核对规则版本、Ledger、View、候选标签和当前人工标签；冲突不会强制覆盖。请求超时请刷新核对状态。</span></div>
      <div class="review-filter-skeleton" aria-labelledby="tag-review-filter-title">
        <form class="form-grid three" data-form="tag-request-filter">
          <label>标签维度<select name="view_id"><option value="">全部 View</option>${viewOptions}</select></label>
          <label>来源规则<select name="rule_id"><option value="">全部规则</option>${ruleOptions}</select></label>
          <label>建议状态<select name="status"><option value="" ${status === "" ? "selected" : ""}>全部状态</option>${Object.entries(requestStatusNames).map(([value, label]) => `<option value="${value}" ${status === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        </form>
      </div>
      <div class="automation-table-wrap"><table class="automation-table request-table" aria-labelledby="tag-review-list-title"><thead><tr><th id="tag-review-list-title"><label class="request-select-all"><input type="checkbox" data-tag-request-select-all> 请求</label></th><th>Ledger / View</th><th>建议标签</th><th>来源规则</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="table-empty"><strong>没有符合条件的建议请求</strong><span>等待下一次规则扫描，或调整上方筛选条件。</span></td></tr>'}</tbody></table></div>
      ${pager}
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
    <div class="automation-notice compact"><strong>保存规则配置</strong><span>仅显式隔离的合成验收环境可注册 CRON；模型只生成待人工确认的建议，不会直接修改标签。</span></div>
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
    await jsonRequest(existing ? `/paam/tag/v1/auto_rule/${id}` : "/paam/tag/v1/auto_rule", existing ? "PUT" : "POST", payload);
    form.closest("dialog").close();
    window.dispatchEvent(new CustomEvent("paam:automation-saved", { detail: { message: "规则已保存并从 SQLite 回读" } }));
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
    ITEM_FAILURE: "至少一条账目分析失败；逐项错误目前未持久化。",
    JOB_CALLBACK_FAILED: "调度回调异常；需要查看服务端日志。",
  };
  return code ? `${explanations[code] || "执行失败，请查看服务端日志。"}（${code}）` : "无错误码记录";
}

function ruleDetailMarkup(rule, schedule, recent) {
  const view = views.find((item) => item.id === rule.view_id);
  const model = setting?.models.find((item) => item.id === rule.method_config.model_id);
  const task = schedule.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  const running = schedule.scheduler_state === "RUNNING" && schedule.worker_state === "HEALTHY";
  const scheduling = !rule.enabled ? "规则已停用，不会调度" : !task ? "未注册 CRON 任务；当前环境可能未启用自动扫描" : !running ? "调度器未运行" : `${task.queue_state}；下一次触发 ${displayTime(task.next_run_at)}`;
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
    ])}<p class="automation-detail-note">调度器仅提供当前进程的最近结果和下一次触发；不保存每次运行的完整日志。累计计数从数据库读取。资格预览不会请求模型，也不会推进游标。</p></section>
    <section><h3>最近建议 / 审查结果</h3>${recent.items.length ? `<div class="automation-detail-list">${recent.items.map((item) => `<div><button type="button" class="quiet" data-action="tag-request-detail" data-id="${item.id}">Request #${item.id} · ${esc(item.ledger_counterparty_name || item.ledger_summary || `Ledger #${item.ledger_id}`)}</button><span>${esc(item.proposed_tag_name)} · ${esc(requestStatusNames[item.status])} · ${esc(displayTime(item.created_time))}</span></div>`).join("")}</div>` : "<p>尚无建议请求；失败的调度也可能未生成请求。</p>"}</section>
    <section><h3>模型输入与输出</h3><p class="automation-detail-note">历史模型请求正文与原始响应未被保存，不能准确回放；当前只保留建议标签和清洗后的理由。此处不会将资格预览冒充模型审查结果。</p></section>
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

export function bindAutomation(root, rerender, notify, navigate) {
  $$('[data-action="model-new"]', root).forEach((button) => button.addEventListener("click", () => modelDialog()));
  $$('[data-action="model-edit"]', root).forEach((button) => button.addEventListener("click", () => modelDialog(setting.models.find((item) => item.id === Number(button.dataset.id)))));
  $$('[data-action="model-test"]', root).forEach((button) => button.addEventListener("click", () => testModel(button)));
  $$('[data-action="model-secret-delete"]', root).forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("清除该模型的本地 API Key？启用状态可能因此不可用。")) return;
    button.disabled = true;
    try {
      await request(`/paam/system/v1/setting/automation/model/${button.dataset.id}/secret`, { method: "DELETE" });
      notify("密钥已从系统凭据存储清除");
      await rerender();
    } catch (error) { button.disabled = false; notify(error.message, true); }
  }));
  $('[data-action="rule-new"]', root)?.addEventListener("click", () => ruleDialog());
  $$('[data-action="rule-edit"]', root).forEach((button) => button.addEventListener("click", () => ruleDialog(rules.find((item) => item.id === Number(button.dataset.id)))));
  $$('[data-action="rule-preview"]', root).forEach((button) => button.addEventListener("click", () => previewRule(button)));
  $$('[data-action="rule-detail"]', root).forEach((button) => button.addEventListener("click", () => navigate?.("auto-rules", new URLSearchParams({ rule_id: button.dataset.id }))));
  $('[data-action="rule-detail-back"]', root)?.addEventListener("click", () => navigate?.("auto-rules"));
  $$('[data-action="tag-request-detail"]', root).forEach((button) => button.addEventListener("click", () => {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    params.set("request_id", button.dataset.id);
    navigate?.("tag-review", params);
  }));
  $('[data-action="tag-request-detail-back"]', root)?.addEventListener("click", () => {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    params.delete("request_id");
    navigate?.("tag-review", params);
  });
  const requestFilter = $('[data-form="tag-request-filter"]', root);
  requestFilter?.addEventListener("change", () => {
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(requestFilter)) if (value) params.set(name, value);
    params.set("page", "1");
    navigate?.("tag-review", params);
  });
  $$('[data-action="tag-request-page"]', root).forEach((button) => button.addEventListener("click", () => {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    params.set("page", button.dataset.page);
    navigate?.("tag-review", params);
  }));
  const selection = $$('[data-tag-request-select]', root);
  const updateRequestActions = () => {
    const selected = selection.filter((item) => item.checked).length;
    $$('[data-action="tag-request-batch"]', root).forEach((button) => {
      button.disabled = selected === 0;
    });
  };
  selection.forEach((checkbox) => checkbox.addEventListener("change", updateRequestActions));
  $('[data-tag-request-select-all]', root)?.addEventListener("change", (event) => {
    selection.filter((item) => !item.disabled).forEach((item) => { item.checked = event.currentTarget.checked; });
    updateRequestActions();
  });
  const transitionRequests = async (button) => {
    const ids = button.dataset.id
      ? [Number(button.dataset.id)]
      : selection.filter((item) => item.checked).map((item) => Number(item.value));
    if (!ids.length) return;
    const approve = button.dataset.operation === "approve";
    if (!window.confirm(approve ? `通过并应用 ${ids.length} 条标签建议？` : `拒绝 ${ids.length} 条标签建议？`)) return;
    button.disabled = true;
    try {
      await jsonRequest(`/paam/tag/v1/assignment_request/batch_${approve ? "approve" : "reject"}`, "POST", { request_ids: ids });
      notify(approve ? "建议已通过，标签与来源已回读" : "建议已拒绝，当前标签未改变");
      await rerender();
    } catch (error) {
      button.disabled = false;
      notify(`${error.message}；若请求超时，请刷新核对状态后再重试`, true);
    }
  };
  $$('[data-action="tag-request-transition"], [data-action="tag-request-batch"]', root).forEach((button) => button.addEventListener("click", () => transitionRequests(button)));
}

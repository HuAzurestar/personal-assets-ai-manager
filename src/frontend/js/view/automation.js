import { request, jsonRequest } from "../api/client.js?v=20260922.3";
import { $, $$, esc } from "../util/core.js";

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
    <div class="automation-notice" role="note"><strong>模型安全边界</strong><span>启用规则后由 CRON 调度通过共享 FIFO 调用配置的模型；API Key 只从系统凭据存储读取，不写入 SQLite，也不会回显。</span></div>
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
  return `<tr data-rule-row="${rule.id}">
    <td class="rule-identity"><span class="eyebrow">RULE #${rule.id} · REV ${rule.rule_revision}</span><strong>${esc(rule.name)}</strong><small>${esc(rule.method_config.prompt)}</small></td>
    <td><strong>${esc(view?.name || `View #${rule.view_id}`)}</strong><small>${esc(model?.name || `Model #${rule.method_config.model_id}`)}</small></td>
    <td class="rule-schedule"><code>${esc(rule.cron || "未设置")}</code><small>Ledger #${rule.scan_after_ledger_id} · Epoch ${rule.scan_epoch}</small></td>
    <td><div class="rule-counts">${counts}</div></td>
    <td class="rule-operation">${statusPill(rule.enabled, modelAvailable, unavailableCopy)}<div class="automation-actions"><button type="button" class="quiet" data-action="rule-preview" data-id="${rule.id}">候选预览</button><button type="button" data-action="rule-edit" data-id="${rule.id}">编辑</button></div></td>
  </tr>`;
}

function scheduleNotice() {
  const tasks = (scheduleStatus?.tasks || []).filter((item) => item.task_key.startsWith("tag-scan:"));
  const running = scheduleStatus?.scheduler_state === "RUNNING" && scheduleStatus?.worker_state === "HEALTHY";
  const nextRuns = tasks.map((item) => item.next_run_at).filter(Boolean).sort();
  const failed = tasks.filter((item) => item.last_result === "FAILED").length;
  const title = running ? `CRON 调度运行中 · ${tasks.length} 条规则已注册` : "CRON 调度未运行";
  const next = nextRuns.length ? `下一次触发：${new Date(nextRuns[0]).toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong" })}` : "当前没有待触发的启用规则";
  const failure = failed ? `；${failed} 条规则上次执行失败，请检查模型配置或网络` : "";
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

export async function autoRulesPage() {
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
    <td><label class="request-selector"><input type="checkbox" data-tag-request-select value="${item.id}" ${item.status === 1 ? "" : "disabled"}><span><strong>Request #${item.id}</strong><small>${esc(item.reason_summary || "无补充理由")}</small></span></label></td>
    <td><strong>Ledger #${item.ledger_id}</strong><small>${esc(item.view_name)} · ${item.ledger_active ? "当前有效" : "Ledger 已失效"}</small></td>
    <td><strong>${esc(item.proposed_tag_name)}</strong><small>${esc(item.proposed_tag_system_name)}</small></td>
    <td><strong>${esc(item.rule_name)}</strong><small>Rule #${item.rule_id} · Revision ${item.rule_revision}</small></td>
    <td>${requestStatusPill(item.status)}</td>
    <td><div class="automation-actions request-actions">${item.status === 1 ? `<button type="button" class="primary" data-action="tag-request-transition" data-operation="approve" data-id="${item.id}">通过并打标</button><button type="button" class="quiet" data-action="tag-request-transition" data-operation="reject" data-id="${item.id}">拒绝</button>` : "—"}</div></td>
  </tr>`).join("");
  const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
  const pager = `<div class="request-pager"><span>共 ${result.total} 条 · 第 ${result.page_index}/${lastPage} 页</span><div><button type="button" class="quiet" data-action="tag-request-page" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><button type="button" class="quiet" data-action="tag-request-page" data-page="${page + 1}" ${page >= lastPage ? "disabled" : ""}>下一页</button></div></div>`;
  return `<div class="tag-review-page">
    <section class="tag-manager automation-requests" aria-labelledby="tag-request-title">
      <div class="tag-manager-head"><div><h2 id="tag-request-title">标签建议请求</h2><p>按规则、Ledger + View 查看建议；只有人工通过后目标 View 的标签才会生效。</p></div><div class="request-batch-actions"><button type="button" class="primary" data-action="tag-request-batch" data-operation="approve" disabled>批量通过</button><button type="button" class="quiet" data-action="tag-request-batch" data-operation="reject" disabled>批量拒绝</button></div></div>
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
    <div class="form-grid"><label>CRON（5 或 6 段）<input name="cron" value="${esc(value.cron)}" ${value.enabled ? "required" : ""} placeholder="*/5 * * * *"><small data-cron-copy>保存时严格校验；启用后立即注册到共享调度器。</small></label><label>金额披露<select name="amount_mode">${Object.entries(amountModeNames).map(([id, label]) => `<option value="${id}" ${Number(id) === value.amount_mode ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用规则配置</label>
    <div class="automation-notice compact"><strong>保存后按 CRON 执行</strong><span>启用规则会注册到共享 FIFO 调度器；模型只生成待人工确认的建议，不会直接修改标签。</span></div>
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

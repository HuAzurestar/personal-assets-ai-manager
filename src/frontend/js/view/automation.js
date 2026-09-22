import { request, jsonRequest } from "../api/client.js?v=20260921.2";
import { $, $$, esc } from "../util/core.js";

let setting = null;
let rules = [];
let views = [];

const amountModeNames = { 1: "金额区间（BAND）", 2: "精确金额（EXACT）", 3: "不发送金额（NONE）" };

function statusPill(enabled, configured = true) {
  const active = enabled && configured;
  const copy = !configured ? "缺少密钥" : enabled ? "已启用" : "已停用";
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
    <div class="automation-notice" role="note"><strong>M1-UI 安全边界</strong><span>连接测试只使用虚构数据且当前为模拟响应，不会请求供应商；真实模型调用和调度将在后续核心任务接通。</span></div>
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

function ruleCard(rule) {
  const view = views.find((item) => item.id === rule.view_id);
  const model = setting?.models.find((item) => item.id === rule.method_config.model_id);
  const counts = [
    ["已分析", rule.analyzed_count], ["失败", rule.failed_count], ["建议", rule.suggested_count],
    ["已启用", rule.accepted_count], ["已拒绝", rule.rejected_count],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${esc(value)}</strong></div>`).join("");
  return `<article class="automation-card rule-card" data-rule-card="${rule.id}">
    <header><div><span class="eyebrow">RULE #${rule.id} · REV ${rule.rule_revision}</span><h3>${esc(rule.name)}</h3></div>${statusPill(rule.enabled, Boolean(model?.key_configured))}</header>
    <p class="rule-source">来源：${esc(view?.name || `View #${rule.view_id}`)} · ${esc(model?.name || `Model #${rule.method_config.model_id}`)}</p>
    <p class="rule-prompt">${esc(rule.method_config.prompt)}</p>
    <div class="rule-schedule"><span>CRON</span><code>${esc(rule.cron || "未设置")}</code><small>游标 Ledger #${rule.scan_after_ledger_id} · Epoch ${rule.scan_epoch}</small></div>
    <div class="rule-counts">${counts}</div>
    <div class="automation-actions"><button type="button" class="quiet" data-action="rule-preview" data-id="${rule.id}">查看候选预览</button><button type="button" data-action="rule-edit" data-id="${rule.id}">编辑规则</button></div>
    <div class="automation-result" data-rule-result="${rule.id}" aria-live="polite" hidden></div>
  </article>`;
}

export async function autoRulesPanel(tagViews) {
  views = tagViews;
  const [rulePage, currentSetting] = await Promise.all([
    request("/paam/tag/v1/auto_rule/list?page_index=1&page_size=100&sorter=%5B%7B%22key%22%3A%22id%22%2C%22direction%22%3A%22asc%22%7D%5D"),
    request("/paam/system/v1/setting/automation"),
  ]);
  rules = rulePage.items;
  setting = currentSetting;
  const canCreate = views.some((item) => item.status === "ACTIVE") && setting.models.some((item) => item.enabled && item.key_configured);
  return `<section class="tag-manager automation-rules" aria-labelledby="auto-rule-title">
    <div class="tag-manager-head"><div><h2 id="auto-rule-title">自动打标签规则</h2><p>同一标签维度可配置多条独立规则；配置和进度从 SQLite 实时读取。</p></div><button type="button" class="primary" data-action="rule-new" ${canCreate ? "" : 'disabled title="需要启用中的标签维度和模型"'}>＋ 新建规则</button></div>
    <div class="automation-notice compact" role="note"><strong>尚未启动调度</strong><span>本页没有“立即执行/重扫”入口。候选预览仅做本地只读资格检查，不调用模型、不移动游标。</span></div>
    <div class="automation-grid">${rules.map(ruleCard).join("") || '<div class="panel empty-state"><strong>尚未创建自动规则</strong><p>启用模型并准备标签维度后即可保存第一条规则。</p></div>'}</div>
  </section>`;
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
    <div class="form-grid"><label>CRON（5 或 6 段）<input name="cron" value="${esc(value.cron)}" ${value.enabled ? "required" : ""} placeholder="*/5 * * * *"><small data-cron-copy>保存时严格校验；当前不注册调度任务。</small></label><label>金额披露<select name="amount_mode">${Object.entries(amountModeNames).map(([id, label]) => `<option value="${id}" ${Number(id) === value.amount_mode ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
    <label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? "checked" : ""}> 启用规则配置</label>
    <div class="automation-notice compact"><strong>保存不等于执行</strong><span>DEV-011 只持久化规则。统一调度、模型调用和建议写入在后续任务接通。</span></div>
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
  const result = $(`[data-rule-result="${button.dataset.id}"]`);
  button.disabled = true;
  result.hidden = false;
  result.textContent = "正在本地检查候选资格…";
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

export function bindAutomation(root, rerender, notify) {
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
}

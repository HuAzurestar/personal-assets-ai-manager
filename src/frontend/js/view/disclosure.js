import { jsonRequest } from "../api/client.js?v=20260927.1";
import { $, esc, money } from "../util/core.js";

export function parseDisclosure(boundariesText, dateGranularity) {
  let bands;
  try { bands = JSON.parse(boundariesText); } catch (_error) { throw new Error("区间必须是 JSON 对象，例如 {\"CNY\":[0,3000,10000]}"); }
  if (!bands || Array.isArray(bands) || typeof bands !== "object") throw new Error("区间必须按币种填写对象");
  const entries = Object.entries(bands);
  if (entries.length > 64) throw new Error("最多配置 64 个币种单位");
  for (const [currency, values] of entries) {
    if (!/^(CNY|USD|HKD|JPY|EUR|GBP)(_[0-8])?$/.test(currency)) throw new Error(`${currency} 不是受支持的规范币种单位`);
    if (!Array.isArray(values) || !values.length || values.length > 64 || values[0] !== 0
      || values.some((value, index) => !Number.isSafeInteger(value) || value < 0 || value > 9000000000000 || (index > 0 && value <= values[index - 1]))) {
      throw new Error(`${currency}：必须从 0 开始，填写 1–64 个严格递增整数，最大 9000000000000`);
    }
  }
  return { date_granularity: dateGranularity, amount_bands: Object.fromEntries(entries.map(([currency, boundaries]) => [currency, { boundaries }])) };
}

export function openDisclosureEditor(setting, openDialog, saved) {
  // Freeze the read token with the displayed values; a background refresh must not rebase this form.
  const expectedUpdatedTime = setting.updated_time;
  const config = setting.disclosure || {};
  const boundaries = Object.fromEntries(Object.entries(config.amount_bands || {}).map(([code, value]) => [code, value.boundaries]));
  const dialog = openDialog("编辑金额披露", `<form class="stack automation-form" data-disclosure-form>
    <div class="form-error-slot" role="alert" aria-live="assertive"></div>
    <label>时间粒度<select name="date_granularity">${[["DAY", "精确到日"], ["MONTH", "精确到月"], ["NONE", "不发送时间"]].map(([value, label]) => `<option value="${value}" ${value === (config.date_granularity || "DAY") ? "selected" : ""}>${label}</option>`).join("")}</select><small>当前消息结构省略交易时间；这里保存策略，不宣称已外发日期。</small></label>
    <label>按币种配置金额边界（JSON）<textarea name="boundaries" rows="10" required spellcheck="false">${esc(JSON.stringify(boundaries, null, 2))}</textarea></label>
    <p>填写最小货币单位整数：CNY 的 3000 表示 30 元；CNY_4 的 3000 表示 0.3000 元。左闭右开，最后一档无上界。未配置币种时 BAND 省略金额，不套用 CNY 区间。</p>
    <div class="automation-notice compact"><strong>保存的影响</strong><span>区间变更使 BAND 规则修订与扫描周期递增、游标归零并取消其待审申请；时间策略变更影响全部规则。既有已通过标签不回滚。启用中的规则会在后续 CRON 重新扫描，真实模型可能计费。</span></div>
    <label class="check-row"><input type="checkbox" name="acknowledged" required>我已了解待审申请失效、重新扫描及可能计费的影响</label>
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存并回读</button></div>
  </form>`);
  const form = $("[data-disclosure-form]", dialog);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type='submit']", form);
    button.disabled = true;
    try {
      const data = new FormData(form);
      if (!data.has("acknowledged")) throw new Error("请先确认保存影响");
      const disclosure = parseDisclosure(String(data.get("boundaries")), String(data.get("date_granularity")));
      const result = await jsonRequest("/paam/system/v1/setting/automation", "PUT", { expected_updated_time: expectedUpdatedTime, disclosure });
      dialog.close();
      saved(result);
    } catch (error) {
      $(".form-error-slot", form).textContent = error.code === "SETTING_VERSION_CONFLICT"
        ? "配置已被修改。请保留你的草稿，关闭后刷新设置并重新核对；未覆盖其他修改。" : error.message;
    } finally { button.disabled = false; }
  });
}

export function previewDisclosureMarkup(body) {
  const input = body.messages.find((message) => message.role === "user");
  const system = body.messages.find((message) => message.role === "system");
  return `<div class="automation-result simulated"><strong>固定虚构样例 · ${body.input_eligible ? "可进入模型分析" : "清洗后不调用模型"}</strong>
    <span>此预览没有调用模型，不代表历史请求，不会生成打标申请。</span>
    <ul>${body.warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul></div>
    <details><summary>虚构原始样例（仅作对照，不是外发内容）</summary><p>${esc(body.sample.label)} · ${esc(money(body.sample))}</p><p>${esc(body.sample.merchant)} · ${esc(body.sample.summary)}</p></details>
    <h3>实际组装的外发消息结构</h3>${input ? `<p>item 是本次生成的随机关联代号，不含 Ledger ID；候选别名仅在该消息上下文中有效。金额在 BAND / NONE 下不会混入文本。</p><h4>System 约束</h4><pre class="automation-code">${esc(system?.content || "")}</pre><h4>User 业务 JSON</h4><pre class="automation-code">${esc(input.content)}</pre>` : "<p>没有外发消息；不能仅靠金额强行分类。</p>"}`;
}

export function openDisclosurePreview(openDialog, amountMode = 1) {
  const dialog = openDialog("已保存配置 · 虚构披露预览", `<form class="form-grid three" data-preview-form>
    <label>样例<select name="sample"><option value="MEAL_SMALL">29 元虚构餐饮</option><option value="MEAL_LARGE">3500 元虚构聚餐</option><option value="NON_MEAL_SMALL">20 元虚构文具</option><option value="NO_CONTEXT">无业务语义</option></select></label>
    <label>金额模式<select name="amount_mode">${[[1, "BAND · 金额区间"], [2, "EXACT · 精确金额"], [3, "NONE · 不发送金额"]].map(([value, label]) => `<option value="${value}" ${value === amountMode ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>币种单位<input name="currency_code" value="CNY" maxlength="16" required></label>
    <button type="submit">预览（不调用模型）</button>
  </form><div data-disclosure-preview aria-live="polite"><p>只使用已保存配置；编辑器里未保存的草稿不会生效。</p></div>`);
  const form = $("[data-preview-form]", dialog);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button", form);
    const result = $("[data-disclosure-preview]", dialog);
    button.disabled = true;
    result.textContent = "正在组装虚构预览…";
    try {
      const data = new FormData(form);
      const body = await jsonRequest("/paam/system/v1/setting/automation/disclosure_preview", "POST", {
        sample: data.get("sample"), amount_mode: Number(data.get("amount_mode")), currency_code: data.get("currency_code"),
      });
      if (dialog.isConnected) result.innerHTML = previewDisclosureMarkup(body);
    } catch (error) { result.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

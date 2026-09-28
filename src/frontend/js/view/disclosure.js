import { jsonRequest } from "../api/client.js?v=20260928.2";
import { $, esc, money } from "../util/core.js";
import { helpTip } from "./automation_feedback.js?v=20260928.3";

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

export function currencyScale(code) {
  if (!/^(CNY|USD|HKD|JPY|EUR|GBP)(_[0-8])?$/.test(code)) throw new Error("请选择有效币种");
  return code.includes("_") ? Number(code.split("_")[1]) : code === "JPY" ? 0 : 2;
}

export function decimalBoundary(text, code) {
  const scale = currencyScale(code);
  if (text.length > 32 || !/^\d+(\.\d+)?$/.test(text)) throw new Error(`${code}：请输入非负金额`);
  const [whole, fraction = ""] = text.split(".");
  if (fraction.length > scale) throw new Error(`${code}：最多 ${scale} 位小数`);
  const value = BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, "0") || "0");
  if (value > 9000000000000n) throw new Error(`${code}：金额超出范围`);
  return Number(value);
}

export function boundaryDecimal(value, code) {
  const scale = currencyScale(code);
  const digits = String(value).padStart(scale + 1, "0");
  return scale ? `${digits.slice(0,-scale)}.${digits.slice(-scale)}` : digits;
}

export function openDisclosureEditor(setting, openDialog, saved) {
  // Freeze the read token with the displayed values; a background refresh must not rebase this form.
  const expectedUpdatedTime = setting.updated_time;
  const config = setting.disclosure || {};
  const boundaries = Object.fromEntries(Object.entries(config.amount_bands || {}).map(([code, value]) => [code, value.boundaries]));
  const dialog = openDialog("编辑金额披露", `<form class="stack automation-form" data-disclosure-form>
    <div class="form-error-slot" role="alert" aria-live="assertive"></div>
    <p>按元或对应货币主单位填写各档起点。第一档从 0 开始，最后一档无上限。</p>
    <div data-band-editor></div><div class="disclosure-add-currency"><label>新增币种<select data-new-currency></select></label><button type="button" data-add-currency>添加币种</button></div>
    <label class="check-row"><input type="checkbox" name="advanced">高级 JSON 编辑</label>
    <label data-band-json hidden>金额边界（最小货币单位）<textarea name="boundaries" rows="6" spellcheck="false">${esc(JSON.stringify(boundaries, null, 2))}</textarea></label>
    <p data-band-preview aria-live="polite"></p>
    <div data-disclosure-impact hidden><p>更改区间会取消受影响规则的待审建议，并从头重新扫描，可能产生模型费用。已通过标签保留。</p>
    <label class="check-row"><input type="checkbox" name="acknowledged">我已了解本次变更的影响</label></div>
    <div class="actions"><button type="button" class="quiet" data-close>取消</button><button type="submit" class="primary">保存</button></div>
  </form>`);
  const form = $("[data-disclosure-form]", dialog);
  const editor = $('[data-band-editor]', form);
  const advanced = $('[name="advanced"]', form);
  const json = $('[name="boundaries"]', form);
  const canonical = (bands) => JSON.stringify(Object.entries(bands).sort(([a],[b]) => a.localeCompare(b)));
  const names = { CNY: "人民币（元）", USD: "美元", HKD: "港元", JPY: "日元", EUR: "欧元", GBP: "英镑" };
  const codes = [...new Set([...Object.keys(names), ...Object.keys(boundaries)])];
  function render(bands) {
    const options = [...new Set([...codes, ...Object.keys(bands)])];
    editor.innerHTML = Object.entries(bands).map(([code, values]) => `<fieldset data-currency-row><legend>${esc(code)} 区间 · ${esc(names[code] || names[code.split('_')[0]])}</legend><input type="hidden" data-band-currency value="${esc(code)}"><div data-boundary-list>${values.map(value => boundaryInput(boundaryDecimal(value,code))).join("")}</div><button type="button" data-add-boundary>增加起点</button><button type="button" data-remove-currency>移除币种</button></fieldset>`).join("");
    syncCurrencies(options);
  }
  function syncCurrencies(options = codes) {
    const used = [...editor.querySelectorAll('[data-band-currency]')].map(input => input.value);
    const select = $('[data-new-currency]', form);
    const selected = select.value;
    select.innerHTML = options.filter(code => !used.includes(code)).map(code => `<option value="${esc(code)}">${esc(code)} · ${esc(names[code] || names[code.split('_')[0]])}</option>`).join('');
    if ([...select.options].some(option => option.value === selected)) select.value = selected;
    $('[data-add-currency]', form).disabled = !select.options.length;
  }
  function boundaryInput(value) {
    return `<div class="boundary-input"><label>区间起点<input data-boundary inputmode="decimal" maxlength="32" value="${esc(value)}"></label><button type="button" data-remove-boundary aria-label="移除此起点">移除</button></div>`;
  }
  function readBands() {
    if (advanced.checked) return Object.fromEntries(Object.entries(parseDisclosure(json.value, config.date_granularity).amount_bands).map(([code,v]) => [code,v.boundaries]));
    const bands = {};
    for (const row of editor.querySelectorAll('[data-currency-row]')) {
      const code = $('[data-band-currency]', row).value;
      if (Object.hasOwn(bands, code)) throw new Error(`${code}：币种重复`);
      bands[code] = [...row.querySelectorAll('[data-boundary]')].map(input => decimalBoundary(input.value.trim(),code));
    }
    parseDisclosure(JSON.stringify(bands), config.date_granularity);
    return bands;
  }
  function preview() {
    try {
      const bands = readBands();
      const changed = canonical(bands) !== canonical(boundaries);
      $('[data-disclosure-impact]', form).hidden = !changed;
      $('[data-band-preview]', form).textContent = Object.entries(bands).map(([code,values]) => `${code}：${values.map((v,i) => `${boundaryDecimal(v,code)}${i+1 < values.length ? `–${boundaryDecimal(values[i+1],code)}` : ' 以上'}`).join(' / ')}`).join('；') || '未配置区间的币种不发送区间金额。';
    } catch (error) { $('[data-band-preview]', form).textContent = error.message; }
  }
  advanced.addEventListener('change', () => {
    try {
      if (advanced.checked) {
        advanced.checked = false;
        json.value = JSON.stringify(readBands(),null,2);
        advanced.checked = true;
      } else {
        advanced.checked = true;
        render(readBands());
        advanced.checked = false;
      }
      editor.hidden = advanced.checked;
      $('.disclosure-add-currency', form).hidden = advanced.checked;
      $('[data-band-json]', form).hidden = !advanced.checked;
      $('.form-error-slot', form).textContent = '';
      preview();
    } catch (error) { $('.form-error-slot', form).textContent = error.message; }
  });
  form.addEventListener('click', event => {
    const row = event.target.closest('[data-currency-row]');
    if (event.target.matches('[data-remove-boundary]')) event.target.closest('.boundary-input').remove();
    if (event.target.matches('[data-remove-currency]')) { row.remove(); syncCurrencies(); }
    if (event.target.matches('[data-add-boundary]')) $('[data-boundary-list]', row).insertAdjacentHTML('beforeend', boundaryInput(''));
    if (event.target.matches('[data-add-currency]')) {
      const code = $('[data-new-currency]', form).value;
      if (!code) return;
      // Read current input property values before rebuilding the rows.
      const bands = (() => { try { return readBands(); } catch (error) { $('.form-error-slot', form).textContent = error.message; return null; } })();
      if (!bands) return;
      render({...bands, [code]:[0]});
    }
    preview();
  });
  form.addEventListener('input', preview);
  form.addEventListener('change', preview);
  render(boundaries);
  preview();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type='submit']", form);
    button.disabled = true;
    try {
      const data = new FormData(form);
      const bands = readBands();
      if (canonical(bands) === canonical(boundaries)) { dialog.close(); return; }
      if (!data.has("acknowledged")) throw new Error("请先确认保存影响");
      const disclosure = parseDisclosure(JSON.stringify(bands), config.date_granularity || "DAY");
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
    ${helpTip("虚构预览说明", "使用已保存配置组装示例，不调用模型。")}
    <ul>${body.warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul></div>
    <section><h3>虚构样例</h3><p>${esc(body.sample.label)} · ${esc(money(body.sample))}</p><p>${esc(body.sample.merchant)} · ${esc(body.sample.summary)}</p></section>
    <h3>发送内容 ${helpTip("发送内容说明", "item 为随机关联代号，候选标签使用临时别名。")}</h3>${input ? `<h4>系统指令</h4><pre class="automation-code">${esc(system?.content || "")}</pre><h4>业务数据</h4><pre class="automation-code">${esc(input.content)}</pre>` : "<p>缺少可分析的业务内容。</p>"}`;
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

import { request, jsonRequest } from "../api/client.js";
import { esc, date, quantityDecimal, money, resourceId } from "../util/core.js";
import { table } from "../component/table.js";
import { input, select, workbenchDialog, writeFailure, namedChoice, bindNamedChoice } from "../component/workbench.js";
import { candidateScan, scanControls } from '../util/candidate-scan.js';
import { unitChoices, unitLabel } from '../util/unit-dictionary.js';
import { financialStateLabel } from '../util/financial-copy.js';

const base = "/paam/financial/v1/position";
export const usages = ["GENERAL", "PERSONAL-LENDING", "SHARED-SETTLEMENT", "STORED-VALUE", "DEPOSIT-PLEDGE", "REIMBURSEMENT", "CREDIT-CARD", "FORMAL-LOAN", "INVESTMENT"];
let controller;
const positionScan = candidateScan(base, 'position');
export function stopPositionRead() { controller?.abort(); positionScan.stop(); }

export function positionFields(row = {}, metadata = false) {
  return input("title", "对象名称", row.title || "", 'required maxlength="160"')
    + `<label>说明<textarea name="description" maxlength="2000">${esc(row.description || "")}</textarea></label>`
    + select("usage_scenario", "用途（不决定资产／负债性质）", usages.map(code=>[code,financialStateLabel('positionUsage',code)]), row.usage_scenario || "GENERAL")
    + (metadata ? select("status", "状态", ["ACTIVE", "ARCHIVED", "SETTLED"].map(code=>[code,financialStateLabel('position',code)]), row.status)
      : select("type", "性质", [["ASSET", "资产／债权"], ["LIABILITY", "负债／债务"]], row.type || "ASSET")
        + namedChoice('party_id', '本方个人', {value: row.party_id || '', pickAttribute: 'data-pick-party', pick: '查找个人'})
        + input("counterparty", "对象对方（文字，不按同名合并）", row.counterparty || "", 'maxlength="200"')
        + select('unit_code', '精确单位（数量不等于估值）', unitChoices().map(item =>
          [item.code, `${item.code} · ${unitLabel(item.code)} · 最小单位 ${item.quantum}`]), row.unit_code || 'CNY'));
}

export function bindPartyPicker(form, signal) {
  if (form.querySelector('[data-named-choice="party_id"]')) bindNamedChoice(form, 'party_id', {
    url: '/paam/ledger/v1/account-party', title: '选择本方个人', signal, pickerAttribute: 'data-party-picker' });
}

function href(params, changes) {
  const next = new URLSearchParams(params);
  Object.entries(changes).forEach(([key, value]) => value == null ? next.delete(key) : next.set(key, value));
  return `#workbench/position?${next}`;
}

export async function positionPage(params) {
  stopPositionRead(); controller = new AbortController();
  const query = new URLSearchParams({ page_size: "20" }), word = params.get("word")?.trim();
  if (word) {
    query.set("query", JSON.stringify([{ key: "title", word }]));
    if (params.get("cursor")) query.set("cursor", params.get("cursor"));
  } else query.set("page_index", params.get("page") || "1");
  if (params.get("status")) query.set("filter", JSON.stringify({ key: "status", op: "=", val: params.get("status") }));
  const result = word ? await positionScan.read(query, location.hash)
    : await request(`${base}/list?${query}`, { signal: controller.signal });
  const rows = positionRows(result.items, params, !!word && result.has_more);
  let detail = "";
  if (params.get("id")) {
    const id = resourceId(params.get("id"));
    const [row, legs] = await Promise.all([
      request(`${base}/${id}`, { signal: controller.signal }),
      request(`${base}/${id}/leg/list?page_size=20&page_index=${Number(params.get("leg_page") || 1)}`, { signal: controller.signal }),
    ]);
    detail = `<section class="panel" data-position-detail="${row.id}"><h2>#${row.id} ${esc(row.title)}</h2>
      <p>${esc(financialStateLabel('positionType',row.type))} · ${esc(financialStateLabel('positionUsage',row.usage_scenario))} · ${esc(financialStateLabel('position',row.status))} · 本方个人 #${row.party_id} · ${esc(row.counterparty)}</p>
      <p data-position-quantity>数量：${esc(positionQuantityLabel(row))}</p>
      <p>成本状态：${esc(financialStateLabel('cost',row.cost_state))}。本页不计算净值、行情或账户余额。</p><p>${esc(row.description)}</p>
      <a href="#workbench/review?position=${row.id}">新增数量变化 / 款项审查</a>
      ${table(["原始腿与审查", "精确数量", "来源腿", "款项归因（不是第二笔现金）"], legs.items.map(leg => `<tr><td>#${leg.id} · ${esc(leg.type)}<br>Review #${leg.review_id} ${esc(financialStateLabel('review',leg.review.status))}<br>${esc(date(leg.occurred_time))}<br>${esc(leg.basis)}</td><td>${esc(leg.leg_direction)} ${quantityDecimal(leg.leg_amount, leg.unit_code)} ${esc(leg.unit_code)}</td><td>#${leg.source_position_leg_id || "无（IN）"}</td><td>${leg.position_allocations.map(link => `Ledger #${link.ledger_id}：${esc(money(link))}`).join("<br>") || "无"}</td></tr>`))}
      <div class="actions">${legs.page_index > 1 ? `<a href="${esc(href(params, { leg_page: legs.page_index - 1 }))}">腿上一页</a>` : ""}<span>腿 ${legs.total} 项 · 第 ${legs.page_index} 页</span>${legs.page_index * legs.page_size < legs.total ? `<a href="${esc(href(params, { leg_page: legs.page_index + 1 }))}">腿下一页</a>` : ""}</div></section>`;
  }
  return `<div data-position-workbench><section class="panel position-list-surface"><h1>资产与负债对象</h1><p>账户是现金来源；Position 是独立数量对象。创建元数据不生成现金或数量；下列有据数量不是账户余额或市场估值。</p><a href="#workbench/account">个人与账户来源</a> · <a href="#workbench/review">审查工作台</a>
    <button data-position-create>独立新建对象</button><form data-position-filter class="actions">${input("word", "名称字面搜索", word || "", 'maxlength="128"')}${select("status", "状态", [["", "全部"], ...["ACTIVE", "ARCHIVED", "SETTLED"].map(code=>[code,financialStateLabel('position',code)])], params.get("status") || "")}<button type="submit">重新查找</button></form>
    <div data-position-list>${table(["对象身份", "性质与用途", "本方 / 对方", "有据数量", "元数据状态", "操作"], rows)}</div>
    <div data-position-footer>${word ? scanControls('position', result) : `<div class="actions">${result.page_index > 1 ? `<a href="${esc(href(params, { page: result.page_index - 1 }))}">上一页</a>` : ""}<span>共 ${result.total} 项 · 第 ${result.page_index} 页</span>${result.page_index * result.page_size < result.total ? `<a href="${esc(href(params, { page: result.page_index + 1 }))}">下一页</a>` : ""}</div>`}</div></section>${detail}</div>`;
}

export function positionQuantityLabel(row) {
  if (['UNKNOWN','NEEDS_REVIEW'].includes(row.quantity_state)) return financialStateLabel('quantity',row.quantity_state);
  if (row.quantity_state !== 'KNOWN' || !Number.isSafeInteger(row.quantity)) throw new Error('数量摘要不完整，请重新读取');
  return `${quantityDecimal(row.quantity, row.unit_code)} ${row.unit_code}`;
}

export function positionRows(items, params, scanning = false) {
  return items.length ? items.map(row => `<tr data-position-row="${row.id}">
    <td data-label="对象"><a href="${esc(href(params, { id: row.id, leg_page: null }))}">#${row.id} ${esc(row.title)}</a></td>
    <td data-label="性质 / 用途">${esc(financialStateLabel('positionType',row.type))}<small>${esc(financialStateLabel('positionUsage',row.usage_scenario))}</small></td>
    <td data-label="本方 / 对方">${esc(row.party_name)}<small>对方：${esc(row.counterparty || '未注明')}</small></td>
    <td data-label="有据数量" data-position-row-quantity>${esc(positionQuantityLabel(row))}<small>单位：${esc(row.unit_code)}</small></td>
    <td data-label="元数据状态">${esc(financialStateLabel('position',row.status))}</td><td data-label="操作"><button data-position-edit="${row.id}">维护元数据</button></td></tr>`)
    : [`<tr><td colspan="6">${scanning ? '尚无命中；空批次不代表扫描结束。' : '没有匹配项。'}</td></tr>`];
}

function paintPositionSearch(host, result) {
  if (!host.isConnected) return;
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  // Only the list is replaced: detail/evidence and metadata handlers survive.
  host.querySelector('[data-position-list] tbody').innerHTML = positionRows(result.items, params, result.has_more).join('');
  host.querySelector('[data-position-footer]').innerHTML = scanControls('position', result);
  positionScan.bind(host, next => paintPositionSearch(host, next));
}

export function bindPosition(root, reload) {
  const host = root.querySelector("[data-position-workbench]");
  if (!host) return;
  host.querySelector("[data-position-filter]").onsubmit = event => {
    event.preventDefault();
    positionScan.reset();
    const route = href(new URLSearchParams(new FormData(event.target)), {});
    // Normalize %20/+ without losing same-condition explicit refresh.
    const current = new URLSearchParams(location.hash.split('?')[1] || '');
    if (location.hash.startsWith('#workbench/position?') && current.toString() === route.split('?')[1]) reload();
    else location.hash = route;
  };
  positionScan.bind(host, next => paintPositionSearch(host, next));
  host.onclick = async event => {
    const button = event.target.closest("[data-position-create], [data-position-edit]");
    if (!button || button.disabled) return;
    button.disabled = true;
    const route = location.hash;
    try {
      const id = button.dataset.positionEdit ? resourceId(button.dataset.positionEdit) : null;
      const row = id ? await request(`${base}/${id}`, { signal: controller.signal }) : {};
      if (!host.isConnected || route !== location.hash) return;
      const node = workbenchDialog(id ? "维护对象元数据" : "独立创建对象", `<form class="stack">${positionFields(row, !!id)}
        <p>${id ? "性质、个人、对方、单位不可修改。身份纠错需新建对象；归档不删除数量，恢复不激活旧 Review，结清要求有据零且来源有效。" : "创建后数量 UNKNOWN；通过 Review 增加 OPENING 或 MOVEMENT 证据。"}</p><p role="status"></p><button type="submit">保存</button></form>`);
      const form = node.querySelector("form");
      bindPartyPicker(form, controller.signal);
      let uncertain = false, writing = false;
      form.onsubmit = async event => {
        event.preventDefault();
        if (uncertain || writing) return;
        const submit = form.querySelector("[type=submit]"); submit.disabled = true; writing = true;
        try {
          const payload = Object.fromEntries(new FormData(form));
          if (id) payload.expected_updated_time = row.updated_time;
          else { payload.party_id = resourceId(payload.party_id); payload.unit_code = payload.unit_code.trim().toUpperCase(); }
          await jsonRequest(`${base}${id ? `/${id}/metadata` : ""}`, id ? "PUT" : "POST", payload);
          node.close(); positionScan.reset(); await reload();
        } catch (error) { uncertain = writeFailure(form, error); submit.disabled = uncertain; }
        finally { writing = false; }
      };
    } catch (error) { if (error.name !== "AbortError" && host.isConnected) workbenchDialog("读取失败", `<p>${esc(error.message)}</p>`); }
    finally { if (button.isConnected) button.disabled = false; }
  };
}

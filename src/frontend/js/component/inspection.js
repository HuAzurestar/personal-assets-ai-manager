import { request } from "../api/client.js";
import { workbenchDialog } from "./workbench.js";
import { preserveView } from "../util/view_state.js?v=20260928.6";
import { esc, money, quantityDecimal, date as when, typeNames, statusNames, reviewTypeNames } from "../util/core.js";
import { financialStateLabel, financialScopeNote } from '../util/financial-copy.js';

const names = { fact: "事实流水", ledger: "账本流水", review: "审查记录", file: "导入文件" };
const sources = { 0: "来源未识别", 1: "手工录入", 101: "支付宝", 102: "微信支付", 201: "建设银行", 202: "农业银行", 203: "招商银行" };
const behavior = { 0: "事实交易", 1: "借款与还款" };
const operations = { 0: "创建审查", 1: "修改审查", 2: "撤销审查", 3: "恢复审查" };
const fileStates = { 0: "待处理", 1: "已导入", 2: "部分导入", 3: "导入失败" };
const rowStates = { 0: "状态未知", 1: "已关联事实", 2: "已跳过", 3: "待处理异常" };
const formats = { 1: "CSV", 2: "XLS", 3: "XLSX", 4: "PDF" };
const endpoints = { fact: "/paam/ledger/v1/transaction_fact/", ledger: "/paam/ledger/v1/flow/", review: "/paam/ledger/v1/review/", file: "/paam/import/v1/import_file/" };
const actionKinds = { "fact-detail": "fact", "economic-detail": "ledger", "economic-review-detail": "review", "import-file-detail": "file" };
const adapters = new Map();

export function registerInspection(kind, adapter) {
  adapters.set(kind, adapter);
  names[kind] = adapter.name;
  actionKinds[adapter.action] = kind;
}

export function readableAccount(value) {
  if (!value || value === "UNKNOWN" || /^[a-f\d]{32,}$/i.test(value)) return "账户名称未识别";
  return value;
}
export function businessTitle(item) {
  return item.summary?.trim() || item.counterparty_name?.trim() || item.title?.trim() || "未提供摘要";
}
function reviewTitle(item, facts) {
  if (facts.length && (!item.title || (facts.length === 1 && item.title === facts[0].counterparty_name))) {
    return `${businessTitle(facts[0])}${facts.length > 1 ? ` 等 ${facts.length} 笔交易` : ""}`;
  }
  return businessTitle(item);
}
function direction(value) { return value === 1 || value === "IN" ? "流入" : value === 2 || value === "OUT" ? "流出" : "方向未提供"; }
function amount(item) { return `${money(item)} ${item.cash_currency_code ?? item.currency_code}`; }
function fields(items) {
  return `<dl class="inspection-fields">${items.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value === "" || value == null ? "未提供" : value)}</dd></div>`).join("")}</dl>`;
}
function card(title, body, { wide = false, tone = "base" } = {}) {
  return `<section class="inspection-card ${wide ? "inspection-wide" : ""} inspection-tone-${tone}"><h3>${esc(title)}</h3>${body}</section>`;
}
function metrics(items) {
  return `<dl class="inspection-metrics">${items.map(([label, value, tone = "base"]) => `<div class="inspection-tone-${tone}"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}
function relationButton(kind, id, label, note = "") {
  if (!id) return "";
  return `<button type="button" class="inspection-link" data-promote data-kind="${kind}" data-id="${id}"><span>${esc(label)}</span>${note ? `<small>${esc(note)}</small>` : ""}</button>`;
}
function jsonPayload(payload, label = "查看原始 JSON") {
  if (!payload) return '<p class="inspection-empty">没有保留原始 JSON</p>';
  let formatted = payload;
  try { formatted = JSON.stringify(typeof payload === "string" ? JSON.parse(payload) : payload, null, 2); } catch { /* Preserve malformed evidence verbatim. */ }
  return `<details class="inspection-json"><summary>${esc(label)}</summary><pre>${esc(formatted)}</pre></details>`;
}
function totals(rows, directionField) {
  const sums = new Map();
  for (const row of rows) {
    const key = `${row.currency_code}:${row[directionField]}`;
    const total = sums.get(key) || { amount: 0, currency_code: row.currency_code, direction: row[directionField] };
    total.amount += row.amount;
    if (!Number.isSafeInteger(total.amount)) throw new Error("金额合计超出安全精度，请缩小查看范围");
    sums.set(key, total);
  }
  return metrics([...sums.values()].map(row => [`${direction(row.direction)} · ${row.currency_code}`, amount(row), row.direction === 1 ? "positive" : "negative"]));
}

class Presentation {
  constructor() { this.collections = []; }
  collection(rows, renderer, label = "条记录") {
    if (!rows.length) return '<p class="inspection-empty">暂无记录</p>';
    const index = this.collections.push({ rows, renderer, label, page: 0, size: 20 }) - 1;
    return `<div data-collection="${index}"></div>`;
  }
  mount(root) {
    root.querySelectorAll("[data-collection]").forEach(host => {
      const item = this.collections[Number(host.dataset.collection)];
      const render = () => {
        const start = item.page * item.size;
        host.innerHTML = `<div class="inspection-record-list">${item.rows.slice(start, start + item.size).map(item.renderer).join("")}</div>`;
        if (item.rows.length > item.size) {
          host.insertAdjacentHTML("beforeend", `<nav class="inspection-pagination" aria-label="关联明细分页"><span>显示 ${start + 1}–${Math.min(start + item.size, item.rows.length)} / ${item.rows.length} ${esc(item.label)}</span><button data-rel-prev ${!item.page ? "disabled" : ""}>上一页</button><button data-rel-next ${start + item.size >= item.rows.length ? "disabled" : ""}>下一页</button></nav>`);
          host.querySelector("[data-rel-prev]").onclick = () => { item.page--; render(); host.scrollIntoView({ block: "nearest" }); };
          host.querySelector("[data-rel-next]").onclick = () => { item.page++; render(); host.scrollIntoView({ block: "nearest" }); };
        }
      };
      render();
    });
  }
}

function allocationSection(presentation, allocations, facts, ledgers, reviews, context = {}) {
  // Read-only presentation compatibility while Fact callers migrate. Financial
  // commands accept only the canonical contract; this is not another writer.
  allocations = allocations.map(row => ({ ...row, review_case_id: row.review_id ?? row.review_case_id,
    transaction_fact_id: row.transaction_id ?? row.transaction_fact_id, ledger_entry_id: row.ledger_id ?? row.ledger_entry_id }));
  const factMap = new Map(facts.map(row => [row.id, row]));
  const ledgerMap = new Map(ledgers.map(row => [row.id, row]));
  const reviewMap = new Map(reviews.map(row => [row.id, row]));
  const isActive = row => ledgerMap.get(row.ledger_entry_id)?.active
    ?? ([0, "CONFIRMED"].includes(reviewMap.get(row.review_case_id)?.status));
  const render = row => {
    const fact = factMap.get(row.transaction_fact_id);
    const ledger = ledgerMap.get(row.ledger_entry_id);
    const review = reviewMap.get(row.review_case_id);
    const active = isActive(row);
    return `<article class="inspection-flow ${active ? "is-active" : "is-history"}"><div class="inspection-flow-main"><span>${active ? "Ledger 有效" : "Ledger 已停用"}</span><strong>${esc(amount(row))}</strong><p>${esc(businessTitle(fact || review || {}))}</p><small>${esc(`${reviewTypeNames[review?.type] || behavior[review?.behavior_type] || "类型未识别"} → ${typeNames[ledger?.economic_type ?? ledger?.entry_type] || "账本分类未识别"} · ${direction(ledger?.cash_direction ?? ledger?.entry_direction)}`)}</small></div><div class="inspection-flow-actions">${context.kind === "fact" ? "" : relationButton("fact", row.transaction_fact_id, "查看来源事实")}${context.kind === "review" ? "" : relationButton("review", row.review_case_id, "查看审查")}${context.kind === "ledger" ? "" : relationButton("ledger", row.ledger_entry_id, "查看账本结果")}</div></article>`;
  };
  const active = allocations.filter(isActive);
  const history = allocations.filter(row => !isActive(row));
  let result = card("当前资金关系", presentation.collection(active, render, "条关系"), { tone: "accent" });
  if (history.length) result += card("历史资金关系", presentation.collection(history, render, "条历史关系"), { tone: "muted" });
  return result;
}

function evidenceRow(row) {
  return `<article class="inspection-source-row"><div><span class="inspection-status status-${row.row_status}">${esc(rowStates[row.row_status] || "状态未识别")}</span><strong>${esc(row.filename)}</strong><p>${esc(`${sources[row.source_type] || "来源未识别"} · 第 ${row.source_row_number} 行`)}</p><small>${esc(row.source_reference)}</small></div>${relationButton("file", row.source_file_id, "查看来源文件")}<button data-fact-source-evidence="${row.id}" data-file-id="${row.source_file_id}">读取此行原始证据（只读）</button></article>`;
}

function rowReason(row, normalized) {
  if (row.issue_message) return row.issue_message;
  if (row.row_status === 1) return "该来源行已关联到事实流水";
  if (normalized?.amount_minor === 0) return "零金额来源行，仅保留证据，未生成事实";
  if (normalized?.status) return `来源状态：${normalized.status}；未生成事实`;
  return row.row_status === 2 ? "该来源行未生成事实" : "需要检查来源数据";
}
function importRow(row) {
  return `<article class="inspection-import-row status-${row.row_status}"><div class="inspection-row-number">第 ${row.source_row_number} 行</div><div class="inspection-row-content"><span class="inspection-status status-${row.row_status}">${esc(rowStates[row.row_status] || "状态未识别")}</span><p>${esc(row.issue_code || "来源证据已保留")} · 来源号 ${esc(row.source_reference || "未提供")}</p></div>${row.transaction_id ? relationButton("fact", row.transaction_id, `事实 #${row.transaction_id}`) : ""}<button type="button" data-source-evidence="${row.id}">查看单行原始证据与关系</button></article>`;
}

function fileRowsCard(fileId, initial) {
  return `<section class="inspection-card inspection-wide inspection-file-rows" data-file-rows="${fileId}"><header><div><h3>来源行处理结果</h3><p>逐行显示是否生成事实；原始 JSON 按需展开。</p></div><label>显示 <select data-row-status><option value="">全部来源行</option><option value="1">已关联事实</option><option value="2">已跳过</option><option value="3">待处理异常</option></select></label></header><div data-row-items>${initial.items.length ? `<div class="inspection-record-list">${initial.items.map(importRow).join("")}</div>` : '<p class="inspection-empty">没有来源行</p>'}</div><nav class="inspection-pagination" data-row-pager><span></span><button data-row-prev>上一页</button><button data-row-next>下一页</button></nav></section>`;
}

async function mountFileRows(root, initial, bindActions) {
  const panel = root.querySelector("[data-file-rows]");
  if (!panel) return;
  const fileId = panel.dataset.fileRows;
  const items = panel.querySelector("[data-row-items]");
  const status = panel.querySelector("[data-row-status]");
  const pager = panel.querySelector("[data-row-pager]");
  let page = 1;
  let busy = false;
  let data = initial;
  const paint = () => {
    items.innerHTML = data.items.length ? `<div class="inspection-record-list">${data.items.map(importRow).join("")}</div>` : '<p class="inspection-empty">当前条件下没有来源行</p>';
    bindActions(items);
    items.querySelectorAll("[data-source-evidence]").forEach(button => {
      button.onclick = async () => {
        const dialog = workbenchDialog("单行原始证据（只读）", '<p role="status">正在读取…</p>');
        try {
          const rowId = Number(button.dataset.sourceEvidence);
          const detail = await request(`${endpoints.file}${fileId}/row/${rowId}`);
          const params = new URLSearchParams({ row_ids: JSON.stringify([rowId]) });
          const relations = await request(`${endpoints.file}${fileId}/row/relations?${params}`);
          if (!dialog.isConnected) return;
          dialog.querySelector(".dialog-body").innerHTML = `<p>来源第 ${detail.row.source_row_number} 行 · ${esc(rowStates[detail.row.row_status])}</p>${detail.fact ? `<p>Fact #${detail.fact.id} · ${esc(amount(detail.fact))} · ${esc(when(detail.fact.occurred_time))}</p>` : "<p>未接受为Fact</p>"}${jsonPayload(detail.raw_payload)}<h3>当前可核验关系</h3>${relations.items.map(row => `<p>Review #${row.review_id || "无"} ${esc(row.review_id ? financialStateLabel('review',row.review_status) : '未关联解释')} · Ledger #${row.ledger_id || "无"}</p>`).join("")}`;
        } catch (error) { if (dialog.isConnected) dialog.querySelector("[role=status]").textContent = error.message; }
      };
    });
    const start = data.total ? (data.page_index - 1) * data.page_size + 1 : 0;
    pager.querySelector("span").textContent = `显示 ${start}–${Math.min(data.page_index * data.page_size, data.total)} / ${data.total} 行`;
    pager.querySelector("[data-row-prev]").disabled = data.page_index <= 1;
    pager.querySelector("[data-row-next]").disabled = data.page_index * data.page_size >= data.total;
  };
  const fetchRows = async requestedPage => {
    if (busy) return;
    busy = true;
    status.disabled = true;
    pager.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const filter = status.value ? `&filter=${encodeURIComponent(JSON.stringify({ key: "row_status", op: "=", val: Number(status.value) }))}` : "";
    try {
      const next = await request(`${endpoints.file}${fileId}/row/list?page_index=${requestedPage}&page_size=20${filter}`);
      if (!panel.isConnected) return;
      data = next;
      page = requestedPage;
      paint();
    } catch (error) {
      if (panel.isConnected) { paint(); items.insertAdjacentHTML("afterbegin", `<p role="status">${esc(error.message)}；分页未推进，请重新读取。</p>`); }
    } finally { busy = false; status.disabled = false; }
  };
  status.onchange = () => fetchRows(1);
  pager.querySelector("[data-row-prev]").onclick = () => { fetchRows(page - 1); panel.scrollIntoView({ block: "start" }); };
  pager.querySelector("[data-row-next]").onclick = () => { fetchRows(page + 1); panel.scrollIntoView({ block: "start" }); };
  paint();
}

function describe(kind, data) {
  if (adapters.has(kind)) return adapters.get(kind).describe(data);
  const p = new Presentation();
  let item, title, subtitle, hero = "", body = "", actions = "";
  if (kind === 'fact' && data.paged_relations) {
    const fact = data.fact;
    return {title:businessTitle(fact), subtitle:`Fact #${fact.id} · 分页只读详情`, hero:amount(fact),
      body:'<p>完整 Fact 详情超出预算，以下只读取当前关系页；没有截断合计。原文通过来源行单独读取。</p>'
        + [['allocation','第一段分占与原始审查'],['source_row','脱敏来源索引']].map(([name,label]) => `<section class="inspection-card" data-fact-relation="${name}" data-fact-id="${fact.id}"><h3>${label}</h3><div data-rel-items></div><nav class="inspection-pagination"><span data-rel-status>正在读取…</span><button data-rel-prev disabled>上一页</button><button data-rel-next disabled>下一页</button></nav></section>`).join(''),
      actions:'来源事实不可修改',presentation:p,kind:'fact-paged'};
  }
  if (kind === "review" && data.paged_relations) {
    const review = data.review;
    const relations = [['allocation','Fact → Ledger 关系'], ['flow','原始现金结果'],
      ['position_leg','原始数量腿'], ['position_allocation','款项归因（不是额外现金）'], ['position','独立数量对象']];
    return { title: review.title || `Review #${review.id}`, subtitle: `${reviewTypeNames[review.type]} · ${statusNames[review.status]} · 分页只读详情`, hero: '',
      body: '<p>完整详情超出预算，各关系独立分页。原始结果不变；当前页不是完整集合，不计算截断合计。</p>'
        + relations.map(([name,label]) => `<section class="inspection-card" data-review-relation="${name}" data-review-id="${review.id}"><h3>${label}</h3><div data-rel-items></div><nav class="inspection-pagination"><span data-rel-status>正在读取…</span><button data-rel-prev disabled>上一页</button><button data-rel-next disabled>下一页</button></nav></section>`).join(''),
      actions: '超大审查关系只读；操作前需重新预览完整影响', presentation: p, kind: 'review-paged' };
  }
  if (kind === "ledger" && data.paged_relations) {
    const source = data.source;
    return { title: businessTitle(source.fact), subtitle: `${typeNames[source.ledger_entry.economic_type]} · ${source.active ? "有效" : "已停用"} · 分页只读详情`, hero: amount(source.ledger_entry),
      body: '<p>完整详情超出预算。以下集合独立分页，当前页不是完整关系；不会提供截断合计。</p>'
        + card("原始资金来源", fields([["Ledger", source.ledger_entry.id], ["Fact", source.fact.id], ["Review", `${source.review.id} · ${source.review.status}`], ["脱敏摘要", source.fact.summary], ["当前归属", source.account.state]]))
        + relationButton("fact", source.fact.id, "查看来源事实") + relationButton("review", source.review.id, "查看原始审查")
        + ['tag', 'position_allocation'].map(name => `<section class="inspection-card" data-flow-relation="${name}" data-ledger-id="${source.ledger_entry.id}"><h3>${name === 'tag' ? '标签证据' : '款项归因与数量腿（不是额外现金）'}</h3><div data-rel-items></div><nav class="inspection-pagination"><span data-rel-status>正在读取…</span><button data-rel-prev disabled>上一页</button><button data-rel-next disabled>下一页</button></nav></section>`).join(''),
      actions: "超大关系详情只读；编辑须重新核对当前数据", presentation: p, kind: "ledger-paged" };
  }
  if (kind === "fact") {
    item = data.transaction_fact;
    title = businessTitle(item);
    subtitle = `${when(item.occurred_time)} · ${direction(item.cash_direction)}`;
    hero = amount(item);
    const reviewIds = new Set(data.reviews.filter(row => row.status === 'CONFIRMED').map(row => row.id));
    const active = data.allocations.filter(row => reviewIds.has(row.review_id) && row.cash_currency_code === item.currency_code);
    const allocated = active.reduce((sum, row) => sum + row.cash_amount, 0);
    body = metrics([["有效解释覆盖", amount({ ...item, amount: allocated }), "accent"], ["尚未解释", amount({ ...item, amount: item.amount - allocated })], ["来源行", data.import_evidence.length]])
      + '<p>分占覆盖包含 DUPLICATE 证据，不等于现金统计。现金计量须核对有效且非重复的 Ledger；多个来源行不是多笔现金。</p>'
      + `<p data-financial-scope-note>${esc(financialScopeNote)}</p>`
      + '<div class="inspection-dashboard">'
      + card("交易概览", fields([["摘要", item.summary], ["交易对手", item.counterparty_name], ["本方账户", readableAccount(item.account_code)], ["发生时间", when(item.occurred_time)]]), { tone: "accent" })
      + card(`来源行 · ${data.import_evidence.length}`, p.collection(data.import_evidence, evidenceRow, "行来源证据"))
      + allocationSection(p, data.allocations, [item], data.ledgers, data.reviews, { kind, id: item.id }) + "</div>";
  } else if (kind === "ledger") {
    item = data.ledger_entry;
    const fact = data.facts[0];
    title = businessTitle(fact || item);
    subtitle = `${typeNames[item.economic_type] || "分类未识别"} · ${data.active ? "Ledger 有效" : "Ledger 已停用"} · ${direction(item.cash_direction)}`;
    hero = amount(item);
    const owner = data.account;
    const ownerText = owner.state === "UNIDENTIFIED" ? "来源未识别（ref = 0）" : owner.state === "UNASSIGNED" ? "来源已识别，尚未分组" : `个人 #${owner.party.id} ${owner.party.name} / 账户 #${owner.account.id} ${owner.account.name}`;
    body = metrics([["原始现金金额", amount(item), "accent"], ["来源事实金额", fact ? amount(fact) : "未提供"], ["占来源事实", fact?.cash_amount > 0 && fact.cash_currency_code === item.cash_currency_code ? `${(item.cash_amount / fact.cash_amount * 100).toFixed(2)}%` : "不适用"]])
      + `<p>${item.economic_type === "DUPLICATE" ? "DUPLICATE 金额仅保留证据，不计入财务汇总。" : "现金效果仅在原 Review 有效时计入。"} 款项归因不是第二笔现金；没有数量腿不代表数量为零。</p>`
      + '<div class="inspection-dashboard">'
      + card("账本概览", fields([["脱敏摘要", data.summary], ["有效状态", data.active ? "有效" : "已停用"], ["经济分类", typeNames[item.economic_type]], ["发生时间", when(item.occurred_time)]]), { tone: "accent" })
      + card("当前账户归属（不改来源事实）", fields([["来源 ID", item.account_ref_id], ["分组", ownerText], ["原账单账户（脱敏）", fact?.account_code]]))
      + card("分类标签", p.collection(data.tags, tag => `<article class="inspection-flow"><strong>${esc(tag.tag_name)}</strong><p>${esc(tag.view_name)} · ${esc(financialStateLabel('metadata',tag.view_status))} / ${esc(financialStateLabel('metadata',tag.tag_status))}</p><small>${tag.source_type === "AUTO_RULE" ? `自动规则 #${esc(tag.rule_id)} · Rev ${esc(tag.rule_revision)} · Request #${esc(tag.request_id)}` : tag.tag_system_name === "unclassified" ? "默认未分类" : "UNKNOWN（现有证据不能证明来源）"}</small></article>`))
      + allocationSection(p, data.allocations, data.facts, [item], data.reviews, { kind, id: item.id })
      + card(`数量身份 · ${financialStateLabel('identity',data.position_identity_state)}`, p.collection(data.positions, row => `<article class="inspection-flow"><a href="#workbench/position?id=${row.id}">Position #${row.id} ${esc(row.title)}</a><p>${esc(financialStateLabel('positionType',row.type))} · ${esc(row.unit_code)} · ${esc(financialStateLabel('position',row.status))}</p></article>`))
      + card("原始数量腿（只读）", p.collection(data.position_legs, row => `<article class="inspection-flow"><strong>${esc(row.leg_direction)} ${quantityDecimal(row.leg_amount, row.unit_code)} ${esc(row.unit_code)}</strong><p>Leg #${row.id} · Review #${row.review_id} · ${esc(row.type)} · 来源腿 #${row.source_position_leg_id}</p><p>${esc(row.basis)}</p></article>`))
      + card("现金到数量腿归因（不是额外现金）", p.collection(data.position_allocations, row => `<article class="inspection-flow"><strong>${esc(amount(row))}</strong><p>Link #${row.id} · Leg #${row.position_leg_id}</p>${relationButton("review", row.review_id, `Review #${row.review_id}`)}</article>`)) + "</div>";
    actions = data.active ? `<button data-action="edit-ledger-account" data-id="${item.id}">用新解释更正账户</button><button data-action="edit-tags" data-id="${item.id}">编辑标签</button>` : "原审查已停用，关系仅供读取";
  } else if (kind === "review") {
    item = data;
    title = businessTitle(item);
    subtitle = `${reviewTypeNames[item.type] || item.type} · ${statusNames[item.status] || item.status}`;
    body = metrics([["涉及事实", new Set(item.allocations.map(row => row.transaction_id)).size], ["原始现金结果", item.ledger_entries.length, "accent"], ["原始数量腿", item.position_legs.length], ["最近状态更新", when(item.updated_time)]])
      + `<p data-financial-scope-note>${esc(financialScopeNote)}</p>`
      + '<p>内容不可变；停用仍保留原始结果与关系。DUPLICATE 保留证据金额但不计现金；款项归因不是第二笔现金。</p><div class="inspection-dashboard">'
      + card("原始现金结果", p.collection(item.ledger_entries, row => `<article class="inspection-flow"><strong>${esc(money(row))}</strong><p>${esc(typeNames[row.economic_type])} · ${esc(row.cash_direction)} · 来源卡 #${row.account_ref_id} · ${esc(when(row.occurred_time))}</p>${relationButton("ledger", row.id, `Ledger #${row.id}`)}</article>`), { tone: "accent" })
      + card("Fact → Ledger 完整关系", p.collection(item.allocations, row => `<article class="inspection-flow"><strong>${esc(money(row))}</strong><p>Allocation #${row.id} · Review #${row.review_id}</p>${relationButton("fact", row.transaction_id, `Fact #${row.transaction_id}`)}${relationButton("ledger", row.ledger_id, `Ledger #${row.ledger_id}`)}</article>`, "条原始关系"))
      + card("原始数量腿", p.collection(item.position_legs, row => `<article class="inspection-flow"><strong>${esc(row.leg_direction)} ${quantityDecimal(row.leg_amount, row.unit_code)} ${esc(row.unit_code)}</strong><p>Leg #${row.id} · ${esc(row.type)} · 来源腿 #${row.source_position_leg_id} · ${esc(when(row.occurred_time))}</p><p>${esc(row.basis)}</p><a href="#workbench/position?id=${row.position_id}">对象 #${row.position_id}</a></article>`))
      + card("Ledger → 数量腿款项归因", p.collection(item.position_allocations, row => `<article class="inspection-flow"><strong>${esc(money(row))}</strong><p>Link #${row.id} · Leg #${row.position_leg_id}（不是额外现金）</p>${relationButton("ledger", row.ledger_id, `Ledger #${row.ledger_id}`)}</article>`)) + "</div>";
    actions = `<button data-action="economic-review-transition" data-kind="${item.status === "CONFIRMED" ? "revoke" : "restore"}" data-id="${item.id}">${item.status === "CONFIRMED" ? "预览停用与默认恢复" : "预览激活原审查"}</button>`;
  } else {
    item = data.file;
    title = `${sources[item.source_type] || "来源未识别"}账单`;
    subtitle = `${fileStates[item.status] || "状态未识别"} · ${formats[item.file_format] || "格式未识别"} · ${when(item.period_start)} 至 ${when(item.period_end)}`;
    const progress = data.progress;
    body = metrics([["来源总行数", item.total_count], ["已关联事实", progress.accepted, "positive"], ["已跳过", progress.skipped, "muted"], ["异常", progress.invalid, progress.invalid ? "negative" : "base"], ["剩余未处理", progress.remaining]])
      + '<div class="inspection-dashboard">'
      + card("文件概览", fields([["文件", item.filename], ["来源", sources[item.source_type]], ["格式", formats[item.file_format]], ["活动范围（不证明完整覆盖）", `${when(item.period_start)} 至 ${when(item.period_end)}`], ["首次导入时间", when(item.created_time)]]), { tone: "accent" })
      + card("核对说明", "<p>接受行仅表示来源已关联Fact；当前金融效果须核对Review状态。原文只在单行详情展示，多条来源证据不能当多份现金。</p>")
      + fileRowsCard(item.id, data.rows) + "</div>";
  }
  return { title, subtitle, hero, body, actions, presentation: p, kind };
}

async function load(kind, id) {
  if (adapters.has(kind)) return adapters.get(kind).load(id);
  if (kind === 'fact') {
    try { return await request(`${endpoints.fact}${id}`); }
    catch (error) {
      if (error.code !== 'DETAIL_LIMIT') throw error;
      const query = new URLSearchParams({page_index:'1',page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:Number(id)})});
      const page = await request(`${endpoints.fact}list?${query}`);
      if (page.total !== 1 || page.items.length !== 1) throw new Error('来源事实不存在，请重新读取');
      return {paged_relations:true,fact:page.items[0]};
    }
  }
  if (kind === 'review') {
    try { return await request(`${endpoints.review}${id}`); }
    catch (error) {
      if (error.code !== 'DETAIL_LIMIT') throw error;
      const query = new URLSearchParams({page_index:'1',page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:Number(id)})});
      const page = await request(`${endpoints.review}list?${query}`);
      if (page.total !== 1 || page.items.length !== 1) throw new Error('原始审查不存在，请重新读取');
      return {paged_relations:true, review:page.items[0]};
    }
  }
  if (kind === "ledger") {
    try { return await request(`${endpoints.ledger}${id}`); }
    catch (error) {
      if (error.code !== 'DETAIL_LIMIT') throw error;
      const page = await request(`${endpoints.ledger}${id}/allocation/list?page_index=1&page_size=1`);
      if (page.total !== 1 || page.items.length !== 1) throw new Error('来源关系不完整，请检查数据');
      return { paged_relations: true, source: page.items[0] };
    }
  }
  if (kind !== "file") return request(`${endpoints[kind]}${id}`);
  const [detail, rows] = await Promise.all([
    request(`${endpoints.file}${id}`),
    request(`${endpoints.file}${id}/row/list?page_index=1&page_size=20`),
  ]);
  return { ...detail, rows };
}

function mountFlowRelations(root) {
  root.querySelectorAll('[data-flow-relation], [data-review-relation], [data-fact-relation]').forEach(panel => {
    const isFact = !!panel.dataset.factRelation;
    const isReview = !!panel.dataset.reviewRelation;
    const name = panel.dataset.factRelation || panel.dataset.reviewRelation || panel.dataset.flowRelation;
    const url = isFact ? `/paam/ledger/v1/fact/${panel.dataset.factId}/${name}/list` : `${isReview ? endpoints.review : endpoints.ledger}${isReview ? panel.dataset.reviewId : panel.dataset.ledgerId}/${name}/list`;
    let page = 1, busy = false, total = 0;
    const prev = panel.querySelector('[data-rel-prev]'), next = panel.querySelector('[data-rel-next]');
    async function fetchPage(target) {
      if (busy) return;
      busy = true; prev.disabled = next.disabled = true;
      const status = panel.querySelector('[data-rel-status]');
      status.textContent = '正在读取…';
      try {
        const result = await request(`${url}?page_index=${target}&page_size=20`);
        if (!panel.isConnected) return;
        page = result.page_index; total = result.total;
        panel.querySelector('[data-rel-items]').innerHTML = result.items.map(row => isFact ? name === 'source_row' ? evidenceRow(row) : `<article class="inspection-flow"><strong>${esc(amount(row.allocation))}</strong><p>Allocation #${row.allocation.id} · ${esc(financialStateLabel('review',row.review.status))} · ${esc(typeNames[row.ledger_entry.economic_type])}</p>${relationButton('review',row.review.id,`Review #${row.review.id}`)}${relationButton('ledger',row.ledger_entry.id,`Ledger #${row.ledger_entry.id}`)}</article>` : isReview ? reviewRelationRow(name, row) : name === 'tag'
          ? `<article class="inspection-flow"><strong>${esc(row.tag_name)}</strong><p>${esc(row.view_name)} · ${esc(financialStateLabel('metadata',row.view_status))} / ${esc(financialStateLabel('metadata',row.tag_status))} · ${esc(row.source_type)}</p></article>`
          : `<article class="inspection-flow"><strong>${esc(amount(row.allocation))}</strong><p>Link #${row.allocation.id} · Leg #${row.position_leg.id} · ${esc(financialStateLabel('review',row.review.status))}</p><p>${esc(row.position_leg.leg_direction)} ${quantityDecimal(row.position_leg.leg_amount, row.position_leg.unit_code)} ${esc(row.position_leg.unit_code)}</p><a href="#workbench/position?id=${row.position.id}">Position #${row.position.id} ${esc(row.position.title)}</a></article>`).join('') || '<p>该集合当前页没有记录</p>';
        status.textContent = `共 ${total} 项 · 第 ${page} 页（仅本页）`;
      } catch (error) { if (panel.isConnected) status.textContent = `读取失败，未更新当前页：${error.message}`; }
      finally { busy = false; if (panel.isConnected) { prev.disabled = page <= 1; next.disabled = page * 20 >= total; } }
    }
    prev.onclick = () => fetchPage(page - 1);
    next.onclick = () => fetchPage(page + 1);
    fetchPage(1);
  });
}

function reviewRelationRow(name, row) {
  if (name === 'flow') return `<article class="inspection-flow"><strong>${esc(amount(row))}</strong><p>${esc(typeNames[row.economic_type])} · ${esc(row.cash_direction)}</p>${relationButton('ledger',row.id,`Ledger #${row.id}`)}</article>`;
  if (name === 'allocation') return `<article class="inspection-flow"><strong>${esc(amount(row))}</strong><p>Allocation #${row.id}</p>${relationButton('fact',row.transaction_id,`Fact #${row.transaction_id}`)}${relationButton('ledger',row.ledger_id,`Ledger #${row.ledger_id}`)}</article>`;
  if (name === 'position_leg') return `<article class="inspection-flow"><strong>${esc(row.leg_direction)} ${quantityDecimal(row.leg_amount,row.unit_code)} ${esc(row.unit_code)}</strong><p>Leg #${row.id} · ${esc(row.type)} · 来源腿 #${row.source_position_leg_id}</p><p>${esc(row.basis)}</p><a href="#workbench/position?id=${row.position_id}">Position #${row.position_id}</a></article>`;
  if (name === 'position_allocation') return `<article class="inspection-flow"><strong>${esc(amount(row))}</strong><p>Link #${row.id} · Leg #${row.position_leg_id}（不是额外现金）</p>${relationButton('ledger',row.ledger_id,`Ledger #${row.ledger_id}`)}</article>`;
  return `<article class="inspection-flow"><a href="#workbench/position?id=${row.id}">Position #${row.id} ${esc(row.title)}</a><p>${esc(financialStateLabel('positionType',row.type))} · ${esc(row.unit_code)} · ${esc(financialStateLabel('position',row.status))}</p></article>`;
}

let current = null;
let backgroundOverflow = null;
let backgroundPaddingRight = null;

function readListContext() {
  const buttons = [...document.querySelectorAll('#page-content .detail-primary[data-action]')]
    .filter((button) => actionKinds[button.dataset.action]);
  const pager = document.querySelector("#page-content .ledger-pagination");
  const pageButtons = pager ? [...pager.querySelectorAll('[data-action="detail-page"]')] : [];
  return {
    rows: buttons.map((button) => ({
      kind: actionKinds[button.dataset.action],
      id: Number(button.dataset.id),
      title: button.dataset.inspectTitle || button.querySelector("strong")?.textContent || button.textContent,
      meta: button.dataset.inspectMeta || "",
      amount: button.dataset.inspectAmount || button.closest("tr")?.querySelector(".fact-amount,.money")?.textContent || "",
    })),
    pageLabel: pager?.querySelector(".page-buttons span")?.textContent?.trim() || "",
    previous: pageButtons[0] || null,
    next: pageButtons.at(-1) || null,
  };
}

function listSignature() {
  const context = readListContext();
  return `${context.pageLabel}|${context.rows.map((row) => `${row.kind}:${row.id}`).join(",")}`;
}

export async function openInspection(kind, id, bindActions, {readOnly = false, signal} = {}) {
  if (signal?.aborted) return;
  if (current?.dialog.isConnected && current.dialog.open) {
    if (current.readOnly === readOnly) return current.navigate(kind, Number(id), true, true);
    current.dialog.close();
  }
  const opener = document.activeElement;
  let listContext = readListContext();
  let rail = listContext.rows;
  const dialog = document.createElement("dialog");
  dialog.className = "detail-view-drawer inspection-workspace";
  dialog.setAttribute("aria-labelledby", "inspection-title");
  dialog.innerHTML = '<div class="inspection-shell"><aside class="inspection-rail" aria-label="当前列表页"><h2>当前列表</h2><div data-rail></div><footer class="inspection-rail-pager"><button data-rail-page="previous">上一页</button><span data-rail-page-label></span><button data-rail-page="next">下一页</button></footer></aside><div class="inspection-main"><header class="inspection-header"><div class="inspection-controls"><button data-inspect-back disabled>返回上层</button><button data-inspect-prev>上一条</button><button data-inspect-next>下一条</button><button data-inspect-full aria-pressed="false">全屏查看</button><button data-close>返回列表</button></div><div class="inspection-heading" tabindex="-1"><div><span data-kind-label></span><h2 id="inspection-title">正在加载…</h2><p data-inspect-subtitle></p></div><strong data-inspect-hero></strong></div><div class="inspection-tools" data-inspect-actions></div></header><div class="detail-view-drawer-body inspection-body" tabindex="0" aria-label="详情内容"></div></div></div>';
  document.body.append(dialog);
  const contentWidth = document.documentElement.clientWidth;
  if (backgroundOverflow === null) {
    backgroundOverflow = document.body.style.overflow;
    backgroundPaddingRight = document.body.style.paddingRight;
  }
  document.body.style.overflow = "hidden";
  const releasedScrollbarWidth = Math.max(0, document.documentElement.clientWidth - contentWidth);
  if (releasedScrollbarWidth) {
    const paddingRight = Number.parseFloat(getComputedStyle(document.body).paddingRight) || 0;
    document.body.style.paddingRight = `${paddingRight + releasedScrollbarWidth}px`;
  }
  dialog.showModal();
  const railRoot = dialog.querySelector("[data-rail]");
  const body = dialog.querySelector(".inspection-body");
  const stack = [];
  let selected = null, version = 0, full = false, listPaging = false;
  const cache = new Map();
  const get = (nextKind, nextId, fresh = false) => {
    const key = `${nextKind}:${nextId}`;
    if (fresh) cache.delete(key);
    if (!cache.has(key)) cache.set(key, load(nextKind, nextId).catch(error => { cache.delete(key); throw error; }));
    return cache.get(key);
  };
  function renderRail() {
    railRoot.innerHTML = rail.map((row) => `<button data-rail-kind="${row.kind}" data-rail-id="${row.id}" title="${esc(row.title)}"><strong>${esc(row.title)}</strong>${row.meta ? `<small>${esc(row.meta)}</small>` : ""}${row.amount ? `<span>${esc(row.amount)}</span>` : ""}</button>`).join("");
    dialog.querySelector("[data-rail-page-label]").textContent = listContext.pageLabel;
    dialog.querySelector('[data-rail-page="previous"]').disabled = !listContext.previous || listContext.previous.disabled;
    dialog.querySelector('[data-rail-page="next"]').disabled = !listContext.next || listContext.next.disabled;
  }
  function syncLayout() {
    const supportsWideWorkspace = window.innerWidth >= 1280 && window.innerHeight >= 680;
    let layout = "full";
    if (full) layout = supportsWideWorkspace ? "split" : "full";
    else if (supportsWideWorkspace) layout = "wide";
    else if (window.innerWidth >= 600 && window.innerHeight < 640) layout = "bottom";
    else if (window.innerWidth >= 768) layout = "right";
    dialog.dataset.layout = layout;
    const button = dialog.querySelector("[data-inspect-full]");
    button.setAttribute("aria-pressed", String(full));
    button.textContent = full ? "恢复自适应" : "全屏查看";
    button.hidden = layout === "full" && !full;
  }
  function updateSelection() {
    const index = rail.findIndex((row) => row.kind === selected?.kind && row.id === selected?.id);
    dialog.querySelector("[data-inspect-prev]").disabled = index <= 0;
    dialog.querySelector("[data-inspect-next]").disabled = index < 0 || index >= rail.length - 1;
    railRoot.querySelectorAll("button").forEach((button) => {
      const active = button.dataset.railKind === selected?.kind && Number(button.dataset.railId) === selected?.id;
      button.setAttribute("aria-current", String(active));
      if (active) button.scrollIntoView({ block: "nearest" });
    });
  }
  async function navigate(nextKind, nextId, record = true, fresh = false) {
    const ticket = ++version;
    const sameRecord = selected?.kind === nextKind && selected?.id === nextId;
    if (record && selected && !sameRecord) stack.push({ ...selected, scroll: body.scrollTop });
    selected = { kind: nextKind, id: nextId };
    if (!sameRecord) {
      dialog.querySelector("[data-kind-label]").textContent = names[nextKind];
      dialog.querySelector("#inspection-title").textContent = "正在加载…";
      dialog.querySelector("[data-inspect-subtitle]").textContent = "";
      dialog.querySelector("[data-inspect-hero]").textContent = "";
      dialog.querySelector("[data-inspect-actions]").innerHTML = "";
      dialog.querySelector("[data-inspect-back]").disabled = !stack.length;
      updateSelection();
      body.innerHTML = '<p role="status">正在加载详情…</p>';
    }
    try {
      const data = await get(nextKind, nextId, fresh);
      if (ticket !== version || !dialog.isConnected) return;
      const view = describe(nextKind, data);
      const apply = () => {
        dialog.querySelector("#inspection-title").textContent = view.title;
        dialog.querySelector("[data-inspect-subtitle]").textContent = view.subtitle;
        dialog.querySelector("[data-inspect-hero]").textContent = view.hero;
        const actions = dialog.querySelector("[data-inspect-actions]");
        actions.innerHTML = readOnly ? '<span>原事项及关联证据只读；关闭返回原页面，不修改草稿或已发布内容。</span>' : view.actions;
        if (!readOnly) bindActions(actions);
        body.innerHTML = view.body;
        view.presentation.mount(body);
        if (view.kind === "file") mountFileRows(body, data.rows, bindActions);
        if (['ledger-paged','review-paged','fact-paged'].includes(view.kind)) mountFlowRelations(body);
        dialog.dataset.renderVersion = String(ticket);
      };
      if (sameRecord) preserveView(dialog, apply);
      else { apply(); body.scrollTop = 0; dialog.querySelector(".inspection-heading").focus({ preventScroll: true }); }
    } catch (error) {
      if (ticket !== version || !dialog.isConnected) return;
      if (sameRecord) { dialog.querySelector('[data-inspect-subtitle]').textContent = `刷新失败，保留上次内容：${error.message}`; return; }
      dialog.querySelector("#inspection-title").textContent = "详情暂时无法加载";
      body.innerHTML = `<p role="alert">${esc(error.message)}</p><button data-inspect-retry>重试</button>`;
    }
  }
  async function changeListPage(direction) {
    const sourceButton = listContext[direction];
    if (!sourceButton || sourceButton.disabled || listPaging) return;
    const before = listSignature();
    listPaging = true;
    const changed = new Promise((resolve) => {
      const root = document.querySelector("#page-content");
      const observer = new MutationObserver(() => {
        if (listSignature() !== before && readListContext().rows.length) {
          observer.disconnect();
          resolve(true);
        }
      });
      observer.observe(root, { childList: true, subtree: true });
      window.setTimeout(() => { observer.disconnect(); resolve(false); }, 8000);
    });
    sourceButton.click();
    const didChange = await changed;
    listPaging = false;
    if (!didChange || !dialog.isConnected) return;
    listContext = readListContext();
    rail = listContext.rows;
    renderRail();
    const first = rail[0];
    if (first) await navigate(first.kind, first.id, false, true);
  }
  renderRail();
  syncLayout();
  current = { dialog, navigate, readOnly };
  const closeOnRoute = () => { if (!listPaging) dialog.close(); };
  const closeOnAbort = () => {if (dialog.open) dialog.close();};
  signal?.addEventListener('abort',closeOnAbort,{once:true});
  const resize = () => syncLayout();
  dialog.addEventListener("close", () => {
    version++;
    window.removeEventListener("hashchange", closeOnRoute);
    window.removeEventListener("resize", resize);
    signal?.removeEventListener('abort',closeOnAbort);
    dialog.remove();
    if (current?.dialog === dialog) current = null;
    if (!document.querySelector(".inspection-workspace[open]")) {
      document.body.style.overflow = backgroundOverflow ?? "";
      document.body.style.paddingRight = backgroundPaddingRight ?? "";
      backgroundOverflow = null;
      backgroundPaddingRight = null;
    }
    if (opener?.isConnected && !document.querySelector("dialog[open]")) opener.focus({ preventScroll: true });
  });
  window.addEventListener("hashchange", closeOnRoute);
  window.addEventListener("resize", resize, { passive: true });
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && ["wide", "right", "bottom"].includes(dialog.dataset.layout)) dialog.close();
  });
  dialog.querySelector("[data-inspect-full]").onclick = () => { full = !full; syncLayout(); };
  dialog.querySelector("[data-inspect-back]").onclick = async () => { const previous = stack.pop(); if (previous) { await navigate(previous.kind, previous.id, false); body.scrollTop = previous.scroll; } };
  for (const [selector, delta] of [["[data-inspect-prev]", -1], ["[data-inspect-next]", 1]]) dialog.querySelector(selector).onclick = () => { const index = rail.findIndex(row => row.kind === selected.kind && row.id === selected.id); const row = rail[index + delta]; if (row) navigate(row.kind, row.id); };
  dialog.addEventListener("click", event => {
    const evidence = event.target.closest('[data-fact-source-evidence]');
    if (evidence) {
      const node = workbenchDialog('单行原始证据（只读）','<p role="status">正在读取…</p>');
      request(`${endpoints.file}${evidence.dataset.fileId}/row/${evidence.dataset.factSourceEvidence}`).then(detail => {
        if (node.isConnected) node.querySelector('.dialog-body').innerHTML = `<p>来源第 ${detail.row.source_row_number} 行 · ${esc(rowStates[detail.row.row_status])}</p>${jsonPayload(detail.raw_payload)}`;
      }).catch(error => { if (node.isConnected) node.querySelector('[role=status]').textContent = error.message; });
    }
    const railButton = event.target.closest("[data-rail-id]");
    if (railButton) navigate(railButton.dataset.railKind, Number(railButton.dataset.railId), true, true);
    const railPage = event.target.closest("[data-rail-page]");
    if (railPage) changeListPage(railPage.dataset.railPage);
    const promote = event.target.closest("[data-promote]");
    if (promote) navigate(promote.dataset.kind, Number(promote.dataset.id));
    if (event.target.closest("[data-inspect-retry]")) navigate(selected.kind, selected.id, false, true);
  });
  return navigate(kind, Number(id), false, true);
}

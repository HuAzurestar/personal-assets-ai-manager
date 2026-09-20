import { request } from "../api/client.js";
import { esc, money, date as when, typeNames, statusNames } from "../util/core.js";

const names = { fact: "事实流水", ledger: "账本流水", review: "审查记录", file: "导入文件" };
const sources = { 0: "来源未识别", 1: "手工录入", 101: "支付宝", 102: "微信支付", 201: "建设银行", 202: "农业银行", 203: "招商银行" };
const behavior = { 0: "事实交易", 1: "借款与还款" };
const operations = { 0: "创建审查", 1: "修改审查", 2: "撤销审查", 3: "恢复审查" };
const fileStates = { 0: "待处理", 1: "已导入", 2: "部分导入", 3: "导入失败" };
const rowStates = { 0: "状态未知", 1: "已关联事实", 2: "已跳过", 3: "待处理异常" };
const formats = { 1: "CSV", 2: "XLS", 3: "XLSX", 4: "PDF" };
const endpoints = { fact: "/paam/ledger/v1/transaction_fact/", ledger: "/paam/ledger/v1/flow/", review: "/paam/ledger/v1/review/", file: "/paam/import/v1/import_file/" };
const actionKinds = { "fact-detail": "fact", "economic-detail": "ledger", "economic-review-detail": "review", "import-file-detail": "file" };

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
function direction(value) { return value === 1 ? "流入" : value === 2 ? "流出" : "方向未提供"; }
function amount(item) { return `${money(item)} ${item.currency_code}`; }
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
  try { formatted = JSON.stringify(JSON.parse(payload), null, 2); } catch { /* Preserve malformed evidence verbatim. */ }
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
  const factMap = new Map(facts.map(row => [row.id, row]));
  const ledgerMap = new Map(ledgers.map(row => [row.id, row]));
  const reviewMap = new Map(reviews.map(row => [row.id, row]));
  const render = row => {
    const fact = factMap.get(row.transaction_fact_id);
    const ledger = ledgerMap.get(row.ledger_entry_id);
    const review = reviewMap.get(row.review_case_id);
    const active = review?.status === 0;
    return `<article class="inspection-flow ${active ? "is-active" : "is-history"}"><div class="inspection-flow-main"><span>${active ? "当前生效" : "历史记录"}</span><strong>${esc(amount(row))}</strong><p>${esc(businessTitle(fact || review || {}))}</p><small>${esc(`${behavior[review?.behavior_type] || "类型未识别"} → ${typeNames[ledger?.entry_type] || "账本分类未识别"} · ${direction(ledger?.entry_direction)}`)}</small></div><div class="inspection-flow-actions">${context.kind === "fact" ? "" : relationButton("fact", row.transaction_fact_id, "查看来源事实")}${context.kind === "review" ? "" : relationButton("review", row.review_case_id, "查看审查")}${context.kind === "ledger" ? "" : relationButton("ledger", row.ledger_entry_id, "查看账本结果")}</div></article>`;
  };
  const active = allocations.filter(row => reviewMap.get(row.review_case_id)?.status === 0);
  const history = allocations.filter(row => reviewMap.get(row.review_case_id)?.status !== 0);
  let result = card("当前资金关系", presentation.collection(active, render, "条关系"), { tone: "accent" });
  if (history.length) result += card("历史资金关系", presentation.collection(history, render, "条历史关系"), { tone: "muted" });
  return result;
}

function evidenceRow(row) {
  return `<article class="inspection-source-row"><div><span class="inspection-status status-${row.row_status}">${esc(rowStates[row.row_status] || "状态未识别")}</span><strong>${esc(row.filename)}</strong><p>${esc(`${sources[row.source_type] || "来源未识别"} · 第 ${row.source_row_number} 行`)}</p>${row.issue_message ? `<small>${esc(row.issue_message)}</small>` : ""}</div>${relationButton("file", row.transaction_import_file_id, "查看来源文件")}${jsonPayload(row.raw_payload)}</article>`;
}

function rowReason(row, normalized) {
  if (row.issue_message) return row.issue_message;
  if (row.row_status === 1) return "该来源行已关联到事实流水";
  if (normalized?.amount_minor === 0) return "零金额来源行，仅保留证据，未生成事实";
  if (normalized?.status) return `来源状态：${normalized.status}；未生成事实`;
  return row.row_status === 2 ? "该来源行未生成事实" : "需要检查来源数据";
}
function importRow(row) {
  let payload = null;
  try { payload = row.raw_payload ? JSON.parse(row.raw_payload) : null; } catch { payload = null; }
  const normalized = payload?.normalized || {};
  const fact = row.transaction_fact;
  const title = fact ? businessTitle(fact) : normalized.note || normalized.merchant || `来源第 ${row.source_row_number} 行`;
  const conflict = row.issue_code === "FACT_CONFLICT"
    ? `<button type="button" class="inspection-link" data-action="fact-conflict-detail" data-id="${row.id}">处理事实冲突</button>`
    : "";
  return `<article class="inspection-import-row status-${row.row_status}"><div class="inspection-row-number">第 ${row.source_row_number} 行</div><div class="inspection-row-content"><span class="inspection-status status-${row.row_status}">${esc(rowStates[row.row_status] || "状态未识别")}</span><strong>${esc(title)}</strong><p>${esc(rowReason(row, normalized))}</p>${fact ? `<small>${esc(`${when(fact.occurred_time)} · ${fact.counterparty_name || "交易方未提供"}`)}</small>` : ""}</div>${fact ? `<div class="inspection-row-money">${esc(amount(fact))}${relationButton("fact", fact.id, "查看事实流水")}</div>` : conflict}<div class="inspection-row-json">${jsonPayload(row.raw_payload)}</div></article>`;
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
  let data = initial;
  const paint = () => {
    items.innerHTML = data.items.length ? `<div class="inspection-record-list">${data.items.map(importRow).join("")}</div>` : '<p class="inspection-empty">当前条件下没有来源行</p>';
    bindActions(items);
    const start = data.total ? (data.page_index - 1) * data.page_size + 1 : 0;
    pager.querySelector("span").textContent = `显示 ${start}–${Math.min(data.page_index * data.page_size, data.total)} / ${data.total} 行`;
    pager.querySelector("[data-row-prev]").disabled = data.page_index <= 1;
    pager.querySelector("[data-row-next]").disabled = data.page_index * data.page_size >= data.total;
  };
  const fetchRows = async () => {
    items.innerHTML = '<p role="status">正在加载来源行…</p>';
    const filter = status.value ? `&filter=${encodeURIComponent(JSON.stringify({ key: "row_status", op: "=", val: Number(status.value) }))}` : "";
    data = await request(`${endpoints.file}${fileId}/row/list?page_index=${page}&page_size=20${filter}`);
    paint();
  };
  status.onchange = () => { page = 1; fetchRows(); };
  pager.querySelector("[data-row-prev]").onclick = () => { page--; fetchRows(); panel.scrollIntoView({ block: "start" }); };
  pager.querySelector("[data-row-next]").onclick = () => { page++; fetchRows(); panel.scrollIntoView({ block: "start" }); };
  paint();
}

function describe(kind, data) {
  const p = new Presentation();
  let item, title, subtitle, hero = "", body = "", actions = "";
  if (kind === "fact") {
    item = data.transaction_fact;
    title = businessTitle(item);
    subtitle = `${when(item.occurred_time)} · ${direction(item.cash_direction)}`;
    hero = amount(item);
    const reviewIds = new Set(data.reviews.filter(row => row.status === 0).map(row => row.id));
    const active = data.allocations.filter(row => reviewIds.has(row.review_case_id) && row.currency_code === item.currency_code);
    const allocated = active.reduce((sum, row) => sum + row.amount, 0);
    body = metrics([["已进入账本", amount({ ...item, amount: allocated }), "accent"], ["尚未分配", amount({ ...item, amount: item.amount - allocated })], ["来源行", data.import_evidence.length]])
      + '<div class="inspection-dashboard">'
      + card("交易概览", fields([["摘要", item.summary], ["交易对手", item.counterparty_name], ["本方账户", readableAccount(item.account_code)], ["发生时间", when(item.occurred_time)]]), { tone: "accent" })
      + card(`来源行 · ${data.import_evidence.length}`, p.collection(data.import_evidence, evidenceRow, "行来源证据"))
      + allocationSection(p, data.allocations, [item], data.ledgers, data.reviews, { kind, id: item.id }) + "</div>";
  } else if (kind === "ledger") {
    item = data.ledger_entry;
    const fact = data.facts[0];
    title = businessTitle(fact || item);
    const effective = data.reviews.some(row => row.status === 0);
    subtitle = `${typeNames[item.entry_type] || "分类未识别"} · ${effective ? "当前生效" : "历史记录"} · ${direction(item.entry_direction)}`;
    hero = amount(item);
    body = metrics([["账本金额", amount(item), "accent"], ["来源事实金额", fact ? amount(fact) : "未提供"], ["占来源事实", fact?.amount > 0 && fact.currency_code === item.currency_code ? `${(item.amount / fact.amount * 100).toFixed(2)}%` : "不适用"]])
      + '<div class="inspection-dashboard">'
      + card("账本概览", fields([["摘要", item.summary], ["经济分类", typeNames[item.entry_type]], ["本方账户", readableAccount(item.account_code)], ["发生时间", when(item.occurred_time)]]), { tone: "accent" })
      + card("分类标签", item.tags.length ? `<div class="inspection-tags">${item.tags.map(tag => `<span><small>${esc(tag.view_name)}</small>${esc(tag.tag_name)}</span>`).join("")}</div>` : '<p class="inspection-empty">暂无标签</p>')
      + allocationSection(p, data.allocations, data.facts, [item], data.reviews, { kind, id: item.id }) + "</div>";
    actions = `<button data-action="edit-ledger-account" data-id="${item.id}">编辑账户</button><button data-action="edit-tags" data-id="${item.id}">编辑标签</button>`;
  } else if (kind === "review") {
    item = data;
    title = reviewTitle(item, item.facts);
    subtitle = `${behavior[item.behavior_type] || "类型未识别"} · ${statusNames[item.status] || "状态未识别"}`;
    body = metrics([["涉及事实", item.facts.length], ["账本结果", item.ledger_entries.length, "accent"], ["最近更新", when(item.updated_time)]])
      + totals(item.ledger_entries, "entry_direction") + '<div class="inspection-dashboard">'
      + allocationSection(p, item.allocations, item.facts, item.ledger_entries, [item], { kind, id: item.id })
      + card(`变更记录 · ${item.history.length}`, p.collection(item.history, row => `<article class="inspection-history"><strong>${esc(operations[row.operation] || "操作未识别")}</strong><time>${esc(when(row.created_time))}</time><p>${esc(row.reason === "ensure exact accepted-fact coverage" ? "系统建立默认分配，确保事实金额完整入账" : row.reason || "未填写原因")}</p></article>`, "次变更")) + "</div>";
    actions = `<button data-action="economic-review-transition" data-kind="${item.status === 0 ? "revoke" : "restore"}" data-id="${item.id}">${item.status === 0 ? "撤销并恢复默认交易" : "恢复审查"}</button>`;
  } else {
    item = data.import_file;
    title = `${sources[item.source_type] || "来源未识别"}账单`;
    subtitle = `${fileStates[item.status] || "状态未识别"} · ${formats[item.file_format] || "格式未识别"} · ${when(item.period_start)} 至 ${when(item.period_end)}`;
    const summary = data.relation_summary;
    body = metrics([["来源总行数", item.total_count], ["已关联事实", item.success_count, "positive"], ["已跳过", item.skip_count, "muted"], ["异常", item.issue_count, item.issue_count ? "negative" : "base"]])
      + '<div class="inspection-dashboard">'
      + card("文件概览", fields([["文件", item.filename], ["来源", sources[item.source_type]], ["格式", formats[item.file_format]], ["覆盖时间", `${when(item.period_start)} 至 ${when(item.period_end)}`], ["导入时间", when(item.created_time)]]), { tone: "accent" })
      + card("形成的账本结果", metrics([["有效审查", summary.review_count], ["账本结果", summary.ledger_count]]) + totals(summary.totals, "entry_direction"))
      + fileRowsCard(item.id, data.rows) + "</div>";
  }
  return { title, subtitle, hero, body, actions, presentation: p, kind };
}

async function load(kind, id) {
  if (kind !== "file") return request(`${endpoints[kind]}${id}`);
  const [detail, rows] = await Promise.all([
    request(`${endpoints.file}${id}`),
    request(`${endpoints.file}${id}/row/list?page_index=1&page_size=20`),
  ]);
  return { ...detail, rows };
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

export async function openInspection(kind, id, bindActions) {
  if (current?.dialog.isConnected && current.dialog.open) return current.navigate(kind, Number(id), true, true);
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
    const layout = full ? "full" : window.innerWidth >= 1280 && window.innerHeight >= 680
      ? "split"
      : window.innerWidth >= 600 && window.innerHeight < 640
        ? "bottom"
        : window.innerWidth >= 768 ? "right" : "full";
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
    if (record && selected) stack.push({ ...selected, scroll: body.scrollTop });
    selected = { kind: nextKind, id: nextId };
    dialog.querySelector("[data-kind-label]").textContent = names[nextKind];
    dialog.querySelector("#inspection-title").textContent = "正在加载…";
    dialog.querySelector("[data-inspect-subtitle]").textContent = "";
    dialog.querySelector("[data-inspect-hero]").textContent = "";
    dialog.querySelector("[data-inspect-actions]").innerHTML = "";
    dialog.querySelector("[data-inspect-back]").disabled = !stack.length;
    updateSelection();
    body.innerHTML = '<p role="status">正在加载详情…</p>';
    try {
      const data = await get(nextKind, nextId, fresh);
      if (ticket !== version || !dialog.isConnected) return;
      const view = describe(nextKind, data);
      dialog.querySelector("#inspection-title").textContent = view.title;
      dialog.querySelector("[data-inspect-subtitle]").textContent = view.subtitle;
      dialog.querySelector("[data-inspect-hero]").textContent = view.hero;
      const actions = dialog.querySelector("[data-inspect-actions]");
      actions.innerHTML = view.actions;
      bindActions(actions);
      body.innerHTML = view.body;
      view.presentation.mount(body);
      if (view.kind === "file") mountFileRows(body, data.rows, bindActions);
      body.scrollTop = 0;
      dialog.dataset.renderVersion = String(ticket);
      dialog.querySelector(".inspection-heading").focus({ preventScroll: true });
    } catch (error) {
      if (ticket !== version || !dialog.isConnected) return;
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
  current = { dialog, navigate };
  const closeOnRoute = () => { if (!listPaging) dialog.close(); };
  const resize = () => syncLayout();
  dialog.addEventListener("close", () => {
    version++;
    window.removeEventListener("hashchange", closeOnRoute);
    window.removeEventListener("resize", resize);
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
    if (event.target === dialog && ["right", "bottom"].includes(dialog.dataset.layout)) dialog.close();
  });
  dialog.querySelector("[data-inspect-full]").onclick = () => { full = !full; syncLayout(); };
  dialog.querySelector("[data-inspect-back]").onclick = async () => { const previous = stack.pop(); if (previous) { await navigate(previous.kind, previous.id, false); body.scrollTop = previous.scroll; } };
  for (const [selector, delta] of [["[data-inspect-prev]", -1], ["[data-inspect-next]", 1]]) dialog.querySelector(selector).onclick = () => { const index = rail.findIndex(row => row.kind === selected.kind && row.id === selected.id); const row = rail[index + delta]; if (row) navigate(row.kind, row.id); };
  dialog.addEventListener("click", event => {
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

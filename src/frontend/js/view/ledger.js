import { request, jsonRequest } from "../api/client.js";
import { toast } from "../component/toast.js";
import { table, pager } from "../component/table.js";
import { now, state } from "../state/ledger.js";
import {
  $, $$, date, esc, key, money, reviewRoles,
  reviewTypeNames, roleNames, statusNames, typeNames,
} from "../util/core.js";
import {
  canonicalHash, pageModule, parseHash, shellMarkup, syncNavigation,
} from "../navigation.js";
import {
  accountsMarkup, cursorFromParam, monthBounds,
} from "./account.js";

const entryTypeCodes = { 0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM" };
const entryTypeValues = { TRANSACTION: 0, ACCOUNT_TRANSFER: 1, CLAIM: 2 };

function entryDirection(direction) {
  return Number(direction) === 1 ? "IN" : "OUT";
}

function closeDialogs() {
  $$('dialog[open]').forEach((dialog) => dialog.close());
}
function tags(entry) {
  return `<div class="tag-list">${entry.tags.length
    ? entry.tags.map((item) => `<span class="tag" title="${esc(item.view_name)}">${esc(item.tag_name)}</span>`).join("")
    : '<span class="muted">无标签维度</span>'}</div>`;
}
function amountValue(item) {
  return Number(item?.amount_value || 0);
}

function signedMoney(item, direction) {
  if (!amountValue(item)) return "0";
  return `${direction === "IN" ? "＋" : "−"} ${money(item)}`;
}

function ledgerAmounts(entry) {
  const hasIncoming = amountValue(entry.incoming) > 0;
  const hasOutgoing = amountValue(entry.outgoing) > 0;
  if (!hasIncoming && !hasOutgoing) return '<span class="ledger-amount zero">0</span>';
  return `${hasIncoming ? `<span class="ledger-amount plus">＋ ${money(entry.incoming)}</span>` : ""}${hasOutgoing ? `<span class="ledger-amount minus">− ${money(entry.outgoing)}</span>` : ""}`;
}

function ledgerAccountPath(entry) {
  const hasIncoming = amountValue(entry.incoming) > 0;
  const hasOutgoing = amountValue(entry.outgoing) > 0;
  if (hasIncoming && hasOutgoing) return `${esc(entry.out_account_code)} <span>↔</span> ${esc(entry.in_account_code)}`;
  if (hasIncoming) return `流入 <span>→</span> ${esc(entry.in_account_code)}`;
  if (hasOutgoing) return `${esc(entry.out_account_code)} <span>→</span> 流出`;
  return "尚未形成现金方向";
}

function dateRangeLabel(start, end) {
  if (start && end) return `${start} — ${end}`;
  if (start) return `${start} — 请选择结束时间`;
  return "选择开始与结束时间";
}

function calendarGrid(start, end) {
  const { year, month } = state.ledgerCalendar;
  const leading = (new Date(year, month, 1).getDay() + 6) % 7;
  const count = new Date(year, month + 1, 0).getDate();
  const cells = Array.from({ length: leading }, () => '<span class="ledger-calendar-blank"></span>');
  for (let day = 1; day <= count; day += 1) {
    const value = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const classes = [
      "ledger-calendar-day",
      value === start ? "start" : "",
      value === end ? "end" : "",
      start && end && value > start && value < end ? "in-range" : "",
    ].filter(Boolean).join(" ");
    cells.push(`<button type="button" class="${classes}" data-action="ledger-date-day" data-value="${value}" aria-label="${value}">${day}</button>`);
  }
  return cells.join("");
}

function calendarHint(start, end) {
  if (state.ledgerCalendar.error) return state.ledgerCalendar.error;
  if (start && end) return `已选择 ${start} 至 ${end}；下次点击会重新设置开始时间。`;
  if (start) return `开始时间是 ${start}；请再点击一次选择结束时间。`;
  return "第 1 次点击设置开始时间，第 2 次点击设置结束时间。";
}

function ledgerDatePicker() {
  const start = state.params.get("date_from") || "";
  const end = state.params.get("date_to") || "";
  const anchor = start ? new Date(`${start}T12:00:00`) : now;
  state.ledgerCalendar.year = anchor.getFullYear();
  state.ledgerCalendar.month = anchor.getMonth();
  state.ledgerCalendar.next = start && !end ? "end" : "start";
  state.ledgerCalendar.error = "";
  return `<div class="ledger-date-picker">
    <input type="hidden" name="date_from" value="${esc(start)}">
    <input type="hidden" name="date_to" value="${esc(end)}">
    <button type="button" class="ledger-date-trigger ${start ? "" : "is-empty"}" data-action="ledger-date-toggle" aria-expanded="false"><span class="calendar-icon" aria-hidden="true">▣</span><span class="ledger-date-caption">${dateRangeLabel(start, end)}</span></button>
    <div class="ledger-date-popover" hidden>
      <div class="ledger-calendar-head"><strong class="ledger-calendar-month">${state.ledgerCalendar.year} 年 ${state.ledgerCalendar.month + 1} 月</strong><div><button type="button" data-action="ledger-date-month" data-value="-1" aria-label="上个月">‹</button><button type="button" data-action="ledger-date-month" data-value="1" aria-label="下个月">›</button></div></div>
      <p class="ledger-date-hint">${calendarHint(start, end)}</p>
      <div class="ledger-calendar-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>
      <div class="ledger-calendar-grid">${calendarGrid(start, end)}</div>
      <div class="ledger-calendar-foot"><small>奇数次设开始，偶数次设结束</small><button type="button" class="quiet" data-action="ledger-date-clear">清除</button></div>
    </div>
  </div>`;
}
function modal(title, body, wide = true) {
  if (String(title).startsWith("Review #")) {
    return detailDrawer({ title, kicker: "REVIEW DETAIL", subtitle: "事实、处理结果与版本历史", body });
  }
  const dialog = document.createElement("dialog");
  if (wide) dialog.className = "wide";
  dialog.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button data-close aria-label="关闭">关闭</button></div><div class="dialog-body">${body}</div>`;
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
  });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function detailDrawer({ title, kicker = "DETAIL", subtitle = "查看完整信息", body, footer = "" }) {
  const dialog = document.createElement("dialog");
  dialog.className = "detail-view-drawer";
  dialog.innerHTML = `<div class="detail-view-drawer-shell">
    <header><div><span class="eyebrow">${esc(kicker)}</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div><button type="button" class="drawer-close" data-close aria-label="关闭详情">×</button></header>
    <div class="detail-view-drawer-body">${body}</div>
    <footer><button type="button" class="quiet" data-close>关闭</button>${footer}</footer>
  </div>`;
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
  });
  document.body.append(dialog);
  dialog.showModal();
  bindPage(dialog);
  return dialog;
}

$("#app").innerHTML = shellMarkup();

const pageInfo = {
  economy: ["明细", "查看最终经济流水，并追溯对应的审查、分配关系与事实。"],
  "ledger-reviews": ["明细", "查看事实如何通过审查和 Allocation 形成经济流水。"],
  "ledger-imports": ["明细", "在统一列表中追溯导入文件、原始行和处理结果。"],
  "ledger-tags": ["明细", "在统一列表中查看分类维度、标签值和启用状态。"],
  summary: ["本月概览", "先回答这个月每天流入、流出了多少；具体流水继续回到“明细”查看。"],
  ledger: ["明细", "查看导入后不可变的事实流水；最终结果请切换到经济明细。"],
  "legacy-ledger": ["兼容流水", "用于账户修正及迁移期 v1 辅助审查；日常阅读请使用经济流水。"],
  import: ["导入账单", "选择来源、添加文件，并在写入账本前逐项核对。"],
  "import-history": ["导入记录", "查找已经写入的文件、处理结果和原始行。"],
  tags: ["分类标签", "用直观的分类维度整理流水，不暴露内部数据结构。"],
  reviews: ["工作台", "这里只放需要完成的任务；浏览和统计不会挤进这里。"],
};
const validPages = new Set(Object.keys(pageInfo));

function route(page, params = new URLSearchParams()) {
  location.hash = canonicalHash(page, params);
}
function readRoute() {
  const { page: nextPage, params } = parseHash(location.hash, validPages);
  const rawPage = location.hash.slice(1).split("?", 1)[0];
  if (!["details", "accounts", "workbench"].includes(rawPage)) {
    history.replaceState(null, "", `#${canonicalHash(nextPage, params)}`);
  }
  if (nextPage !== "import-history") {
    clearTimeout(state.historyFilterTimer);
    state.historyRequestController?.abort();
  }
  state.page = nextPage;
  state.params = params;
  render();
}
window.addEventListener("hashchange", readRoute);

async function render() {
  const renderVersion = ++state.renderVersion;
  const page = state.page;
  const root = $("#page-content");
  const [title, help] = pageInfo[page];
  $("#title").textContent = title;
  $("#help").textContent = help;
  syncNavigation(page);
  renderPageActions(page);
  root.innerHTML = '<div class="busy">正在加载…</div>';
  try {
    const content = await ({
      economy: economicPage,
      "legacy-ledger": legacyLedgerPage,
      "ledger-reviews": ledgerReviewsPage,
      "ledger-imports": ledgerImportsPage,
      "ledger-tags": ledgerTagsPage,
      summary: summaryPage,
      ledger: ledgerPage,
      import: importPage,
      "import-history": importHistoryPage,
      tags: tagsPage,
      reviews: reviewsPage,
    })[page]();
    if (renderVersion !== state.renderVersion || page !== state.page) return;
    root.innerHTML = content;
    bindPage(root);
    if (page === "import") renderImportPlan();
  } catch (error) {
    if (renderVersion !== state.renderVersion || page !== state.page) return;
    root.innerHTML = `<section class="panel"><div class="error">${esc(error.message)}</div><div class="actions"><button data-action="reload">重新加载</button></div></section>`;
    bindPage(root);
  }
}

function renderPageActions(page) {
  const target = $("#page-actions");
  if (pageModule(page) !== "workbench") {
    target.innerHTML = page === "ledger"
      ? '<button type="button" class="primary" data-action="new-economic-review">＋ 加入审查</button>'
      : '';
    bindPage(target);
    return;
  }
  if (page === "reviews" && !state.params.get("view")) {
    target.innerHTML = "";
    return;
  }
  const tasks = [
    ["reviews", "审查"], ["import", "导入"], ["import-history", "导入记录"], ["tags", "分类标签"],
  ];
  target.innerHTML = `<nav class="workbench-tabs" aria-label="工作台功能">${tasks.map(([id, label]) => `<button type="button" data-page="${id}" class="${page === id ? "active" : ""}">${label}</button>`).join("")}</nav>`;
  bindPage(target);
}

async function summaryPage() {
  state.accountMonth = cursorFromParam(state.params.get("month"), state.accountMonth);
  const range = monthBounds(state.accountMonth);
  const makePath = (page) => `/paam/ledger/v2/entry/list?${new URLSearchParams({ page, page_size: "100", date_from: range.from, date_to: range.to })}`;
  const [economicSummary, first] = await Promise.all([
    request(`/paam/ledger/v2/summary?${new URLSearchParams({ date_from: range.from, date_to: range.to })}`),
    request(makePath(1)),
  ]);
  const pages = Math.ceil(first.total / 100);
  const rest = pages > 1 ? await Promise.all(Array.from({ length: pages - 1 }, (_, index) => request(makePath(index + 2)))) : [];
  const flows = [first, ...rest].flatMap((result) => result.items);
  const trendMap = new Map();
  const activityMap = new Map();
  for (const flow of flows) {
    const day = date(flow.occurred_time).slice(0, 10);
    const scale = flow.amount.amount_scale;
    const currency = flow.amount.currency_code;
    const trendKey = `${day}:${currency}`;
    const trend = trendMap.get(trendKey) || { day, currency_code: currency, amount_scale: scale, income_value: 0, expense_value: 0, net_value: 0 };
    if (flow.entry_type === 0) {
      if (flow.entry_direction === 1) trend.income_value += flow.amount.amount_value;
      else trend.expense_value += flow.amount.amount_value;
      trend.net_value = trend.income_value - trend.expense_value;
    }
    trendMap.set(trendKey, trend);
    const typeCode = entryTypeCodes[flow.entry_type];
    const activityKey = `${typeCode}:${currency}`;
    const activity = activityMap.get(activityKey) || { ledger_type: typeCode, currency_code: currency, amount_scale: scale, in_amount_value: 0, out_amount_value: 0, nettable: true };
    if (flow.entry_direction === 1) activity.in_amount_value += flow.amount.amount_value;
    else activity.out_amount_value += flow.amount.amount_value;
    activityMap.set(activityKey, activity);
  }
  const summary = {
    entry_count: economicSummary.entry_count,
    provisional_count: 0,
    totals: economicSummary.totals.map((item) => ({
      ...item,
      income_value: item.transaction_in_value,
      expense_value: item.transaction_out_value,
      refund_offset_value: 0,
      net_value: item.transaction_in_value - item.transaction_out_value,
    })),
    trend: [...trendMap.values()],
    activities: [...activityMap.values()],
  };
  return accountsMarkup({
    summary,
    accounts: [],
    accountCode: "",
    currency: state.params.get("currency_code") || "CNY",
    cursor: state.accountMonth,
  });
}

function accountLedgerParams(extra = {}) {
  const range = monthBounds(state.accountMonth);
  const params = new URLSearchParams({
    date_from: extra.day || range.from,
    date_to: extra.day || range.to,
    currency_code: $('[data-form="account-filter"] select[name="currency_code"]')?.value
      || state.params.get("currency_code")
      || "CNY",
  });
  if (extra.ledgerType in entryTypeValues) params.set("entry_type", entryTypeValues[extra.ledgerType]);
  return params;
}

function ledgerSelector(entry) {
  return `<label class="ledger-selector" title="选择流水建立 Review"><input type="checkbox" data-action="ledger-select" data-id="${entry.id}" ${state.selectedLedgers.has(entry.id) ? "checked" : ""}><span aria-hidden="true"></span><span class="sr-only">选择流水 #${entry.id}</span></label>`;
}

function reviewSelectionBar() {
  const count = state.selectedLedgers.size;
  return `<div class="review-selection-bar ${count ? "active" : ""}" aria-live="polite"><span>${count ? `已选择 <strong>${count}</strong> 条流水` : "选择流水后可建立人工 Review"}</span><div><button type="button" class="quiet" data-action="clear-ledger-selection" ${count ? "" : "disabled"}>清除</button><button type="button" class="primary" data-action="review-selected" ${count ? "" : "disabled"}>建立 Review</button></div></div>`;
}

function detailTabs(active) {
  const items = [
    ["ledger", "事实明细", "导入后不可变的事实"],
    ["economy", "经济明细", "最终可阅读的结果"],
    ["ledger-reviews", "审查明细", "解释与分配历史"],
    ["ledger-imports", "导入记录", "文件与原始行"],
    ["ledger-tags", "分类标签", "维度与标签值"],
  ];
  return `<nav class="detail-tabs" aria-label="明细类型">${items.map(([page, label, note]) => `
    <button type="button" data-page="${page}" class="${page === active ? "active" : ""}" aria-pressed="${page === active}"><strong>${label}</strong><small>${note}</small></button>`).join("")}</nav>`;
}

function detailList({ active, toolbar = "", title, description, total, headers, rows, footer = "" }) {
  return `${detailTabs(active)}<article class="list-surface detail-list-surface">
    ${toolbar ? `<div class="detail-list-toolbar">${toolbar}</div>` : ""}
    <div class="list-context"><div><strong>${esc(title)}</strong><span>${esc(description)}</span></div><small>共 ${total} 条</small></div>
    <div class="table-scroll"><table class="reusable-table detail-data-table"><thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}" class="empty-row">暂无符合条件的数据</td></tr>`}</tbody></table></div>
    ${footer ? `<footer class="list-footer">${footer}</footer>` : ""}
  </article>`;
}

function detailPager(result, pageId) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.total, result.page * result.page_size);
  return `<div class="pagination ledger-pagination"><span class="range">${start}–${end} / ${result.total}</span><div class="page-buttons"><button data-action="detail-page" data-page-id="${pageId}" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${result.page} / ${pages} 页</span><button data-action="detail-page" data-page-id="${pageId}" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
}

async function ledgerPage() {
  const facts = await request("/paam/review/v2/fact/candidates?limit=500");
  const q = (state.params.get("q") || "").trim().toLowerCase();
  const currency = (state.params.get("currency_code") || "").trim().toUpperCase();
  const dateFrom = state.params.get("date_from") || "";
  const dateTo = state.params.get("date_to") || "";
  const filtered = facts.filter((fact) => {
    const day = date(fact.occurred_time).slice(0, 10);
    const haystack = `${fact.id} ${fact.counterparty} ${fact.summary} ${fact.account_code}`.toLowerCase();
    return (!q || haystack.includes(q)) && (!currency || fact.currency_code === currency)
      && (!dateFrom || day >= dateFrom) && (!dateTo || day <= dateTo);
  });
  const page = Math.max(1, Number(state.params.get("page") || 1));
  const pageSize = 25;
  const items = filtered.slice((page - 1) * pageSize, page * pageSize);
  state.detailFacts = new Map(items.map((item) => [item.id, item]));
  const rows = items.map((fact) => `<tr class="detail-click-row" tabindex="0" data-fact-row="${fact.id}">
    <td>${date(fact.occurred_time)}</td><td><button type="button" class="detail-primary" data-action="fact-detail" data-id="${fact.id}"><strong>${esc(fact.summary || fact.counterparty || `事实 #${fact.id}`)}</strong><small>#${fact.id} · ${esc(fact.counterparty || "未知交易方")}</small></button></td>
    <td><span class="badge neutral">${fact.cash_direction === "IN" ? "流入" : "流出"}</span></td><td class="money ${fact.cash_direction === "IN" ? "income" : "expense"}">${signedMoney(fact, fact.cash_direction)}</td><td>${esc(fact.currency_code)}</td><td class="mono">${esc(fact.account_code)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="fact-filter"><label class="grow">搜索<input name="q" value="${esc(state.params.get("q") || "")}" placeholder="事实、交易方、摘要或账户"></label><label>币种<input name="currency_code" maxlength="12" value="${esc(currency)}" placeholder="全部币种"></label><label>开始日期<input type="date" name="date_from" value="${esc(dateFrom)}"></label><label>结束日期<input type="date" name="date_to" value="${esc(dateTo)}"></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger">清空</button><button class="primary">筛选</button></form>`;
  const result = { total: filtered.length, page, page_size: pageSize };
  return detailList({ active: "ledger", toolbar, title: "事实流水", description: "外部账单接受后的不可变事实，只用于追溯与审查。", total: filtered.length, headers: ["发生时间", "事实", "方向", "金额", "币种", "来源账户", ""], rows, footer: detailPager(result, "ledger") });
}

function showFactDetail(id) {
  const fact = state.detailFacts.get(Number(id));
  if (!fact) return;
  detailDrawer({
    title: fact.summary || fact.counterparty || `事实 #${fact.id}`,
    kicker: `FACT #${fact.id}`,
    subtitle: "事实层记录导入来源，不直接代表最终经济分类。",
    body: `<section class="drawer-record-card"><div><span>${fact.cash_direction === "IN" ? "收入事实" : "支出事实"}</span><h3>${esc(fact.counterparty || "未知交易方")}</h3><small>${date(fact.occurred_time)} · ${esc(fact.account_code)}</small></div><strong class="ledger-fact-money ${fact.cash_direction === "IN" ? "plus" : "minus"}">${signedMoney(fact, fact.cash_direction)}</strong></section><section class="drawer-section"><h3>规范事实</h3><dl class="ledger-review-basis"><div><dt>摘要</dt><dd>${esc(fact.summary || "—")}</dd></div><div><dt>币种</dt><dd>${esc(fact.currency_code)}</dd></div><div><dt>仍由默认交易覆盖</dt><dd>${money({ amount_value: fact.available_value, amount_scale: fact.amount_scale, currency_code: fact.currency_code })}</dd></div></dl></section>`,
    footer: '<button type="button" class="primary" data-action="new-economic-review">加入经济审查</button>',
  });
}

async function economicPage() {
  const query = new URLSearchParams();
  for (const name of ["page", "page_size", "q", "currency_code", "date_from", "date_to", "entry_type"]) {
    for (const value of state.params.getAll(name)) if (value) query.append(name, value);
  }
  if (!query.has("page")) query.set("page", "1");
  if (!query.has("page_size")) query.set("page_size", "25");
  const result = await request(`/paam/ledger/v2/entry/list?${query}`);
  state.detailEconomics = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-economic-row="${item.id}">
    <td>${date(item.occurred_time)}</td><td><button type="button" class="detail-primary" data-action="economic-detail" data-id="${item.id}"><strong>账本流水 #${item.id}</strong><small>${esc(item.account_code)}</small></button></td><td>${esc(typeNames[entryTypeCodes[item.entry_type]] || entryTypeCodes[item.entry_type])}</td><td>${item.entry_direction === 1 ? "流入" : "流出"}</td><td class="money ${item.entry_direction === 1 ? "income" : "expense"}">${signedMoney(item.amount, entryDirection(item.entry_direction))}</td><td>${tags(item)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const selectedType = state.params.get("entry_type") || "";
  const toolbar = `<form class="detail-filter" data-form="economic-filter"><label class="grow">搜索<input name="q" value="${esc(state.params.get("q") || "")}" placeholder="审查说明、交易方或摘要"></label><label>账本类型<select name="entry_type"><option value="">全部类型</option>${Object.entries(entryTypeCodes).map(([value, code]) => `<option value="${value}" ${selectedType === value ? "selected" : ""}>${esc(typeNames[code] || code)}</option>`).join("")}</select></label><label>币种<input name="currency_code" maxlength="12" value="${esc(state.params.get("currency_code") || "")}" placeholder="全部币种"></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="economy">清空</button><button class="primary">筛选</button></form>`;
  return detailList({ active: "economy", toolbar, title: "经济流水", description: "已生效的最终经济结果；每笔都可追溯到审查、Allocation 与事实。", total: result.total, headers: ["发生时间", "经济结果", "类型", "方向", "金额", "标签", ""], rows, footer: detailPager(result, "economy") });
}

async function showEconomicDetail(id) {
  const detail = await request(`/paam/ledger/v2/entry/detail/${id}`);
  const flow = detail.entry;
  const allocations = detail.allocations.map((item) => `<div class="drawer-review-row"><span><strong>Allocation #${item.id}</strong><small>Fact #${item.transaction_fact_id} → Ledger #${item.ledger_entry_id}</small></span><strong>${money(item.amount)}</strong></div>`).join("");
  const facts = detail.facts.map((item) => `<div class="drawer-review-row"><span><strong>Fact #${item.id} · ${esc(item.summary || item.counterparty)}</strong><small>${date(item.occurred_time)} · ${esc(item.account_code)}</small></span><strong>${money(item.amount)}</strong></div>`).join("");
  const reviews = detail.reviews.map((item) => `<button type="button" class="drawer-review-row" data-action="economic-review-detail" data-id="${item.id}"><span><strong>Review #${item.id} · ${esc(item.behavior_code)}</strong><small>${esc(item.description)} · ${esc(statusNames[item.status] || item.status)}</small></span><span>查看审查 →</span></button>`).join("");
  const typeCode = entryTypeCodes[flow.entry_type];
  detailDrawer({ title: `账本流水 #${flow.id}`, kicker: `${esc(typeNames[typeCode] || typeCode)} · LEDGER #${flow.id}`, subtitle: `${date(flow.occurred_time)} · ${flow.entry_direction === 1 ? "流入" : "流出"}`, body: `<section class="drawer-record-card"><div><span>已确认账本投影</span><h3>${esc(typeNames[typeCode] || typeCode)}</h3><small>${esc(flow.account_code)} · 单方向、单币种</small></div><strong class="ledger-fact-money ${flow.entry_direction === 1 ? "plus" : "minus"}">${signedMoney(flow.amount, entryDirection(flow.entry_direction))}</strong></section><section class="drawer-section"><h3>来源事实</h3>${facts || '<p class="muted">没有关联事实</p>'}</section><section class="drawer-section"><h3>审查与分配</h3>${reviews}${allocations}</section>`, footer: `<button type="button" class="primary" data-action="edit-tags" data-id="${flow.id}">编辑标签</button>` });
}

async function legacyLedgerPage() {
  const query = new URLSearchParams(state.params);
  query.delete("task");
  query.delete("detail");
  if (!query.has("page")) query.set("page", "1");
  if (!query.has("page_size")) query.set("page_size", "25");
  const [result, views] = await Promise.all([
    request(`/paam/ledger/v1/entry/list?${query}`),
    request("/paam/tag/v1/view/list"),
  ]);
  const selectedTags = new Map(state.params.getAll("tag").map((selector) => selector.split(":", 2)));
  const tagFilters = views.map((view) => `<label class="ledger-tag-view"><span>${esc(view.name)}</span><select name="tag" aria-label="${esc(view.name)}"><option value="">全部</option>${view.tags.map((tag) => {
    const value = `${view.system_name}:${tag.system_name}`;
    return `<option value="${esc(value)}" ${selectedTags.get(view.system_name) === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`;
  }).join("")}</select></label>`).join("");
  const rowGroups = result.items.map((entry) => `<tbody class="ledger-table-item ${state.selectedLedgers.has(entry.id) ? "selected" : ""}" data-ledger-card="${entry.id}">
    <tr class="ledger-table-row" data-ledger-row="${entry.id}" tabindex="0">
      <td class="ledger-table-select">${ledgerSelector(entry)}</td>
      <td class="ledger-table-time"><strong>${date(entry.start_time).slice(0, 10)}</strong><small>${date(entry.start_time).slice(11) || "00:00"} · #${entry.id}</small></td>
      <td><button type="button" class="ledger-table-primary" data-action="detail" data-id="${entry.id}"><strong>${esc(entry.title || "未命名流水")}</strong><small>${esc(typeNames[entry.ledger_type] || entry.ledger_type)} · ${esc(statusNames[entry.allocation_status] || entry.allocation_status)} · 投影 v${entry.projection_version}</small></button></td>
      <td class="ledger-table-account">${ledgerAccountPath(entry)}</td>
      <td class="ledger-table-tags">${tags(entry)}</td>
      <td class="ledger-table-money"><div class="ledger-card-amounts">${ledgerAmounts(entry)}</div></td>
      <td class="ledger-table-chevron"><button type="button" class="ledger-row-open" data-action="detail" data-id="${entry.id}" aria-label="打开流水详情">→</button></td>
    </tr>
  </tbody>`).join("");
  const accountCode = state.params.get("account_code") || "";
  return `${detailTabs("ledger")}<article class="list-surface ledger-list-surface"><section class="ledger-filter-panel"><form data-form="ledger-filter">
    ${accountCode ? `<input type="hidden" name="account_code" value="${esc(accountCode)}"><div class="applied-context"><span>当前账户：<strong>${esc(accountCode)}</strong></span><button type="button" class="quiet" data-action="clear-account-filter">取消账户筛选</button></div>` : ""}
    <div class="ledger-filter-main">
      <label class="ledger-type-filter"><span>业务类型</span><select name="ledger_type"><option value="">全部业务类型</option>${Object.entries(typeNames).map(([value, label]) => `<option value="${value}" ${state.params.get("ledger_type") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="ledger-search-filter"><span>补充搜索</span><input name="q" value="${esc(state.params.get("q") || "")}" placeholder="标题、交易方或摘要"></label>
      <div class="ledger-date-field"><span>发生时间</span>${ledgerDatePicker()}</div>
      <div class="ledger-filter-actions"><button type="button" class="quiet" data-action="clear-ledger">重置</button><button class="primary">应用筛选</button></div>
    </div>
    <div class="ledger-tag-filter"><div class="ledger-tag-filter-copy"><strong>标签筛选</strong><small>每个视图可选一个标签</small></div><div class="ledger-tag-views">${tagFilters || '<span class="muted">还没有有效标签视图</span>'}</div></div>
  </form></section>
  ${reviewSelectionBar()}
  <div class="list-context"><div><strong>实际流水</strong><span>收入、支出与特殊业务均沿用当前 v1 口径。</span></div><small>共 ${result.total} 条</small></div>
  <div class="table-scroll"><table class="reusable-table ledger-table"><thead><tr><th aria-label="选择"></th><th>发生时间</th><th>流水</th><th>账户</th><th>标签</th><th class="number">金额</th><th aria-label="展开"></th></tr></thead>
  ${rowGroups || '<tbody><tr><td colspan="7" class="empty-row">没有符合条件的实际流水</td></tr></tbody>'}</table></div>
  <footer class="list-footer">${pager(result)}</footer></article>`;
}

async function ledgerSummaryPage() {
  const currentMonth = monthBounds(new Date(now.getFullYear(), now.getMonth(), 1));
  const dateFrom = state.params.get("date_from") || currentMonth.from;
  const dateTo = state.params.get("date_to") || currentMonth.to;
  const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  const accountCode = state.params.get("account_code") || "";
  if (accountCode) query.set("account_code", accountCode);
  const [summary, accounts] = await Promise.all([
    request(`/paam/ledger/v1/summary?${query}`),
    request("/paam/import/v1/account/list"),
  ]);
  state.detailSummaries = new Map(summary.activities.map((item) => [item.ledger_type, item]));
  const accountOptions = accounts.map((account) => {
    const identity = account.identity || account.account_code;
    return `<option value="${esc(identity)}" ${identity === accountCode ? "selected" : ""}>${esc(account.display_name || identity)}</option>`;
  }).join("");
  const rows = summary.activities.map((item) => `<tr class="detail-click-row" tabindex="0" data-summary-row="${esc(item.ledger_type)}">
    <td><button type="button" class="detail-primary" data-action="summary-detail" data-type="${esc(item.ledger_type)}"><strong>${esc(typeNames[item.ledger_type] || item.ledger_type)}</strong><small>${esc(item.ledger_type)}</small></button></td>
    <td>${item.nettable ? "可核算" : "资金活动"}</td>
    <td class="money income">${money({ amount_value: item.in_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code })}</td>
    <td class="money expense">${money({ amount_value: item.out_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code })}</td>
    <td>${esc(item.currency_code)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="detail-summary-filter">
    <label class="grow">发生时间<span><input type="date" name="date_from" value="${esc(dateFrom)}"><i>至</i><input type="date" name="date_to" value="${esc(dateTo)}"></span></label>
    <label>账户<select name="account_code"><option value="">全部账户</option>${accountOptions}</select></label>
    <button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-summary">清空</button><button class="primary">筛选</button>
  </form>`;
  return detailList({ active: "ledger-summary", toolbar, title: "收支汇总", description: "沿用 v1 业务类型展示流入、流出与核算口径。", total: summary.activities.length, headers: ["业务类型", "口径", "流入", "流出", "币种", ""], rows });
}

function showSummaryDetail(type) {
  const item = state.detailSummaries.get(type);
  if (!item) return;
  const incoming = money({ amount_value: item.in_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code });
  const outgoing = money({ amount_value: item.out_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code });
  detailDrawer({
    title: typeNames[item.ledger_type] || item.ledger_type,
    kicker: `V1 BUSINESS TYPE · ${item.ledger_type}`,
    subtitle: "查看当前业务类型的收支构成与核算口径。",
    body: `<div class="cards drawer-metrics"><div class="metric"><span>流入</span><strong>${incoming}</strong></div><div class="metric"><span>流出</span><strong>${outgoing}</strong></div></div><section class="drawer-section"><h3>核算说明</h3><p>${item.nettable ? "该业务允许在同一币种内按流入与流出核算。" : "该业务保留资金活动原貌，不进行跨业务净额化。"}</p></section>`,
    footer: `<button type="button" class="primary" data-action="summary-drilldown" data-type="${esc(item.ledger_type)}">查看对应流水</button>`,
  });
}

async function ledgerReviewsPage() {
  const query = new URLSearchParams({ page: state.params.get("page") || "1", page_size: "20" });
  if (state.params.get("status")) query.set("status", state.params.get("status"));
  const result = await request(`/paam/review/v2/case/page?${query}`);
  state.detailEconomicReviews = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-review-row="${item.id}">
    <td>${date(item.updated_time)}</td><td><button type="button" class="detail-primary" data-action="economic-review-detail" data-id="${item.id}"><strong>${esc(item.description || item.behavior_code || "未填写说明")}</strong><small>#${item.id} · ${esc(item.behavior_code || "未说明行为")}</small></button></td>
    <td><span class="badge ${item.status === "PENDING" ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td>${item.ledger_entry_count} 条账本流水</td><td>${item.allocation_count} 条分配</td><td>v${item.version}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="detail-review-filter"><label>状态<select name="status"><option value="">全部状态</option>${["PENDING", "CONFIRMED", "REVOKED"].map((value) => `<option value="${value}" ${state.params.get("status") === value ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-reviews">清空</button><button class="primary">筛选</button><button type="button" class="primary" data-action="new-economic-review">新建经济审查</button></form>`;
  return detailList({ active: "ledger-reviews", toolbar, title: "审查记录", description: "审查连接事实与经济结果；Allocation 保存明确的分配金额。", total: result.total, headers: ["更新时间", "审查", "状态", "经济结果", "分配关系", "版本", ""], rows, footer: detailPager(result, "ledger-reviews") });
}

async function showEconomicReview(id) {
  const item = await request(`/paam/review/v2/case/detail/${id}`);
  const economics = item.ledger_entries.map((flow) => {
    const typeCode = entryTypeCodes[flow.entry_type];
    return `<div class="drawer-review-row"><span><strong>Ledger #${flow.id} · ${esc(typeNames[typeCode] || typeCode)}</strong><small>${flow.entry_direction === 1 ? "流入" : "流出"} · ${esc(flow.currency_code)}</small></span><strong>${money({ amount_value: flow.amount_value, amount_scale: flow.amount_scale, currency_code: flow.currency_code })}</strong></div>`;
  }).join("");
  const allocations = item.allocations.map((row) => `<div class="drawer-review-row"><span><strong>Fact #${row.transaction_fact_id} → Ledger #${row.ledger_entry_id || "待确认"}</strong></span><strong>${money({ amount_value: row.amount_value, amount_scale: row.amount_scale, currency_code: row.currency_code })}</strong></div>`).join("");
  let action = "";
  if (item.status === "PENDING") action = `<button type="button" class="primary" data-action="economic-review-transition" data-kind="confirm" data-id="${item.id}" data-version="${item.version}">确认审查</button>`;
  if (item.status === "CONFIRMED") action = `<button type="button" data-action="economic-review-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销并恢复默认交易</button>`;
  if (item.status === "REVOKED") action = `<button type="button" class="primary" data-action="economic-review-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复审查</button>`;
  detailDrawer({ title: item.description || `审查 #${item.id}`, kicker: `REVIEW #${item.id} · ${item.behavior_code}`, subtitle: `${esc(statusNames[item.status] || item.status)} · 版本 ${item.version}`, body: `<section class="drawer-section"><h3>账本流水</h3>${economics || '<p class="muted">待确认，尚未生成账本流水</p>'}</section><section class="drawer-section"><h3>事实—账本分配</h3>${allocations || '<p class="muted">没有分配关系</p>'}</section>`, footer: action });
}

async function transitionEconomicReview(button) {
  const labels = { confirm: "确认", revoke: "撤销", restore: "恢复" };
  if (!confirm(`${labels[button.dataset.kind]}这次经济审查？`)) return;
  await jsonRequest(`/paam/review/v2/case/${button.dataset.kind}/${button.dataset.id}`, "POST", { expected_version: Number(button.dataset.version), reason: `人工${labels[button.dataset.kind]}经济审查`, idempotency_key: key() });
  closeDialogs();
  toast(`${labels[button.dataset.kind]}完成，经济流水已重新投影`);
  await render();
}

async function openEconomicReviewEditor() {
  const facts = await request("/paam/review/v2/fact/candidates?limit=100");
  if (!facts.length) throw new Error("当前没有可分配的事实流水");
  const selected = new Set();
  const values = new Map();
  let sequence = 1;
  const economics = [{ key: `economic-${sequence}`, type: "TRANSACTION" }];
  const dialog = modal("新建经济审查", `<form data-form="economic-review-create" class="review-wizard stack"><section><h3>1. 选择事实流水</h3><div data-economic-review-facts class="review-fact-choices"></div></section><section><div class="section-head"><h3>2. 定义账本流水</h3><button type="button" data-action="add-economic">＋ 添加账本流水</button></div><div data-economic-definitions class="stack"></div></section><section><h3>3. 分配金额</h3><p class="muted">每条账本流水只能分配一条事实；一条事实可以拆成多条账本流水。</p><div data-allocation-matrix></div></section><label>行为代码<input name="behavior_code" maxlength="40" placeholder="例如 ADVANCE、LOAN、FX_EXCHANGE" required></label><label>行为解释<textarea name="description" maxlength="2000"></textarea></label><label>操作原因<input name="reason" maxlength="2000"></label><div class="actions"><button type="button" data-close>取消</button><button class="primary">保存为待确认审查</button></div></form>`);
  const form = $('[data-form="economic-review-create"]', dialog);
  const factRoot = $("[data-economic-review-facts]", form);
  const definitionRoot = $("[data-economic-definitions]", form);
  const matrixRoot = $("[data-allocation-matrix]", form);
  factRoot.innerHTML = facts.map((fact) => `<label class="review-fact-choice"><input type="checkbox" data-review-fact="${fact.id}"><span><strong>#${fact.id} · ${esc(fact.counterparty || fact.summary || "未命名事实")}</strong><small>${date(fact.occurred_time)} · ${fact.cash_direction} · 可分配 ${money({ amount_value: fact.available_value, amount_scale: fact.amount_scale, currency_code: fact.currency_code })}</small></span></label>`).join("");
  const preserveMatrix = () => {
    $$('[data-allocation-value]', matrixRoot).forEach((input) => values.set(`${input.dataset.fact}:${input.dataset.economic}`, input.value));
  };
  const renderDefinitions = () => {
    definitionRoot.innerHTML = economics.map((item) => `<article class="review-fact-choice" data-economic-definition="${item.key}"><div><strong>${esc(item.key)}</strong><small>方向、币种、账户和发生时间由唯一关联的事实推导</small></div><label>类型<select name="type-${item.key}">${["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"].map((value) => `<option value="${value}" ${item.type === value ? "selected" : ""}>${typeNames[value]}</option>`).join("")}</select></label>${economics.length > 1 ? `<button type="button" class="quiet" data-action="remove-economic" data-key="${item.key}">移除</button>` : ""}</article>`).join("");
    $$('[name^="type-"]', definitionRoot).forEach((input) => input.onchange = () => {
      economics.find((item) => `type-${item.key}` === input.name).type = input.value;
    });
    $$('[data-action="remove-economic"]', definitionRoot).forEach((button) => button.onclick = () => {
      preserveMatrix();
      economics.splice(economics.findIndex((item) => item.key === button.dataset.key), 1);
      renderDefinitions();
      renderMatrix();
    });
  };
  const renderMatrix = () => {
    preserveMatrix();
    const selectedFacts = facts.filter((fact) => selected.has(fact.id));
    if (!selectedFacts.length) {
      matrixRoot.innerHTML = '<div class="empty-state">先选择至少一条事实流水</div>';
      return;
    }
    const rows = selectedFacts.map((fact) => `<tr><td>#${fact.id}<br><small>${fact.cash_direction} · ${esc(fact.currency_code)}</small></td>${economics.map((item) => `<td><input data-allocation-value data-fact="${fact.id}" data-economic="${item.key}" inputmode="decimal" value="${esc(values.get(`${fact.id}:${item.key}`) || "")}" placeholder="0"></td>`).join("")}<td>${money({ amount_value: fact.available_value, amount_scale: fact.amount_scale, currency_code: fact.currency_code })}</td></tr>`);
    matrixRoot.innerHTML = table(["事实", ...economics.map((item) => esc(item.key)), "可分配"], rows);
  };
  $$('[data-review-fact]', factRoot).forEach((input) => input.onchange = () => {
    const id = Number(input.dataset.reviewFact);
    if (input.checked) selected.add(id); else selected.delete(id);
    renderMatrix();
  });
  $('[data-action="add-economic"]', form).onclick = () => {
    preserveMatrix();
    sequence += 1;
    economics.push({ key: `economic-${sequence}`, type: "TRANSACTION" });
    renderDefinitions();
    renderMatrix();
  };
  renderDefinitions();
  renderMatrix();
  form.onsubmit = async (event) => {
    event.preventDefault();
    const idempotencyKey = beginSubmit(form);
    if (!idempotencyKey) return;
    try {
      const data = new FormData(form);
      const allocations = [];
      $$('[data-allocation-value]', matrixRoot).forEach((input) => {
        const fact = facts.find((item) => item.id === Number(input.dataset.fact));
        const amount = decimalAmount(input.value, fact.amount_scale);
        if (amount > 0) allocations.push({ transaction_fact_id: fact.id, entry_key: input.dataset.economic, amount_value: amount });
      });
      if (!allocations.length) throw new Error("至少填写一条大于 0 的分配关系");
      const allocationCounts = allocations.reduce((counts, item) => counts.set(item.entry_key, (counts.get(item.entry_key) || 0) + 1), new Map());
      if ([...allocationCounts.values()].some((count) => count !== 1)) throw new Error("每条账本流水必须且只能关联一条事实");
      const used = new Set(allocations.map((item) => item.entry_key));
      const definitions = economics.filter((item) => used.has(item.key)).map((item) => ({
        client_key: item.key,
        entry_type: entryTypeValues[data.get(`type-${item.key}`)],
      }));
      const created = await jsonRequest("/paam/review/v2/case/create", "POST", {
        behavior_code: data.get("behavior_code"),
        description: data.get("description") || "",
        result: {}, entries: definitions, allocations,
        reason: data.get("reason") || "",
        idempotency_key: idempotencyKey,
      });
      closeDialogs();
      toast("待确认经济审查已创建");
      await render();
      await showEconomicReview(created.id);
    } catch (error) {
      endSubmit(form);
      showFormError(form, error);
    }
  };
}

async function ledgerImportsPage() {
  const query = new URLSearchParams({ page: state.params.get("page") || "1", page_size: "20" });
  if (state.params.get("q")) query.set("q", state.params.get("q"));
  if (state.params.get("account")) query.set("account", state.params.get("account"));
  const [result, accounts] = await Promise.all([request(`/paam/import/v1/batch/list?${query}`), request("/paam/import/v1/account/list")]);
  state.historyAccountNames = new Map(accounts.map((account) => [account.identity, account.display_name || account.identity]));
  const rows = result.items.map((item) => {
    const accountLabels = (item.account_codes || []).map((identity) => state.historyAccountNames.get(identity) || identity).join("、") || "未识别";
    return `<tr class="detail-click-row" tabindex="0" data-import-row="${item.id}"><td>${date(item.imported_at)}</td><td><button type="button" class="detail-primary" data-action="batch-rows" data-id="${item.id}" data-filename="${esc(item.filename)}"><strong>${esc(item.filename)}</strong><small>批次 #${item.id} · ${esc(sourceLabels[item.source_type] || item.source_type)}</small></button></td><td>${esc(accountLabels)}</td><td>${item.imported_count} / ${item.row_count}</td><td><span class="badge ${item.status === "IMPORTED" ? "neutral" : "warn"}">${esc(statusLabels[item.status] || item.status)}</span></td><td class="detail-arrow">→</td></tr>`;
  }).join("");
  const accountOptions = accounts.map((account) => `<option value="${esc(account.identity)}" ${state.params.get("account") === account.identity ? "selected" : ""}>${esc(account.display_name || account.identity)}</option>`).join("");
  const toolbar = `<form class="detail-filter" data-form="detail-import-filter"><label class="grow">搜索<input name="q" value="${esc(state.params.get("q") || "")}" placeholder="文件名、来源或批次编号"></label><label>来源账户<select name="account"><option value="">全部账户</option>${accountOptions}</select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-imports">清空</button><button class="primary">筛选</button></form>`;
  return detailList({ active: "ledger-imports", toolbar, title: "导入记录", description: "文件、来源账户与原始行处理结果。", total: result.total, headers: ["导入时间", "文件", "来源账户", "成功 / 总数", "状态", ""], rows, footer: detailPager(result, "ledger-imports") });
}

async function ledgerTagsPage() {
  const views = await request("/paam/tag/v1/view/list?include_archived=true");
  state.detailTagViews = new Map(views.map((view) => [view.id, view]));
  const rows = views.map((view) => `<tr class="detail-click-row" tabindex="0" data-tag-view-row="${view.id}"><td><button type="button" class="detail-primary" data-action="tag-view-detail" data-id="${view.id}"><strong>${esc(view.name)}</strong><small>${esc(view.system_name)}</small></button></td><td>${view.tags.length}</td><td><div class="tag-list">${view.tags.slice(0, 5).map((tag) => `<span class="tag">${esc(tag.name)}</span>`).join("")}${view.tags.length > 5 ? `<span class="muted">+${view.tags.length - 5}</span>` : ""}</div></td><td><span class="badge neutral">${esc(statusNames[view.status] || view.status)}</span></td><td class="detail-arrow">→</td></tr>`).join("");
  return detailList({ active: "ledger-tags", title: "分类标签", description: "查看维度、标签值和启用状态；维护操作仍使用 v1 接口。", total: views.length, headers: ["维度", "标签数", "标签值", "状态", ""], rows });
}

function showTagViewDetail(id) {
  const view = state.detailTagViews.get(Number(id));
  if (!view) return;
  const rows = view.tags.map((tag) => `<div class="drawer-tag-row"><span><strong>${esc(tag.name)}</strong><small>${esc(tag.system_name)}</small></span><span class="badge neutral">${esc(statusNames[tag.status] || tag.status)}</span></div>`).join("");
  detailDrawer({ title: view.name, kicker: `TAG VIEW · ${view.system_name}`, subtitle: `${view.tags.length} 个标签值 · ${statusNames[view.status] || view.status}`, body: `<section class="drawer-section"><h3>标签值</h3><div class="drawer-tag-list">${rows || '<p class="muted">暂无标签值</p>'}</div></section>`, footer: '<button type="button" class="primary" data-page="tags">管理分类标签</button>' });
}

function ledgerDetailMarkup(detail) {
  const entry = detail.entry;
  const effectiveAccounts = new Map();
  detail.reviews.filter((item) => item.review_type === "ACCOUNT" && item.status === "CONFIRMED").forEach((item) => {
    item.lines.forEach((line) => effectiveAccounts.set(line.bill_id, item.result.account_name));
  });
  const facts = detail.facts.map((fact) => {
    const effective = effectiveAccounts.get(fact.id) || fact.account_code;
    const direction = fact.cash_direction === "IN" ? "IN" : "OUT";
    return `<div class="ledger-fact-row"><span class="direction">${direction === "IN" ? "收入事实" : "支出事实"}</span><span><strong>${esc(fact.counterparty || "未知交易方")}</strong><small>${date(fact.occurred_time)} · ${esc(effective)}</small></span><span class="ledger-fact-money ${direction === "IN" ? "plus" : "minus"}">${signedMoney(fact.amount, direction)}</span><button type="button" class="quiet" data-action="account" data-fact="${fact.id}" data-ledger="${entry.id}" data-version="${entry.projection_version}" data-account="${esc(effective)}">修正账户</button></div>`;
  }).join("");
  const tagRows = entry.tags.map((tag) => `<div class="ledger-detail-tag"><span>${esc(tag.view_name)}</span><strong>${esc(tag.tag_name)}</strong></div>`).join("");
  const financialReview = detail.reviews.find((item) => item.is_projection_source && !["TAG", "ACCOUNT"].includes(item.review_type));
  const reviewBasis = financialReview
    ? `${typeNames[financialReview.review_type] || financialReview.review_type} #${financialReview.id} · ${statusNames[financialReview.status] || financialReview.status}`
    : "默认事实投影";
  const rawEvidence = detail.raw_evidence.map((raw) => `<details class="ledger-raw"><summary>Raw #${raw.id} · 原始行 ${raw.source_row_number}</summary><pre class="raw-json">${esc(JSON.stringify(raw.raw_payload, null, 2))}</pre></details>`).join("");
  const reviewEvidence = detail.reviews.map((review) => `<button type="button" class="drawer-review-row" data-action="review-detail" data-id="${review.id}"><span><strong>${esc(review.title || reviewTypeNames[review.review_type] || review.review_type)}</strong><small>#${review.id} · ${esc(statusNames[review.status] || review.status)} · v${review.version}</small></span><span>查看历史 →</span></button>`).join("");
  return `<div class="ledger-detail-grid">
    <div class="ledger-detail-main"><h3>构成事实</h3><div class="ledger-facts">${facts || '<span class="muted">没有关联事实</span>'}</div><p class="ledger-projection-note">收入与支出由事实方向及已确认 Review 聚合得出；“${esc(typeNames[entry.ledger_type] || entry.ledger_type)}”是业务类型，不替代收支结果。</p>${reviewEvidence ? `<section class="ledger-related-reviews"><h3>相关审查</h3>${reviewEvidence}</section>` : ""}${rawEvidence ? `<div class="ledger-raw-list">${rawEvidence}</div>` : ""}</div>
    <aside class="ledger-detail-side"><h3>按视图分配的标签</h3><div class="ledger-detail-tags">${tagRows || '<span class="muted">暂无标签</span>'}</div><dl class="ledger-review-basis"><div><dt>归集依据</dt><dd>${esc(reviewBasis)}</dd></div><div><dt>分配状态</dt><dd>${esc(statusNames[entry.allocation_status] || entry.allocation_status)}</dd></div><div><dt>发生区间</dt><dd>${date(entry.start_time)}<br>${date(entry.end_time)}</dd></div></dl><div class="actions"><button type="button" data-action="edit-tags" data-id="${entry.id}" data-version="${entry.projection_version}">编辑标签</button></div></aside>
  </div>`;
}

async function showDetail(id) {
  const renderVersion = state.renderVersion;
  const detail = await request(`/paam/ledger/v1/entry/detail/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const entry = detail.entry;
  detailDrawer({
    title: entry.title || `流水 #${entry.id}`,
    kicker: `${typeNames[entry.ledger_type] || entry.ledger_type} · 流水 #${entry.id}`,
    subtitle: `${date(entry.start_time)} · 投影版本 ${entry.projection_version}`,
    body: `<section class="drawer-record-card"><div><span>实际流水 · ${esc(typeNames[entry.ledger_type] || entry.ledger_type)}</span><h3>${esc(entry.title || `流水 #${entry.id}`)}</h3><small>${date(entry.start_time)} · ${esc(ledgerAccountPath(entry).replace(/<[^>]+>/g, ""))}</small></div><div class="ledger-card-amounts">${ledgerAmounts(entry)}</div></section>${ledgerDetailMarkup(detail)}`,
  });
}

async function editTags(ledgerId) {
  const renderVersion = state.renderVersion;
  const [views, detail] = await Promise.all([
    request("/paam/tag/v1/view/list"),
    request(`/paam/ledger/v2/entry/detail/${ledgerId}`),
  ]);
  if (renderVersion !== state.renderVersion) return;
  if (!views.length) return toast("请先创建标签维度", true);
  const current = Object.fromEntries(detail.entry.tags.map((item) => [item.view_system_name, item.tag_system_name]));
  const dialog = modal("编辑最终流水标签", `<form data-form="tag-assignment" data-ledger="${ledgerId}" data-version="${detail.tag_review_version}" class="stack">${views.map((view) => `<label>${esc(view.name)}<select name="${esc(view.system_name)}">${view.tags.map((tag) => `<option value="${esc(tag.system_name)}" ${(current[view.system_name] || "unclassified") === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`).join("")}</select></label>`).join("")}<label>修改原因<input name="reason" value="用户修订标签"></label><div class="actions"><button class="primary">保存标签</button></div></form>`);
  bindPage(dialog);
}

async function editAccount(button) {
  const dialog = modal("修正事实账户", `<form data-form="account" data-fact="${button.dataset.fact}" data-ledger="${button.dataset.ledger}" data-version="${button.dataset.version}" class="stack"><label>账户代码<input name="account_code" value="${esc(button.dataset.account)}" required maxlength="120"></label><label>修正原因<input name="reason" value="人工核对原始证据"></label><div class="actions"><button class="primary">保存账户修正</button></div></form>`, false);
  bindPage(dialog);
}

function importPage() {
  const sources = [
    ["", "自动识别", "推荐", "✦", "auto"],
    ["alipay", "支付宝", "支付宝账单", "支", "blue"],
    ["wechat", "微信支付", "微信账单", "微", "green"],
    ["ccb", "建设银行", "银行卡流水", "建", "indigo"],
    ["abc", "农业银行", "银行卡流水", "农", "olive"],
    ["cmb", "招商银行", "银行卡流水", "招", "red"],
  ];
  const sourceCards = sources.map(([value, label, note, icon, tone], index) => `<button type="button" class="source-card${index === 0 ? " selected" : ""}" data-action="import-source" data-value="${value}" data-tone="${tone}" aria-pressed="${index === 0}"><span class="source-icon" aria-hidden="true">${icon}</span><span><strong>${label}</strong><small>${note}</small></span><span class="source-check" aria-hidden="true">✓</span></button>`).join("");
  return `<div class="import-workflow" data-import-workflow data-step="1"><nav class="import-stepper" aria-label="数据导入步骤"><button type="button" class="import-step active" data-action="import-step" data-step="1" aria-current="step"><span>1</span><strong>选择来源</strong><small>确认账单平台</small></button><button type="button" class="import-step" data-action="import-step" data-step="2"><span>2</span><strong>添加文件</strong><small>上传待导入账单</small></button><button type="button" class="import-step" data-action="import-step" data-step="3" disabled><span>3</span><strong>预览确认</strong><small>核对后写入</small></button></nav><form data-form="import-preview"><section class="panel import-source-panel" data-import-step-panel="1"><div class="section-head"><div><span class="step-kicker">步骤 1 / 3</span><h2 tabindex="-1">选择数据来源</h2></div><button type="button" class="quiet" data-page="import-history">查看导入历史 →</button></div><p class="import-section-help">不确定时选择自动识别，系统会从文件表头与内容判断来源。</p><div class="source-card-grid" role="group" aria-label="数据来源">${sourceCards}</div><label class="visually-hidden">数据来源<select name="source_type"><option value="">自动识别</option><option value="alipay">支付宝</option><option value="wechat">微信</option><option value="ccb">建设银行</option><option value="abc">农业银行</option><option value="cmb">招商银行</option></select></label><div class="step-nav-actions"><span>已选择：<strong data-selected-source>自动识别</strong></span><button type="button" class="primary" data-action="import-step" data-step="2">下一步：添加文件 →</button></div></section><section class="panel import-upload-panel" data-import-step-panel="2" hidden><div class="section-head"><div><span class="step-kicker">步骤 2 / 3</span><h2 tabindex="-1">添加账单文件</h2></div><span class="format-note">CSV · XLS · XLSX · PDF · ZIP</span></div><label class="import-dropzone" data-import-dropzone><input class="visually-hidden" type="file" name="files" multiple required accept=".csv,.xls,.xlsx,.zip,.pdf"><span class="dropzone-icon" aria-hidden="true">↥</span><strong>拖放账单到这里，或点击选择文件</strong><small>单个文件不超过 25 MB，最多同时处理 100 个文件</small><span class="dropzone-button">选择文件</span></label><div id="selected-files" class="selected-files"><div class="selected-files-empty">选择文件后，将在这里显示待预览清单。</div></div><details class="import-options"><summary>加密文件与高级选项</summary><div class="import-option-body"><label>ZIP / PDF 密码<input type="password" name="password" autocomplete="off" placeholder="仅用于本次解析，不会保存"><small>密码只随本次预览请求使用。</small></label></div></details><div class="import-submit-row"><button type="button" class="quiet" data-action="import-step" data-step="1">← 返回选择来源</button><div class="import-submit-copy"><strong>先预览，再写入</strong><small>确认前不会修改任何账本数据。</small></div><button class="primary" data-action="preview-import">生成导入预览</button></div></section></form><section id="import-preview" data-import-step-panel="3" hidden></section></div>`;
}

const sourceLabels = {
  alipay: "支付宝", wechat: "微信支付", ccb: "建设银行", abc: "农业银行", cmb: "招商银行",
};
const actionLabels = {
  new: "新增", supplement: "补充证据", duplicate_file: "重复文件", record: "仅保留记录", ambiguous: "待确认", error: "有错误",
};
const statusLabels = { IMPORTED: "已导入", PARTIAL: "部分导入", FAILED: "失败" };

function fileExtension(filename) {
  return String(filename || "FILE").split(".").pop().slice(0, 4).toUpperCase();
}
function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
function updateSelectedFiles(form) {
  const root = $("#selected-files", form);
  const files = [...form.elements.files.files];
  if (!root) return;
  root.innerHTML = files.length ? files.map((file, index) => `<div class="selected-file-card"><span class="file-type-icon">${esc(fileExtension(file.name))}</span><span class="selected-file-copy"><strong>${esc(file.name)}</strong><small>${formatFileSize(file.size)} · 等待生成预览</small></span><button type="button" class="quiet file-remove" data-action="remove-import-file" data-index="${index}" aria-label="移除 ${esc(file.name)}">移除</button></div>`).join("") : '<div class="selected-files-empty">选择文件后，将在这里显示待预览清单。</div>';
}
function clearImportPlan() {
  state.importPlan = null;
  const preview = $("#import-preview");
  if (preview) preview.innerHTML = "";
  syncImportSteps();
}
function syncImportSteps(activeStep = Number($("[data-import-workflow]")?.dataset.step || 1)) {
  const workflow = $("[data-import-workflow]");
  if (!workflow) return;
  $$(".import-step", workflow).forEach((button) => {
    const step = Number(button.dataset.step);
    const active = step === activeStep;
    button.disabled = step === 3 && !state.importPlan;
    button.classList.toggle("active", active);
    button.classList.toggle("complete", step < activeStep);
    if (active) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
}
function showImportStep(step, focusHeading = true) {
  const workflow = $("[data-import-workflow]");
  const target = Number(step);
  if (!workflow || ![1, 2, 3].includes(target) || (target === 3 && !state.importPlan)) return;
  workflow.dataset.step = String(target);
  $$('[data-import-step-panel]', workflow).forEach((panel) => {
    panel.hidden = Number(panel.dataset.importStepPanel) !== target;
  });
  syncImportSteps(target);
  if (focusHeading) $(`[data-import-step-panel="${target}"] h2`, workflow)?.focus({ preventScroll: true });
}

function historySummaryMarkup(result) {
  return `<div class="history-metric"><span>匹配批次</span><strong>${result.summary.batch_count}</strong><small>当前筛选结果</small></div><div class="history-metric"><span>完整导入</span><strong>${result.summary.complete_count}</strong><small>无异常完成</small></div><div class="history-metric"><span>已写入记录</span><strong>${result.summary.imported_count}</strong><small>当前结果合计</small></div>`;
}
function historyResultsMarkup(result) {
  const batchCards = result.items.map((item) => {
    const accountLabels = (item.account_codes || []).map((identity) => state.historyAccountNames.get(identity) || identity);
    return `<button type="button" class="batch-card" data-action="batch-rows" data-id="${item.id}" data-filename="${esc(item.filename)}" aria-label="查看批次 ${item.id}：${esc(item.filename)}"><span class="file-type-icon">${esc(fileExtension(item.filename))}</span><span class="batch-file"><span class="history-id">批次 #${item.id}</span><strong>${esc(item.filename)}</strong><small>${esc(sourceLabels[item.source_type] || item.source_type)}</small></span><span class="batch-field batch-account"><small>来源账户</small><span>${accountLabels.length ? accountLabels.map((label) => `<span class="account-chip">${esc(label)}</span>`).join("") : '<span class="muted">未识别</span>'}</span></span><span class="batch-field"><small>成功 / 总数</small><span class="progress-count"><strong>${item.imported_count}</strong> / ${item.row_count}</span></span><span class="batch-field"><small>状态</small><span><span class="badge ${item.status === "IMPORTED" ? "" : "warn"}">${esc(statusLabels[item.status] || item.status)}</span></span></span><span class="batch-field batch-time"><small>导入时间</small><span>${date(item.imported_at)}</span></span><span class="batch-chevron" aria-hidden="true">›</span></button>`;
  });
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.page * result.page_size, result.total);
  const historyPager = `<div class="pagination"><span class="range">显示 ${start}–${end}，共 ${result.total} 个批次</span><button data-action="history-page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><span>${result.page} / ${pages}</span><button data-action="history-page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
  return batchCards.length
    ? `<div class="batch-card-list">${batchCards.join("")}</div>${historyPager}`
    : '<div class="empty-state"><span class="empty-state-icon">⌁</span><strong>没有匹配的导入记录</strong><p>调整上方关键词或来源账户，下方结果会自动更新。</p><button class="primary" data-page="import">导入新账单</button></div>';
}

async function importHistoryPage() {
  const [result, accounts] = await Promise.all([
    request("/paam/import/v1/batch/list?page=1&page_size=10"),
    request("/paam/import/v1/account/list"),
  ]);
  state.historyAccountNames = new Map(accounts.map((account) => [account.identity, account.display_name || account.identity]));
  const accountOptions = accounts.map((account) => `<option value="${esc(account.identity)}">${esc(account.display_name || account.identity)}</option>`).join("");
  return `<div class="history-summary" data-history-summary>${historySummaryMarkup(result)}</div><section class="panel history-panel"><div class="section-head"><div><h2>导入批次</h2><p class="import-section-help">搜索和账户筛选只更新下方结果，不会刷新页面或打断输入。</p></div><button class="primary" data-page="import">＋ 导入新数据</button></div><form class="toolbar history-toolbar" data-form="history-filter"><label class="grow">搜索<input name="q" placeholder="文件名、来源或批次编号" autocomplete="off"></label><label>来源账户<select name="account"><option value="">全部账户</option>${accountOptions}</select></label><span class="history-updating" data-history-updating aria-live="polite"></span></form><div data-history-results>${historyResultsMarkup(result)}</div></section>`;
}

async function refreshHistoryResults(form, page = 1) {
  clearTimeout(state.historyFilterTimer);
  state.historyRequestController?.abort();
  const controller = new AbortController();
  const requestVersion = ++state.historyRequestVersion;
  state.historyRequestController = controller;
  const params = new URLSearchParams({ page: String(page), page_size: "10" });
  const query = form.elements.q.value.trim();
  const account = form.elements.account.value;
  if (query) params.set("q", query);
  if (account) params.set("account", account);
  const resultsRoot = $("[data-history-results]");
  const summaryRoot = $("[data-history-summary]");
  const updating = $("[data-history-updating]", form);
  resultsRoot?.setAttribute("aria-busy", "true");
  form.classList.add("updating");
  if (updating) updating.textContent = "正在更新…";
  try {
    const result = await request(`/paam/import/v1/batch/list?${params}`, { signal: controller.signal });
    if (requestVersion !== state.historyRequestVersion || state.page !== "import-history") return;
    if (summaryRoot) summaryRoot.innerHTML = historySummaryMarkup(result);
    if (resultsRoot) {
      resultsRoot.innerHTML = historyResultsMarkup(result);
      bindPage(resultsRoot);
    }
  } catch (error) {
    if (error.name !== "AbortError") toast(error.message, true);
  } finally {
    if (requestVersion === state.historyRequestVersion) {
      resultsRoot?.removeAttribute("aria-busy");
      form.classList.remove("updating");
      if (updating) updating.textContent = "";
    }
  }
}

function scheduleHistoryRefresh(form) {
  clearTimeout(state.historyFilterTimer);
  state.historyFilterTimer = setTimeout(() => refreshHistoryResults(form, 1), 200);
}

function importRowMoney(row) {
  const amount = Number(row.amount_minor);
  if (!Number.isFinite(amount)) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency", currency: row.currency || "CNY",
  }).format(amount / 100);
}

function importBatchRow(item) {
  const status = { SUCCESS: "已写入", SKIPPED: "已跳过", INVALID: "异常" };
  const normalized = item.record?.normalized || {};
  const account = normalized.account?.display_name || normalized.account?.identity || "未识别";
  const summary = normalized.merchant || normalized.counterparty || normalized.note || "未命名交易";
  return `<tr><td>${item.id}</td><td>${date(normalized.occurred_at)}</td><td><strong>${esc(summary)}</strong>${normalized.note && normalized.note !== summary ? `<small>${esc(normalized.note)}</small>` : ""}</td><td>${esc(account)}</td><td class="money ${Number(normalized.amount_minor) > 0 ? "income" : "expense"}">${esc(importRowMoney(normalized))}</td><td><span class="badge ${item.disposition === "INVALID" ? "warn" : "neutral"}">${esc(status[item.disposition] || item.disposition)}</span>${item.bill_id ? `<small>Fact #${item.bill_id}</small>` : ""}</td></tr>`;
}

async function renderImportBatchDrawer(dialog) {
  dialog.requestController?.abort();
  const controller = new AbortController();
  dialog.requestController = controller;
  const page = Number(dialog.dataset.page || 1);
  const pageSize = Number(dialog.dataset.pageSize || 20);
  const body = $('[data-batch-drawer-body]', dialog);
  const range = $('[data-batch-range]', dialog);
  body.innerHTML = '<div class="preview-drawer-loading">正在读取当前页…</div>';
  range.textContent = "正在读取当前页…";
  try {
    const result = await request(`/paam/import/v1/batch/row/list?batch_id=${dialog.dataset.batchId}&page=${page}&page_size=${pageSize}`, {
      signal: controller.signal,
    });
    if (!dialog.open || dialog.requestController !== controller) return;
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    const currentPage = Math.min(Math.max(1, result.page), pages);
    const start = result.total ? (currentPage - 1) * result.page_size + 1 : 0;
    const end = Math.min(currentPage * result.page_size, result.total);
    const summary = result.summary || {};
    dialog.dataset.page = String(currentPage);
    range.textContent = result.total ? `显示 ${start}–${end}，共 ${result.total} 行` : "没有原始行记录";
    $('[data-batch-page]', dialog).textContent = `${currentPage} / ${pages}`;
    $('[data-action="batch-page-prev"]', dialog).disabled = currentPage <= 1;
    $('[data-action="batch-page-next"]', dialog).disabled = currentPage >= pages;
    const summaryCards = `<div class="batch-detail-summary"><span>总行数 <strong>${result.total}</strong></span><span>已写入 <strong>${summary.success || 0}</strong></span><span>已跳过 <strong>${summary.skipped || 0}</strong></span><span>异常 <strong>${summary.invalid || 0}</strong></span></div>`;
    body.innerHTML = `${summaryCards}${result.items.length ? table(["Raw", "交易时间", "摘要 / 备注", "账户", "金额", "处理结果"], result.items.map(importBatchRow)) : '<div class="empty-state">这个批次没有原始行记录。</div>'}`;
  } catch (error) {
    if (error.name !== "AbortError" && dialog.open) {
      body.innerHTML = `<div class="error">${esc(error.message)}</div>`;
      range.textContent = "读取失败";
    }
  }
}

function showImportBatch(button) {
  const dialog = document.createElement("dialog");
  dialog.className = "preview-drawer batch-detail-drawer";
  dialog.dataset.batchId = button.dataset.id;
  dialog.dataset.page = "1";
  dialog.dataset.pageSize = "20";
  dialog.innerHTML = `<div class="preview-drawer-shell"><div class="preview-drawer-head"><div><span class="step-kicker">导入批次 #${esc(button.dataset.id)}</span><h2>${esc(button.dataset.filename || "未命名文件")}</h2><p>按页读取原始记录与处理结果</p></div><button type="button" class="drawer-close" data-close aria-label="关闭批次明细">×</button></div><div class="preview-drawer-toolbar"><span data-batch-range>正在读取当前页…</span><label>每页显示<select data-batch-page-size aria-label="批次明细每页行数"><option value="20">20 行</option><option value="30">30 行</option><option value="50">50 行</option><option value="100">100 行</option></select></label></div><div class="preview-drawer-body" data-batch-drawer-body><div class="preview-drawer-loading">正在读取当前页…</div></div><div class="preview-drawer-footer"><button type="button" data-action="batch-page-prev" disabled>← 上一页</button><span data-batch-page>—</span><button type="button" data-action="batch-page-next" disabled>下一页 →</button></div></div>`;
  dialog.addEventListener("close", () => {
    dialog.requestController?.abort();
    dialog.remove();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) {
      dialog.close();
      return;
    }
    if (event.target.closest('[data-action="batch-page-prev"]')) {
      dialog.dataset.page = String(Number(dialog.dataset.page) - 1);
      renderImportBatchDrawer(dialog);
    }
    if (event.target.closest('[data-action="batch-page-next"]')) {
      dialog.dataset.page = String(Number(dialog.dataset.page) + 1);
      renderImportBatchDrawer(dialog);
    }
  });
  $('[data-batch-page-size]', dialog).addEventListener("change", (event) => {
    dialog.dataset.pageSize = event.currentTarget.value;
    dialog.dataset.page = "1";
    renderImportBatchDrawer(dialog);
  });
  document.body.append(dialog);
  dialog.showModal();
  requestAnimationFrame(() => {
    if (dialog.open) renderImportBatchDrawer(dialog);
  });
}
const fileBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(",")[1]);
  reader.onerror = reject;
  reader.readAsDataURL(file);
});
async function previewImport(form) {
  if (!beginSubmit(form)) return;
  const files = [...form.elements.files.files];
  if (!files.length) { endSubmit(form); throw new Error("请选择至少一个账单文件"); }
  const source = form.elements.source_type.value || null;
  const password = form.elements.password.value || null;
  const payload = { files: await Promise.all(files.map(async (file) => ({
    filename: file.name, content_base64: await fileBase64(file), source_type: source, password,
  }))) };
  try {
    state.importPlan = await jsonRequest("/paam/import/v1/preview", "POST", payload);
    renderImportPlan();
  } finally {
    endSubmit(form);
  }
}
function previewImportRow(row) {
  const amount = Number(row.amount_minor);
  const hasAmount = Number.isFinite(amount);
  const amountText = hasAmount
    ? new Intl.NumberFormat("zh-CN", { style: "currency", currency: row.currency || "CNY" }).format(amount / 100)
    : "—";
  const summary = row.merchant || row.counterparty || row.note || row.summary || "未命名交易";
  return `<tr><td>${date(row.occurred_at)}</td><td><strong>${esc(summary)}</strong>${row.note && row.note !== summary ? `<small>${esc(row.note)}</small>` : ""}</td><td class="money ${hasAmount && amount > 0 ? "income" : "expense"}">${esc(amountText)}</td><td><span class="badge ${["ambiguous", "error"].includes(row.action) ? "warn" : "neutral"}">${esc(actionLabels[row.action] || row.action || "待处理")}</span></td></tr>`;
}
function renderPreviewDrawer(dialog) {
  const documentIndex = Number(dialog.dataset.documentIndex);
  const document = state.importPlan?.documents?.[documentIndex];
  if (!document) return dialog.close();
  const rows = document.rows || [];
  const pageSize = Number(dialog.dataset.pageSize || 20);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(Math.max(1, Number(dialog.dataset.page || 1)), pages);
  const start = rows.length ? (page - 1) * pageSize : 0;
  const end = Math.min(start + pageSize, rows.length);
  dialog.dataset.page = String(page);
  $('[data-preview-range]', dialog).textContent = rows.length
    ? `显示 ${start + 1}–${end}，共 ${rows.length} 行`
    : "没有可预览的交易";
  $('[data-preview-page]', dialog).textContent = `${page} / ${pages}`;
  $('[data-action="preview-page-prev"]', dialog).disabled = page <= 1;
  $('[data-action="preview-page-next"]', dialog).disabled = page >= pages;
  $('[data-preview-drawer-body]', dialog).innerHTML = rows.length
    ? table(["交易时间", "摘要 / 备注", "金额", "处理结果"], rows.slice(start, end).map(previewImportRow))
    : '<div class="empty-state">文件中没有可预览的交易。</div>';
}
function openPreviewDrawer(documentIndex) {
  const previewDocument = state.importPlan?.documents?.[Number(documentIndex)];
  if (!previewDocument) return;
  const source = sourceLabels[previewDocument.source_type] || previewDocument.source_type || "未识别来源";
  const dialog = document.createElement("dialog");
  dialog.className = "preview-drawer";
  dialog.dataset.documentIndex = String(documentIndex);
  dialog.dataset.page = "1";
  dialog.dataset.pageSize = "20";
  dialog.innerHTML = `<div class="preview-drawer-shell"><div class="preview-drawer-head"><div><span class="step-kicker">仔细预览</span><h2>${esc(previewDocument.filename || "未命名文件")}</h2><p>${esc(source)} · 逐页检查解析结果</p></div><button type="button" class="drawer-close" data-close aria-label="关闭仔细预览">×</button></div><div class="preview-drawer-toolbar"><span data-preview-range>正在准备当前页…</span><label>每页<select data-preview-page-size><option value="20">20 行</option><option value="30">30 行</option><option value="50">50 行</option><option value="100">100 行</option></select></label></div><div class="preview-drawer-body" data-preview-drawer-body><div class="preview-drawer-loading">正在准备当前页…</div></div><div class="preview-drawer-footer"><button type="button" data-action="preview-page-prev" disabled>← 上一页</button><span data-preview-page>—</span><button type="button" data-action="preview-page-next" disabled>下一页 →</button></div></div>`;
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
    if (event.target.closest('[data-action="preview-page-prev"]')) {
      dialog.dataset.page = String(Number(dialog.dataset.page) - 1);
      renderPreviewDrawer(dialog);
    }
    if (event.target.closest('[data-action="preview-page-next"]')) {
      dialog.dataset.page = String(Number(dialog.dataset.page) + 1);
      renderPreviewDrawer(dialog);
    }
  });
  $('[data-preview-page-size]', dialog).addEventListener("change", (event) => {
    dialog.dataset.pageSize = event.currentTarget.value;
    dialog.dataset.page = "1";
    renderPreviewDrawer(dialog);
  });
  document.body.append(dialog);
  dialog.showModal();
  requestAnimationFrame(() => {
    if (dialog.open) renderPreviewDrawer(dialog);
  });
}
function renderImportPlan() {
  const plan = state.importPlan;
  const root = $("#import-preview");
  if (!plan || !root) return;
  const rows = (plan.documents || []).flatMap((document) => document.rows || []);
  const accountRows = new Map();
  rows.forEach((row) => {
    if (row.detected_account_identity && !accountRows.has(row.detected_account_identity)) {
      accountRows.set(row.detected_account_identity, row);
    }
  });
  const accountFields = [...accountRows.entries()].map(([detected, row]) => {
    const current = row.account?.identity || detected;
    const options = (plan.accounts || []).map((account) => {
      const label = `${account.display_name || account.identity} · ${account.provider || "unknown"}${account.number ? ` · ${account.number}` : ""}`;
      return `<option value="${esc(account.identity)}" ${account.identity === current ? "selected" : ""}>${esc(label)}</option>`;
    }).join("");
    return `<label>${esc(row.account?.display_name || detected)}<select name="account:${esc(detected)}">${options}</select><small>${esc(row.account_basis || "按导入文件识别")}</small></label>`;
  }).join("");
  const decisions = rows.filter((row) => row.action === "ambiguous").map((row) => {
    const options = (row.candidates || []).map((candidate) => `<option value="match:${esc(candidate)}">补充已有 Fact #${esc(candidate)}</option>`).join("");
    return `<label>第 ${esc(row.row_id)} 行：${esc(row.counterparty || row.summary || "未命名交易")}<select name="decision:${esc(row.row_id)}"><option value="">请选择</option><option value="new">保留为新 Fact</option>${options}</select><small>${esc(row.error || "需要人工确定")}</small></label>`;
  }).join("");
  const errors = rows.filter((row) => row.action === "error").map((row) => `<li>第 ${esc(row.row_id || "?")} 行：${esc(row.error || "无法导入")}</li>`).join("");
  const counts = plan.counts || {};
  const issueCount = Number(counts.error || 0) + Number(counts.errors || 0) + Number(counts.ambiguous || 0);
  const metric = (label, value, tone, note) => `<div class="preview-metric" data-tone="${tone}"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
  const documentCards = (plan.documents || []).map((document, index) => {
    const documentRows = document.rows || [];
    const previewRows = documentRows.slice(0, 5).map(previewImportRow);
    const source = sourceLabels[document.source_type] || document.source_type || "未识别来源";
    const documentIssue = document.error || documentRows.some((row) => ["ambiguous", "error"].includes(row.action));
    const status = document.error ? "解析失败" : document.duplicate ? "重复文件" : documentIssue ? "需要核对" : "可以导入";
    const body = document.error
      ? `<div class="error">${esc(document.error)}</div>`
      : `<div class="preview-file-meta"><span>来源 <strong>${esc(source)}</strong></span>${document.account?.display_name ? `<span>账户 <strong>${esc(document.account.display_name)}</strong></span>` : ""}<span>共 <strong>${documentRows.length}</strong> 行</span></div>${previewRows.length ? `${table(["交易时间", "摘要 / 备注", "金额", "处理结果"], previewRows)}<div class="preview-detail-row"><span>默认展示前 5 行，确认时仍会处理全部 ${documentRows.length} 行。</span><button type="button" class="quiet" data-action="detail-preview" data-document="${index}">仔细预览全部 ${documentRows.length} 行 →</button></div>` : '<div class="empty-state">文件中没有可预览的交易。</div>'}`;
    return `<details class="preview-file-card" ${index === 0 ? "open" : ""}><summary><span class="file-type-icon">${esc(fileExtension(document.filename || document.format))}</span><span class="preview-file-title"><strong>${esc(document.filename || "未命名文件")}</strong><small>${esc(source)} · ${documentRows.length} 行</small></span><span class="preview-status ${documentIssue ? "issue" : "ready"}">${status}</span><span class="preview-chevron" aria-hidden="true">⌄</span></summary><div class="preview-file-body">${body}</div></details>`;
  }).join("");
  root.innerHTML = `<div class="preview-section" aria-labelledby="preview-title"><div class="preview-heading"><div><span class="step-kicker">步骤 3 / 3</span><h2 id="preview-title" tabindex="-1">预览结果</h2><p>文件卡片默认快速展示前 5 行；需要逐条核对时可打开右侧仔细预览。</p></div><span class="preview-verdict ${plan.can_confirm ? "ready" : "issue"}">${plan.can_confirm ? "✓ 可以写入" : "! 需要处理"}</span></div><div class="preview-metrics">${metric("新增事实", Number(counts.new || 0), "green", "将生成新流水")}${metric("补充证据", Number(counts.supplement || 0), "blue", "关联已有事实")}${metric("跳过 / 留档", Number(counts.duplicate_file || 0) + Number(counts.record || 0), "gray", "不重复写入")}${metric("需处理", issueCount, issueCount ? "orange" : "green", issueCount ? "请检查下方项目" : "未发现阻塞项")}</div><div class="preview-file-list">${documentCards}</div>${errors ? `<div class="error"><strong>无法直接导入的记录</strong><ul class="error-list">${errors}</ul></div>` : ""}<form data-form="import-revise" class="preview-confirm-card"><div><h3>${plan.can_confirm ? "核对完成，准备写入" : "完成核对后再写入"}</h3><p>${plan.can_confirm ? "写入采用原子事务，原始文件和证据会一并保留。" : "请处理账号匹配或交易歧义；文件解析错误需重新导出后上传。"}</p></div>${accountFields ? `<details class="review-fields"><summary>核对或调整账号匹配</summary><div class="stack inset">${accountFields}</div></details>` : ""}${decisions ? `<fieldset><legend>交易匹配决策</legend><div class="stack">${decisions}</div></fieldset>` : ""}<details class="raw-plan" data-raw-plan><summary>查看技术明细</summary><div class="raw-plan-placeholder" data-raw-plan-content>展开后加载技术明细</div></details><div class="confirm-actions"><button type="button" class="quiet back-to-files" data-action="import-step" data-step="2">← 返回文件步骤</button><button type="submit">重新计算预览</button><button type="button" class="primary" data-action="confirm-import" ${plan.can_confirm ? "" : "disabled"}>确认并写入事实层</button></div></form></div>`;
  bindPage(root);
  showImportStep(3);
}
async function reviseImport(event) {
  event.preventDefault();
  if (!state.importPlan) return;
  const form = event.currentTarget;
  if (!beginSubmit(form)) return;
  const accounts = {};
  const decisions = {};
  for (const [name, value] of new FormData(form)) {
    if (name.startsWith("account:") && value) accounts[name.slice(8)] = value;
    if (name.startsWith("decision:") && value) decisions[name.slice(9)] = value;
  }
  try {
    state.importPlan = await jsonRequest(`/paam/import/v1/preview/${state.importPlan.token}`, "PUT", { accounts, decisions });
    renderImportPlan();
    toast("导入预览已重新计算");
  } catch (error) { toast(error.message, true); }
  finally { endSubmit(form); }
}

async function tagsPage() {
  const views = await request("/paam/tag/v1/view/list?include_archived=true");
  const activeCount = views.filter((view) => view.status === "ACTIVE").length;
  const cards = views.map((view) => {
    const isActive = view.status === "ACTIVE";
    const tagsMarkup = view.tags.map((tag) => {
      const isSystem = tag.system_name === "unclassified";
      const isTagActive = tag.status === "ACTIVE";
      const action = isSystem ? "" : `<button class="tag-pill-action" type="button" data-action="tag-status" data-view="${view.id}" data-id="${tag.id}" data-status="${isTagActive ? "ARCHIVED" : "ACTIVE"}" aria-label="${isTagActive ? "归档" : "恢复"}标签 ${esc(tag.name)}" title="${isTagActive ? "归档标签" : "恢复标签"}">${isTagActive ? "×" : "恢复"}</button>`;
      return `<span class="tag-pill${isSystem ? " system" : ""}${isTagActive ? "" : " archived"}" title="系统名称：${esc(tag.system_name)}">${isSystem ? '<span class="tag-pill-lock" aria-hidden="true">◆</span>' : ""}<span>${esc(tag.name)}</span>${isSystem ? `<code>${esc(tag.system_name)}</code>` : ""}${action}</span>`;
    }).join("");
    const creator = isActive ? `<div class="tag-inline-creator"><button class="tag-inline-launch" type="button" data-action="new-tag-inline" data-id="${view.id}" aria-controls="tag-create-${view.id}" aria-expanded="false"><span aria-hidden="true">＋</span> 新标签</button><form id="tag-create-${view.id}" class="tag-inline-form" data-form="inline-tag" data-view="${view.id}" hidden><label class="sr-only" for="tag-name-${view.id}">标签名称</label><input id="tag-name-${view.id}" name="name" maxlength="120" placeholder="标签名称" autocomplete="off" required><span class="tag-inline-divider" aria-hidden="true"></span><label class="sr-only" for="tag-system-${view.id}">系统名称</label><input id="tag-system-${view.id}" name="system_name" maxlength="64" pattern="[a-z][a-z0-9_]{0,63}" placeholder="system_name" autocomplete="off" required><button class="tag-inline-submit" type="submit" aria-label="保存标签" title="保存">✓</button><button class="tag-inline-cancel" type="button" data-action="cancel-tag" aria-label="取消添加标签" title="取消">×</button></form></div>` : "";
    return `<article class="tag-view-card${isActive ? "" : " archived"}" aria-labelledby="tag-view-${view.id}"><header class="tag-view-head"><div class="tag-view-meta"><div class="tag-view-title"><h3 id="tag-view-${view.id}">${esc(view.name)}</h3><span class="tag-view-status ${isActive ? "active" : "archived"}"><span aria-hidden="true">●</span>${esc(statusNames[view.status] || view.status)}</span></div><code>${esc(view.system_name)}</code></div><div class="tag-view-actions">${isActive ? `<button type="button" data-action="new-tag" data-id="${view.id}" aria-controls="tag-create-${view.id}" aria-expanded="false">＋ 添加标签</button>` : ""}<button class="quiet" type="button" data-action="view-status" data-id="${view.id}" data-status="${isActive ? "ARCHIVED" : "ACTIVE"}">${isActive ? "归档维度" : "恢复维度"}</button></div></header><div class="tag-pill-list">${tagsMarkup}${creator}</div></article>`;
  }).join("");
  return `<section class="tag-manager" aria-labelledby="tag-manager-title"><div class="tag-manager-head"><div><h2 id="tag-manager-title">标签维度</h2><p>${views.length ? `共 ${views.length} 个维度，${activeCount} 个启用中` : "用维度组织同一类标签"}</p></div><button class="primary" data-action="new-view">＋ 新建维度</button></div><div class="tag-view-list">${cards || '<div class="panel empty-state">尚未创建标签维度</div>'}</div></section>`;
}

async function reviewsPage() {
  const query = new URLSearchParams({
    page: state.params.get("review_page") || "1",
    page_size: "25",
  });
  if (state.params.get("status")) query.set("status", state.params.get("status"));
  if (state.params.get("review_type")) query.set("review_type", state.params.get("review_type"));
  const workQuery = new URLSearchParams({
    page: state.params.get("work_page") || "1",
    page_size: "10",
    ledger_type: "UNRESOLVED",
  });
  const [result, unresolved, pending] = await Promise.all([
    request(`/paam/review/v1/case/page?${query}`),
    request(`/paam/ledger/v1/entry/list?${workQuery}`),
    request("/paam/review/v1/case/page?page=1&page_size=1&status=PENDING"),
  ]);
  const rows = result.items.map((item) => `<tr><td>${item.id}</td><td><strong>${esc(reviewTypeNames[item.review_type] || item.review_type)}</strong><br><small>${esc(item.title || "未填写标题")}</small></td><td><span class="badge ${item.status === "PENDING" ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td>${esc(statusNames[item.allocation_status] || item.allocation_status)}</td><td>${item.lines.length}</td><td>v${item.version}</td><td><button data-action="review-detail" data-id="${item.id}">处理 / 详情</button></td></tr>`);
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const paging = `<div class="pagination"><span>共 ${result.total} 条 · 第 ${result.page}/${pages} 页</span><button data-action="review-page" data-param="review_page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="review-page" data-param="review_page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
  const workPages = Math.max(1, Math.ceil(unresolved.total / unresolved.page_size));
  const workRows = unresolved.items.map((entry) => `<article class="review-work-item ${state.selectedLedgers.has(entry.id) ? "selected" : ""}">${ledgerSelector(entry)}<div><strong>${esc(entry.title || "未命名流水")}</strong><small>${date(entry.start_time)} · #${entry.id} · ${ledgerAccountPath(entry)}</small></div><span>${ledgerAmounts(entry)}</span><button type="button" data-action="detail" data-id="${entry.id}">查看事实</button></article>`).join("");
  const workPaging = `<div class="pagination"><span>第 ${unresolved.page}/${workPages} 页</span><button data-action="review-page" data-param="work_page" data-value="${unresolved.page - 1}" ${unresolved.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="review-page" data-param="work_page" data-value="${unresolved.page + 1}" ${unresolved.page >= workPages ? "disabled" : ""}>下一页</button></div>`;
  const typeOptions = Object.entries(reviewTypeNames).map(([value, label]) => `<option value="${value}" ${result.review_type === value ? "selected" : ""}>${esc(label)}</option>`).join("");
  const launchers = `<section class="task-launchers" aria-label="常用工作">
    <article><span class="task-number">01</span><div><span class="eyebrow">IMPORT</span><h2>账单导入</h2><p>上传文件、核对预览，再写入不可变的账单事实。</p></div><div class="task-meta"><span>${unresolved.total} 条待核验流水</span><span>支持 CSV / XLSX / PDF / ZIP</span></div><button type="button" class="primary" data-page="import">进入账单导入</button></article>
    <article><span class="task-number">02</span><div><span class="eyebrow">REVIEW</span><h2>创建审查</h2><p>从事实中选择完整行为，建立收入、支出、转账、退款等关系。</p></div><div class="task-meta"><span>${pending.total} 个待确认审查</span><span>继续使用可理解的 v1 规则</span></div><button type="button" class="primary" data-action="review-queue">打开创建审查</button></article>
  </section>`;
  if (!state.params.get("view")) return `<div class="review-dashboard review-overview">${launchers}</div>`;
  return `<div class="review-dashboard">${launchers}<section class="review-metrics"><div><span>待核验流水</span><strong>${unresolved.total}</strong><small>尚未明确为普通收支或特殊业务</small></div><div><span>待确认 Review</span><strong>${pending.total}</strong><small>确认前不会改变实际账本</small></div><div><span>当前筛选结果</span><strong>${result.total}</strong><small>统一审查历史</small></div></section>
  <section class="panel review-workbench"><div class="section-head"><div><h2>待处理流水</h2><p>选择一条确认普通收支，或选择多条建立转账、退款、AA、借贷等关联。</p></div><button class="primary" data-action="new-review">从已选流水创建</button></div>${reviewSelectionBar()}<div class="review-work-list">${workRows || '<div class="empty-state">没有待核验流水。</div>'}</div>${workPaging}</section>
  <section class="panel"><div class="section-head"><div><h2>Review 事项</h2><p>待确认建议不会改变实际账本；确认后才会原子更新投影。</p></div></div><form class="toolbar review-toolbar" data-form="review-filter"><label>状态<select name="status"><option value="">全部状态</option>${["PENDING","CONFIRMED","REJECTED","REVOKED"].map((value) => `<option value="${value}" ${result.status === value ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><label>类型<select name="review_type"><option value="">全部类型</option>${typeOptions}</select></label><button>筛选</button></form>${rows.length ? table(["ID", "类型", "状态", "分配", "Fact 数", "版本", ""], rows) : '<div class="empty-state">没有符合条件的 Review。</div>'}${paging}</section></div>`;
}

async function showReview(id) {
  const renderVersion = state.renderVersion;
  const item = await request(`/paam/review/v1/case/detail/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const lines = item.lines.map((line) => `<tr><td>#${line.bill_id}</td><td><strong>${esc(roleNames[line.role] || line.role)}</strong><br><small>${esc(line.role)}</small></td><td>${money({amount_value:line.amount_value,amount_scale:line.amount_scale,currency_code:line.currency_code})}</td><td>${esc(line.party || "—")}</td></tr>`);
  const operationNames = { CREATE: "创建", UPDATE: "修订", CONFIRM: "确认", REVOKE: "撤销", RESTORE: "恢复", DISMISS: "忽略", REOPEN: "重新打开", ASSIGN: "分配标签", ACCOUNT_SET: "修正账户", RESOLVE: "解决冲突" };
  const history = item.history.map((event) => `<details class="review-history-event"><summary><span>v${event.version} · ${esc(operationNames[event.operation] || event.operation)}</span><small>${date(event.created_time)}</small></summary><p>${esc(event.reason || "无说明")}</p><details><summary>查看技术快照</summary><pre>${esc(JSON.stringify({request:event.request,before:event.before,after:event.after}, null, 2))}</pre></details></details>`).join("");
  let actions = "";
  if (["CLASSIFICATION","AA","LOAN_BORROW","LOAN_LEND","REFUND","TRANSFER","FX_EXCHANGE","DUPLICATE"].includes(item.review_type)) {
    if (item.status === "PENDING") actions = `<button data-action="edit-review" data-id="${item.id}">编辑</button><button data-action="review-transition" data-kind="dismiss" data-id="${item.id}" data-version="${item.version}">忽略</button><button class="primary" data-action="review-transition" data-kind="confirm" data-id="${item.id}" data-version="${item.version}">确认并更新流水</button>`;
    if (item.status === "CONFIRMED") actions = `<button data-action="review-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销</button>`;
    if (item.status === "REVOKED") actions = `<button data-action="edit-review" data-id="${item.id}">编辑</button><button class="primary" data-action="review-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复</button>`;
    if (item.status === "REJECTED") actions = `<button class="primary" data-action="review-transition" data-kind="reopen" data-id="${item.id}" data-version="${item.version}">重新打开</button>`;
  } else if (item.review_type === "ACCOUNT") {
    if (item.status === "CONFIRMED") actions = `<button data-action="account-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销账户修正</button>`;
    if (item.status === "REVOKED") actions = `<button data-action="account-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复账户修正</button>`;
  } else if (item.review_type === "FACT_CONFLICT") {
    if (item.status === "PENDING") actions = `<button data-action="conflict-resolve" data-id="${item.id}" data-version="${item.version}">解决冲突</button><button data-action="conflict-transition" data-kind="dismiss" data-id="${item.id}" data-version="${item.version}">忽略</button>`;
    if (item.status === "REJECTED") actions = `<button data-action="conflict-transition" data-kind="reopen" data-id="${item.id}" data-version="${item.version}">重新打开</button>`;
  }
  const dialog = modal(`Review #${item.id}`, `<div class="review-detail-head"><div><span class="review-type-kicker">${esc(reviewTypeNames[item.review_type] || item.review_type)}</span><h3>${esc(item.title || "未填写标题")}</h3><small>${esc(statusNames[item.status] || item.status)} · ${esc(statusNames[item.allocation_status] || item.allocation_status)} · 版本 ${item.version}</small></div><div class="actions">${actions}</div></div><section><h3>涉及的账单事实</h3>${lines.length ? table(["Fact", "业务角色", "分配金额", "对象"], lines) : '<p class="muted">当前没有已接受的 Fact。</p>'}</section>${Object.keys(item.result || {}).length ? `<section><h3>类型结果</h3><pre>${esc(JSON.stringify(item.result, null, 2))}</pre></section>` : ""}<section><h3>操作历史</h3><div class="review-history">${history || '<p class="muted">没有历史。</p>'}</div></section>`);
  bindPage(dialog);
}

async function newReview() {
  if (!state.selectedLedgers.size) {
    toast("请先选择需要处理的流水");
    if (state.page !== "ledger") route("ledger");
    return;
  }
  await openReviewWizard();
}

function reviewTypeOptions(factCount) {
  const types = ["CLASSIFICATION", "TRANSFER", "REFUND", "AA", "LOAN_BORROW", "LOAN_LEND", "FX_EXCHANGE", "DUPLICATE"];
  return types.map((value) => `<option value="${value}" ${value === "CLASSIFICATION" && factCount !== 1 ? "disabled" : ""}>${esc(reviewTypeNames[value])}</option>`).join("");
}

function reviewRoleOptions(type, direction, index, selectedRole = "") {
  const values = reviewRoles[type]?.[direction] || [];
  return values.map((value, optionIndex) => {
    const selected = selectedRole
      ? value === selectedRole
      : type === "DUPLICATE"
        ? (index === 0 ? value === "DUPLICATE_RETAINED" : value === "DUPLICATE_EXCLUDED")
        : optionIndex === 0;
    return `<option value="${value}" ${selected ? "selected" : ""}>${esc(roleNames[value] || value)}</option>`;
  }).join("");
}

function reviewRoleDirection(type, role) {
  return Object.entries(reviewRoles[type] || {}).find(([, roles]) => roles.includes(role))?.[0] || "";
}

function decimalText(value, scale) {
  return (Number(value) / (10 ** Number(scale))).toFixed(Number(scale));
}

function reviewWizardNotice(type, facts) {
  const directions = new Set(facts.map((fact) => fact.cash_direction));
  if (type === "CLASSIFICATION" && facts.length !== 1) return "确认普通收支一次只能处理一条事实。";
  if (["TRANSFER", "REFUND", "FX_EXCHANGE"].includes(type) && !(directions.has("IN") && directions.has("OUT"))) return "此类型通常需要同时选择一条流入和一条流出事实。";
  if (type === "DUPLICATE" && facts.length < 2) return "重复交易至少需要两条事实。";
  return "角色和资金方向会由后端再次校验；确认前不会修改实际流水。";
}

function renderReviewWizardLines(dialog) {
  const form = $('[data-form="review-wizard"]', dialog);
  const type = form.elements.review_type.value;
  const facts = dialog.reviewFacts;
  const rows = facts.map((fact, index) => `<article class="review-fact-choice" data-fact="${fact.id}" data-direction="${fact.cash_direction}" data-scale="${fact.amount.amount_scale}"><div class="review-fact-main"><span class="badge neutral">${fact.cash_direction === "IN" ? "流入" : "流出"}</span><div><strong>${esc(fact.counterparty || fact.summary || `Fact #${fact.id}`)}</strong><small>${date(fact.occurred_time)} · Fact #${fact.id} · ${money(fact.amount)}</small></div></div><label>在本次审查中的角色<select name="role-${fact.id}" required>${reviewRoleOptions(type, fact.cash_direction, index)}</select></label><label>分配金额（留空表示全额）<input name="amount-${fact.id}" inputmode="decimal" placeholder="全额 ${money(fact.amount)}"></label><label>相关对象（可选）<input name="party-${fact.id}" maxlength="120" placeholder="例如共同付款人"></label></article>`).join("");
  $("[data-review-facts]", dialog).innerHTML = rows;
  $("[data-review-notice]", dialog).textContent = reviewWizardNotice(type, facts);
  form.dataset.rolesValid = String(!facts.some((fact, index) => !reviewRoleOptions(type, fact.cash_direction, index)));
}

function renderReviewWizardSummary(dialog) {
  const form = $('[data-form="review-wizard"]', dialog);
  const data = new FormData(form);
  const type = data.get("review_type");
  const factRows = dialog.reviewFacts.map((fact) => {
    const role = data.get(`role-${fact.id}`);
    const entered = data.get(`amount-${fact.id}`);
    return `<div><span>Fact #${fact.id} · ${esc(roleNames[role] || role)}</span><strong>${entered ? esc(entered) : money(fact.amount)}</strong></div>`;
  }).join("");
  $('[data-review-summary]', dialog).innerHTML = `<article><span>处理方式</span><strong>${esc(reviewTypeNames[type] || type)}</strong><small>${esc(data.get("title") || "未填写标题")}</small></article><article><span>处理说明</span><strong>${esc(data.get("reason") || "无")}</strong><small>创建后仍需再次确认</small></article><article class="full"><span>涉及的账单事实</span><div class="review-summary-lines">${factRows}</div></article>`;
}

function showReviewWizardStep(dialog, step) {
  const form = $('[data-form="review-wizard"]', dialog);
  dialog.reviewStep = Math.max(1, Math.min(4, step));
  $$('[data-review-step-panel]', form).forEach((panel) => {
    panel.hidden = Number(panel.dataset.reviewStepPanel) !== dialog.reviewStep;
  });
  $$('[data-review-step]', form).forEach((button) => {
    const current = Number(button.dataset.reviewStep);
    button.classList.toggle("active", current === dialog.reviewStep);
    button.classList.toggle("complete", current < dialog.reviewStep);
    button.setAttribute("aria-current", current === dialog.reviewStep ? "step" : "false");
  });
  $('[data-review-back]', form).disabled = dialog.reviewStep === 1;
  $('[data-review-next]', form).hidden = dialog.reviewStep === 4;
  $('[data-review-submit]', form).hidden = dialog.reviewStep !== 4;
  $('[data-review-progress]', form).textContent = `步骤 ${dialog.reviewStep} / 4`;
  if (dialog.reviewStep === 4) renderReviewWizardSummary(dialog);
  $('[data-review-step-panel]:not([hidden]) h3', form)?.focus();
}

function nextReviewWizardStep(dialog) {
  const form = $('[data-form="review-wizard"]', dialog);
  if (dialog.reviewStep === 2) {
    const type = form.elements.review_type;
    if (!type.checkValidity()) return type.reportValidity();
    renderReviewWizardLines(dialog);
  }
  if (dialog.reviewStep === 3) {
    if (form.dataset.rolesValid !== "true") {
      toast("当前处理方式与所选流水方向不匹配", true);
      return;
    }
    const controls = $$('[data-review-step-panel="3"] select, [data-review-step-panel="3"] input', form);
    const invalid = controls.find((control) => !control.checkValidity());
    if (invalid) return invalid.reportValidity();
  }
  showReviewWizardStep(dialog, dialog.reviewStep + 1);
}

async function openReviewWizard() {
  const selections = [...state.selectedLedgers.values()];
  if (!selections.length) throw new Error("请先选择需要处理的流水");
  const details = await Promise.all(selections.map((entry) => request(`/paam/ledger/v1/entry/detail/${entry.id}`)));
  const locked = details.filter((detail) => detail.reviews.some((review) => review.is_projection_source && !["TAG", "ACCOUNT"].includes(review.review_type)));
  if (locked.length) throw new Error("所选流水已由已确认 Review 归集；请先撤销原 Review 再重新处理");
  const facts = details.flatMap((detail) => detail.facts).filter((fact, index, all) => all.findIndex((candidate) => candidate.id === fact.id) === index);
  if (!facts.length) throw new Error("所选流水没有可审查的 Fact");
  const defaultType = facts.length === 1 ? "CLASSIFICATION" : "TRANSFER";
  const selectedFacts = facts.map((fact) => `<article class="selected-review-fact"><span>${fact.cash_direction === "IN" ? "流入" : "流出"}</span><div><strong>${esc(fact.counterparty || fact.summary || `Fact #${fact.id}`)}</strong><small>${date(fact.occurred_time)} · Fact #${fact.id}</small></div><b>${money(fact.amount)}</b></article>`).join("");
  const dialog = modal("从流水建立审查", `<form data-form="review-wizard" class="review-wizard">
    <nav class="review-stepper" aria-label="创建审查步骤">
      <button type="button" data-review-step="1"><span>1</span><strong>选择流水</strong><small>确认处理范围</small></button>
      <button type="button" data-review-step="2"><span>2</span><strong>描述业务</strong><small>选择处理方式</small></button>
      <button type="button" data-review-step="3"><span>3</span><strong>设置分配</strong><small>核对角色和金额</small></button>
      <button type="button" data-review-step="4"><span>4</span><strong>汇总确认</strong><small>创建待确认事项</small></button>
    </nav>
    <section class="review-step-panel" data-review-step-panel="1"><h3 tabindex="-1">确认需要一起处理的流水</h3><p>已经载入 ${selections.length} 条流水，共包含 ${facts.length} 条账单事实。</p><div class="selected-review-facts">${selectedFacts}</div></section>
    <section class="review-step-panel" data-review-step-panel="2" hidden><h3 tabindex="-1">这些流水属于什么业务？</h3><p>继续使用当前 v1 中可理解的收入、转账、退款、AA、借贷和换汇处理方式。</p><div class="review-description-grid"><label>处理方式<select name="review_type" required>${reviewTypeOptions(facts.length)}</select></label><label>标题<input name="title" maxlength="160" placeholder="例如：微信零钱转入银行卡"></label><label class="full">处理说明<input name="reason" maxlength="2000" value="人工核对流水关系"></label></div><div class="review-notice" data-review-notice></div></section>
    <section class="review-step-panel" data-review-step-panel="3" hidden><h3 tabindex="-1">设置每条事实的角色和金额</h3><p>金额留空时由 v1 后端按该事实的完整剩余金额处理。</p><div class="review-fact-choices" data-review-facts></div></section>
    <section class="review-step-panel" data-review-step-panel="4" hidden><h3 tabindex="-1">核对后创建待确认审查</h3><p>创建不会立即改变实际流水；进入审查详情再次确认后才会更新投影。</p><div class="review-summary-grid" data-review-summary></div></section>
    <footer class="review-wizard-footer"><button type="button" class="quiet" data-review-back>← 上一步</button><div><strong data-review-progress>步骤 1 / 4</strong><small>所有财务规则仍由 v1 后端校验</small></div><button type="button" class="primary" data-review-next>下一步 →</button><button class="primary" type="submit" data-review-submit hidden>创建待确认审查</button></footer>
  </form>`);
  dialog.reviewFacts = facts;
  const form = $('[data-form="review-wizard"]', dialog);
  form.elements.review_type.value = defaultType;
  form.elements.review_type.addEventListener("change", () => renderReviewWizardLines(dialog));
  $('[data-review-next]', form).addEventListener("click", () => nextReviewWizardStep(dialog));
  $('[data-review-back]', form).addEventListener("click", () => showReviewWizardStep(dialog, dialog.reviewStep - 1));
  form.addEventListener("submit", submitReviewWizard);
  renderReviewWizardLines(dialog);
  showReviewWizardStep(dialog, 1);
}

function decimalAmount(value, scale) {
  const text = String(value || "").trim();
  if (!text) return null;
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error(`金额格式不正确：${text}`);
  const [whole, decimal = ""] = text.split(".");
  if (decimal.length > scale) throw new Error(`金额最多允许 ${scale} 位小数`);
  const result = Number(`${whole}${decimal.padEnd(scale, "0")}`);
  if (!Number.isSafeInteger(result) || result <= 0) throw new Error("金额超出可处理范围");
  return result;
}

async function submitReviewWizard(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = form.closest("dialog");
  if (dialog.reviewStep !== 4) {
    nextReviewWizardStep(dialog);
    return;
  }
  const idempotencyKey = beginSubmit(form);
  if (!idempotencyKey) return;
  try {
    const data = new FormData(form);
    const lines = dialog.reviewFacts.map((fact) => {
      const line = {
        bill_id: fact.id,
        role: data.get(`role-${fact.id}`),
        party: data.get(`party-${fact.id}`) || "",
      };
      const amount = decimalAmount(data.get(`amount-${fact.id}`), fact.amount.amount_scale);
      if (amount !== null) line.amount_value = amount;
      return line;
    });
    const item = await jsonRequest("/paam/review/v1/case/create", "POST", {
      review_type: data.get("review_type"),
      title: data.get("title"),
      result: {},
      lines,
      reason: data.get("reason"),
      idempotency_key: idempotencyKey,
    });
    state.selectedLedgers.clear();
    closeDialogs();
    toast("待确认 Review 已创建，请核对后确认");
    await render();
    await showReview(item.id);
  } catch (error) {
    endSubmit(form);
    showFormError(form, error);
  }
}

async function editReview(id) {
  const item = await request(`/paam/review/v1/case/detail/${id}`);
  if (!["PENDING", "REVOKED"].includes(item.status)) throw new Error("只有待确认或已撤销 Review 可以编辑");
  const lines = item.lines.map((line, index) => {
    const direction = reviewRoleDirection(item.review_type, line.role);
    return `<article class="review-fact-choice review-edit-line" data-fact="${line.bill_id}" data-direction="${direction}" data-scale="${line.amount_scale}"><div class="review-fact-main"><span class="badge neutral">${direction === "IN" ? "流入" : "流出"}</span><div><strong>Fact #${line.bill_id}</strong><small>${esc(line.currency_code)} · 当前分配 ${esc(decimalText(line.amount_value, line.amount_scale))}</small></div></div><label>在本次审查中的角色<select name="role-${line.bill_id}" required>${reviewRoleOptions(item.review_type, direction, index, line.role)}</select></label><label>分配金额<input name="amount-${line.bill_id}" inputmode="decimal" value="${esc(decimalText(line.amount_value, line.amount_scale))}" required></label><label>相关对象（可选）<input name="party-${line.bill_id}" value="${esc(line.party)}" maxlength="120"></label></article>`;
  }).join("");
  const dialog = modal(`编辑 Review #${item.id}`, `<form data-form="edit-review" data-id="${item.id}" data-version="${item.version}" class="review-wizard stack"><div class="review-wizard-intro"><strong>${esc(reviewTypeNames[item.review_type] || item.review_type)} · ${item.lines.length} 条事实</strong><span>修改角色、分配金额或相关对象；审查类型保持不变。</span></div><label>标题<input name="title" value="${esc(item.title)}" maxlength="160"></label><div class="review-fact-choices">${lines}</div><label>修改说明<input name="reason" maxlength="2000" value="人工修订待确认审查"></label><div class="review-submit-note">保存只更新待确认内容；确认后才会改变实际流水。</div><div class="actions"><button type="button" data-close>取消</button><button class="primary">保存修订</button></div></form>`);
  const form = $('[data-form="edit-review"]', dialog);
  form.reviewResult = item.result;
  bindPage(dialog);
}

function beginSubmit(form) {
  if (form.dataset.submitting === "true") return null;
  form.dataset.submitting = "true";
  form.dataset.idempotencyKey ||= key();
  $$('button, input[type="submit"]', form).forEach((control) => control.disabled = true);
  return form.dataset.idempotencyKey;
}
function endSubmit(form) {
  delete form.dataset.submitting;
  $$('button, input[type="submit"]', form).forEach((control) => control.disabled = false);
}
function showFormError(form, error) {
  $(".form-error", form)?.remove();
  const node = document.createElement("div");
  node.className = "form-error error";
  node.setAttribute("role", "alert");
  node.innerHTML = `<span>${esc(error.message)}</span>${/changed|reload|version|conflict|变化|冲突/.test(error.message) ? '<button type="button" data-action="reload-current">重新载入</button>' : ""}`;
  node.querySelector('[data-action="reload-current"]')?.addEventListener("click", () => {
    closeDialogs(); render();
  });
  form.prepend(node);
  toast(error.message, true);
}
function bindCommandForm(form) {
  form?.addEventListener("input", () => {
    if (form.dataset.submitting !== "true") delete form.dataset.idempotencyKey;
  });
}

function tagSystemName(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 64);
}

function openInlineTag(button) {
  const card = button.closest(".tag-view-card");
  const form = $("[data-form=\"inline-tag\"]", card);
  if (!form) return;
  $$('[data-action="new-tag"], [data-action="new-tag-inline"]', card).forEach((trigger) => {
    trigger.hidden = true;
    trigger.setAttribute("aria-expanded", "true");
  });
  form.hidden = false;
  $('[name="name"]', form)?.focus();
}

function closeInlineTag(form) {
  const card = form.closest(".tag-view-card");
  form.reset();
  delete $('[name="system_name"]', form)?.dataset.manual;
  $(".form-error", form)?.remove();
  form.hidden = true;
  $$('[data-action="new-tag"], [data-action="new-tag-inline"]', card).forEach((trigger) => {
    trigger.hidden = false;
    trigger.setAttribute("aria-expanded", "false");
  });
}

function refreshLedgerDatePicker(picker) {
  const start = $('input[name="date_from"]', picker).value;
  const end = $('input[name="date_to"]', picker).value;
  $(".ledger-calendar-month", picker).textContent = `${state.ledgerCalendar.year} 年 ${state.ledgerCalendar.month + 1} 月`;
  $(".ledger-calendar-grid", picker).innerHTML = calendarGrid(start, end);
  const hint = $(".ledger-date-hint", picker);
  hint.textContent = calendarHint(start, end);
  hint.classList.toggle("error", Boolean(state.ledgerCalendar.error));
  $(".ledger-date-caption", picker).textContent = dateRangeLabel(start, end);
  $(".ledger-date-trigger", picker).classList.toggle("is-empty", !start);
}

function bindLedgerDatePicker(picker) {
  if (!picker) return;
  picker.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button || !picker.contains(button)) return;
    const action = button.dataset.action;
    const popover = $(".ledger-date-popover", picker);
    const trigger = $(".ledger-date-trigger", picker);
    if (action === "ledger-date-toggle") {
      popover.hidden = !popover.hidden;
      trigger.setAttribute("aria-expanded", String(!popover.hidden));
      return;
    }
    if (action === "ledger-date-month") {
      const target = new Date(state.ledgerCalendar.year, state.ledgerCalendar.month + Number(button.dataset.value), 1);
      state.ledgerCalendar.year = target.getFullYear();
      state.ledgerCalendar.month = target.getMonth();
      refreshLedgerDatePicker(picker);
      return;
    }
    const startInput = $('input[name="date_from"]', picker);
    const endInput = $('input[name="date_to"]', picker);
    if (action === "ledger-date-clear") {
      startInput.value = "";
      endInput.value = "";
      state.ledgerCalendar.next = "start";
      state.ledgerCalendar.error = "";
      refreshLedgerDatePicker(picker);
      return;
    }
    if (action !== "ledger-date-day") return;
    state.ledgerCalendar.error = "";
    if (state.ledgerCalendar.next === "start" || !startInput.value) {
      startInput.value = button.dataset.value;
      endInput.value = "";
      state.ledgerCalendar.next = "end";
    } else if (button.dataset.value < startInput.value) {
      state.ledgerCalendar.error = `结束时间不能早于开始时间 ${startInput.value}，请重新选择。`;
    } else {
      endInput.value = button.dataset.value;
      state.ledgerCalendar.next = "start";
    }
    refreshLedgerDatePicker(picker);
  });
  $(".ledger-date-trigger", picker)?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $(".ledger-date-popover", picker).hidden = true;
      event.currentTarget.setAttribute("aria-expanded", "false");
    }
  });
}

function bindPage(root) {
  $$('button[data-page], a[data-page]', root).forEach((button) => button.onclick = () => {
    if (button.closest("dialog")) closeDialogs();
    route(button.dataset.page);
  });
  const detailForms = [
    ["fact-filter", "ledger"],
    ["economic-filter", "economy"],
    ["detail-review-filter", "ledger-reviews"],
    ["detail-import-filter", "ledger-imports"],
  ];
  detailForms.forEach(([formName, pageId]) => {
    const form = $(`[data-form="${formName}"]`, root);
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      const params = new URLSearchParams();
      for (const [name, value] of new FormData(form)) if (value) params.set(name, value);
      route(pageId, params);
    });
  });
  $$('[data-action="detail-clear"]', root).forEach((button) => button.onclick = () => route(button.dataset.pageId));
  $$('[data-action="detail-page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params);
    params.set("page", button.dataset.value);
    route(button.dataset.pageId, params);
  });
  $$('[data-action="summary-detail"]', root).forEach((button) => button.onclick = () => showSummaryDetail(button.dataset.type));
  $$('[data-action="summary-drilldown"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params);
    params.delete("detail");
    params.delete("page");
    params.set("ledger_type", button.dataset.type);
    closeDialogs();
    route("ledger", params);
  });
  $$('[data-action="tag-view-detail"]', root).forEach((button) => button.onclick = () => showTagViewDetail(button.dataset.id));
  $$('[data-action="fact-detail"]', root).forEach((button) => button.onclick = () => showFactDetail(button.dataset.id));
  $$('[data-action="economic-detail"]', root).forEach((button) => button.onclick = () => showEconomicDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="economic-review-detail"]', root).forEach((button) => button.onclick = () => showEconomicReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="economic-review-transition"]', root).forEach((button) => button.onclick = () => transitionEconomicReview(button).catch((error) => toast(error.message, true)));
  $('[data-action="new-economic-review"]', root)?.addEventListener("click", () => openEconomicReviewEditor().catch((error) => toast(error.message, true)));
  $$('[data-ledger-row]', root).forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button,input,label,select,a")) return;
      showDetail(row.dataset.ledgerRow).catch((error) => toast(error.message, true));
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter") showDetail(row.dataset.ledgerRow).catch((error) => toast(error.message, true));
    });
  });
  $$('[data-summary-row],[data-fact-row],[data-economic-row],[data-review-row],[data-import-row],[data-tag-view-row]', root).forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button,input,label,select,a")) return;
      if (row.dataset.summaryRow) showSummaryDetail(row.dataset.summaryRow);
      if (row.dataset.factRow) showFactDetail(row.dataset.factRow);
      if (row.dataset.economicRow) showEconomicDetail(row.dataset.economicRow).catch((error) => toast(error.message, true));
      if (row.dataset.reviewRow) showEconomicReview(row.dataset.reviewRow).catch((error) => toast(error.message, true));
      if (row.dataset.importRow) showImportBatch($("[data-action='batch-rows']", row));
      if (row.dataset.tagViewRow) showTagViewDetail(row.dataset.tagViewRow);
    });
  });
  $('[data-action="review-queue"]', root)?.addEventListener("click", () => {
    const params = new URLSearchParams(state.params);
    params.set("view", "queue");
    route("reviews", params);
  });
  $$('[data-action="import-step"]', root).forEach((button) => button.onclick = () => {
    showImportStep(Number(button.dataset.step));
  });
  $$('[data-action="detail-preview"]', root).forEach((button) => button.onclick = () => {
    openPreviewDrawer(Number(button.dataset.document));
  });
  $('[data-action="reload"]', root)?.addEventListener("click", render);
  $('[data-action="clear-summary"]', root)?.addEventListener("click", () => route("summary"));
  $('[data-action="clear-ledger"]', root)?.addEventListener("click", () => route("ledger"));
  $('[data-action="clear-account-filter"]', root)?.addEventListener("click", () => {
    const params = new URLSearchParams(state.params);
    params.delete("account_code");
    route("ledger", params);
  });
  const accountFilter = $('[data-form="account-filter"]', root);
  accountFilter?.addEventListener("change", () => {
    const data = new FormData(accountFilter);
    const params = new URLSearchParams();
    const range = monthBounds(state.accountMonth);
    params.set("month", `${range.year}-${String(range.month + 1).padStart(2, "0")}`);
    for (const [name, value] of data) if (value) params.set(name, value);
    route("summary", params);
  });
  $$('[data-action="account-month"]', root).forEach((button) => button.onclick = () => {
    state.accountMonth = new Date(
      state.accountMonth.getFullYear(),
      state.accountMonth.getMonth() + Number(button.dataset.value),
      1,
    );
    const params = new URLSearchParams(state.params);
    params.set("month", `${state.accountMonth.getFullYear()}-${String(state.accountMonth.getMonth() + 1).padStart(2, "0")}`);
    route("summary", params);
  });
  $('[data-action="account-current"]', root)?.addEventListener("click", () => {
    state.accountMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    const params = new URLSearchParams(state.params);
    params.set("month", `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
    route("summary", params);
  });
  $$('[data-action="account-metric"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams());
  });
  $$('[data-action="account-type"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams({ ledgerType: button.dataset.value }));
  });
  $$('[data-action="account-day"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams({ day: button.dataset.value }));
  });
  $('[data-action="account-drilldown"]', root)?.addEventListener("click", () => {
    route("economy", accountLedgerParams());
  });
  $$('[data-action="page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params); params.set("page", button.dataset.value); route("ledger", params);
  });
  $('[data-action="ledger-page-size"]', root)?.addEventListener("change", (event) => {
    const params = new URLSearchParams(state.params);
    params.set("page", "1");
    params.set("page_size", event.currentTarget.value);
    route("ledger", params);
  });
  $$('[data-action="review-page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params); params.set(button.dataset.param || "review_page", button.dataset.value); route("reviews", params);
  });
  $$('[data-action="history-page"]', root).forEach((button) => button.onclick = () => {
    const form = $('[data-form="history-filter"]');
    if (form) refreshHistoryResults(form, Number(button.dataset.value));
  });
  $$('[data-action="detail"]', root).forEach((button) => button.onclick = () => showDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="ledger-select"]', root).forEach((input) => input.onchange = async () => {
    const id = Number(input.dataset.id);
    if (input.checked) state.selectedLedgers.set(id, { id });
    else state.selectedLedgers.delete(id);
    await render();
  });
  $$('[data-action="clear-ledger-selection"]', root).forEach((button) => button.onclick = async () => {
    state.selectedLedgers.clear();
    await render();
  });
  $$('[data-action="review-selected"]', root).forEach((button) => button.onclick = () => openReviewWizard().catch((error) => toast(error.message, true)));
  $$('[data-action="review-detail"]', root).forEach((button) => button.onclick = () => showReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-review"]', root).forEach((button) => button.onclick = () => editReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-tags"]', root).forEach((button) => button.onclick = () => editTags(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="account"]', root).forEach((button) => button.onclick = () => editAccount(button));
  $('[data-action="new-review"]', root)?.addEventListener("click", () => newReview().catch((error) => toast(error.message, true)));
  $('[data-action="new-view"]', root)?.addEventListener("click", () => simpleDictionaryDialog("view"));
  $$('[data-action="new-tag"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="new-tag-inline"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="cancel-tag"]', root).forEach((button) => button.onclick = () => closeInlineTag(button.closest("form")));
  $$('[data-action="view-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("view", button).catch((error) => toast(error.message, true)));
  $$('[data-action="tag-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("tag", button).catch((error) => toast(error.message, true)));
  $$('[data-action="review-transition"]', root).forEach((button) => button.onclick = () => transitionReview(button).catch((error) => toast(error.message, true)));
  $$('[data-action="account-transition"]', root).forEach((button) => button.onclick = () => transitionAccount(button).catch((error) => toast(error.message, true)));
  $$('[data-action="conflict-transition"]', root).forEach((button) => button.onclick = () => transitionConflict(button).catch((error) => toast(error.message, true)));
  $$('[data-action="conflict-resolve"]', root).forEach((button) => button.onclick = () => conflictDialog(button));
  $$('[data-action="batch-rows"]', root).forEach((button) => button.onclick = () => showImportBatch(button));
  const importForm = $('[data-form="import-preview"]', root);
  if (importForm) {
    $$('[data-action="import-source"]', importForm).forEach((button) => button.addEventListener("click", () => {
      clearImportPlan();
      importForm.elements.source_type.value = button.dataset.value;
      const selectedSource = $('[data-selected-source]', importForm);
      if (selectedSource) selectedSource.textContent = $("strong", button).textContent;
      $$('[data-action="import-source"]', importForm).forEach((card) => {
        const selected = card === button;
        card.classList.toggle("selected", selected);
        card.setAttribute("aria-pressed", String(selected));
      });
    }));
    importForm.elements.files.addEventListener("change", () => {
      clearImportPlan();
      updateSelectedFiles(importForm);
    });
    importForm.addEventListener("click", (event) => {
      const remove = event.target.closest('[data-action="remove-import-file"]');
      if (!remove) return;
      const transfer = new DataTransfer();
      [...importForm.elements.files.files].forEach((file, index) => {
        if (index !== Number(remove.dataset.index)) transfer.items.add(file);
      });
      importForm.elements.files.files = transfer.files;
      clearImportPlan();
      updateSelectedFiles(importForm);
    });
    const dropzone = $('[data-import-dropzone]', importForm);
    ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    }));
    dropzone.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.files.length) return;
      importForm.elements.files.files = event.dataTransfer.files;
      clearImportPlan();
      updateSelectedFiles(importForm);
    });
  }
  $('[data-raw-plan]', root)?.addEventListener("toggle", (event) => {
    const details = event.currentTarget;
    if (!details.open || details.dataset.loaded === "true") return;
    details.dataset.loaded = "true";
    requestAnimationFrame(() => {
      if (!details.open) {
        delete details.dataset.loaded;
        return;
      }
      const content = $('[data-raw-plan-content]', details);
      if (!content) return;
      const pre = document.createElement("pre");
      pre.className = "raw-json";
      pre.textContent = JSON.stringify(state.importPlan?.documents || state.importPlan || {}, null, 2);
      content.replaceWith(pre);
    });
  });
  $('[data-action="confirm-import"]', root)?.addEventListener("click", confirmImport);
  $('[data-form="summary-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); route("summary", new URLSearchParams([...data].filter(([, value]) => value))); });
  $('[data-form="ledger-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("page", "1"); route("ledger", params); });
  $('[data-form="review-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("review_page", "1"); params.set("view", "queue"); route("reviews", params); });
  const historyFilter = $('[data-form="history-filter"]', root);
  historyFilter?.addEventListener("submit", (event) => {
    event.preventDefault();
    refreshHistoryResults(event.currentTarget, 1);
  });
  historyFilter?.elements.q.addEventListener("input", (event) => {
    if (!event.isComposing) scheduleHistoryRefresh(historyFilter);
  });
  historyFilter?.elements.q.addEventListener("compositionend", () => scheduleHistoryRefresh(historyFilter));
  historyFilter?.elements.account.addEventListener("change", () => scheduleHistoryRefresh(historyFilter));
  importForm?.addEventListener("submit", async (event) => { event.preventDefault(); try { await previewImport(event.currentTarget); } catch (error) { showFormError(event.currentTarget, error); } });
  $('[data-form="import-revise"]', root)?.addEventListener("submit", reviseImport);
  $('[data-form="tag-assignment"]', root)?.addEventListener("submit", submitTags);
  $$('[data-form="inline-tag"]', root).forEach((form) => {
    const nameInput = $('[name="name"]', form);
    const systemInput = $('[name="system_name"]', form);
    nameInput?.addEventListener("input", () => {
      if (!systemInput.dataset.manual) systemInput.value = tagSystemName(nameInput.value);
    });
    systemInput?.addEventListener("input", () => { systemInput.dataset.manual = "true"; });
    form.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeInlineTag(form);
      }
    });
    form.addEventListener("submit", submitInlineTag);
  });
  $('[data-form="account"]', root)?.addEventListener("submit", submitAccount);
  $('[data-form="dictionary"]', root)?.addEventListener("submit", submitDictionary);
  $('[data-form="edit-review"]', root)?.addEventListener("submit", submitReviewUpdate);
  $('[data-form="conflict"]', root)?.addEventListener("submit", submitConflict);
  $$('form[data-form]', root).forEach(bindCommandForm);
  bindLedgerDatePicker($(".ledger-date-picker", root));
}

async function submitTags(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  const reason = data.get("reason"); data.delete("reason");
  try {
    await jsonRequest(`/paam/tag/v1/assignment/set/${form.dataset.ledger}`, "PUT", { tag_state: Object.fromEntries(data), expected_version: Number(form.dataset.version), reason, idempotency_key: idempotencyKey });
    closeDialogs(); toast("标签已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function submitAccount(event) {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  try {
    await jsonRequest(`/paam/review/v1/account/set/${form.dataset.fact}`, "PUT", { ...data, expected_projection_version: Number(form.dataset.version), idempotency_key: idempotencyKey });
    closeDialogs(); toast("账户修正已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function confirmImport() {
  if (!state.importPlan || state.confirmingImport) return;
  state.confirmingImport = true;
  const button = $('[data-action="confirm-import"]');
  if (button) button.disabled = true;
  try { const result = await jsonRequest(`/paam/import/v1/preview/confirm/${state.importPlan.token}`, "POST", { version: state.importPlan.version }); state.importPlan = null; toast(`导入完成：${result.bill_fact_ids?.length || 0} 条新事实`); route("import-history"); } catch (error) { if (button) button.disabled = false; const form = button?.closest("form"); if (form) showFormError(form, error); else toast(error.message, true); }
  finally { state.confirmingImport = false; }
}
function simpleDictionaryDialog(kind, viewId = "") {
  const dialog = modal(kind === "view" ? "新建标签维度" : "新增标签", `<form data-form="dictionary" data-kind="${kind}" data-view="${viewId}" class="stack"><label>显示名称<input name="name" required maxlength="120"></label><label>系统名称<input name="system_name" required pattern="[a-z][a-z0-9_]{0,63}" placeholder="lower_case_name"></label><div class="actions"><button class="primary">保存</button></div></form>`, false); bindPage(dialog);
}
async function submitInlineTag(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/tag/v1/tag/create/${form.dataset.view}`, "POST", payload);
    toast(`标签“${payload.name}”已添加`);
    await render();
  } catch (error) {
    endSubmit(form);
    showFormError(form, error);
  }
}
async function submitDictionary(event) {
  event.preventDefault(); const form = event.currentTarget; const payload = Object.fromEntries(new FormData(form));
  if (!beginSubmit(form)) return;
  const url = form.dataset.kind === "view" ? "/paam/tag/v1/view/create" : `/paam/tag/v1/tag/create/${form.dataset.view}`;
  try { await jsonRequest(url, "POST", payload); form.closest("dialog").close(); toast("标签定义已保存"); await render(); } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function dictionaryStatus(kind, button) {
  if (button.disabled) return;
  button.disabled = true;
  const url = kind === "view" ? `/paam/tag/v1/view/status/${button.dataset.id}` : `/paam/tag/v1/tag/status/${button.dataset.view}/${button.dataset.id}`;
  try { await jsonRequest(url, "PUT", { status: button.dataset.status }); toast("状态已更新"); await render(); }
  catch (error) { button.disabled = false; throw error; }
}
async function submitReviewUpdate(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  try {
    const lines = $$(".review-edit-line", form).map((row) => ({
      bill_id: Number(row.dataset.fact),
      role: data.get(`role-${row.dataset.fact}`),
      amount_value: decimalAmount(data.get(`amount-${row.dataset.fact}`), Number(row.dataset.scale)),
      party: data.get(`party-${row.dataset.fact}`) || "",
    }));
    await jsonRequest(`/paam/review/v1/case/update/${form.dataset.id}`, "PUT", { expected_version: Number(form.dataset.version), title: data.get("title"), result: form.reviewResult || {}, lines, reason: data.get("reason"), idempotency_key: idempotencyKey });
    closeDialogs(); toast("待确认 Review 已修订"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function transitionReview(button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await jsonRequest(`/paam/review/v1/case/${button.dataset.kind}/${button.dataset.id}`, "POST", { expected_version: Number(button.dataset.version), reason: `用户${button.dataset.kind}审查`, idempotency_key: key() });
    closeDialogs(); toast("Review 状态已更新"); await render();
  } catch (error) { button.disabled = false; throw error; }
}
async function transitionAccount(button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await jsonRequest(`/paam/review/v1/account/${button.dataset.kind}/${button.dataset.id}`, "POST", { expected_version: Number(button.dataset.version), reason: "用户更新账户修正", idempotency_key: key() });
    closeDialogs(); toast("账户 Review 已更新"); await render();
  } catch (error) { button.disabled = false; throw error; }
}
async function transitionConflict(button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await jsonRequest(`/paam/review/v1/fact-conflict/${button.dataset.kind}/${button.dataset.id}`, "POST", { expected_version: Number(button.dataset.version), reason: "用户处理事实冲突", idempotency_key: key() });
    closeDialogs(); toast("冲突状态已更新"); await render();
  } catch (error) { button.disabled = false; throw error; }
}
function conflictDialog(button) {
  const dialog = modal("解决事实冲突", `<form data-form="conflict" data-id="${button.dataset.id}" data-version="${button.dataset.version}" class="stack"><label>处理方式<select name="resolution_type"><option value="LINK_EXISTING">关联已有 Fact</option><option value="CREATE_NEW">确认为新 Fact</option></select></label><label>已有 Fact ID（创建新 Fact 时留空）<input type="number" min="1" name="existing_bill_id"></label><label>说明<input name="reason" value="人工核对事实冲突"></label><div class="actions"><button class="primary">确认解决</button></div></form>`, false); bindPage(dialog);
}
async function submitConflict(event) {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  data.existing_bill_id = data.existing_bill_id ? Number(data.existing_bill_id) : 0;
  try { await jsonRequest(`/paam/review/v1/fact-conflict/resolve/${form.dataset.id}`, "POST", { ...data, expected_version: Number(form.dataset.version), idempotency_key: idempotencyKey }); closeDialogs(); toast("事实冲突已解决"); await render(); } catch (error) { endSubmit(form); showFormError(form, error); }
}

$$('[data-page]').forEach((button) => button.onclick = () => route(button.dataset.page));
if (!location.hash) route("ledger"); else readRoute();

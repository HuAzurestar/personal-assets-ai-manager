import { checkConnection, request, jsonRequest } from "../api/client.js?v=20260921.2";
import { toast } from "../component/toast.js";
import { table } from "../component/table.js";
import { openInspection } from "../component/inspection.js?v=20260921.4";
import {
  detailList, detailPager,
} from "../component/detail.js?v=20260917.10";
import {
  bindDateTimeRanges, dateTimeRangeControl,
} from "../component/date-time-range.js?v=20260921.1";
import { state } from "../state/ledger.js";
import {
  $, $$, currencyPrecision, date, decimalAmount, esc, key, money,
  reviewTypeNames, roleNames, statusNames, typeNames,
  selectedCalendarDate, selectedImportTimeZone, selectedTimeZone,
  setSelectedImportTimeZone, setSelectedTimeZone, zonedISOString,
} from "../util/core.js";
import {
  canonicalHash, parseHash, shellMarkup, syncNavigation,
} from "../navigation.js?v=20260921.3";
import {
  accountsMarkup, cursorFromParam, monthBounds,
} from "./account.js?v=20260917.10";

const entryTypeValues = { TRANSACTION: 0, ACCOUNT_TRANSFER: 1, CLAIM: 2 };
const entryTypeCodes = { 0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM" };
const reviewBehaviorNames = { 0: "事实交易", 1: "借款与还款" };
const timeZoneNames = {
  "Asia/Hong_Kong": "香港",
  "Asia/Shanghai": "上海",
  "Asia/Tokyo": "东京",
  "Europe/London": "伦敦",
  "America/New_York": "纽约",
  UTC: "UTC",
};

function timeZoneName(value) {
  return timeZoneNames[value] ? `${timeZoneNames[value]}（${value}）` : value;
}

function currentAccountMonth() {
  const { year, month } = selectedCalendarDate();
  return new Date(year, month - 1, 1);
}

function closeDialogs() {
  $$('dialog[open]').forEach((dialog) => dialog.close());
}
function amountValue(item) {
  return Number(item?.amount || 0);
}

function signedMoney(item, direction) {
  if (!amountValue(item)) return "0";
  return `${direction === "IN" || direction === 1 ? "＋" : "−"} ${money(item)}`;
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
checkConnection();
window.addEventListener("focus", checkConnection);
window.setInterval(() => {
  if (!document.hidden) checkConnection();
}, 30_000);
const timezoneSelect = $('[data-timezone]');
timezoneSelect.value = selectedTimeZone();
timezoneSelect.addEventListener("change", () => {
  setSelectedTimeZone(timezoneSelect.value);
  if (state.page === "summary" && !state.params.has("month")) {
    state.accountMonth = currentAccountMonth();
  }
  render();
});

const pageInfo = {
  economy: ["明细", "查看最终经济流水，并追溯对应的审查、分配关系与事实。"],
  "ledger-reviews": ["明细", "查看事实如何通过审查和 Allocation 形成经济流水。"],
  "ledger-imports": ["明细", "在统一列表中追溯导入文件、原始行和处理结果。"],
  "ledger-tags": ["明细", "在统一列表中查看分类维度、标签值和启用状态。"],
  summary: ["概览", "基于 Ledger Summary 查看月度收支、趋势和账本活动；具体流水继续回到“明细”查看。"],
  ledger: ["明细", "查看导入后不可变的事实流水；最终结果请切换到经济明细。"],
  import: ["导入 / 上传", "选择来源、添加文件，并在写入账本前逐项核对。"],
  "import-history": ["导入记录", "查找已经写入的文件、处理结果和原始行。"],
  reviews: ["账单审查", "选择待审查事实，配置 Fact 与 Ledger 的关系，预览后生成账本流水。"],
};
const validPages = new Set(Object.keys(pageInfo));

function route(page, params = new URLSearchParams()) {
  location.hash = canonicalHash(page, params);
}
function readRoute() {
  const { page: nextPage, params } = parseHash(location.hash, validPages);
  const canonical = canonicalHash(nextPage, params);
  if (location.hash.slice(1) !== canonical) history.replaceState(null, "", `#${canonical}`);
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
  $(".module-heading").hidden = true;
  $("#content").classList.add("compact-content");
  syncNavigation(page);
  const secondaryNavigation = $("#secondary-nav");
  if (secondaryNavigation) bindPage(secondaryNavigation);
  renderPageActions();
  root.innerHTML = '<div class="busy">正在加载…</div>';
  try {
    const content = await ({
      economy: economicPage,
      "ledger-reviews": ledgerReviewsPage,
      "ledger-imports": ledgerImportsPage,
      "ledger-tags": ledgerTagsPage,
      summary: summaryPage,
      ledger: ledgerPage,
      import: importPage,
      "import-history": importHistoryPage,
      reviews: reviewCreatePage,
    })[page]();
    if (renderVersion !== state.renderVersion || page !== state.page) return;
    root.innerHTML = content;
    bindPage(root);
    if (page === "import") renderImportPlan();
    if (page === "reviews") await mountEconomicReviewEditor(root);
  } catch (error) {
    if (renderVersion !== state.renderVersion || page !== state.page) return;
    root.innerHTML = `<section class="panel"><div class="error">${esc(error.message)}</div><div class="actions"><button data-action="reload">重新加载</button></div></section>`;
    bindPage(root);
  }
}

function renderPageActions() {
  const target = $("#page-actions");
  target.innerHTML = "";
  bindPage(target);
}

async function summaryPage() {
  state.accountMonth = cursorFromParam(
    state.params.get("month"),
    state.accountMonth || currentAccountMonth(),
  );
  const range = monthBounds(state.accountMonth);
  const economicSummary = await request(`/paam/ledger/v1/flow/summary?${new URLSearchParams({ date_from: range.from, date_to: range.to, timezone: selectedTimeZone() })}`);
  const summary = {
    entry_count: economicSummary.entry_count,
    provisional_count: 0,
    totals: economicSummary.totals.map((item) => ({
      ...item,
      income_amount: item.income_and_expense_in_amount,
      expense_amount: item.income_and_expense_out_amount,
      refund_offset_amount: 0,
      net_amount: item.income_and_expense_in_amount - item.income_and_expense_out_amount,
    })),
    trend: economicSummary.trend,
    activities: economicSummary.activities,
  };
  return accountsMarkup({
    summary,
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
  const entryTypeCode = entryTypeCodes[extra.entryTypeCode] || extra.entryTypeCode;
  if (entryTypeCode in entryTypeValues) params.set("economic_type", entryTypeCode);
  const entryDirection = Number(extra.entryDirection);
  if ([1, 2].includes(entryDirection)) params.set("entry_direction", String(entryDirection));
  return params;
}

function detailListView(options) {
  return detailList(options);
}

function filterBoundary(value, exclusiveEnd = false) {
  return zonedISOString(value, exclusiveEnd);
}

const commonCurrencies = ["CNY", "USD", "HKD", "EUR", "GBP", "JPY"];

function currencySelect(selected) {
  const currencies = [...new Set([selected, ...commonCurrencies].filter(Boolean))];
  return `<select name="currency_code"><option value="">全部币种</option>${currencies.map((currency) => `<option value="${esc(currency)}" ${selected === currency ? "selected" : ""}>${esc(currency)}</option>`).join("")}</select>`;
}

function directionSelect(name, selected) {
  return `<select name="${name}"><option value="">全部方向</option><option value="1" ${selected === "1" ? "selected" : ""}>收入 / 流入</option><option value="2" ${selected === "2" ? "selected" : ""}>支出 / 流出</option></select>`;
}

function sortPresetSelect(sortField, sortOrder) {
  const selected = `${sortField}.${sortOrder}`;
  const options = [
    ["occurred_time.desc", "时间：最新优先"],
    ["occurred_time.asc", "时间：最早优先"],
    ["amount.desc", "金额：从高到低"],
    ["amount.asc", "金额：从低到高"],
  ];
  return `<select name="sort">${options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("")}</select>`;
}

function reviewSortSelect(sortField, sortOrder) {
  const selected = `${sortField}.${sortOrder}`;
  const options = [
    ["updated_time.desc", "更新时间：最新优先"],
    ["updated_time.asc", "更新时间：最早优先"],
    ["created_time.desc", "创建时间：最新优先"],
    ["created_time.asc", "创建时间：最早优先"],
  ];
  return `<select name="sort">${options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("")}</select>`;
}

function importSortSelect(sortField, sortOrder) {
  const selected = `${sortField}.${sortOrder}`;
  const options = [
    ["created_time.desc", "导入时间：最新优先"],
    ["created_time.asc", "导入时间：最早优先"],
    ["updated_time.desc", "更新时间：最新优先"],
    ["updated_time.asc", "更新时间：最早优先"],
    ["id.desc", "编号：从新到旧"],
    ["id.asc", "编号：从旧到新"],
  ];
  return `<select name="sort">${options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("")}</select>`;
}

function compactAmount(item, direction) {
  const code = String(item.currency_code || "").toUpperCase();
  const precision = currencyPrecision(code);
  const value = Number(item.amount) / (10 ** precision);
  const amount = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  }).format(value);
  return `${direction === 1 ? "+" : "−"} ${amount} ${code.split("_", 1)[0]}`;
}

function compactAccount(value) {
  const code = String(value || "");
  return code.length > 22 ? `${code.slice(0, 10)}…${code.slice(-8)}` : code;
}

async function ledgerPage() {
  const currency = (state.params.get("currency_code") || "").trim().toUpperCase();
  const direction = state.params.get("cash_direction") || "";
  const dateFrom = state.params.get("date_from") || "";
  const dateTo = state.params.get("date_to") || "";
  const page = Math.max(1, Number(state.params.get("page") || 1));
  const pageSize = Math.min(100, Math.max(1, Number(state.params.get("page_size") || 20)));
  const sortField = state.params.get("sort_field") || "occurred_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const expressions = [];
  if (currency) expressions.push({ key: "currency_code", op: "=", val: currency });
  if (["1", "2"].includes(direction)) expressions.push({ key: "cash_direction", op: "=", val: Number(direction) });
  if (dateFrom) expressions.push({ key: "occurred_time", op: ">=", val: filterBoundary(dateFrom) });
  if (dateTo) expressions.push({ key: "occurred_time", op: "<", val: filterBoundary(dateTo, true) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: String(page),
    page_size: String(pageSize),
    sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  const result = await request(`/paam/ledger/v1/transaction_fact/list?${query}`);
  const rows = result.items.map((fact) => `<tr class="detail-click-row" tabindex="0" data-fact-row="${fact.id}">
    <td data-label="摘要"><button type="button" class="detail-primary" data-action="fact-detail" data-id="${fact.id}" data-inspect-title="${esc(fact.summary || "未填写摘要")}" data-inspect-amount="${esc(compactAmount(fact, fact.cash_direction))}"><strong>${esc(fact.summary || "未填写摘要")}</strong><small>Fact #${fact.id}</small></button></td><td data-label="交易对手">${esc(fact.counterparty_name || "—")}</td><td data-label="本方账户" class="mono fact-account" title="${esc(fact.account_code)}">${esc(compactAccount(fact.account_code))}</td><td data-label="金额" class="fact-amount ${fact.cash_direction === 1 ? "inflow" : "outflow"}">${esc(compactAmount(fact, fact.cash_direction))}</td><td data-label="发生时间">${date(fact.occurred_time)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="fact-filter"><label>收支方向${directionSelect("cash_direction", direction)}</label><label>币种${currencySelect(currency)}</label>${dateTimeRangeControl(dateFrom, dateTo)}<label class="grow">排序${sortPresetSelect(sortField, sortOrder)}</label><div class="detail-filter-actions"><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger">清空</button></div></form>`;
  return detailListView({ active: "ledger", toolbar, title: "Transaction Fact", description: "外部账单接受后的不可变事实 PO；查找、筛选与排序均由服务端执行。", total: result.total, headers: ["摘要", "交易对手", "本方账户", "金额", "发生时间", ""], rows, footer: detailPager(result, "ledger") });
}

async function showFactDetail(id) {
  return openInspection("fact", id, bindPage);
}

async function economicPage() {
  const selectedType = state.params.get("economic_type") || "";
  const active = state.params.get("active") || "";
  const currency = (state.params.get("currency_code") || "").trim().toUpperCase();
  const direction = state.params.get("entry_direction") || "";
  const dateFrom = state.params.get("date_from") || "";
  const dateTo = state.params.get("date_to") || "";
  const sortField = state.params.get("sort_field") || "occurred_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const expressions = [];
  if (selectedType in entryTypeValues) expressions.push({ key: "entry_type", op: "=", val: entryTypeValues[selectedType] });
  if (["true", "false"].includes(active)) expressions.push({ key: "active", op: "=", val: active === "true" });
  if (currency) expressions.push({ key: "currency_code", op: "=", val: currency });
  if (["1", "2"].includes(direction)) expressions.push({ key: "entry_direction", op: "=", val: Number(direction) });
  if (dateFrom) expressions.push({ key: "occurred_time", op: ">=", val: filterBoundary(dateFrom) });
  if (dateTo) expressions.push({ key: "occurred_time", op: "<", val: filterBoundary(dateTo, true) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  const result = await request(`/paam/ledger/v1/flow/list?${query}`);
  state.detailEconomics = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => {
    const tags = (item.tags || []).filter((tag) => tag.tag_system_name !== "unclassified");
    const tagMarkup = tags.length ? `<small class="ledger-row-tags">${tags.map((tag) => `<span title="${esc(tag.view_name)}">${esc(tag.tag_name)}</span>`).join("")}</small>` : "";
    return `<tr class="detail-click-row" tabindex="0" data-economic-row="${item.id}">
    <td data-label="摘要"><button type="button" class="detail-primary" data-action="economic-detail" data-id="${item.id}" data-inspect-title="${esc(item.summary || "未填写摘要")}" data-inspect-amount="${esc(compactAmount(item, item.entry_direction))}"><strong>${esc(item.summary || "未填写摘要")}</strong><small>Ledger #${item.id}</small>${tagMarkup}</button></td><td data-label="有效状态"><span class="badge ${item.active ? "" : "warn"}">${item.active ? "有效" : "已停用"}</span></td><td data-label="对手账户" title="${esc(item.counterparty_account_ref || "")}">${esc(compactAccount(item.counterparty_account_ref) || "—")}</td><td data-label="本方账户" class="mono fact-account" title="${esc(item.account_code)}">${esc(compactAccount(item.account_code))}</td><td data-label="金额" class="fact-amount ${item.entry_direction === 1 ? "inflow" : "outflow"}">${esc(compactAmount(item, item.entry_direction))}</td><td data-label="发生时间">${date(item.occurred_time)}</td><td class="detail-arrow">→</td>
  </tr>`;
  }).join("");
  const toolbar = `<form class="detail-filter ledger-detail-filter" data-form="economic-filter"><label>账本类型<select name="economic_type"><option value="">全部类型</option>${Object.keys(entryTypeValues).map((value) => `<option value="${value}" ${selectedType === value ? "selected" : ""}>${esc(typeNames[value] || value)}</option>`).join("")}</select></label><label>有效状态<select name="active"><option value="">全部状态</option><option value="true" ${active === "true" ? "selected" : ""}>有效</option><option value="false" ${active === "false" ? "selected" : ""}>已停用</option></select></label><label>收支方向${directionSelect("entry_direction", direction)}</label><label>币种${currencySelect(currency)}</label>${dateTimeRangeControl(dateFrom, dateTo)}<label class="grow">排序${sortPresetSelect(sortField, sortOrder)}</label><div class="detail-filter-actions"><button type="button" class="quiet" data-action="detail-clear" data-page-id="economy">清空</button></div></form>`;
  return detailListView({ active: "economy", toolbar, title: "Ledger", description: "数据库中的 Ledger PO；摘要来自关联 Fact，行为类型与有效状态来自创建它的 Review。", total: result.total, headers: ["摘要", "有效状态", "对手账户", "本方账户", "金额", "发生时间", ""], rows, footer: detailPager(result, "economy") });
}

async function showEconomicDetail(id) {
  return openInspection("ledger", id, bindPage);
}

function showSummaryDetail(type) {
  const item = state.detailSummaries.get(type);
  if (!item) return;
  const incoming = money({ amount: item.in_amount, currency_code: item.currency_code });
  const outgoing = money({ amount: item.out_amount, currency_code: item.currency_code });
  detailDrawer({
    title: typeNames[item.entry_type_code] || item.entry_type_code,
    kicker: `ENTRY TYPE · ${item.entry_type_code}`,
    subtitle: "查看当前业务类型的收支构成与核算口径。",
    body: `<div class="cards drawer-metrics"><div class="metric"><span>流入</span><strong>${incoming}</strong></div><div class="metric"><span>流出</span><strong>${outgoing}</strong></div></div><section class="drawer-section"><h3>核算说明</h3><p>${item.nettable ? "该业务允许在同一币种内按流入与流出核算。" : "该业务保留资金活动原貌，不进行跨业务净额化。"}</p></section>`,
    footer: `<button type="button" class="primary" data-action="summary-drilldown" data-type="${esc(item.entry_type_code)}">查看对应流水</button>`,
  });
}

async function ledgerReviewsPage() {
  const status = state.params.get("status") || "";
  const behaviorType = state.params.get("behavior_type") || "";
  const sortField = state.params.get("sort_field") || "updated_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const query = new URLSearchParams({
    page_index: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]),
  });
  const expressions = [];
  if (status !== "") expressions.push({ key: "status", op: "=", val: Number(status) });
  if (behaviorType !== "") expressions.push({ key: "behavior_type", op: "=", val: Number(behaviorType) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  if (filter) query.set("filter", JSON.stringify(filter));
  const result = await request(`/paam/ledger/v1/review/list?${query}`);
  state.detailEconomicReviews = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-review-row="${item.id}">
    <td data-label="审查标题"><button type="button" class="detail-primary" data-action="economic-review-detail" data-id="${item.id}" data-inspect-title="${esc(item.title || `审查 #${item.id}`)}" data-inspect-meta="${esc(`${reviewBehaviorNames[item.behavior_type] || `未知类型（${item.behavior_type}）`} · ${statusNames[item.status] || item.status}`)}"><strong>${esc(item.title || `审查 #${item.id}`)}</strong><small>Review #${item.id}</small></button></td><td data-label="类型">${esc(reviewBehaviorNames[item.behavior_type] || `未知类型（${item.behavior_type}）`)}</td>
    <td data-label="状态"><span class="badge ${item.status === 1 ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td data-label="更新时间">${date(item.updated_time)}</td><td data-label="创建时间">${date(item.created_time)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="detail-review-filter"><label>状态<select name="status"><option value="">全部状态</option>${[0, 1].map((value) => `<option value="${value}" ${status === String(value) ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><label>类型<select name="behavior_type"><option value="">全部类型</option>${[0, 1].map((value) => `<option value="${value}" ${behaviorType === String(value) ? "selected" : ""}>${esc(reviewBehaviorNames[value])}</option>`).join("")}</select></label><label class="grow">排序${reviewSortSelect(sortField, sortOrder)}</label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-reviews">清空</button><button type="button" class="primary" data-page="reviews">创建审查</button></form>`;
  return detailListView({ active: "ledger-reviews", toolbar, title: "Review", description: "每行对应数据库中的一个 Review Case。", total: result.total, headers: ["审查标题", "类型", "状态", "更新时间", "创建时间", ""], rows, footer: detailPager(result, "ledger-reviews") });
}

async function showEconomicReview(id) {
  return openInspection("review", id, bindPage);
}

async function transitionEconomicReview(button) {
  const labels = { confirm: "确认", revoke: "撤销", restore: "恢复" };
  if (!confirm(`${labels[button.dataset.kind]}这次经济审查？`)) return;
  await jsonRequest(`/paam/ledger/v1/review/${button.dataset.id}/${button.dataset.kind}`, "POST", { reason: `人工${labels[button.dataset.kind]}经济审查`, idempotency_key: key() });
  closeDialogs();
  toast(`${labels[button.dataset.kind]}完成，经济流水已重新投影`);
  await render();
}

function reviewCreatePage() {
  return '<section class="review-workflow-host" data-review-workflow><div class="busy">正在加载可审查流水…</div></section>';
}

async function mountEconomicReviewEditor(root) {
  const firstPage = await request("/paam/ledger/v1/review_candidate/list?page_index=1&page_size=20");
  if (!firstPage.total) throw new Error("当前没有可审查的流水");
  const facts = new Map(firstPage.items.map((fact) => [fact.id, fact]));
  const selected = new Set();
  const ledgers = [];
  let candidatePage = firstPage;
  let behaviorCode = "TRANSACTION";
  let step = 1;
  let sequence = 0;
  const host = $("[data-review-workflow]", root);
  host.innerHTML = `<section class="review-workflow import-workflow"><div class="review-workflow-head"><div><span class="eyebrow">REVIEW WORKFLOW</span><h2>创建审查</h2><p>按三步完成事实选择、账本关系配置和结果检查。</p></div><button type="button" class="quiet" data-page="ledger-reviews">查看审查记录</button></div><form data-form="economic-review-create" class="review-wizard">
    <nav class="review-stepper" aria-label="审查步骤">
      <button type="button" class="active" data-review-step="1"><span>1</span><strong>选择范围</strong><small>流水与审查类型</small></button>
      <button type="button" data-review-step="2"><span>2</span><strong>配置关系</strong><small>Fact → Ledger</small></button>
      <button type="button" data-review-step="3"><span>3</span><strong>检查并生成</strong><small>预览最终结果</small></button>
    </nav>
    <section class="review-step-panel" data-review-panel="1">
      <h3>选择待审查流水</h3><p>先确定要重新解释的流水，再选择本次审查的业务类型。</p>
      <div class="review-type-grid" role="radiogroup" aria-label="审查类型">
        <label><input type="radio" name="behavior_code" value="TRANSACTION" checked><span><strong>事实交易</strong><small>普通收支、拆分和归类调整</small></span></label>
        <label><input type="radio" name="behavior_code" value="BORROW_AND_REPAY"><span><strong>借款与还款</strong><small>借入、借出、偿还和收回</small></span></label>
      </div>
      <div class="detail-filter review-candidate-filter" data-review-candidate-filter><label>收支方向${directionSelect("cash_direction", "")}</label><label>币种${currencySelect("")}</label>${dateTimeRangeControl("", "")}<label class="grow">排序<select name="sort"><option value="occurred_time.desc">时间：最新优先</option><option value="occurred_time.asc">时间：最早优先</option></select></label><button type="button" class="quiet" data-action="review-candidate-clear">清空</button></div>
      <div class="review-candidate-toolbar"><span>从可分配事实中选择</span><span data-review-selection-count></span></div>
      <div data-economic-review-facts class="review-candidate-list"></div>
      <div class="review-candidate-pager" data-review-candidate-pager></div>
    </section>
    <section class="review-step-panel" data-review-panel="2" hidden>
      <div class="section-head"><div><h3>配置 Fact → Ledger</h3><p>每条 Ledger 只能来源于一个 Fact；同一个 Fact 可以拆成多条 Ledger。</p></div><button type="button" data-action="add-economic">＋ 添加 Ledger</button></div>
      <div data-economic-definitions class="review-ledger-list"></div>
      <div class="review-notice">未分配的剩余金额会继续保留为默认事实交易，不会丢失。</div>
    </section>
    <section class="review-step-panel" data-review-panel="3" hidden>
      <h3>检查并生成</h3><p>确认审查类型、Fact 覆盖和 Ledger 结果；提交后会直接生成最终账本流水。</p>
      <div data-review-preview class="review-summary-grid"></div>
      <div class="review-description-grid">
        <label class="full">审查说明<textarea name="title" maxlength="160" placeholder="例如：将本次付款拆分为两条事实交易"></textarea></label>
        <label class="full">操作原因<input name="reason" maxlength="2000" placeholder="可选，用于审计记录"></label>
      </div>
    </section>
    <footer class="review-wizard-footer"><button type="button" data-action="review-back" hidden>← 上一步</button><button type="button" data-page="ledger-reviews">取消</button><div><strong data-review-footer-title>选择范围</strong><small data-review-footer-help>选择至少一条待审查流水</small></div><button type="button" class="primary" data-action="review-next">下一步：配置关系 →</button><button type="submit" class="primary" data-action="review-submit" hidden>确认并生成账本流水</button></footer>
  </form></section>`;
  bindPage(host);
  const form = $('[data-form="economic-review-create"]', host);
  const factRoot = $("[data-economic-review-facts]", form);
  const candidateFilter = $("[data-review-candidate-filter]", form);
  const definitionRoot = $("[data-economic-definitions]", form);
  const previewRoot = $("[data-review-preview]", form);
  const defaultEconomicType = () => behaviorCode === "BORROW_AND_REPAY" ? "CLAIM" : "TRANSACTION";
  const decimalText = (fact) => (Number(fact.available_amount) / (10 ** currencyPrecision(fact.currency_code))).toFixed(currencyPrecision(fact.currency_code));
  const addLedger = (factId, amount = "") => {
    sequence += 1;
    ledgers.push({ key: `ledger-${sequence}`, factId, type: defaultEconomicType(), amount, automaticType: true });
  };
  const selectedFacts = () => [...selected].map((id) => facts.get(id)).filter(Boolean);
  const renderCandidatePage = () => {
    candidatePage.items.forEach((fact) => facts.set(fact.id, fact));
    const rows = candidatePage.items.map((fact) => `<tr class="${selected.has(fact.id) ? "selected" : ""}"><td><input type="checkbox" data-review-fact="${fact.id}" aria-label="选择 Fact #${fact.id}" ${selected.has(fact.id) ? "checked" : ""}></td><td data-label="摘要"><strong>${esc(fact.summary || "未填写摘要")}</strong><small>Fact #${fact.id}</small></td><td data-label="交易对手">${esc(fact.counterparty_name || "—")}</td><td data-label="本方账户" class="mono" title="${esc(fact.account_code)}">${esc(compactAccount(fact.account_code))}</td><td data-label="可分配金额" class="fact-amount ${fact.cash_direction === 1 ? "inflow" : "outflow"}">${esc(compactAmount({ ...fact, amount: fact.available_amount }, fact.cash_direction))}</td><td data-label="发生时间">${date(fact.occurred_time)}</td></tr>`).join("");
    factRoot.innerHTML = rows ? `<div class="table-scroll"><table class="detail-data-table review-candidate-table"><thead><tr><th></th><th>摘要</th><th>交易对手</th><th>本方账户</th><th>可分配金额</th><th>发生时间</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty-state">没有匹配的待审查流水</div>';
    const pages = Math.max(1, Math.ceil(candidatePage.total / candidatePage.page_size));
    $("[data-review-candidate-pager]", form).innerHTML = `<span>共 ${candidatePage.total} 条 · 第 ${candidatePage.page_index}/${pages} 页</span><button type="button" data-candidate-page="${candidatePage.page_index - 1}" ${candidatePage.page_index <= 1 ? "disabled" : ""}>上一页</button><button type="button" data-candidate-page="${candidatePage.page_index + 1}" ${candidatePage.page_index >= pages ? "disabled" : ""}>下一页</button>`;
    $("[data-review-selection-count]", form).textContent = `已选择 ${selected.size} 条`;
    $$('[data-review-fact]', factRoot).forEach((input) => input.onchange = () => {
      const id = Number(input.dataset.reviewFact);
      if (input.checked) {
        selected.add(id);
        if (!ledgers.some((item) => item.factId === id)) addLedger(id, decimalText(facts.get(id)));
      } else {
        selected.delete(id);
        for (let index = ledgers.length - 1; index >= 0; index -= 1) {
          if (ledgers[index].factId === id) ledgers.splice(index, 1);
        }
      }
      renderCandidatePage();
    });
    $$('[data-candidate-page]', form).forEach((button) => button.onclick = async () => {
      await loadCandidatePage(Number(button.dataset.candidatePage));
    });
  };
  const loadCandidatePage = async (page = 1) => {
    const expressions = [];
    const direction = $("[name=cash_direction]", candidateFilter).value;
    const currency = $("[name=currency_code]", candidateFilter).value;
    const dateFrom = $("[name=date_from]", candidateFilter).value;
    const dateTo = $("[name=date_to]", candidateFilter).value;
    if (direction) expressions.push({ key: "cash_direction", op: "=", val: Number(direction) });
    if (currency) expressions.push({ key: "currency_code", op: "=", val: currency });
    if (dateFrom) expressions.push({ key: "occurred_time", op: ">=", val: filterBoundary(dateFrom) });
    if (dateTo) expressions.push({ key: "occurred_time", op: "<", val: filterBoundary(dateTo, true) });
    const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
    const [sortField, sortOrder] = $("[name=sort]", candidateFilter).value.split(".");
    const params = new URLSearchParams({ page_index: page, page_size: "20", sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]) });
    if (filter) params.set("filter", JSON.stringify(filter));
    candidatePage = await request(`/paam/ledger/v1/review_candidate/list?${params}`);
    renderCandidatePage();
  };
  const renderLedgers = () => {
    const choices = selectedFacts();
    definitionRoot.innerHTML = ledgers.map((item, index) => `<article class="review-ledger-row" data-ledger-key="${item.key}"><header><span>LEDGER ${String(index + 1).padStart(2, "0")}</span><strong>${esc(typeNames[item.type])}</strong></header><label>来源 Fact<select data-ledger-fact>${choices.map((fact) => `<option value="${fact.id}" ${fact.id === item.factId ? "selected" : ""}>#${fact.id} · ${esc(fact.counterparty_name || fact.summary || "未命名流水")}</option>`).join("")}</select></label><label>账本类型<select data-ledger-type>${["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"].map((value) => `<option value="${value}" ${value === item.type ? "selected" : ""}>${esc(typeNames[value])}</option>`).join("")}</select></label><label>分配金额<input data-ledger-amount inputmode="decimal" value="${esc(item.amount)}" placeholder="0.00"></label><button type="button" class="quiet" data-remove-ledger>移除</button></article>`).join("") || '<div class="empty-state">请返回上一步选择待审查流水</div>';
    $$('[data-ledger-key]', definitionRoot).forEach((row) => {
      const item = ledgers.find((value) => value.key === row.dataset.ledgerKey);
      $("[data-ledger-fact]", row).onchange = (event) => { item.factId = Number(event.target.value); };
      $("[data-ledger-type]", row).onchange = (event) => { item.type = event.target.value; item.automaticType = false; renderLedgers(); };
      $("[data-ledger-amount]", row).oninput = (event) => { item.amount = event.target.value; };
      $("[data-remove-ledger]", row).onclick = () => { ledgers.splice(ledgers.indexOf(item), 1); renderLedgers(); };
    });
  };
  const reviewPlan = () => {
    if (!selected.size) throw new Error("请选择至少一条待审查流水");
    if (!ledgers.length) throw new Error("请至少配置一条 Ledger");
    const totals = new Map();
    const allocations = ledgers.map((item, index) => {
      const fact = facts.get(item.factId);
      if (!selected.has(item.factId) || !fact) throw new Error("Ledger 必须关联已选择的 Fact");
      const amountValue = decimalAmount(item.amount, fact.currency_code);
      if (amountValue === null) throw new Error(`Ledger ${index + 1} 请填写分配金额`);
      totals.set(fact.id, (totals.get(fact.id) || 0) + amountValue);
      return { fact_id: fact.id, economic_key: item.key, amount: amountValue };
    });
    selectedFacts().forEach((fact) => {
      const allocated = totals.get(fact.id) || 0;
      if (!allocated) throw new Error(`Fact #${fact.id} 尚未配置 Ledger`);
      if (allocated > fact.available_amount) throw new Error(`Fact #${fact.id} 的分配金额超过可用金额`);
    });
    return {
      allocations,
      economics: ledgers.map((item) => ({ client_key: item.key, economic_type: item.type })),
      totals,
    };
  };
  const renderPreview = () => {
    const plan = reviewPlan();
    const behaviorName = behaviorCode === "BORROW_AND_REPAY" ? "借款与还款" : "事实交易";
    previewRoot.innerHTML = `<article><span>审查类型</span><strong>${behaviorName}</strong><small>${selected.size} 条 Fact · ${ledgers.length} 条 Ledger</small></article><article><span>生成方式</span><strong>提交后立即生效</strong><small>同时保留 Review 与 Revision 审计记录</small></article><article class="full"><span>Fact → Ledger 关系</span><div class="review-summary-lines">${ledgers.map((item, index) => { const fact = facts.get(item.factId); return `<div><span>Fact #${fact.id} → Ledger ${String(index + 1).padStart(2, "0")} · ${esc(typeNames[item.type])}</span><strong>${money({ amount: plan.allocations[index].amount, currency_code: fact.currency_code })}</strong></div>`; }).join("")}</div></article><article class="full"><span>覆盖检查</span><div class="review-summary-lines">${selectedFacts().map((fact) => `<div><span>Fact #${fact.id} 已分配</span><strong>${money({ amount: plan.totals.get(fact.id), currency_code: fact.currency_code })} / ${money({ amount: fact.available_amount, currency_code: fact.currency_code })}</strong></div>`).join("")}</div></article>`;
  };
  const setStep = (next) => {
    if (next === 2 && !selected.size) throw new Error("请选择至少一条待审查流水");
    if (next === 3) renderPreview();
    step = next;
    $$('[data-review-panel]', form).forEach((panel) => { panel.hidden = Number(panel.dataset.reviewPanel) !== step; });
    $$('[data-review-step]', form).forEach((button) => {
      const number = Number(button.dataset.reviewStep);
      button.classList.toggle("active", number === step);
      button.classList.toggle("complete", number < step);
    });
    $('[data-action="review-back"]', form).hidden = step === 1;
    const nextButton = $('[data-action="review-next"]', form);
    nextButton.hidden = step === 3;
    nextButton.textContent = step === 1 ? "下一步：配置关系 →" : "下一步：检查并生成 →";
    $('[data-action="review-submit"]', form).hidden = step !== 3;
    const titles = { 1: "选择范围", 2: "配置关系", 3: "检查并生成" };
    const helps = { 1: "选择至少一条待审查流水", 2: "每条 Ledger 关联一个 Fact", 3: "确认后直接生成账本流水" };
    $("[data-review-footer-title]", form).textContent = titles[step];
    $("[data-review-footer-help]", form).textContent = helps[step];
  };
  $$('[name="behavior_code"]', form).forEach((input) => input.onchange = () => {
    behaviorCode = input.value;
    ledgers.filter((item) => item.automaticType).forEach((item) => { item.type = defaultEconomicType(); });
  });
  $$("select", candidateFilter).forEach((select) => select.onchange = () => loadCandidatePage(1).catch((error) => showFormError(form, error)));
  bindDateTimeRanges(candidateFilter, () => loadCandidatePage(1).catch((error) => showFormError(form, error)));
  $('[data-action="review-candidate-clear"]', candidateFilter).onclick = () => {
    $$("select", candidateFilter).forEach((select) => { select.selectedIndex = 0; });
    $("[name=date_from]", candidateFilter).value = "";
    $("[name=date_to]", candidateFilter).value = "";
    bindDateTimeRanges(candidateFilter, () => loadCandidatePage(1).catch((error) => showFormError(form, error)));
    loadCandidatePage(1).catch((error) => showFormError(form, error));
  };
  $('[data-action="add-economic"]', form).onclick = () => {
    const fact = selectedFacts()[0];
    if (!fact) return;
    addLedger(fact.id);
    renderLedgers();
  };
  $('[data-action="review-back"]', form).onclick = () => { setStep(step - 1); if (step === 2) renderLedgers(); };
  $('[data-action="review-next"]', form).onclick = () => {
    try { setStep(step + 1); if (step === 2) renderLedgers(); } catch (error) { showFormError(form, error); }
  };
  renderCandidatePage();
  form.onsubmit = async (event) => {
    event.preventDefault();
    const idempotencyKey = beginSubmit(form);
    if (!idempotencyKey) return;
    try {
      const data = new FormData(form);
      const plan = reviewPlan();
      await jsonRequest("/paam/ledger/v1/review", "POST", {
        behavior_type: behaviorCode === "BORROW_AND_REPAY" ? 1 : 0,
        title: data.get("title") || "",
        economics: plan.economics, allocations: plan.allocations,
        reason: data.get("reason") || "",
        idempotency_key: idempotencyKey,
      });
      toast("审查已发布，账本流水已生成");
      route("ledger-reviews");
    } catch (error) {
      endSubmit(form);
      showFormError(form, error);
    }
  };
}

async function ledgerImportsPage() {
  const sourceType = state.params.get("source_type") || "";
  const status = state.params.get("status") || "";
  const sortField = state.params.get("sort_field") || "created_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const expressions = [];
  if (sourceType !== "") expressions.push({ key: "source_type", op: "=", val: Number(sourceType) });
  if (status !== "") expressions.push({ key: "status", op: "=", val: Number(status) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  const result = await request(`/paam/import/v1/import_file/list?${query}`);
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-import-file-row="${item.id}"><td data-label="文件名"><button type="button" class="detail-primary" data-action="import-file-detail" data-id="${item.id}" data-inspect-title="${esc(item.filename)}" data-inspect-meta="${esc(sourceLabels[item.source_type] || item.source_type)}"><strong>${esc(item.filename)}</strong><small>Import File #${item.id} · ${esc(item.batch_code || "无批次码")}</small></button></td><td data-label="来源">${esc(sourceLabels[item.source_type] || item.source_type)}</td><td data-label="格式">${esc(fileFormatLabels[item.file_format] || item.file_format)}</td><td data-label="状态"><span class="badge ${item.status === 1 ? "neutral" : "warn"}">${esc(statusLabels[item.status] || item.status)}</span></td><td data-label="成功记录">${item.success_count}</td><td data-label="导入时间">${date(item.created_time)}</td><td class="detail-arrow">→</td></tr>`).join("");
  const sourceOptions = [...new Set(Object.entries(sourceLabels).map(([value, label]) => `<option value="${esc(value)}" ${sourceType === value ? "selected" : ""}>${esc(label)}</option>`))].join("");
  const toolbar = `<form class="detail-filter" data-form="detail-import-filter"><label>来源<select name="source_type"><option value="">全部来源</option>${sourceOptions}</select></label><label>状态<select name="status"><option value="">全部状态</option>${[0, 1, 2, 3].map((value) => `<option value="${value}" ${status === String(value) ? "selected" : ""}>${esc(statusLabels[value])}</option>`).join("")}</select></label><label class="grow">排序${importSortSelect(sortField, sortOrder)}</label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-imports">清空</button></form>`;
  return detailListView({ active: "ledger-imports", toolbar, title: "Import File", description: "每行代表一个导入文件 PO；Transaction Fact 是只读的详情子资源。", total: result.total, headers: ["文件", "来源", "格式", "状态", "成功数量", "时间", ""], rows, footer: detailPager(result, "ledger-imports") });
}

async function showImportFileDetail(id) {
  return openInspection("file", id, bindPage);
}

async function ledgerTagsPage() {
  return tagsPage();
}

async function editTags(ledgerId) {
  const renderVersion = state.renderVersion;
  const viewQuery = new URLSearchParams({
    page_index: "1",
    page_size: "100",
    filter: JSON.stringify({ key: "status", op: "=", val: "ACTIVE" }),
  });
  const [viewPage, assignment] = await Promise.all([
    request(`/paam/tag/v1/view/list?${viewQuery}`),
    request(`/paam/tag/v1/assignment/${ledgerId}`),
  ]);
  const views = viewPage.items;
  if (renderVersion !== state.renderVersion) return;
  if (!views.length) return toast("请先创建标签维度", true);
  const current = assignment.tag_state;
  const dialog = modal("编辑最终流水标签", `<form data-form="tag-assignment" data-ledger="${ledgerId}" data-updated-time="${esc(assignment.updated_time || "")}" class="stack">${views.map((view) => `<label>${esc(view.name)}<select name="${esc(view.system_name)}">${view.tags.filter((tag) => tag.status === "ACTIVE").map((tag) => `<option value="${esc(tag.system_name)}" ${(current[view.system_name] || "unclassified") === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`).join("")}</select></label>`).join("")}<div class="actions"><button class="primary">保存标签</button></div></form>`);
  bindPage(dialog);
}

async function editLedgerAccount(ledgerId) {
  const account = await request(`/paam/ledger/v1/flow/${ledgerId}/account`);
  const dialog = modal("编辑账本账户", `<form data-form="ledger-account" data-ledger="${account.ledger_id}" class="stack"><label>账户代码<input name="account_code" value="${esc(account.account_code)}" required maxlength="120"></label><div class="actions"><button class="primary">保存账本账户</button></div></form>`, false);
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
  return `<div class="import-workflow" data-import-workflow data-step="1"><nav class="import-stepper" aria-label="数据导入步骤"><button type="button" class="import-step active" data-action="import-step" data-step="1" aria-current="step"><span>1</span><strong>选择来源</strong><small>确认账单平台</small></button><button type="button" class="import-step" data-action="import-step" data-step="2"><span>2</span><strong>添加文件</strong><small>上传待导入账单</small></button><button type="button" class="import-step" data-action="import-step" data-step="3" disabled><span>3</span><strong>预览确认</strong><small>核对后写入</small></button></nav><form data-form="import-preview"><section class="panel import-source-panel" data-import-step-panel="1"><div class="section-head"><div><span class="step-kicker">步骤 1 / 3</span><h2 tabindex="-1">选择数据来源</h2></div><button type="button" class="quiet" data-page="import-history">查看导入历史 →</button></div><p class="import-section-help">不确定时选择自动识别，系统会从文件表头与内容判断来源。</p><div class="source-card-grid" role="group" aria-label="数据来源">${sourceCards}</div><label class="visually-hidden">数据来源<select name="source_type"><option value="">自动识别</option><option value="alipay">支付宝</option><option value="wechat">微信</option><option value="ccb">建设银行</option><option value="abc">农业银行</option><option value="cmb">招商银行</option></select></label><div class="step-nav-actions"><span>已选择：<strong data-selected-source>自动识别</strong></span><button type="button" class="primary" data-action="import-step" data-step="2">下一步：添加文件 →</button></div></section><section class="panel import-upload-panel" data-import-step-panel="2" hidden><div class="section-head"><div><span class="step-kicker">步骤 2 / 3</span><h2 tabindex="-1">添加账单文件</h2></div><span class="format-note">CSV · XLS · XLSX · PDF · ZIP</span></div><label class="import-dropzone" data-import-dropzone><input class="visually-hidden" type="file" name="files" multiple required accept=".csv,.xls,.xlsx,.zip,.pdf"><span class="dropzone-icon" aria-hidden="true">↥</span><strong>拖放账单到这里，或点击选择文件</strong><small>单个文件不超过 25 MB，最多同时处理 100 个文件</small><span class="dropzone-button">选择文件</span></label><div id="selected-files" class="selected-files"><div class="selected-files-empty">选择文件后，将在这里显示待预览清单。</div></div><details class="import-options"><summary>加密文件与高级选项</summary><div class="import-option-body"><label>账单时区<select name="timezone"><option value="Asia/Hong_Kong" ${selectedTimeZone() === "Asia/Hong_Kong" ? "selected" : ""}>香港</option><option value="Asia/Shanghai" ${selectedTimeZone() === "Asia/Shanghai" ? "selected" : ""}>上海</option><option value="Asia/Tokyo" ${selectedTimeZone() === "Asia/Tokyo" ? "selected" : ""}>东京</option><option value="Europe/London" ${selectedTimeZone() === "Europe/London" ? "selected" : ""}>伦敦</option><option value="America/New_York" ${selectedTimeZone() === "America/New_York" ? "selected" : ""}>纽约</option><option value="UTC" ${selectedTimeZone() === "UTC" ? "selected" : ""}>UTC</option></select><small>用于解释账单中没有时区的交易时间，默认香港。</small></label><label>ZIP / PDF 密码<input type="password" name="password" autocomplete="off" placeholder="仅用于本次解析，不会保存"><small>密码只随本次预览请求使用。</small></label></div></details><div class="import-submit-row"><button type="button" class="quiet" data-action="import-step" data-step="1">← 返回选择来源</button><div class="import-submit-copy"><strong>先预览，再写入</strong><small>确认前不会修改任何账本数据。</small></div><button class="primary" data-action="preview-import">生成导入预览</button></div></section></form><section id="import-preview" data-import-step-panel="3" hidden></section></div>`;
}

const sourceLabels = {
  0: "未知", 1: "手工", 101: "支付宝", 102: "微信支付", 201: "建设银行", 202: "农业银行", 203: "招商银行",
};
const fileFormatLabels = { 0: "UNKNOWN", 1: "CSV", 2: "XLS", 3: "XLSX", 4: "PDF" };
const actionLabels = {
  new: "新增", supplement: "补充证据", duplicate_file: "重复文件", record: "仅保留记录", ambiguous: "待确认", error: "有错误",
};
const statusLabels = { 0: "待确认", 1: "已导入", 2: "部分导入", 3: "失败" };

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

function historySummaryMarkup(summary, pendingConflicts = null) {
  const conflictMetric = pendingConflicts === null ? "" : `<div class="history-metric"><span>待处理冲突</span><strong>${pendingConflicts}</strong><small>需要人工确认</small></div>`;
  return `<div class="history-metric"><span>匹配文件</span><strong>${summary.import_file_count}</strong><small>当前筛选结果</small></div><div class="history-metric"><span>完整导入</span><strong>${summary.imported_file_count}</strong><small>无异常完成</small></div><div class="history-metric"><span>已写入记录</span><strong>${summary.success_count}</strong><small>当前结果合计</small></div>${conflictMetric}`;
}
function historyResultsMarkup(result) {
  const fileCards = result.items.map((item) => `<button type="button" class="batch-card" data-action="import-file-detail" data-id="${item.id}" aria-label="查看导入文件 ${item.id}：${esc(item.filename)}"><span class="file-type-icon">${esc(fileExtension(item.filename))}</span><span class="batch-file"><span class="history-id">Import File #${item.id}</span><strong>${esc(item.filename)}</strong><small>${esc(sourceLabels[item.source_type] || item.source_type)}</small></span><span class="batch-field batch-account"><small>文件格式</small><span>${esc(fileFormatLabels[item.file_format] || item.file_format)}</span></span><span class="batch-field"><small>成功 / 总数</small><span class="progress-count"><strong>${item.success_count}</strong> / ${item.total_count}</span></span><span class="batch-field"><small>状态</small><span><span class="badge ${item.status === 1 ? "" : "warn"}">${esc(statusLabels[item.status] || item.status)}</span></span></span><span class="batch-field batch-time"><small>导入时间</small><span>${date(item.created_time)}</span></span><span class="batch-chevron" aria-hidden="true">›</span></button>`);
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const start = result.total ? (result.page_index - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.page_index * result.page_size, result.total);
  const historyPager = `<div class="pagination"><span class="range">显示 ${start}–${end}，共 ${result.total} 个文件</span><button data-action="history-page" data-value="${result.page_index - 1}" ${result.page_index <= 1 ? "disabled" : ""}>上一页</button><span>${result.page_index} / ${pages}</span><button data-action="history-page" data-value="${result.page_index + 1}" ${result.page_index >= pages ? "disabled" : ""}>下一页</button></div>`;
  return fileCards.length
    ? `<div class="batch-card-list">${fileCards.join("")}</div>${historyPager}`
    : '<div class="empty-state"><span class="empty-state-icon">⌁</span><strong>没有匹配的导入记录</strong><p>调整上方关键词、来源或状态，下方结果会自动更新。</p><button class="primary" data-page="import">导入新账单</button></div>';
}

async function importHistoryPage() {
  const initialQuery = new URLSearchParams({
    page_index: "1",
    page_size: "10",
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
  });
  const conflictFilter = encodeURIComponent(JSON.stringify({ key: "status", op: "=", val: "PENDING" }));
  const [result, summary, conflicts] = await Promise.all([
    request(`/paam/import/v1/import_file/list?${initialQuery}`),
    request("/paam/import/v1/import_file/summary"),
    request(`/paam/import/v1/fact_conflict/list?page_index=1&page_size=20&filter=${conflictFilter}`),
  ]);
  const sourceOptions = Object.entries(sourceLabels).map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  const statusOptions = [0, 1, 2, 3].map((value) => `<option value="${value}">${esc(statusLabels[value])}</option>`).join("");
  const conflictRows = conflicts.items.map((item) => `<button type="button" class="batch-card" data-action="fact-conflict-detail" data-id="${item.id}"><span class="file-type-icon">!</span><span class="batch-file"><span class="history-id">Fact Conflict #${item.id}</span><strong>${esc(item.title)}</strong><small>等待人工处理</small></span><span class="batch-field batch-time"><small>更新时间</small><span>${date(item.updated_time)}</span></span><span class="batch-chevron" aria-hidden="true">›</span></button>`).join("");
  const conflictPanel = conflicts.total ? `<section class="panel history-panel"><div class="section-head"><div><h2>待处理事实冲突</h2><p class="import-section-help">显示最近 ${conflicts.items.length} / ${conflicts.total} 条，进入详情后可关联、创建或忽略。</p></div></div><div class="batch-card-list">${conflictRows}</div></section>` : "";
  return `<div class="history-summary" data-history-summary>${historySummaryMarkup(summary, conflicts.total)}</div>${conflictPanel}<section class="panel history-panel"><div class="section-head"><div><h2>导入文件</h2><p class="import-section-help">来源和状态筛选只更新下方结果。</p></div><button class="primary" data-page="import">＋ 导入新数据</button></div><form class="toolbar history-toolbar" data-form="history-filter"><label>来源<select name="source_type"><option value="">全部来源</option>${sourceOptions}</select></label><label>状态<select name="status"><option value="">全部状态</option>${statusOptions}</select></label><span class="history-updating" data-history-updating aria-live="polite"></span></form><div data-history-results>${historyResultsMarkup(result)}</div></section>`;
}

async function refreshHistoryResults(form, page = 1) {
  clearTimeout(state.historyFilterTimer);
  state.historyRequestController?.abort();
  const controller = new AbortController();
  const requestVersion = ++state.historyRequestVersion;
  state.historyRequestController = controller;
  const params = new URLSearchParams({
    page_index: String(page),
    page_size: "10",
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
  });
  const expressions = [];
  if (form.elements.source_type.value !== "") expressions.push({ key: "source_type", op: "=", val: Number(form.elements.source_type.value) });
  if (form.elements.status.value !== "") expressions.push({ key: "status", op: "=", val: Number(form.elements.status.value) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  if (filter) {
    const encodedFilter = JSON.stringify(filter);
    params.set("filter", encodedFilter);
  }
  const resultsRoot = $("[data-history-results]");
  const summaryRoot = $("[data-history-summary]");
  const updating = $("[data-history-updating]", form);
  resultsRoot?.setAttribute("aria-busy", "true");
  form.classList.add("updating");
  if (updating) updating.textContent = "正在更新…";
  try {
    const [result, summary] = await Promise.all([
      request(`/paam/import/v1/import_file/list?${params}`, { signal: controller.signal }),
      request("/paam/import/v1/import_file/summary", { signal: controller.signal }),
    ]);
    if (requestVersion !== state.historyRequestVersion || state.page !== "import-history") return;
    if (summaryRoot) summaryRoot.innerHTML = historySummaryMarkup(summary);
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

const MAX_IMPORT_FILES = 100;
const MAX_IMPORT_FILE_BYTES = 25 * 1024 * 1024;
const MAX_IMPORT_TOTAL_BYTES = 100 * 1024 * 1024;
const importExtensions = new Set(["csv", "xls", "xlsx", "pdf", "zip"]);

function validateImportFiles(files) {
  if (!files.length) throw new Error("请选择至少一个账单文件");
  if (files.length > MAX_IMPORT_FILES) throw new Error(`单次最多选择 ${MAX_IMPORT_FILES} 个文件`);
  const oversized = files.find((file) => file.size > MAX_IMPORT_FILE_BYTES);
  if (oversized) throw new Error(`${oversized.name} 超过单文件 25 MB 限制`);
  const unsupported = files.find((file) => !importExtensions.has(fileExtension(file.name).toLowerCase()));
  if (unsupported) throw new Error(`${unsupported.name} 的格式不受支持`);
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > MAX_IMPORT_TOTAL_BYTES) throw new Error("所选文件总计超过单次 100 MB 限制");
}

const fileBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(",")[1]);
  reader.onerror = () => reject(reader.error || new Error(`无法读取文件：${file.name}`));
  reader.onabort = () => reject(new Error(`文件读取已取消：${file.name}`));
  reader.readAsDataURL(file);
});

async function encodeImportFiles(files, source, password) {
  const encoded = new Array(files.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < files.length) {
      const index = nextIndex;
      nextIndex += 1;
      const file = files[index];
      encoded[index] = {
        filename: file.name,
        content_base64: await fileBase64(file),
        source_type: source,
        password,
      };
    }
  };
  await Promise.all(Array.from(
    { length: Math.min(4, files.length) },
    () => worker(),
  ));
  return encoded;
}

async function previewImport(form) {
  if (!beginSubmit(form)) return;
  try {
    const files = [...form.elements.files.files];
    validateImportFiles(files);
    const source = form.elements.source_type.value || null;
    const password = form.elements.password.value || null;
    const timezone = form.elements.timezone.value || selectedImportTimeZone();
    setSelectedImportTimeZone(timezone);
    const payload = { timezone, files: await encodeImportFiles(files, source, password) };
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
  const sourceTimeZones = [...new Set(
    (plan.documents || []).map((document) => document.source_timezone).filter(Boolean),
  )];
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
  const previewHelp = $(".preview-heading p", root);
  if (previewHelp && sourceTimeZones.length) {
    previewHelp.append(` 账单原始时间按 ${sourceTimeZones.map(timeZoneName).join("、")} 解释。`);
  }
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
  const viewPage = await request("/paam/tag/v1/view/list?page_index=1&page_size=100");
  const views = viewPage.items;
  const activeCount = views.filter((view) => view.status === "ACTIVE").length;
  const atViewLimit = viewPage.total >= 100;
  const cards = views.map((view) => {
    const isActive = view.status === "ACTIVE";
    const tagsMarkup = view.tags.filter((tag) => tag.system_name !== "unclassified").map((tag) => {
      const isSystem = tag.system_name === "unclassified";
      const isTagActive = tag.status === "ACTIVE";
      const action = isSystem ? "" : `<button class="tag-pill-action" type="button" data-action="tag-status" data-view="${view.id}" data-id="${tag.id}" data-status="${isTagActive ? "ARCHIVED" : "ACTIVE"}" aria-label="${isTagActive ? "归档" : "恢复"}标签 ${esc(tag.name)}" title="${isTagActive ? "归档标签" : "恢复标签"}">${isTagActive ? "×" : "恢复"}</button>`;
      return `<span class="tag-pill${isSystem ? " system" : ""}${isTagActive ? "" : " archived"}" title="系统名称：${esc(tag.system_name)}">${isSystem ? '<span class="tag-pill-lock" aria-hidden="true">◆</span>' : ""}<span>${esc(tag.name)}</span>${isSystem ? `<code>${esc(tag.system_name)}</code>` : ""}${action}</span>`;
    }).join("");
    const creator = isActive ? `<div class="tag-inline-creator"><button class="tag-inline-launch" type="button" data-action="new-tag-inline" data-id="${view.id}" aria-controls="tag-create-${view.id}" aria-expanded="false"><span aria-hidden="true">＋</span> 新标签</button><form id="tag-create-${view.id}" class="tag-inline-form" data-form="inline-tag" data-view="${view.id}" hidden><label class="sr-only" for="tag-name-${view.id}">显示名称</label><input id="tag-name-${view.id}" name="name" maxlength="120" placeholder="显示名称" autocomplete="off" required><span class="tag-inline-divider" aria-hidden="true"></span><label class="sr-only" for="tag-system-${view.id}">系统名称</label><input id="tag-system-${view.id}" name="system_name" maxlength="64" pattern="[a-z][a-z0-9_]{0,63}" placeholder="自动生成系统名称" autocomplete="off" required><button class="tag-inline-submit" type="submit" aria-label="保存标签" title="保存">✓</button><button class="tag-inline-cancel" type="button" data-action="cancel-tag" aria-label="取消添加标签" title="取消">×</button></form></div>` : "";
    return `<article class="tag-view-card${isActive ? "" : " archived"}" aria-labelledby="tag-view-${view.id}"><header class="tag-view-head"><div class="tag-view-meta"><div class="tag-view-title"><h3 id="tag-view-${view.id}">${esc(view.name)}</h3><span class="tag-view-status ${isActive ? "active" : "archived"}"><span aria-hidden="true">●</span>${esc(statusNames[view.status] || view.status)}</span></div><code>${esc(view.system_name)}</code></div><div class="tag-view-actions">${isActive ? `<button type="button" data-action="new-tag" data-id="${view.id}" aria-controls="tag-create-${view.id}" aria-expanded="false">＋ 添加标签</button>` : ""}<button class="quiet" type="button" data-action="view-status" data-id="${view.id}" data-status="${isActive ? "ARCHIVED" : "ACTIVE"}">${isActive ? "归档维度" : "恢复维度"}</button></div></header><div class="tag-pill-list">${tagsMarkup}${creator}</div></article>`;
  }).join("");
  return `<section class="tag-manager" aria-labelledby="tag-manager-title"><div class="tag-manager-head"><div><h2 id="tag-manager-title">标签维度</h2><p>${views.length ? `共 ${viewPage.total} 个维度，${activeCount} 个启用中${atViewLimit ? "；已达 100 个上限" : ""}` : "用维度组织同一类标签"}</p></div><button class="primary" data-action="new-view" ${atViewLimit ? 'disabled title="标签维度上限为 100"' : ""}>${atViewLimit ? "已达维度上限" : "＋ 新建维度"}</button></div><div class="tag-view-list">${cards || '<div class="panel empty-state">尚未创建标签维度</div>'}</div></section>`;
}

async function showFactConflict(id) {
  const renderVersion = state.renderVersion;
  const item = await request(`/paam/import/v1/fact_conflict/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const lines = item.lines.map((line) => `<tr><td>#${line.bill_id}</td><td><strong>${esc(roleNames[line.role] || line.role)}</strong><br><small>${esc(line.role)}</small></td><td>${money(line)}</td><td>${esc(line.party || "—")}</td></tr>`);
  const operationNames = { CREATE: "创建", UPDATE: "修订", CONFIRM: "确认", REVOKE: "撤销", RESTORE: "恢复", DISMISS: "忽略", REOPEN: "重新打开", ASSIGN: "分配标签", ACCOUNT_SET: "修正账户", RESOLVE: "解决冲突" };
  const history = item.history.map((event) => `<details class="review-history-event"><summary><span>v${event.version} · ${esc(operationNames[event.operation] || event.operation)}</span><small>${date(event.created_time)}</small></summary><p>${esc(event.reason || "无说明")}</p><details><summary>查看技术快照</summary><pre>${esc(JSON.stringify({request:event.request,before:event.before,after:event.after}, null, 2))}</pre></details></details>`).join("");
  let actions = "";
  if (item.review_type === "ACCOUNT") {
    if (item.status === "CONFIRMED") actions = `<button data-action="account-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销账户修正</button>`;
    if (item.status === "REVOKED") actions = `<button data-action="account-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复账户修正</button>`;
  } else if (item.review_type === "FACT_CONFLICT") {
    if (item.status === "PENDING") actions = `<button data-action="conflict-resolve" data-id="${item.id}" data-version="${item.version}">解决冲突</button><button data-action="conflict-transition" data-kind="dismiss" data-id="${item.id}" data-version="${item.version}">忽略</button>`;
    if (item.status === "REJECTED") actions = `<button data-action="conflict-transition" data-kind="reopen" data-id="${item.id}" data-version="${item.version}">重新打开</button>`;
  }
  const dialog = modal(`Review #${item.id}`, `<div class="review-detail-head"><div><span class="review-type-kicker">${esc(reviewTypeNames[item.review_type] || item.review_type)}</span><h3>${esc(item.title || "未填写标题")}</h3><small>${esc(statusNames[item.status] || item.status)} · ${esc(statusNames[item.allocation_status] || item.allocation_status)} · 版本 ${item.version}</small></div><div class="actions">${actions}</div></div><section><h3>涉及的账单事实</h3>${lines.length ? table(["Fact", "业务角色", "分配金额", "对象"], lines) : '<p class="muted">当前没有已接受的 Fact。</p>'}</section>${Object.keys(item.result || {}).length ? `<section><h3>类型结果</h3><pre>${esc(JSON.stringify(item.result, null, 2))}</pre></section>` : ""}<section><h3>操作历史</h3><div class="review-history">${history || '<p class="muted">没有历史。</p>'}</div></section>`);
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
  const systemName = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  if (!systemName || /^[a-z]/.test(systemName)) return systemName;
  return `tag_${systemName}`.slice(0, 64);
}

function bindTagSystemName(form) {
  const nameInput = $('[name="name"]', form);
  const systemInput = $('[name="system_name"]', form);
  if (!nameInput || !systemInput) return;
  let timer = 0;
  let sequence = 0;
  const applyGenerated = (value) => {
    systemInput.value = value;
    systemInput.dataset.generated = value;
    systemInput.setAttribute("aria-busy", "false");
  };
  const generate = () => {
    if (systemInput.dataset.manual === "true") return;
    clearTimeout(timer);
    const displayName = nameInput.value.trim();
    if (!displayName) {
      applyGenerated("");
      return;
    }
    if (!/[\u3400-\u9fff]/u.test(displayName)) {
      applyGenerated(tagSystemName(displayName));
      return;
    }
    const current = ++sequence;
    systemInput.setAttribute("aria-busy", "true");
    timer = setTimeout(async () => {
      try {
        const result = await jsonRequest("/paam/tag/v1/system_name/preview", "POST", { name: displayName });
        if (current === sequence && systemInput.dataset.manual !== "true") applyGenerated(result.system_name);
      } catch (_error) {
        if (current === sequence) systemInput.setAttribute("aria-busy", "false");
      }
    }, 160);
  };
  nameInput.addEventListener("input", generate);
  systemInput.addEventListener("input", () => {
    if (systemInput.value) systemInput.dataset.manual = "true";
    else {
      delete systemInput.dataset.manual;
      generate();
    }
  });
  generate();
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
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const params = new URLSearchParams();
      const data = new FormData(form);
      try {
        if (data.get("date_from")) filterBoundary(data.get("date_from"));
        if (data.get("date_to")) filterBoundary(data.get("date_to"), true);
      } catch (error) {
        showFormError(form, error);
        return;
      }
      for (const [name, value] of data) if (value && name !== "sort") params.set(name, value);
      const [sortField, sortOrder] = String(data.get("sort") || "").split(".");
      if (sortField && sortOrder) {
        params.set("sort_field", sortField);
        params.set("sort_order", sortOrder);
      }
      params.set("page", "1");
      params.set("page_size", state.params.get("page_size") || "20");
      route(pageId, params);
    });
    $$('select', form).forEach((select) => select.addEventListener("change", () => form.requestSubmit()));
  });
  bindDateTimeRanges(root, (form) => form?.requestSubmit());
  $$('[data-action="detail-clear"]', root).forEach((button) => button.onclick = () => route(button.dataset.pageId));
  $$('[data-action="detail-page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params);
    params.set("page", button.dataset.value);
    route(button.dataset.pageId, params);
  });
  $$('[data-action="detail-page-size"]', root).forEach((select) => select.onchange = () => {
    const params = new URLSearchParams(state.params);
    params.set("page", "1");
    params.set("page_size", select.value);
    route(select.dataset.pageId, params);
  });
  $$('[data-action="summary-detail"]', root).forEach((button) => button.onclick = () => showSummaryDetail(button.dataset.type));
  $$('[data-action="summary-drilldown"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params);
    params.delete("detail");
    params.delete("page");
    params.set("economic_type", button.dataset.type);
    closeDialogs();
    route("economy", params);
  });
  $$('[data-action="fact-detail"]', root).forEach((button) => button.onclick = () => showFactDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="import-file-detail"]', root).forEach((button) => button.onclick = () => showImportFileDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="economic-detail"]', root).forEach((button) => button.onclick = () => showEconomicDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="economic-review-detail"]', root).forEach((button) => button.onclick = () => showEconomicReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="economic-review-transition"]', root).forEach((button) => button.onclick = () => transitionEconomicReview(button).catch((error) => toast(error.message, true)));
  $$('[data-summary-row],[data-fact-row],[data-economic-row],[data-review-row],[data-import-row],[data-import-file-row]', root).forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button,input,label,select,a")) return;
      if (row.dataset.summaryRow) showSummaryDetail(row.dataset.summaryRow);
      if (row.dataset.factRow) showFactDetail(row.dataset.factRow).catch((error) => toast(error.message, true));
      if (row.dataset.economicRow) showEconomicDetail(row.dataset.economicRow).catch((error) => toast(error.message, true));
      if (row.dataset.reviewRow) showEconomicReview(row.dataset.reviewRow).catch((error) => toast(error.message, true));
      if (row.dataset.importFileRow) showImportFileDetail(row.dataset.importFileRow).catch((error) => toast(error.message, true));
    });
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      row.click();
    });
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
    state.accountMonth = currentAccountMonth();
    const range = monthBounds(state.accountMonth);
    const params = new URLSearchParams(state.params);
    params.set("month", `${range.year}-${String(range.month + 1).padStart(2, "0")}`);
    route("summary", params);
  });
  $$('[data-action="account-metric"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams({
      entryTypeCode: 0,
      entryDirection: button.dataset.value === "INCOME" ? 1 : 2,
    }));
  });
  $$('[data-action="account-type"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams({ entryTypeCode: button.dataset.value }));
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
  $$('[data-action="history-page"]', root).forEach((button) => button.onclick = () => {
    const form = $('[data-form="history-filter"]');
    if (form) refreshHistoryResults(form, Number(button.dataset.value));
  });
  $$('[data-action="fact-conflict-detail"]', root).forEach((button) => button.onclick = () => showFactConflict(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-tags"]', root).forEach((button) => button.onclick = () => editTags(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-ledger-account"]', root).forEach((button) => button.onclick = () => editLedgerAccount(button.dataset.id).catch((error) => toast(error.message, true)));
  $('[data-action="new-view"]', root)?.addEventListener("click", () => simpleDictionaryDialog("view"));
  $$('[data-action="new-tag"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="new-tag-inline"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="cancel-tag"]', root).forEach((button) => button.onclick = () => closeInlineTag(button.closest("form")));
  $$('[data-action="view-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("view", button).catch((error) => toast(error.message, true)));
  $$('[data-action="tag-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("tag", button).catch((error) => toast(error.message, true)));
  $$('[data-action="conflict-transition"]', root).forEach((button) => button.onclick = () => transitionConflict(button).catch((error) => toast(error.message, true)));
  $$('[data-action="conflict-resolve"]', root).forEach((button) => button.onclick = () => conflictDialog(button));
  const importForm = $('[data-form="import-preview"]', root);
  if (importForm) {
    const limitCopy = $("[data-import-dropzone] small", importForm);
    if (limitCopy) limitCopy.textContent = "单个不超过 25 MB，最多 100 个，单次总计不超过 100 MB";
    const importTimeZone = importForm.elements.timezone;
    importTimeZone.value = selectedImportTimeZone();
    const importTimeZoneHelp = $("small", importTimeZone.closest("label"));
    if (importTimeZoneHelp) {
      importTimeZoneHelp.textContent = "仅用于解释文件中未带时区的交易时间，不影响页面显示；国内账单默认上海。";
    }
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
  const historyFilter = $('[data-form="history-filter"]', root);
  historyFilter?.addEventListener("submit", (event) => {
    event.preventDefault();
    refreshHistoryResults(event.currentTarget, 1);
  });
  historyFilter?.elements.source_type.addEventListener("change", () => scheduleHistoryRefresh(historyFilter));
  historyFilter?.elements.status.addEventListener("change", () => scheduleHistoryRefresh(historyFilter));
  importForm?.addEventListener("submit", async (event) => { event.preventDefault(); try { await previewImport(event.currentTarget); } catch (error) { showFormError(event.currentTarget, error); } });
  $('[data-form="import-revise"]', root)?.addEventListener("submit", reviseImport);
  $('[data-form="tag-assignment"]', root)?.addEventListener("submit", submitTags);
  $$('[data-form="inline-tag"]', root).forEach((form) => {
    bindTagSystemName(form);
    form.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeInlineTag(form);
      }
    });
    form.addEventListener("submit", submitInlineTag);
  });
  $('[data-form="ledger-account"]', root)?.addEventListener("submit", submitLedgerAccount);
  $('[data-form="dictionary"]', root)?.addEventListener("submit", submitDictionary);
  if ($('[data-form="dictionary"]', root)) bindTagSystemName($('[data-form="dictionary"]', root));
  $('[data-form="conflict"]', root)?.addEventListener("submit", submitConflict);
  $$('form[data-form]', root).forEach(bindCommandForm);
}

async function submitTags(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/tag/v1/assignment/${form.dataset.ledger}`, "PUT", {
      expected_updated_time: form.dataset.updatedTime || null,
      tag_state: Object.fromEntries(data),
    });
    closeDialogs(); toast("标签已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function submitLedgerAccount(event) {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/ledger/v1/flow/${form.dataset.ledger}/account`, "PUT", data);
    closeDialogs(); toast("账本账户已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function confirmImport() {
  if (!state.importPlan || state.confirmingImport) return;
  state.confirmingImport = true;
  const button = $('[data-action="confirm-import"]');
  if (button) button.disabled = true;
  try { const result = await jsonRequest(`/paam/import/v1/preview/${state.importPlan.token}/confirm`, "POST", { version: state.importPlan.version }); state.importPlan = null; toast(`导入完成：${result.bill_fact_ids?.length || 0} 条新事实`); route("import-history"); } catch (error) { if (button) button.disabled = false; const form = button?.closest("form"); if (form) showFormError(form, error); else toast(error.message, true); }
  finally { state.confirmingImport = false; }
}
function simpleDictionaryDialog(kind, viewId = "") {
  const dialog = modal(kind === "view" ? "新建标签维度" : "新增标签", `<form data-form="dictionary" data-kind="${kind}" data-view="${viewId}" class="stack"><label>显示名称<input name="name" required maxlength="120" autocomplete="off" placeholder="用于界面展示"></label><label>系统名称 <span><i>根据显示名称自动生成，可修改</i></span><input name="system_name" required maxlength="64" pattern="[a-z][a-z0-9_]{0,63}" placeholder="自动生成系统名称" autocomplete="off"></label><div class="actions"><button class="primary">保存</button></div></form>`, false); bindPage(dialog);
}
async function submitInlineTag(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/tag/v1/view/${form.dataset.view}/tag`, "POST", payload);
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
  const url = form.dataset.kind === "view" ? "/paam/tag/v1/view" : `/paam/tag/v1/view/${form.dataset.view}/tag`;
  try { await jsonRequest(url, "POST", payload); form.closest("dialog").close(); toast("标签定义已保存"); await render(); } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function dictionaryStatus(kind, button) {
  if (button.disabled) return;
  button.disabled = true;
  const url = kind === "view" ? `/paam/tag/v1/view/${button.dataset.id}` : `/paam/tag/v1/view/${button.dataset.view}/tag/${button.dataset.id}`;
  try { await jsonRequest(url, "PUT", { status: button.dataset.status }); toast("状态已更新"); await render(); }
  catch (error) { button.disabled = false; throw error; }
}
async function transitionConflict(button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await jsonRequest(`/paam/import/v1/fact_conflict/${button.dataset.id}/${button.dataset.kind}`, "POST", { expected_version: Number(button.dataset.version), reason: "用户处理事实冲突" });
    closeDialogs(); toast("冲突状态已更新"); await render();
  } catch (error) { button.disabled = false; throw error; }
}
function conflictDialog(button) {
  const dialog = modal("解决事实冲突", `<form data-form="conflict" data-id="${button.dataset.id}" data-version="${button.dataset.version}" class="stack"><label>处理方式<select name="resolution_type"><option value="LINK_EXISTING">关联已有 Fact</option><option value="CREATE_NEW">确认为新 Fact</option></select></label><label>已有 Fact ID（创建新 Fact 时留空）<input type="number" min="1" name="existing_bill_id"></label><label>说明<input name="reason" value="人工核对事实冲突"></label><div class="actions"><button class="primary">确认解决</button></div></form>`, false); bindPage(dialog);
}
async function submitConflict(event) {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
  if (!beginSubmit(form)) return;
  data.existing_bill_id = data.existing_bill_id ? Number(data.existing_bill_id) : 0;
  try { await jsonRequest(`/paam/import/v1/fact_conflict/${form.dataset.id}/resolve`, "POST", { ...data, expected_version: Number(form.dataset.version) }); closeDialogs(); toast("事实冲突已解决"); await render(); } catch (error) { endSubmit(form); showFormError(form, error); }
}

$$('[data-page]').forEach((button) => button.onclick = () => route(button.dataset.page));
if (!location.hash) route("ledger"); else readRoute();

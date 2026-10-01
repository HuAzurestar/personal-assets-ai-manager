import { checkConnection, request, jsonRequest } from "../api/client.js";
import { accountManagementPage, bindAccountManagement, stopAccountRead } from "./account-management.js";
import { positionPage, bindPosition, stopPositionRead } from "./position.js";
import { mountReviewWorkbench, transitionReview, stopReviewRead } from "./review-workbench.js";
import { mountImportBatch, stopImportRead } from "./import-batch.js";
import { readFlowSearch, bindFlowSearch, stopFlowRead, flowReadBusy, resetFlowSearch } from "./flow-search.js";
import { preserveView } from "../util/view_state.js?v=20260928.6";
import { toast } from "../component/toast.js";
import { table } from "../component/table.js";
import { openInspection } from "../component/inspection.js?v=20260928.6";
import {
  detailList, detailPager,
} from "../component/detail.js?v=20260917.10";
import {
  bindDateTimeRanges, dateTimeRangeControl,
} from "../component/date-time-range.js?v=20260928.6";
import { state } from "../state/ledger.js";
import {
  $, $$, currencyPrecision, date, decimalAmount, esc, key, money,
  reviewTypeNames, roleNames, statusNames, typeNames,
  selectedCalendarDate, selectedImportTimeZone, selectedTimeZone,
  setSelectedImportTimeZone, setSelectedTimeZone, zonedISOString,
} from "../util/core.js";
import {
  canonicalHash, parseHash, shellMarkup, syncNavigation,
} from "../navigation.js?v=20260928.6";
import {
  accountsMarkup, cursorFromParam, monthBounds,
} from "./account.js?v=20260917.10";
import {
  automationSettingsPage, autoRulesPage, bindAutomation, tagReviewPage, stopAutomationPolling, startAutomationRefresh,
} from "./automation.js?v=20260929.1";

const entryTypeValues = { TRANSACTION: 0, ACCOUNT_TRANSFER: 1, ASSET_LIABILITY: 2, DUPLICATE: 3 };
const entryTypeCodes = { 0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "ASSET_LIABILITY", 3: "DUPLICATE" };
const reviewBehaviorNames = { 0: "系统原始交易", 1: "借款与还款", 2: "信用卡", 3: "共同费用", 4: "人工解释" };
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
  position: ["资产与负债对象", "查询独立数量与原始证据；不是现金账户。"],
  "account-management": ["个人与账户", "维护个人、管理集合和具体来源卡，不改历史现金。"],
  economy: ["明细", "查看最终经济流水，并追溯对应的审查、分配关系与事实。"],
  "ledger-reviews": ["明细", "查看事实如何通过审查和 Allocation 形成经济流水。"],
  "ledger-imports": ["明细", "在统一列表中追溯导入文件、原始行和处理结果。"],
  "ledger-tags": ["明细", "在统一列表中查看分类维度、标签值和启用状态。"],
  "auto-rules": ["明细", ""],
  summary: ["概览", "基于 Ledger Summary 查看月度收支、趋势和账本活动；具体流水继续回到“明细”查看。"],
  ledger: ["明细", "查看导入后不可变的事实流水；最终结果请切换到经济明细。"],
  import: ["导入 / 上传", "选择来源、添加文件，并在写入账本前逐项核对。"],
  "import-history": ["导入记录", "查找已经写入的文件、处理结果和原始行。"],
  reviews: ["账单审查", "选择待审查事实，配置 Fact 与 Ledger 的关系，预览后生成账本流水。"],
  "tag-review": ["打标签审查", ""],
  settings: ["设置", ""],
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

let renderedRoute = null;
let backgroundBusy = false;
let foregroundBusy = 0;
let pendingCommands = 0;
let interactionVersion = 0;
document.addEventListener('paam:mutation', (event) => {
  pendingCommands = Math.max(0, pendingCommands + (event.detail.phase === 'start' ? 1 : -1));
  ++interactionVersion;
  ++state.renderVersion; // Invalidate page reads issued before this command.
  $('#page-content')?.removeAttribute('aria-busy');
});
document.addEventListener('input', () => { ++interactionVersion; });
document.addEventListener('change', () => { ++interactionVersion; });
const livePages = new Set(['summary', 'ledger', 'economy', 'ledger-reviews', 'ledger-imports', 'ledger-tags', 'import-history']);
function canRefreshPage() {
  return !document.hidden && !pendingCommands && !foregroundBusy && !flowReadBusy() && !document.querySelector('dialog[open]')
    && !document.activeElement?.matches('input, textarea, select, [contenteditable="true"]')
    && !document.querySelector('[data-range-popover]:not([hidden])')
    && !document.querySelector('[data-form="inline-tag"]:not([hidden])');
}
window.setInterval(async () => {
  if (backgroundBusy || !livePages.has(state.page) || !canRefreshPage()) return;
  backgroundBusy = true;
  try {
    const history = $('[data-form="history-filter"]');
    if (state.page === 'import-history' && history) await refreshHistoryResults(history, Number(history.dataset.page || 1), true);
    else await render({ background: true });
  } finally { backgroundBusy = false; }
}, 5000);

async function render({ background = false } = {}) {
  stopAccountRead();
  stopPositionRead();
  stopReviewRead();
  stopImportRead();
  stopFlowRead();
  stopAutomationPolling();
  const renderVersion = background ? state.renderVersion : ++state.renderVersion;
  if (!background) foregroundBusy++;
  const interaction = interactionVersion;
  const routeKey = location.hash;
  const samePage = renderedRoute === routeKey;
  const page = state.page;
  const root = $("#page-content");
  const [title, help] = pageInfo[page];
  $("#title").textContent = title;
  $("#help").textContent = help;
  $(".module-heading").hidden = true;
  $("#content").classList.add("compact-content");
  if (!samePage) {
    syncNavigation(page);
    const secondaryNavigation = $("#secondary-nav");
    if (secondaryNavigation) bindPage(secondaryNavigation);
    renderPageActions();
  }
  if (!samePage) root.innerHTML = '<div class="busy">正在加载…</div>';
  root.setAttribute('aria-busy', 'true');
  try {
    const content = await ({
      economy: economicPage,
      "account-management": () => accountManagementPage(state.params),
      position: () => positionPage(state.params),
      "ledger-reviews": ledgerReviewsPage,
      "ledger-imports": ledgerImportsPage,
      "ledger-tags": ledgerTagsPage,
      "auto-rules": () => autoRulesPage(state.params),
      summary: summaryPage,
      ledger: ledgerPage,
      import: importPage,
      "import-history": importHistoryPage,
      reviews: reviewCreatePage,
      "tag-review": () => tagReviewPage(state.params),
      settings: automationSettingsPage,
    })[page]();
    if (renderVersion !== state.renderVersion || page !== state.page || routeKey !== location.hash) return;
    if (background && (interaction !== interactionVersion || !canRefreshPage())) return;
    const historyPage = $('[data-form="history-filter"]', root)?.dataset.page;
    const apply = () => {
      root.innerHTML = content;
      bindPage(root);
      const history = $('[data-form="history-filter"]', root);
      if (samePage && history && historyPage) history.dataset.page = historyPage;
    };
    if (samePage) preserveView(root, apply); else apply();
    renderedRoute = routeKey;
    if (page === "import") renderImportPlan();
    if (page === "reviews") await mountEconomicReviewEditor(root);
  } catch (error) {
    if (renderVersion !== state.renderVersion || page !== state.page || routeKey !== location.hash) return;
    if (samePage) {
      let errorSlot = root.querySelector('[data-refresh-error]');
      if (!errorSlot) { errorSlot = document.createElement('p'); errorSlot.dataset.refreshError = ''; errorSlot.setAttribute('role', 'status'); root.append(errorSlot); }
      errorSlot.textContent = `刷新失败，保留上次内容：${error.message}`;
      startAutomationRefresh(root, { stale: true });
    } else {
      renderedRoute = null;
      root.innerHTML = `<section class="panel"><div class="error">${esc(error.message)}</div><div class="actions"><button data-action="reload">重新加载</button></div></section>`;
      bindPage(root);
    }
  } finally {
    if (!background) foregroundBusy--;
    if (renderVersion === state.renderVersion) root.removeAttribute('aria-busy');
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
  const code = String(item.cash_currency_code ?? item.currency_code ?? "").toUpperCase();
  const precision = currencyPrecision(code);
  const value = Number(item.cash_amount ?? item.amount) / (10 ** precision);
  const amount = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  }).format(value);
  return `${direction === 1 || direction === "IN" ? "+" : "−"} ${amount} ${code.split("_", 1)[0]}`;
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
  const currency = (state.params.get("cash_currency_code") || "").trim().toUpperCase();
  const direction = state.params.get("cash_direction") || "";
  const word = state.params.get("word")?.trim() || "";
  const searchField = state.params.get("search_field") || "summary";
  const dateFrom = state.params.get("date_from") || "";
  const dateTo = state.params.get("date_to") || "";
  const sortField = state.params.get("sort_field") || "occurred_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const expressions = [];
  if (selectedType in entryTypeValues) expressions.push({ key: "economic_type", op: "=", val: selectedType });
  if (["true", "false"].includes(active)) expressions.push({ key: "active", op: "=", val: active === "true" });
  if (currency) expressions.push({ key: "cash_currency_code", op: "=", val: currency });
  if (["IN", "OUT"].includes(direction)) expressions.push({ key: "cash_direction", op: "=", val: direction });
  for (const key of ["account_ref_id", "account_id", "party_id", "tag_id"]) {
    if (state.params.has(key)) expressions.push({ key, op: "=", val: Number(state.params.get(key)) });
  }
  if (dateFrom) expressions.push({ key: "occurred_time", op: ">=", val: filterBoundary(dateFrom) });
  if (dateTo) expressions.push({ key: "occurred_time", op: "<", val: filterBoundary(dateTo, true) });
  const filter = expressions.length > 1 ? { op: "AND", expression: expressions } : expressions[0];
  const query = new URLSearchParams({
    page_index: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    sorter: JSON.stringify([{ key: sortField, direction: sortOrder }]),
  });
  if (filter) query.set("filter", JSON.stringify(filter));
  if (word) { query.delete("page_index"); query.set("query", JSON.stringify([{ key: searchField, word }])); }
  const result = word ? await readFlowSearch(query, location.hash) : await request(`/paam/ledger/v1/flow/list?${query}`);
  const sortOptions = [["occurred_time.desc", "时间：最新优先"], ["occurred_time.asc", "时间：最早优先"],
    ["cash_amount.desc", "绝对金额：从高到低"], ["cash_amount.asc", "绝对金额：从低到高"],
    ["signed_cash_amount.desc", "带方向金额：从高到低"], ["signed_cash_amount.asc", "带方向金额：从低到高"]];
  const toolbar = `<form class="detail-filter ledger-detail-filter" data-form="economic-filter"><label>账本类型<select name="economic_type"><option value="">全部类型</option>${Object.keys(entryTypeValues).map(value => `<option value="${value}" ${selectedType === value ? "selected" : ""}>${esc(typeNames[value] || value)}</option>`).join("")}</select></label><label>有效状态<select name="active"><option value="">全部状态</option><option value="true" ${active === "true" ? "selected" : ""}>有效</option><option value="false" ${active === "false" ? "selected" : ""}>已停用</option></select></label><label>收支方向<select name="cash_direction"><option value="">全部方向</option>${["IN", "OUT"].map(value => `<option ${value === direction ? "selected" : ""}>${value}</option>`).join("")}</select></label><label>币种${currencySelect(currency).replace('name="currency_code"', 'name="cash_currency_code"')}</label>${dateTimeRangeControl(dateFrom, dateTo)}<label>排序<select name="sort">${sortOptions.map(([value, label]) => `<option value="${value}" ${value === `${sortField}.${sortOrder}` ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
    ${[["account_ref_id", "来源卡 ID（0 未识别）"], ["account_id", "账户 ID（0 已识别未分组）"], ["party_id", "个人 ID"], ["tag_id", "标签 ID"]].map(([key, label]) => `<label>${label}<input type="number" name="${key}" min="${key.startsWith("account") ? 0 : 1}" step="1" value="${esc(state.params.get(key) || "")}"></label>`).join("")}
    <label>字面搜索<select name="search_field"><option value="summary" ${searchField === "summary" ? "selected" : ""}>脱敏摘要</option><option value="counterparty" ${searchField === "counterparty" ? "selected" : ""}>脱敏交易对手</option></select><input name="word" maxlength="128" value="${esc(word)}" autocomplete="off"></label><button type="submit">查找</button><button type="button" class="quiet" data-action="detail-clear" data-page-id="economy">清空</button></form><p>金额排序先按币种分组，不作汇率换算。账户只筛选，不参与排序。有效性、标签、脱敏摘要及数量关系请打开详情核对。</p>`;
  return `<div data-flow-read>${detailListView({ toolbar, headers: ["原始现金结果", "本方来源", "金额", "发生时间", ""], rows: flowRows(result.items, !!word), footer: word ? flowScanFooter(result) : detailPager(result, "economy") })}</div>`;
}

function flowRows(items, scanning = false) {
  return items.map(item => `<tr class="detail-click-row" tabindex="0" data-economic-row="${item.id}"><td data-label="原始现金结果"><button type="button" class="detail-primary" data-action="economic-detail" data-id="${item.id}" data-inspect-title="Ledger #${item.id}" data-inspect-amount="${esc(compactAmount(item, item.cash_direction))}"><strong>${esc(typeNames[item.economic_type] || item.economic_type)}</strong><small>Ledger #${item.id}${item.economic_type === "DUPLICATE" ? " · 仅证据，不计财务汇总" : ""}</small></button></td><td data-label="本方来源">${item.account_ref_id ? `来源卡 #${item.account_ref_id}（归属见详情）` : "来源未识别"}</td><td data-label="金额" class="fact-amount ${item.cash_direction === "IN" ? "inflow" : "outflow"}">${esc(compactAmount(item, item.cash_direction))}</td><td data-label="发生时间">${date(item.occurred_time)}</td><td class="detail-arrow">→</td></tr>`).join("") || (scanning ? '<tr><td colspan="5">尚未找到命中；扫描未结束时可继续推进。</td></tr>' : "");
}

function flowScanFooter(result) {
  return `<div class="actions"><span data-flow-scan-status>已扫描 ${result.scanned_count} 个候选；找到 ${result.items.length} 项；总数未知。${result.has_more ? "空命中也可继续" : "本次扫描结束"}</span><button type="button" data-flow-continue ${result.has_more ? "" : "disabled"}>继续检索</button></div>`;
}

function paintFlowSearch(root, result) {
  const host = root.matches('[data-flow-read]') ? root : root.querySelector('[data-flow-read]');
  if (!host) return;
  const body = host.querySelector('tbody');
  body.innerHTML = flowRows(result.items, true);
  host.querySelector('.list-footer').innerHTML = flowScanFooter(result);
  bindPage(body);
  bindFlowSearch(host, next => paintFlowSearch(host, next));
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
  return transitionReview(Number(button.dataset.id), button.dataset.kind === "restore", async () => { closeDialogs(); await render(); });
}

function reviewCreatePage() {
  return '<section class="review-workflow-host" data-review-workflow><div class="busy">正在加载可审查流水…</div></section>';
}

async function mountEconomicReviewEditor(root) {
  return mountReviewWorkbench(root, state.params, async () => { toast("不可变审查已发布"); route("ledger-reviews"); });
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
  const dialog = modal("编辑最终流水标签", `<form data-form="tag-assignment" data-ledger="${ledgerId}" data-updated-time="${esc(assignment.updated_time || "")}" class="stack"><p>修改标签只影响对应维度；标签不变时，可勾选将该维度接管为人工。</p>${views.map((view) => `<div><label>${esc(view.name)}<select name="${esc(view.system_name)}" data-original="${esc(current[view.system_name] || "unclassified")}">${view.tags.filter((tag) => tag.status === "ACTIVE").map((tag) => `<option value="${esc(tag.system_name)}" ${(current[view.system_name] || "unclassified") === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`).join("")}</select></label><label><input type="checkbox" data-manual-view="${esc(view.system_name)}">将${esc(view.name)}接管为人工</label></div>`).join("")}<div class="actions"><button class="primary">保存标签</button></div></form>`);
  bindPage(dialog);
}

async function editLedgerAccount(ledgerId) {
  const detail = await request(`/paam/ledger/v1/flow/${ledgerId}`);
  const ids = [...new Set(detail.allocations.map(row => row.transaction_id ?? row.transaction_fact_id))];
  closeDialogs();
  route("reviews", new URLSearchParams({ facts: ids.join(",") }));
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
  return `<div class="import-workflow" data-import-workflow data-step="1"><nav class="import-stepper" aria-label="数据导入步骤"><button type="button" class="import-step active" data-action="import-step" data-step="1" aria-current="step"><span>1</span><strong>选择来源</strong><small>确认账单平台</small></button><button type="button" class="import-step" data-action="import-step" data-step="2"><span>2</span><strong>添加文件</strong><small>上传待导入账单</small></button><button type="button" class="import-step" data-action="import-step" data-step="3" disabled><span>3</span><strong>预览确认</strong><small>核对后写入</small></button></nav><form data-form="import-preview"><section class="panel import-source-panel" data-import-step-panel="1"><div class="section-head"><div><span class="step-kicker">步骤 1 / 3</span><h2 tabindex="-1">选择数据来源</h2></div><button type="button" class="quiet" data-page="import-history">查看导入历史 →</button></div><p class="import-section-help">不确定时选择自动识别，系统会从文件表头与内容判断来源。</p><div class="source-card-grid" role="group" aria-label="数据来源">${sourceCards}</div><label class="visually-hidden">数据来源<select name="source_type"><option value="">自动识别</option><option value="alipay">支付宝</option><option value="wechat">微信</option><option value="ccb">建设银行</option><option value="abc">农业银行</option><option value="cmb">招商银行</option></select></label><div class="step-nav-actions"><span>已选择：<strong data-selected-source>自动识别</strong></span><button type="button" class="primary" data-action="import-step" data-step="2">下一步：添加文件 →</button></div></section><section class="panel import-upload-panel" data-import-step-panel="2" hidden><div class="section-head"><div><span class="step-kicker">步骤 2 / 3</span><h2 tabindex="-1">添加账单文件</h2></div><span class="format-note">CSV · XLS · XLSX · PDF · ZIP</span></div><label class="import-dropzone" data-import-dropzone><input class="visually-hidden" type="file" name="files" multiple required accept=".csv,.xls,.xlsx,.zip,.pdf"><span class="dropzone-icon" aria-hidden="true">↥</span><strong>拖放账单到这里，或点击选择文件</strong><small>单次总计不超过 20 MiB，最多 100 个文件和 20,000 来源行</small><span class="dropzone-button">选择文件</span></label><div id="selected-files" class="selected-files"><div class="selected-files-empty">选择文件后，将在这里显示待预览清单。</div></div><details class="import-options"><summary>加密文件与高级选项</summary><div class="import-option-body"><label>账单时区<select name="timezone"><option value="Asia/Hong_Kong" ${selectedTimeZone() === "Asia/Hong_Kong" ? "selected" : ""}>香港</option><option value="Asia/Shanghai" ${selectedTimeZone() === "Asia/Shanghai" ? "selected" : ""}>上海</option><option value="Asia/Tokyo" ${selectedTimeZone() === "Asia/Tokyo" ? "selected" : ""}>东京</option><option value="Europe/London" ${selectedTimeZone() === "Europe/London" ? "selected" : ""}>伦敦</option><option value="America/New_York" ${selectedTimeZone() === "America/New_York" ? "selected" : ""}>纽约</option><option value="UTC" ${selectedTimeZone() === "UTC" ? "selected" : ""}>UTC</option></select><small>用于解释账单中没有时区的交易时间，默认香港。</small></label><label>ZIP / PDF 密码<input type="password" name="password" autocomplete="off" placeholder="仅用于本次解析，不会保存"><small>密码只随本次预览请求使用。</small></label></div></details><div class="import-submit-row"><button type="button" class="quiet" data-action="import-step" data-step="1">← 返回选择来源</button><div class="import-submit-copy"><strong>先预览，再写入</strong><small>解析前登记文件；确认前不创建金融事实。</small></div><button class="primary" data-action="preview-import">生成导入预览</button></div></section></form><section id="import-preview" data-import-step-panel="3" hidden></section></div>`;
}

const sourceLabels = {
  0: "未知", 1: "手工", 101: "支付宝", 102: "微信支付", 201: "建设银行", 202: "农业银行", 203: "招商银行",
};
const fileFormatLabels = { 0: "UNKNOWN", 1: "CSV", 2: "XLS", 3: "XLSX", 4: "PDF" };
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
  state.importPreviewGeneration = (state.importPreviewGeneration || 0) + 1;
  stopImportRead();
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

function historySummaryMarkup(summary) {
  return `<div class="history-metric"><span>匹配文件</span><strong>${summary.import_file_count}</strong><small>当前筛选结果</small></div><div class="history-metric"><span>完整导入</span><strong>${summary.imported_file_count}</strong><small>无异常完成</small></div><div class="history-metric"><span>已写入记录</span><strong>${summary.success_count}</strong><small>当前结果合计</small></div>`;
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
  const previous = $('[data-form="history-filter"]');
  const initialQuery = new URLSearchParams({
    page_index: previous?.dataset.page || "1",
    page_size: "10",
    sorter: JSON.stringify([{ key: "created_time", direction: "desc" }]),
  });
  if (previous) {
    const expressions = ['source_type', 'status'].filter((name) => previous.elements[name].value !== '')
      .map((name) => ({ key: name, op: '=', val: Number(previous.elements[name].value) }));
    if (expressions.length) initialQuery.set('filter', JSON.stringify(expressions.length === 1 ? expressions[0] : { op: 'AND', expression: expressions }));
  }
  const [result, summary] = await Promise.all([
    request(`/paam/import/v1/import_file/list?${initialQuery}`),
    request("/paam/import/v1/import_file/summary"),
  ]);
  const sourceOptions = Object.entries(sourceLabels).map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  const statusOptions = [0, 1, 2, 3].map((value) => `<option value="${value}">${esc(statusLabels[value])}</option>`).join("");
  return `<div class="history-summary" data-history-summary>${historySummaryMarkup(summary)}</div><section class="panel history-panel"><div class="section-head"><div><h2>导入文件</h2><p class="import-section-help">来源和状态筛选只更新下方结果。</p></div><button class="primary" data-page="import">＋ 导入新数据</button></div><form class="toolbar history-toolbar" data-form="history-filter"><label>来源<select name="source_type"><option value="">全部来源</option>${sourceOptions}</select></label><label>状态<select name="status"><option value="">全部状态</option>${statusOptions}</select></label><span class="history-updating" data-history-updating aria-live="polite"></span></form><div data-history-results>${historyResultsMarkup(result)}</div></section>`;
}

async function refreshHistoryResults(form, page = 1, background = false) {
  const interaction = interactionVersion;
  form.dataset.page = String(page);
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
    if (!form.isConnected || (background && (interaction !== interactionVersion || !canRefreshPage()))) return;
    preserveView($('#page-content'), () => {
      if (summaryRoot) summaryRoot.innerHTML = historySummaryMarkup(summary);
      if (resultsRoot) { resultsRoot.innerHTML = historyResultsMarkup(result); bindPage(resultsRoot); }
    });
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
const MAX_IMPORT_FILE_BYTES = 20 * 1024 * 1024;
const MAX_IMPORT_TOTAL_BYTES = 20 * 1024 * 1024;
const importExtensions = new Set(["csv", "xls", "xlsx", "pdf", "zip"]);

function validateImportFiles(files) {
  if (!files.length) throw new Error("请选择至少一个账单文件");
  if (files.length > MAX_IMPORT_FILES) throw new Error(`单次最多选择 ${MAX_IMPORT_FILES} 个文件`);
  const oversized = files.find((file) => file.size > MAX_IMPORT_FILE_BYTES);
  if (oversized) throw new Error(`${oversized.name} 超过单次20 MiB限制`);
  const unsupported = files.find((file) => !importExtensions.has(fileExtension(file.name).toLowerCase()));
  if (unsupported) throw new Error(`${unsupported.name} 的格式不受支持`);
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > MAX_IMPORT_TOTAL_BYTES) throw new Error("所选文件总计超过单次20 MiB限制");
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
  const issued = state.importPreviewGeneration = (state.importPreviewGeneration || 0) + 1;
  try {
    const files = [...form.elements.files.files];
    validateImportFiles(files);
    const source = form.elements.source_type.value || null;
    const password = form.elements.password.value || null;
    const timezone = form.elements.timezone.value || selectedImportTimeZone();
    setSelectedImportTimeZone(timezone);
    const payload = { timezone, files: await encodeImportFiles(files, source, password) };
    if (!form.isConnected || issued !== state.importPreviewGeneration) return;
    const preview = await jsonRequest("/paam/import/v1/preview", "POST", payload);
    if (!form.isConnected || issued !== state.importPreviewGeneration) return;
    state.importPlan = preview;
    form.elements.password.value = "";
    renderImportPlan();
  } finally {
    endSubmit(form);
  }
}
function renderImportPlan() {
  const root = $('#import-preview');
  if (root) preserveView(root, renderImportPlanContent);
}

function renderImportPlanContent() {
  const plan = state.importPlan;
  const root = $("#import-preview");
  if (!plan || !root) return;
  mountImportBatch(root, plan, current => { state.importPlan = current; syncImportSteps(); });
  showImportStep(3);
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
    return `<article data-view-card="${view.id}" class="tag-view-card${isActive ? "" : " archived"}" aria-labelledby="tag-view-${view.id}"><header class="tag-view-head"><div class="tag-view-meta"><div class="tag-view-title"><h3 id="tag-view-${view.id}">${esc(view.name)}</h3><span class="tag-view-status ${isActive ? "active" : "archived"}"><span aria-hidden="true">●</span>${esc(statusNames[view.status] || view.status)}</span></div><code>${esc(view.system_name)}</code></div><div class="tag-view-actions">${isActive ? `<button type="button" data-action="new-tag" data-id="${view.id}" aria-controls="tag-create-${view.id}" aria-expanded="false">＋ 添加标签</button>` : ""}<button class="quiet" type="button" data-action="view-status" data-id="${view.id}" data-status="${isActive ? "ARCHIVED" : "ACTIVE"}">${isActive ? "归档维度" : "恢复维度"}</button></div></header><div class="tag-pill-list">${tagsMarkup}${creator}</div></article>`;
  }).join("");
  return `<section class="tag-manager" aria-labelledby="tag-manager-title"><div class="tag-manager-head"><div><h2 id="tag-manager-title">标签维度</h2><p>${views.length ? `共 ${viewPage.total} 个维度，${activeCount} 个启用中${atViewLimit ? "；已达 100 个上限" : ""}` : "用维度组织同一类标签"}</p></div><button class="primary" data-action="new-view" ${atViewLimit ? 'disabled title="标签维度上限为 100"' : ""}>${atViewLimit ? "已达维度上限" : "＋ 新建维度"}</button></div><div class="tag-view-list">${cards || '<div class="panel empty-state">尚未创建标签维度</div>'}</div></section>`;
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
  bindFlowSearch(root, result => paintFlowSearch(root, result));
  bindAccountManagement(root, render);
  bindPosition(root, render);
  bindAutomation(root, render, toast, route);
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
      if (formName === "economic-filter") resetFlowSearch();
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
  $$('[data-action="edit-tags"]', root).forEach((button) => button.onclick = () => editTags(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-ledger-account"]', root).forEach((button) => button.onclick = () => editLedgerAccount(button.dataset.id).catch((error) => toast(error.message, true)));
  $('[data-action="new-view"]', root)?.addEventListener("click", () => simpleDictionaryDialog("view"));
  $$('[data-action="new-tag"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="new-tag-inline"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="cancel-tag"]', root).forEach((button) => button.onclick = () => closeInlineTag(button.closest("form")));
  $$('[data-action="view-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("view", button).catch((error) => toast(error.message, true)));
  $$('[data-action="tag-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("tag", button).catch((error) => toast(error.message, true)));
  const importForm = $('[data-form="import-preview"]', root);
  if (importForm) {
    $('[data-import-dropzone] small', importForm).textContent = "单次解码总计不超过20 MiB、100个文件；最多20,000来源行";
    $('.import-submit-copy small', importForm).textContent = "先创建来源预览；确认所选行后才写金融事实。";
  }
  if (importForm) {
    const limitCopy = $("[data-import-dropzone] small", importForm);
    if (limitCopy) limitCopy.textContent = "单次总计不超过 20 MiB，最多 100 个文件和 20,000 来源行";
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
  $('[data-form="dictionary"]', root)?.addEventListener("submit", submitDictionary);
  if ($('[data-form="dictionary"]', root)) bindTagSystemName($('[data-form="dictionary"]', root));
  $$('form[data-form]', root).forEach(bindCommandForm);
}

window.addEventListener("paam:automation-saved", async (event) => {
  toast(event.detail?.message || "自动化配置已保存");
  if (event.detail?.createdRuleId) {
    route("auto-rules", new URLSearchParams({ sort: "desc", rule_id: String(event.detail.createdRuleId) }));
    return;
  }
  await render();
});

function manualTagViewNames(form) {
  const changed = [...form.querySelectorAll("select[name]")]
    .filter((select) => select.value !== select.dataset.original)
    .map((select) => select.name);
  const claimed = [...form.querySelectorAll("[data-manual-view]:checked")]
    .map((checkbox) => checkbox.dataset.manualView);
  return [...new Set([...changed, ...claimed])];
}

async function submitTags(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/tag/v1/assignment/${form.dataset.ledger}`, "PUT", {
      expected_updated_time: form.dataset.updatedTime || null,
      tag_state: Object.fromEntries(data),
      view_names: manualTagViewNames(form),
    });
    closeDialogs(); toast("标签已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
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

$$('[data-page]').forEach((button) => button.onclick = () => route(button.dataset.page));
if (!location.hash) route("ledger"); else readRoute();

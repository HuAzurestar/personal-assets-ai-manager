import { request, jsonRequest } from "../api/client.js";
import { toast } from "../component/toast.js";
import { table } from "../component/table.js";
import {
  detailFields, detailList, detailPager, detailRelationRows, detailSection,
  detailTabs,
} from "../component/detail.js";
import { now, state } from "../state/ledger.js";
import {
  $, $$, date, esc, key, money,
  reviewTypeNames, roleNames, statusNames, typeNames,
} from "../util/core.js";
import {
  canonicalHash, pageModule, parseHash, shellMarkup, syncNavigation,
} from "../navigation.js";
import {
  accountsMarkup, cursorFromParam, monthBounds,
} from "./account.js";

const entryTypeValues = { TRANSACTION: 0, ACCOUNT_TRANSFER: 1, CLAIM: 2 };

function closeDialogs() {
  $$('dialog[open]').forEach((dialog) => dialog.close());
}
function amountValue(item) {
  return Number(item?.amount_value || 0);
}

function signedMoney(item, direction) {
  if (!amountValue(item)) return "0";
  return `${direction === "IN" ? "＋" : "−"} ${money(item)}`;
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
    target.innerHTML = page === "ledger-reviews"
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
  const makePath = (page) => `/paam/ledger/v1/flow/list?${new URLSearchParams({ page, page_size: "100", date_from: range.from, date_to: range.to })}`;
  const [economicSummary, first] = await Promise.all([
    request(`/paam/ledger/v1/flow/summary?${new URLSearchParams({ date_from: range.from, date_to: range.to })}`),
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
    if (flow.economic_type === "TRANSACTION") {
      if (flow.cash_direction === "IN") trend.income_value += flow.amount.amount_value;
      else trend.expense_value += flow.amount.amount_value;
      trend.net_value = trend.income_value - trend.expense_value;
    }
    trendMap.set(trendKey, trend);
    const typeCode = flow.economic_type;
    const activityKey = `${typeCode}:${currency}`;
    const activity = activityMap.get(activityKey) || { entry_type_code: typeCode, currency_code: currency, amount_scale: scale, in_amount_value: 0, out_amount_value: 0, nettable: true };
    if (flow.cash_direction === "IN") activity.in_amount_value += flow.amount.amount_value;
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
  if (extra.entryTypeCode in entryTypeValues) params.set("economic_type", extra.entryTypeCode);
  return params;
}

const detailTabItems = [
  ["ledger", "Transaction Fact", "导入后不可变的事实"],
  ["economy", "Ledger", "最终可阅读的账本"],
  ["ledger-reviews", "Review", "解释与分配历史"],
  ["ledger-imports", "Import File", "文件与导入事实"],
  ["ledger-tags", "Tag", "维度与标签值"],
];

function detailListView(options) {
  return detailList({ tabs: detailTabItems, ...options });
}

async function loadFactCandidates(maxItems) {
  const items = [];
  const pageSize = Math.min(maxItems, 100);
  let page = 1;
  while (items.length < maxItems) {
    const result = await request(`/paam/ledger/v1/fact/list?${new URLSearchParams({ page, page_size: pageSize })}`);
    items.push(...result.items);
    if (!result.items.length || items.length >= result.total) break;
    page += 1;
  }
  return items.slice(0, maxItems);
}

async function ledgerPage() {
  const q = (state.params.get("q") || "").trim();
  const currency = (state.params.get("currency_code") || "").trim().toUpperCase();
  const dateFrom = state.params.get("date_from") || "";
  const dateTo = state.params.get("date_to") || "";
  const page = Math.max(1, Number(state.params.get("page") || 1));
  const pageSize = Math.min(100, Math.max(1, Number(state.params.get("page_size") || 20)));
  const sortField = state.params.get("sort_field") || "occurred_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const filter = Object.fromEntries(Object.entries({
    currency_code: currency,
    date_from: dateFrom,
    date_to: dateTo,
  }).filter(([, value]) => value));
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    q,
    filter: JSON.stringify(filter),
    sorter: JSON.stringify({ field: sortField, order: sortOrder }),
  });
  const result = await request(`/paam/ledger/v1/transaction_fact/list?${query}`);
  const rows = result.items.map((fact) => `<tr class="detail-click-row" tabindex="0" data-fact-row="${fact.id}">
    <td>${date(fact.occurred_time)}</td><td><button type="button" class="detail-primary" data-action="fact-detail" data-id="${fact.id}"><strong>${esc(fact.summary || fact.counterparty || `事实 #${fact.id}`)}</strong><small>#${fact.id} · ${esc(fact.counterparty || "未知交易方")}</small></button></td>
    <td><span class="badge neutral">${fact.cash_direction === "IN" ? "流入" : "流出"}</span></td><td class="money ${fact.cash_direction === "IN" ? "income" : "expense"}">${signedMoney(fact, fact.cash_direction)}</td><td>${esc(fact.currency_code)}</td><td class="mono">${esc(fact.account_code)}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="fact-filter"><label class="grow">查找<input name="q" value="${esc(q)}" placeholder="ID、交易方、摘要或账户"></label><label>币种<input name="currency_code" maxlength="12" value="${esc(currency)}" placeholder="全部币种"></label><label>开始日期<input type="date" name="date_from" value="${esc(dateFrom)}"></label><label>结束日期<input type="date" name="date_to" value="${esc(dateTo)}"></label><label>排序<select name="sort_field"><option value="occurred_time" ${sortField === "occurred_time" ? "selected" : ""}>发生时间</option><option value="amount_value" ${sortField === "amount_value" ? "selected" : ""}>金额</option><option value="created_time" ${sortField === "created_time" ? "selected" : ""}>创建时间</option><option value="id" ${sortField === "id" ? "selected" : ""}>ID</option></select></label><label>顺序<select name="sort_order"><option value="desc" ${sortOrder === "desc" ? "selected" : ""}>降序</option><option value="asc" ${sortOrder === "asc" ? "selected" : ""}>升序</option></select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger">清空</button><button class="primary">查询</button></form>`;
  return detailListView({ active: "ledger", toolbar, title: "Transaction Fact", description: "外部账单接受后的不可变事实 PO；查找、筛选与排序均由服务端执行。", total: result.total, headers: ["发生时间", "事实", "方向", "金额", "币种", "来源账户", ""], rows, footer: detailPager(result, "ledger") });
}

async function showFactDetail(id) {
  const detail = await request(`/paam/ledger/v1/transaction_fact/${id}`);
  const fact = detail.transaction_fact;
  const evidence = detailRelationRows(detail.import_evidence, (item) => `<span><strong>${esc(item.filename)}</strong><small>Import File #${item.import_file_id} · 第 ${item.source_row_number} 行 · ${esc(item.source_type)}</small></span><span>${esc(item.parse_status)}</span>`);
  const allocations = detailRelationRows(detail.allocations, (item) => `<span><strong>Allocation #${item.id}</strong><small>Review #${item.review_id} · Fact #${item.fact_id} · Ledger #${item.economic_id || "待确认"}</small></span><strong>${money(item)}</strong>`);
  const reviews = detailRelationRows(detail.reviews, (item) => `<span><strong>Review #${item.id} · ${esc(item.behavior_code)}</strong><small>${esc(item.title || "未填写标题")} · ${esc(statusNames[item.status] || item.status)}</small></span><span>v${item.version}</span>`);
  const ledgers = detailRelationRows(detail.ledgers, (item) => `<span><strong>Ledger #${item.id} · ${esc(typeNames[item.economic_type] || item.economic_type)}</strong><small>${date(item.occurred_time)} · ${esc(item.account_code)}</small></span><strong>${money(item)}</strong>`);
  detailDrawer({
    title: fact.summary || fact.counterparty || `事实 #${fact.id}`,
    kicker: `TRANSACTION FACT #${fact.id}`,
    subtitle: "事实层记录导入来源，不直接代表最终经济分类。",
    body: `<section class="drawer-record-card"><div><span>${fact.cash_direction === "IN" ? "收入事实" : "支出事实"}</span><h3>${esc(fact.counterparty || "未知交易方")}</h3><small>${date(fact.occurred_time)} · ${esc(fact.account_code)}</small></div><strong class="ledger-fact-money ${fact.cash_direction === "IN" ? "plus" : "minus"}">${signedMoney(fact, fact.cash_direction)}</strong></section>${detailSection("规范事实（Transaction Fact）", detailFields([["Fact Key", fact.fact_key], ["摘要", fact.summary], ["币种", fact.currency_code], ["创建时间", date(fact.created_time)], ["更新时间", date(fact.updated_time)]]))}${detailSection("Import File 来源", evidence, "没有关联导入文件")}${detailSection("Allocation", allocations, "没有关联分配")}${detailSection("Review", reviews, "没有关联审查")}${detailSection("Ledger", ledgers, "没有关联账本")}`,
  });
}

async function economicPage() {
  const q = (state.params.get("q") || "").trim();
  const selectedType = state.params.get("economic_type") || "";
  const currency = (state.params.get("currency_code") || "").trim().toUpperCase();
  const sortField = state.params.get("sort_field") || "occurred_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const filter = Object.fromEntries(Object.entries({
    economic_type: selectedType in entryTypeValues ? [selectedType] : [],
    currency_code: currency ? [currency] : [],
    date_from: state.params.get("date_from") || "",
    date_to: state.params.get("date_to") || "",
  }).filter(([, value]) => Array.isArray(value) ? value.length : value));
  const query = new URLSearchParams({
    page: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    q,
    filter: JSON.stringify(filter),
    sorter: JSON.stringify({ field: sortField, order: sortOrder }),
  });
  const result = await request(`/paam/ledger/v1/flow/list?${query}`);
  state.detailEconomics = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-economic-row="${item.id}">
    <td>${date(item.occurred_time)}</td><td><button type="button" class="detail-primary" data-action="economic-detail" data-id="${item.id}"><strong>账本流水 #${item.id}</strong><small>${esc(item.account_code)}</small></button></td><td>${esc(typeNames[item.economic_type] || item.economic_type)}</td><td>${item.cash_direction === "IN" ? "流入" : "流出"}</td><td class="money ${item.cash_direction === "IN" ? "income" : "expense"}">${signedMoney(item.amount, item.cash_direction)}</td><td>v${item.projection_version}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="economic-filter"><label class="grow">查找<input name="q" value="${esc(q)}" placeholder="审查说明、交易方或摘要"></label><label>账本类型<select name="economic_type"><option value="">全部类型</option>${Object.keys(entryTypeValues).map((value) => `<option value="${value}" ${selectedType === value ? "selected" : ""}>${esc(typeNames[value] || value)}</option>`).join("")}</select></label><label>币种<input name="currency_code" maxlength="12" value="${esc(currency)}" placeholder="全部币种"></label><label>排序<select name="sort_field"><option value="occurred_time" ${sortField === "occurred_time" ? "selected" : ""}>发生时间</option><option value="amount_value" ${sortField === "amount_value" ? "selected" : ""}>金额</option><option value="projection_version" ${sortField === "projection_version" ? "selected" : ""}>投影版本</option><option value="id" ${sortField === "id" ? "selected" : ""}>ID</option></select></label><label>顺序<select name="sort_order"><option value="desc" ${sortOrder === "desc" ? "selected" : ""}>降序</option><option value="asc" ${sortOrder === "asc" ? "selected" : ""}>升序</option></select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="economy">清空</button><button class="primary">查询</button></form>`;
  return detailListView({ active: "economy", toolbar, title: "Ledger", description: "已生效的最终账本 PO；Tag 等关系仅在详情中显示。", total: result.total, headers: ["发生时间", "账本", "类型", "方向", "金额", "投影版本", ""], rows, footer: detailPager(result, "economy") });
}

async function showEconomicDetail(id) {
  const detail = await request(`/paam/ledger/v1/flow/${id}`);
  const flow = detail.flow;
  const allocations = detail.allocations.map((item) => `<div class="drawer-review-row"><span><strong>Allocation #${item.id}</strong><small>Fact #${item.fact_id} → Ledger #${item.economic_id}</small></span><strong>${money(item.amount)}</strong></div>`).join("");
  const facts = detail.facts.map((item) => `<div class="drawer-review-row"><span><strong>Fact #${item.id} · ${esc(item.summary || item.counterparty)}</strong><small>${date(item.occurred_time)} · Fact 来源账户 ${esc(item.account_code)}</small></span><strong>${money(item.amount)}</strong></div>`).join("");
  const reviews = detail.reviews.map((item) => `<div class="drawer-review-row"><span><strong>Review #${item.id} · ${esc(item.behavior_code)}</strong><small>${esc(item.title)} · ${esc(statusNames[item.status] || item.status)}</small></span><span>v${item.version}</span></div>`).join("");
  const typeCode = flow.economic_type;
  detailDrawer({ title: `账本流水 #${flow.id}`, kicker: `${esc(typeNames[typeCode] || typeCode)} · LEDGER #${flow.id}`, subtitle: `${date(flow.occurred_time)} · ${flow.cash_direction === "IN" ? "流入" : "流出"}`, body: `<section class="drawer-record-card"><div><span>已确认账本投影</span><h3>${esc(typeNames[typeCode] || typeCode)}</h3><small>${esc(flow.account_code)} · 单方向、单币种</small></div><strong class="ledger-fact-money ${flow.cash_direction === "IN" ? "plus" : "minus"}">${signedMoney(flow.amount, flow.cash_direction)}</strong></section><section class="drawer-section"><h3>来源事实</h3>${facts || '<p class="muted">没有关联事实</p>'}</section><section class="drawer-section"><h3>审查与分配</h3>${reviews}${allocations}</section>`, footer: `<button type="button" class="primary" data-action="edit-tags" data-id="${flow.id}">编辑标签</button>` });
}

function showSummaryDetail(type) {
  const item = state.detailSummaries.get(type);
  if (!item) return;
  const incoming = money({ amount_value: item.in_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code });
  const outgoing = money({ amount_value: item.out_amount_value, amount_scale: item.amount_scale, currency_code: item.currency_code });
  detailDrawer({
    title: typeNames[item.entry_type_code] || item.entry_type_code,
    kicker: `ENTRY TYPE · ${item.entry_type_code}`,
    subtitle: "查看当前业务类型的收支构成与核算口径。",
    body: `<div class="cards drawer-metrics"><div class="metric"><span>流入</span><strong>${incoming}</strong></div><div class="metric"><span>流出</span><strong>${outgoing}</strong></div></div><section class="drawer-section"><h3>核算说明</h3><p>${item.nettable ? "该业务允许在同一币种内按流入与流出核算。" : "该业务保留资金活动原貌，不进行跨业务净额化。"}</p></section>`,
    footer: `<button type="button" class="primary" data-action="summary-drilldown" data-type="${esc(item.entry_type_code)}">查看对应流水</button>`,
  });
}

async function ledgerReviewsPage() {
  const q = (state.params.get("q") || "").trim();
  const status = state.params.get("status") || "";
  const sortField = state.params.get("sort_field") || "updated_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const query = new URLSearchParams({
    page: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    q,
    filter: JSON.stringify(status ? { status } : {}),
    sorter: JSON.stringify({ field: sortField, order: sortOrder }),
  });
  const result = await request(`/paam/ledger/v1/review/list?${query}`);
  state.detailEconomicReviews = new Map(result.items.map((item) => [item.id, item]));
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-review-row="${item.id}">
    <td>${date(item.updated_time)}</td><td><button type="button" class="detail-primary" data-action="economic-review-detail" data-id="${item.id}"><strong>${esc(item.title || item.behavior_code || "未填写说明")}</strong><small>#${item.id} · ${esc(item.behavior_code || "未说明行为")}</small></button></td>
    <td><span class="badge ${item.status === "PENDING" ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td>${item.economic_count} 条账本流水</td><td>${item.allocation_count} 条分配</td><td>v${item.version}</td><td class="detail-arrow">→</td>
  </tr>`).join("");
  const toolbar = `<form class="detail-filter" data-form="detail-review-filter"><label class="grow">查找<input name="q" value="${esc(q)}" placeholder="ID、标题或行为代码"></label><label>状态<select name="status"><option value="">全部状态</option>${["PENDING", "CONFIRMED", "REVOKED"].map((value) => `<option value="${value}" ${status === value ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><label>排序<select name="sort_field"><option value="updated_time" ${sortField === "updated_time" ? "selected" : ""}>更新时间</option><option value="created_time" ${sortField === "created_time" ? "selected" : ""}>创建时间</option><option value="version" ${sortField === "version" ? "selected" : ""}>版本</option><option value="id" ${sortField === "id" ? "selected" : ""}>ID</option></select></label><label>顺序<select name="sort_order"><option value="desc" ${sortOrder === "desc" ? "selected" : ""}>降序</option><option value="asc" ${sortOrder === "asc" ? "selected" : ""}>升序</option></select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-reviews">清空</button><button class="primary">查询</button><button type="button" class="primary" data-action="new-economic-review">新建 Review</button></form>`;
  return detailListView({ active: "ledger-reviews", toolbar, title: "Review", description: "Review 是 Allocation 的集合；Fact 与 Ledger 关系必须通过 Allocation 解释。", total: result.total, headers: ["更新时间", "审查", "状态", "账本数量", "分配数量", "版本", ""], rows, footer: detailPager(result, "ledger-reviews") });
}

async function showEconomicReview(id) {
  const item = await request(`/paam/ledger/v1/review/${id}`);
  const facts = item.facts.map((fact) => `<div class="drawer-review-row"><span><strong>Fact #${fact.id} · ${esc(fact.summary || fact.counterparty || "未命名事实")}</strong><small>${date(fact.occurred_time)} · ${esc(fact.account_code)}</small></span><strong>${money(fact)}</strong></div>`).join("");
  const economics = item.economics.map((flow) => {
    const typeCode = flow.economic_type;
    return `<div class="drawer-review-row"><span><strong>Ledger #${flow.id} · ${esc(typeNames[typeCode] || typeCode)}</strong><small>${flow.cash_direction === "IN" ? "流入" : "流出"} · ${esc(flow.currency_code)}</small></span><strong>${money({ amount_value: flow.amount_value, amount_scale: flow.amount_scale, currency_code: flow.currency_code })}</strong></div>`;
  }).join("");
  const allocations = item.allocations.map((row) => `<div class="drawer-review-row"><span><strong>Fact #${row.fact_id} → Ledger #${row.economic_id || "待确认"}</strong></span><strong>${money({ amount_value: row.amount_value, amount_scale: row.amount_scale, currency_code: row.currency_code })}</strong></div>`).join("");
  const history = item.history.map((row) => `<div class="drawer-review-row"><span><strong>v${row.version} · ${esc(row.operation)}</strong><small>${date(row.created_time)} · ${esc(row.actor)}</small></span><span>${esc(row.reason || "未填写原因")}</span></div>`).join("");
  let action = "";
  if (item.status === "PENDING") action = `<button type="button" class="primary" data-action="economic-review-transition" data-kind="confirm" data-id="${item.id}" data-version="${item.version}">确认审查</button>`;
  if (item.status === "CONFIRMED") action = `<button type="button" data-action="economic-review-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销并恢复默认交易</button>`;
  if (item.status === "REVOKED") action = `<button type="button" class="primary" data-action="economic-review-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复审查</button>`;
  detailDrawer({ title: item.title || `审查 #${item.id}`, kicker: `REVIEW #${item.id} · ${item.behavior_code}`, subtitle: `${esc(statusNames[item.status] || item.status)} · 版本 ${item.version}`, body: `<section class="drawer-section"><h3>Transaction Fact</h3>${facts || '<p class="muted">没有关联事实</p>'}</section><section class="drawer-section"><h3>Allocation</h3>${allocations || '<p class="muted">没有分配关系</p>'}</section><section class="drawer-section"><h3>Ledger</h3>${economics || '<p class="muted">待确认，尚未生成账本流水</p>'}</section><section class="drawer-section"><h3>审计历史</h3>${history || '<p class="muted">没有历史记录</p>'}</section>`, footer: action });
}

async function transitionEconomicReview(button) {
  const labels = { confirm: "确认", revoke: "撤销", restore: "恢复" };
  if (!confirm(`${labels[button.dataset.kind]}这次经济审查？`)) return;
  await jsonRequest(`/paam/ledger/v1/review/${button.dataset.id}/${button.dataset.kind}`, "POST", { expected_version: Number(button.dataset.version), reason: `人工${labels[button.dataset.kind]}经济审查`, idempotency_key: key() });
  closeDialogs();
  toast(`${labels[button.dataset.kind]}完成，经济流水已重新投影`);
  await render();
}

async function openEconomicReviewEditor() {
  const facts = await loadFactCandidates(100);
  if (!facts.length) throw new Error("当前没有可分配的事实流水");
  const selected = new Set();
  const values = new Map();
  let sequence = 1;
  const economics = [{ key: `economic-${sequence}`, type: "TRANSACTION" }];
  const dialog = modal("新建经济审查", `<form data-form="economic-review-create" class="review-wizard stack"><section><h3>1. 选择事实流水</h3><div data-economic-review-facts class="review-fact-choices"></div></section><section><div class="section-head"><h3>2. 定义账本流水</h3><button type="button" data-action="add-economic">＋ 添加账本流水</button></div><div data-economic-definitions class="stack"></div></section><section><h3>3. 分配金额</h3><p class="muted">每条账本流水只能分配一条事实；一条事实可以拆成多条账本流水。</p><div data-allocation-matrix></div></section><label>行为代码<input name="behavior_code" maxlength="40" placeholder="例如 ADVANCE、LOAN、FX_EXCHANGE" required></label><label>标题<textarea name="title" maxlength="160"></textarea></label><label>操作原因<input name="reason" maxlength="2000"></label><div class="actions"><button type="button" data-close>取消</button><button class="primary">保存为待确认审查</button></div></form>`);
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
        if (amount > 0) allocations.push({ fact_id: fact.id, economic_key: input.dataset.economic, amount_value: amount });
      });
      if (!allocations.length) throw new Error("至少填写一条大于 0 的分配关系");
      const allocationCounts = allocations.reduce((counts, item) => counts.set(item.economic_key, (counts.get(item.economic_key) || 0) + 1), new Map());
      if ([...allocationCounts.values()].some((count) => count !== 1)) throw new Error("每条账本流水必须且只能关联一条事实");
      const used = new Set(allocations.map((item) => item.economic_key));
      const definitions = economics.filter((item) => used.has(item.key)).map((item) => ({
        client_key: item.key,
        economic_type: data.get(`type-${item.key}`),
      }));
      const created = await jsonRequest("/paam/ledger/v1/review", "POST", {
        behavior_code: data.get("behavior_code"),
        title: data.get("title") || "",
        result: {}, economics: definitions, allocations,
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
  const q = (state.params.get("q") || "").trim();
  const sourceType = state.params.get("source_type") || "";
  const status = state.params.get("status") || "";
  const sortField = state.params.get("sort_field") || "created_time";
  const sortOrder = state.params.get("sort_order") || "desc";
  const filter = Object.fromEntries(Object.entries({
    source_type: sourceType,
    status,
  }).filter(([, value]) => value));
  const query = new URLSearchParams({
    page: state.params.get("page") || "1",
    page_size: state.params.get("page_size") || "20",
    q,
    filter: JSON.stringify(filter),
    sorter: JSON.stringify({ field: sortField, order: sortOrder }),
  });
  const result = await request(`/paam/import/v1/import_file/list?${query}`);
  const rows = result.items.map((item) => `<tr class="detail-click-row" tabindex="0" data-import-file-row="${item.id}"><td>${date(item.created_time)}</td><td><button type="button" class="detail-primary" data-action="import-file-detail" data-id="${item.id}"><strong>${esc(item.filename)}</strong><small>Import File #${item.id} · ${esc(item.batch_code || "无批次码")}</small></button></td><td>${esc(sourceLabels[item.source_type] || item.source_type)}</td><td>${esc(item.file_format)}</td><td>${item.success_count} / ${item.total_count}</td><td><span class="badge ${item.status === "IMPORTED" ? "neutral" : "warn"}">${esc(statusLabels[item.status] || item.status)}</span></td><td class="detail-arrow">→</td></tr>`).join("");
  const sourceOptions = [...new Set(Object.entries(sourceLabels).map(([value, label]) => `<option value="${esc(value)}" ${sourceType === value ? "selected" : ""}>${esc(label)}</option>`))].join("");
  const toolbar = `<form class="detail-filter" data-form="detail-import-filter"><label class="grow">查找<input name="q" value="${esc(q)}" placeholder="ID、文件名、来源或批次码"></label><label>来源<select name="source_type"><option value="">全部来源</option>${sourceOptions}</select></label><label>状态<select name="status"><option value="">全部状态</option>${["PENDING", "IMPORTED", "FAILED"].map((value) => `<option value="${value}" ${status === value ? "selected" : ""}>${esc(statusLabels[value] || value)}</option>`).join("")}</select></label><label>排序<select name="sort_field"><option value="created_time" ${sortField === "created_time" ? "selected" : ""}>导入时间</option><option value="filename" ${sortField === "filename" ? "selected" : ""}>文件名</option><option value="id" ${sortField === "id" ? "selected" : ""}>ID</option></select></label><label>顺序<select name="sort_order"><option value="desc" ${sortOrder === "desc" ? "selected" : ""}>降序</option><option value="asc" ${sortOrder === "asc" ? "selected" : ""}>升序</option></select></label><button type="button" class="quiet" data-action="detail-clear" data-page-id="ledger-imports">清空</button><button class="primary">查询</button></form>`;
  return detailListView({ active: "ledger-imports", toolbar, title: "Import File", description: "每行代表一个导入文件 PO；Transaction Fact 是只读的详情子资源。", total: result.total, headers: ["导入时间", "文件", "来源", "格式", "成功 / 总数", "状态", ""], rows, footer: detailPager(result, "ledger-imports") });
}

async function showImportFileDetail(id) {
  const factQuery = new URLSearchParams({
    page: "1",
    page_size: "20",
    filter: "{}",
    sorter: JSON.stringify({ field: "occurred_time", order: "desc" }),
  });
  const [detail, facts] = await Promise.all([
    request(`/paam/import/v1/import_file/${id}`),
    request(`/paam/import/v1/import_file/${id}/transaction_fact/list?${factQuery}`),
  ]);
  const item = detail.import_file;
  const factRows = facts.items.map((fact) => `<tr><td>#${fact.id}</td><td>${date(fact.occurred_time)}</td><td>${esc(fact.summary || fact.counterparty || "—")}</td><td>${esc(fact.cash_direction)}</td><td class="money">${money(fact)}</td><td>${esc(fact.currency_code)}</td></tr>`);
  const childTable = factRows.length ? `${table(["Fact", "发生时间", "摘要", "方向", "金额", "币种"], factRows)}<p class="muted">显示 ${facts.items.length} / ${facts.total} 条；完整结果可通过 Transaction Fact 子资源分页查询。</p>` : "";
  detailDrawer({
    title: item.filename || `Import File #${item.id}`,
    kicker: `IMPORT FILE #${item.id}`,
    subtitle: `${esc(sourceLabels[item.source_type] || item.source_type)} · ${esc(statusLabels[item.status] || item.status)}`,
    body: `${detailSection("Import File", detailFields([["Batch Code", item.batch_code], ["机构", item.institution_code], ["文件格式", item.file_format], ["SHA-256", item.sha256], ["覆盖期间", `${item.period_start || "—"} – ${item.period_end || "—"}`], ["总行数", item.total_count], ["成功", item.success_count], ["跳过", item.skip_count], ["异常", item.issue_count], ["创建时间", date(item.created_time)], ["更新时间", date(item.updated_time)]]))}${detailSection("Transaction Fact", childTable, "没有关联 Transaction Fact")}`,
  });
}

async function ledgerTagsPage() {
  return `${detailTabs("ledger-tags", detailTabItems)}${await tagsPage()}`;
}

async function editTags(ledgerId) {
  const renderVersion = state.renderVersion;
  const [viewPage, detail] = await Promise.all([
    request("/paam/tag/v1/view/list?page=1&page_size=100"),
    request(`/paam/ledger/v1/flow/${ledgerId}`),
  ]);
  const views = viewPage.items;
  if (renderVersion !== state.renderVersion) return;
  if (!views.length) return toast("请先创建标签维度", true);
  const current = Object.fromEntries(detail.flow.tags.map((item) => [item.view_system_name, item.tag_system_name]));
  const dialog = modal("编辑最终流水标签", `<form data-form="tag-assignment" data-ledger="${ledgerId}" data-version="${detail.flow.projection_version}" class="stack">${views.map((view) => `<label>${esc(view.name)}<select name="${esc(view.system_name)}">${view.tags.map((tag) => `<option value="${esc(tag.system_name)}" ${(current[view.system_name] || "unclassified") === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`).join("")}</select></label>`).join("")}<div class="actions"><button class="primary">保存标签</button></div></form>`);
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
  const [result, accountPage] = await Promise.all([
    request("/paam/import/v1/batch/list?page=1&page_size=10"),
    request("/paam/import/v1/account/list?page=1&page_size=100"),
  ]);
  const accounts = accountPage.items;
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
  if (account) params.set("account_code", account);
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
    const result = await request(`/paam/import/v1/batch/${dialog.dataset.batchId}/row/list?page=${page}&page_size=${pageSize}`, {
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
  const viewPage = await request("/paam/tag/v1/view/list?page=1&page_size=100&include_archived=true");
  const views = viewPage.items;
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
  const reviewQuery = new URLSearchParams({
    page: state.params.get("review_page") || "1",
    page_size: "25",
  });
  reviewQuery.set("filter", JSON.stringify({
    exclude_behavior_code: "DEFAULT",
    ...(state.params.get("status") ? { status: state.params.get("status") } : {}),
  }));
  const conflictQuery = new URLSearchParams({
    page: state.params.get("conflict_page") || "1",
    page_size: "25",
    review_type: "FACT_CONFLICT",
  });
  const [result, candidates, pending, conflicts] = await Promise.all([
    request(`/paam/ledger/v1/review/list?${reviewQuery}`),
    loadFactCandidates(500),
    request(`/paam/ledger/v1/review/list?${new URLSearchParams({ page: "1", page_size: "1", filter: JSON.stringify({ status: "PENDING", exclude_behavior_code: "DEFAULT" }) })}`),
    request(`/paam/review/v1/case/page?${conflictQuery}`),
  ]);
  const rows = result.items.map((item) => `<tr><td>${item.id}</td><td><strong>${esc(item.behavior_code || "未说明行为")}</strong><br><small>${esc(item.title || "未填写说明")}</small></td><td><span class="badge ${item.status === "PENDING" ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td>${item.economic_count}</td><td>${item.allocation_count}</td><td>v${item.version}</td><td><button data-action="economic-review-detail" data-id="${item.id}">处理 / 详情</button></td></tr>`);
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const paging = `<div class="pagination"><span>共 ${result.total} 条 · 第 ${result.page}/${pages} 页</span><button data-action="review-page" data-param="review_page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="review-page" data-param="review_page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
  const conflictRows = conflicts.items.map((item) => `<tr><td>${item.id}</td><td><strong>${esc(item.title || "事实冲突")}</strong><br><small>${item.lines.length} 条候选事实</small></td><td><span class="badge ${item.status === "PENDING" ? "warn" : "neutral"}">${esc(statusNames[item.status] || item.status)}</span></td><td>v${item.version}</td><td><button data-action="review-detail" data-id="${item.id}">处理 / 详情</button></td></tr>`);
  const conflictPages = Math.max(1, Math.ceil(conflicts.total / conflicts.page_size));
  const conflictPaging = `<div class="pagination"><span>共 ${conflicts.total} 条 · 第 ${conflicts.page}/${conflictPages} 页</span><button data-action="review-page" data-param="conflict_page" data-value="${conflicts.page - 1}" ${conflicts.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="review-page" data-param="conflict_page" data-value="${conflicts.page + 1}" ${conflicts.page >= conflictPages ? "disabled" : ""}>下一页</button></div>`;
  const launchers = `<section class="task-launchers" aria-label="常用工作">
    <article><span class="task-number">01</span><div><span class="eyebrow">IMPORT</span><h2>账单导入</h2><p>上传文件、核对预览，再写入不可变的账单事实。</p></div><div class="task-meta"><span>${candidates.length} 条可分配事实</span><span>支持 CSV / XLSX / PDF / ZIP</span></div><button type="button" class="primary" data-page="import">进入账单导入</button></article>
    <article><span class="task-number">02</span><div><span class="eyebrow">REVIEW</span><h2>创建经济审查</h2><p>用明确的 Allocation 描述事实如何形成最终账本流水。</p></div><div class="task-meta"><span>${pending.total} 个待确认审查</span><span>确认后才生成账本流水</span></div><button type="button" class="primary" data-action="new-economic-review">新建经济审查</button></article>
  </section>`;
  return `<div class="review-dashboard">${launchers}<section class="review-metrics"><div><span>可分配事实</span><strong>${candidates.length}</strong><small>每条 Ledger 只关联一个 Fact</small></div><div><span>待确认 Review</span><strong>${pending.total}</strong><small>确认前不会生成账本流水</small></div><div><span>事实冲突</span><strong>${conflicts.total}</strong><small>继续使用独立冲突处理流程</small></div></section>
  <section class="panel"><div class="section-head"><div><h2>经济审查</h2><p>Review 解释行为，Allocation 明确 Fact 与 LedgerEntry 的金额关系。</p></div><button class="primary" data-action="new-economic-review">新建经济审查</button></div><form class="toolbar review-toolbar" data-form="review-filter"><label>状态<select name="status"><option value="">全部状态</option>${["PENDING","CONFIRMED","REVOKED"].map((value) => `<option value="${value}" ${state.params.get("status") === value ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><button>筛选</button></form>${rows.length ? table(["ID", "行为", "状态", "Ledger 数", "Allocation 数", "版本", ""], rows) : '<div class="empty-state">没有符合条件的经济审查。</div>'}${paging}</section>
  <section class="panel"><div class="section-head"><div><h2>事实冲突</h2><p>导入去重无法自动裁决的事实，仍在专用流程中解决。</p></div></div>${conflictRows.length ? table(["ID", "冲突", "状态", "版本", ""], conflictRows) : '<div class="empty-state">没有事实冲突。</div>'}${conflictPaging}</section></div>`;
}

async function showReview(id) {
  const renderVersion = state.renderVersion;
  const item = await request(`/paam/review/v1/case/detail/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const lines = item.lines.map((line) => `<tr><td>#${line.bill_id}</td><td><strong>${esc(roleNames[line.role] || line.role)}</strong><br><small>${esc(line.role)}</small></td><td>${money({amount_value:line.amount_value,amount_scale:line.amount_scale,currency_code:line.currency_code})}</td><td>${esc(line.party || "—")}</td></tr>`);
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
      params.set("page", "1");
      params.set("page_size", state.params.get("page_size") || "20");
      route(pageId, params);
    });
  });
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
  $('[data-action="new-economic-review"]', root)?.addEventListener("click", () => openEconomicReviewEditor().catch((error) => toast(error.message, true)));
  $$('[data-summary-row],[data-fact-row],[data-economic-row],[data-review-row],[data-import-row],[data-import-file-row]', root).forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button,input,label,select,a")) return;
      if (row.dataset.summaryRow) showSummaryDetail(row.dataset.summaryRow);
      if (row.dataset.factRow) showFactDetail(row.dataset.factRow).catch((error) => toast(error.message, true));
      if (row.dataset.economicRow) showEconomicDetail(row.dataset.economicRow).catch((error) => toast(error.message, true));
      if (row.dataset.reviewRow) showEconomicReview(row.dataset.reviewRow).catch((error) => toast(error.message, true));
      if (row.dataset.importRow) showImportBatch($("[data-action='batch-rows']", row));
      if (row.dataset.importFileRow) showImportFileDetail(row.dataset.importFileRow).catch((error) => toast(error.message, true));
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
    state.accountMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    const params = new URLSearchParams(state.params);
    params.set("month", `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
    route("summary", params);
  });
  $$('[data-action="account-metric"]', root).forEach((button) => button.onclick = () => {
    route("economy", accountLedgerParams());
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
  $$('[data-action="review-page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params); params.set(button.dataset.param || "review_page", button.dataset.value); route("reviews", params);
  });
  $$('[data-action="history-page"]', root).forEach((button) => button.onclick = () => {
    const form = $('[data-form="history-filter"]');
    if (form) refreshHistoryResults(form, Number(button.dataset.value));
  });
  $$('[data-action="review-detail"]', root).forEach((button) => button.onclick = () => showReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-tags"]', root).forEach((button) => button.onclick = () => editTags(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="account"]', root).forEach((button) => button.onclick = () => editAccount(button));
  $('[data-action="new-view"]', root)?.addEventListener("click", () => simpleDictionaryDialog("view"));
  $$('[data-action="new-tag"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="new-tag-inline"]', root).forEach((button) => button.onclick = () => openInlineTag(button));
  $$('[data-action="cancel-tag"]', root).forEach((button) => button.onclick = () => closeInlineTag(button.closest("form")));
  $$('[data-action="view-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("view", button).catch((error) => toast(error.message, true)));
  $$('[data-action="tag-status"]', root).forEach((button) => button.onclick = () => dictionaryStatus("tag", button).catch((error) => toast(error.message, true)));
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
  $('[data-form="review-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("review_page", "1"); route("reviews", params); });
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
  $('[data-form="conflict"]', root)?.addEventListener("submit", submitConflict);
  $$('form[data-form]', root).forEach(bindCommandForm);
}

async function submitTags(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  if (!beginSubmit(form)) return;
  try {
    await jsonRequest(`/paam/tag/v1/assignment/${form.dataset.ledger}`, "PUT", { tag_state: Object.fromEntries(data), expected_projection_version: Number(form.dataset.version) });
    closeDialogs(); toast("标签已保存"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function submitAccount(event) {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  try {
    await jsonRequest(`/paam/review/v1/account/set/${form.dataset.fact}`, "PUT", { ...data, expected_version: Number(form.dataset.version), idempotency_key: idempotencyKey });
    closeDialogs(); toast("账户修正已保存"); await render();
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
  const dialog = modal(kind === "view" ? "新建标签维度" : "新增标签", `<form data-form="dictionary" data-kind="${kind}" data-view="${viewId}" class="stack"><label>显示名称<input name="name" required maxlength="120"></label><label>系统名称<input name="system_name" required pattern="[a-z][a-z0-9_]{0,63}" placeholder="lower_case_name"></label><div class="actions"><button class="primary">保存</button></div></form>`, false); bindPage(dialog);
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
    await jsonRequest(`/paam/import/v1/fact-conflict/${button.dataset.id}/${button.dataset.kind}`, "POST", { expected_version: Number(button.dataset.version), reason: "用户处理事实冲突", idempotency_key: key() });
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
  try { await jsonRequest(`/paam/import/v1/fact-conflict/${form.dataset.id}/resolve`, "POST", { ...data, expected_version: Number(form.dataset.version), idempotency_key: idempotencyKey }); closeDialogs(); toast("事实冲突已解决"); await render(); } catch (error) { endSubmit(form); showFormError(form, error); }
}

$$('[data-page]').forEach((button) => button.onclick = () => route(button.dataset.page));
if (!location.hash) route("ledger"); else readRoute();

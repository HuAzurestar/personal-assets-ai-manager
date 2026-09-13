const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const key = () => crypto.randomUUID();
const date = (value) => String(value || "").replace("T", " ").slice(0, 16);
const typeNames = {
  INCOME: "收入", EXPENSE: "支出", AA: "AA", LOAN_BORROW: "借入",
  LOAN_LEND: "借出", REFUND: "退款", TRANSFER: "转账",
  FX_EXCHANGE: "换汇", UNRESOLVED: "待核验",
};
const statusNames = {
  PENDING: "待确认", CONFIRMED: "已确认", REVOKED: "已撤销",
  REJECTED: "已忽略", DEFAULT: "默认", COMPLETE: "完整",
  PARTIAL: "部分", CONFLICT: "冲突", ACTIVE: "启用中", ARCHIVED: "已归档",
};
const now = new Date();
const state = {
  page: "summary",
  params: new URLSearchParams(),
  importPlan: null,
  renderVersion: 0,
  historyFilterTimer: null,
  historyRequestController: null,
  historyRequestVersion: 0,
  historyAccountNames: new Map(),
  ledgerCalendar: {
    year: now.getFullYear(),
    month: now.getMonth(),
    next: "start",
    error: "",
  },
};

async function request(url, options = {}) {
  const response = await fetch(url, options).catch((error) => {
    if (error.name === "AbortError") throw error;
    throw new Error("无法连接本地服务");
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const text = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("；")
      : detail || `请求失败（${response.status}）`;
    throw new Error(text);
  }
  return payload && payload.status === "success" && Object.hasOwn(payload, "body")
    ? payload.body
    : payload;
}
const jsonRequest = (url, method, body) => request(url, {
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function money(item) {
  if (!item) return "—";
  const value = Number(item.amount_value) / (10 ** Number(item.amount_scale));
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency", currency: item.currency_code, maximumFractionDigits: item.amount_scale,
    }).format(value);
  } catch {
    return `${value.toFixed(item.amount_scale)} ${item.currency_code}`;
  }
}
function toast(text, error = false) {
  $(".toast")?.remove();
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.textContent = text;
  const openDialogs = $$('dialog[open]');
  (openDialogs.at(-1) || document.body).append(node);
  setTimeout(() => node.remove(), 4500);
}
function closeDialogs() {
  $$('dialog[open]').forEach((dialog) => dialog.close());
}
function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((item) => `<th>${item}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function tags(entry) {
  return `<div class="tag-list">${entry.tags.length
    ? entry.tags.map((item) => `<span class="tag" title="${esc(item.view_name)}">${esc(item.tag_name)}</span>`).join("")
    : '<span class="muted">无标签维度</span>'}</div>`;
}
function pager(result) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const visible = [...new Set([1, pages, result.page - 1, result.page, result.page + 1])]
    .filter((page) => page >= 1 && page <= pages)
    .sort((left, right) => left - right);
  const numbers = [];
  visible.forEach((page, index) => {
    if (index && page - visible[index - 1] > 1) numbers.push('<span class="page-gap">…</span>');
    numbers.push(`<button data-action="page" data-value="${page}" class="${page === result.page ? "active" : ""}" ${page === result.page ? 'aria-current="page"' : ""}>${page}</button>`);
  });
  const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.total, result.page * result.page_size);
  return `<div class="pagination ledger-pagination">
    <label class="page-size">每页<select data-action="ledger-page-size"><option value="10" ${result.page_size === 10 ? "selected" : ""}>10 条</option><option value="25" ${result.page_size === 25 ? "selected" : ""}>25 条</option><option value="50" ${result.page_size === 50 ? "selected" : ""}>50 条</option></select></label>
    <div class="page-buttons"><button data-action="page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""} aria-label="上一页">‹</button>${numbers.join("")}<button data-action="page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""} aria-label="下一页">›</button></div>
    <span class="range">${start}–${end} / ${result.total}</span>
  </div>`;
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

const nav = [
  ["summary", "概览", "收支与业务活动"],
  ["ledger", "流水", "最终账本"],
  ["import", "数据导入", "上传并预览账单"],
  ["import-history", "导入历史", "查看历史导入批次"],
  ["tags", "标签", "标签维度"],
  ["reviews", "审查", "统一 Review"],
];
$("#app").innerHTML = `<div class="workspace target-shell"><aside class="sidebar"><div class="brand"><img src="/static/personal-assets-ai-manager.svg" alt=""><div class="brand-text"><strong>个人账本</strong><small>PIRC-9 目标模型</small></div></div><nav aria-label="主导航">${nav.map(([id, label, title]) => `<button data-page="${id}" title="${title}"><span class="nav-icon">•</span><span class="nav-label">${label}</span></button>`).join("")}</nav><div class="sidebar-foot"><small>本地 SQLite · 事实与审查分层</small></div></aside><main class="workspace-main" id="content" tabindex="-1"><header class="page-header"><div><div class="eyebrow">PIRC-9 LEDGER</div><h1 id="title"></h1><p id="help"></p></div><button class="primary" data-page="import">导入账单</button></header><div id="page-content" aria-live="polite"></div></main></div>`;

const pageInfo = {
  summary: ["收支概览", "只读取热投影；不加载原始文本和历史。"],
  ledger: ["实际流水", "收支由最终投影决定；业务类型只说明事实之间的聚合关系。"],
  import: ["数据导入", "选择账单来源，上传文件并在写入前逐项预览。"],
  "import-history": ["导入历史", "查看已经写入的文件、处理结果和原始行记录。"],
  tags: ["标签管理", "按维度管理标签；在卡片内即可快速添加。"],
  reviews: ["统一审查", "财务、标签、账户与事实冲突共用一套历史模型。"],
};

function route(page, params = new URLSearchParams()) {
  location.hash = `${page}${params.toString() ? `?${params}` : ""}`;
}
function readRoute() {
  const [page, query = ""] = location.hash.slice(1).split("?");
  const nextPage = pageInfo[page] ? page : "summary";
  if (nextPage !== "import-history") {
    clearTimeout(state.historyFilterTimer);
    state.historyRequestController?.abort();
  }
  state.page = nextPage;
  state.params = new URLSearchParams(query);
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
  $$('[data-page]').forEach((button) => button.classList.toggle("active", button.dataset.page === state.page));
  root.innerHTML = '<div class="busy">正在加载…</div>';
  try {
    const content = await ({
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

async function summaryPage() {
  const query = new URLSearchParams();
  for (const name of ["date_from", "date_to"]) if (state.params.get(name)) query.set(name, state.params.get(name));
  const data = await request(`/paam/ledger/v1/summary?${query}`);
  const total = data.totals[0];
  const metric = (label, value, note = "") => `<div class="metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
  const totals = data.totals.length ? data.totals.map((item) => {
    const asMoney = (value) => money({ amount_value: value, amount_scale: item.amount_scale, currency_code: item.currency_code });
    return `<tr><td>${esc(item.currency_code)}</td><td class="money income">${asMoney(item.income_value)}</td><td class="money">${asMoney(item.expense_value)}</td><td class="money">${asMoney(item.refund_offset_value)}</td><td class="money">${asMoney(item.net_value)}</td></tr>`;
  }) : [];
  return `<form class="toolbar" data-form="summary-filter"><label>开始日期<input type="date" name="date_from" value="${esc(state.params.get("date_from") || "")}"></label><label>结束日期<input type="date" name="date_to" value="${esc(state.params.get("date_to") || "")}"></label><button>应用</button><button type="button" data-action="clear-summary">全部日期</button></form><div class="cards">${metric("实际流水", data.entry_count, "热投影行数")}${metric("待完善", data.provisional_count, "部分分配或待核验")}${metric("币种", data.totals.length, "分别统计，不隐式换汇")}${metric("净额", total ? money({ amount_value: total.net_value, amount_scale: total.amount_scale, currency_code: total.currency_code }) : "—", total ? `主要币种 ${total.currency_code}` : "暂无数据")}</div><section class="panel"><div class="section-head"><h2>按币种汇总</h2><small>${esc(data.basis_version)}</small></div>${totals.length ? table(["币种", "实际收入", "实际支出", "退款抵扣", "净额"], totals) : '<div class="empty-state">还没有可汇总的流水</div>'}</section><section class="panel"><h2>特殊业务活动</h2><div class="activity-grid">${data.activities.length ? data.activities.map((item) => `<div class="activity-item"><strong>${esc(typeNames[item.ledger_type] || item.ledger_type)}</strong><p>${money({amount_value:item.in_amount_value,amount_scale:item.amount_scale,currency_code:item.currency_code})} 入 / ${money({amount_value:item.out_amount_value,amount_scale:item.amount_scale,currency_code:item.currency_code})} 出</p><small>${item.nettable ? "可计算净额" : "跨币种或仅展示活动"}</small></div>`).join("") : '<span class="muted">暂无 AA、借贷、退款、转账或换汇活动。</span>'}</div></section>`;
}

async function ledgerPage() {
  const query = new URLSearchParams(state.params);
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
  const cards = result.items.map((entry) => `<article class="ledger-card" data-ledger-card="${entry.id}">
    <button type="button" class="ledger-card-summary" data-action="ledger-toggle" data-id="${entry.id}" aria-expanded="false">
      <span class="ledger-card-time"><strong>${date(entry.start_time).slice(0, 10)}</strong><small>${date(entry.start_time).slice(11) || "00:00"} · #${entry.id}</small></span>
      <span class="ledger-card-copy"><span class="ledger-card-title"><strong>${esc(entry.title || "未命名流水")}</strong><span class="ledger-business-type">${esc(typeNames[entry.ledger_type] || entry.ledger_type)}</span></span><small>${esc(statusNames[entry.allocation_status] || entry.allocation_status)} · 投影 v${entry.projection_version}</small></span>
      <span class="ledger-account-path">${ledgerAccountPath(entry)}</span>
      <span class="ledger-card-tags">${tags(entry)}</span>
      <span class="ledger-card-amounts">${ledgerAmounts(entry)}</span>
      <span class="ledger-chevron" aria-hidden="true">⌄</span>
    </button>
    <div class="ledger-inline-detail" data-ledger-detail hidden></div>
  </article>`).join("");
  return `<section class="panel ledger-filter-panel"><form data-form="ledger-filter">
    <div class="ledger-filter-main">
      <label class="ledger-type-filter"><span>业务类型</span><select name="ledger_type"><option value="">全部业务类型</option>${Object.entries(typeNames).map(([value, label]) => `<option value="${value}" ${state.params.get("ledger_type") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="ledger-search-filter"><span>补充搜索</span><input name="q" value="${esc(state.params.get("q") || "")}" placeholder="标题、交易方或摘要"></label>
      <div class="ledger-date-field"><span>发生时间</span>${ledgerDatePicker()}</div>
      <div class="ledger-filter-actions"><button type="button" class="quiet" data-action="clear-ledger">重置</button><button class="primary">应用筛选</button></div>
    </div>
    <div class="ledger-tag-filter"><div class="ledger-tag-filter-copy"><strong>标签筛选</strong><small>每个视图可选一个标签</small></div><div class="ledger-tag-views">${tagFilters || '<span class="muted">还没有有效标签视图</span>'}</div></div>
  </form></section>
  <div class="ledger-list-head"><span><strong>${result.total}</strong> 条实际流水</span><div class="ledger-money-legend"><span class="plus">● 红色 ＋ 收入</span><span class="minus">● 绿色 − 支出</span><span class="zero">● 灰色 0</span></div></div>
  ${cards ? `<section class="ledger-card-list">${cards}</section>` : '<section class="panel empty-state">没有符合条件的最终流水</section>'}
  ${pager(result)}`;
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
  return `<div class="ledger-detail-grid">
    <div class="ledger-detail-main"><h3>构成事实</h3><div class="ledger-facts">${facts || '<span class="muted">没有关联事实</span>'}</div><p class="ledger-projection-note">收入与支出由事实方向及已确认 Review 聚合得出；“${esc(typeNames[entry.ledger_type] || entry.ledger_type)}”是业务类型，不替代收支结果。</p>${rawEvidence ? `<div class="ledger-raw-list">${rawEvidence}</div>` : ""}</div>
    <aside class="ledger-detail-side"><h3>按视图分配的标签</h3><div class="ledger-detail-tags">${tagRows || '<span class="muted">暂无标签</span>'}</div><dl class="ledger-review-basis"><div><dt>归集依据</dt><dd>${esc(reviewBasis)}</dd></div><div><dt>分配状态</dt><dd>${esc(statusNames[entry.allocation_status] || entry.allocation_status)}</dd></div><div><dt>发生区间</dt><dd>${date(entry.start_time)}<br>${date(entry.end_time)}</dd></div></dl><div class="actions"><button type="button" data-action="edit-tags" data-id="${entry.id}" data-version="${entry.projection_version}">编辑标签</button><button type="button" data-action="detail" data-id="${entry.id}">完整详情</button></div></aside>
  </div>`;
}

async function toggleLedgerCard(button) {
  const card = button.closest("[data-ledger-card]");
  const detail = $("[data-ledger-detail]", card);
  const willOpen = detail.hidden;
  $$("[data-ledger-card]").forEach((item) => {
    const otherDetail = $("[data-ledger-detail]", item);
    const otherButton = $('[data-action="ledger-toggle"]', item);
    if (item !== card) {
      otherDetail.hidden = true;
      item.classList.remove("open");
      otherButton.setAttribute("aria-expanded", "false");
    }
  });
  detail.hidden = !willOpen;
  card.classList.toggle("open", willOpen);
  button.setAttribute("aria-expanded", String(willOpen));
  if (!willOpen || detail.dataset.loaded === "true") return;
  detail.innerHTML = '<div class="busy">正在加载流水证据…</div>';
  button.setAttribute("aria-busy", "true");
  try {
    const payload = await request(`/paam/ledger/v1/entry/detail/${button.dataset.id}`);
    if (!card.isConnected) return;
    detail.innerHTML = ledgerDetailMarkup(payload);
    detail.dataset.loaded = "true";
    bindPage(detail);
  } catch (error) {
    detail.innerHTML = `<div class="error">${esc(error.message)}</div>`;
  } finally {
    button.removeAttribute("aria-busy");
  }
}

async function showDetail(id) {
  const renderVersion = state.renderVersion;
  const detail = await request(`/paam/ledger/v1/entry/detail/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const entry = detail.entry;
  const effectiveAccounts = new Map();
  detail.reviews.filter((item) => item.review_type === "ACCOUNT" && item.status === "CONFIRMED").forEach((item) => {
    item.lines.forEach((line) => effectiveAccounts.set(line.bill_id, item.result.account_name));
  });
  const factRows = detail.facts.map((fact) => {
    const effective = effectiveAccounts.get(fact.id) || fact.account_code;
    return `<tr><td>${fact.id}</td><td>${date(fact.occurred_time)}</td><td>${esc(fact.cash_direction)}</td><td>${money(fact.amount)}</td><td><span title="原始账户：${esc(fact.account_code)}">${esc(effective)}</span></td><td>${esc(fact.counterparty)}</td><td><button data-action="account" data-fact="${fact.id}" data-ledger="${entry.id}" data-version="${entry.projection_version}" data-account="${esc(effective)}">修正账户</button></td></tr>`;
  });
  const reviews = detail.reviews.map((item) => `<div class="review-card"><div class="section-head"><div><h3>${esc(item.review_type)} · ${esc(item.title)}</h3><small>版本 ${item.version} · ${esc(statusNames[item.status] || item.status)}${item.is_projection_source ? " · 当前有效" : ""}</small></div><button data-action="review-detail" data-id="${item.id}">查看历史</button></div></div>`).join("");
  const raws = detail.raw_evidence.map((raw) => `<details><summary>Raw #${raw.id} · 行 ${raw.source_row_number} · ${esc(raw.parse_status)}</summary><pre class="raw-json">${esc(JSON.stringify(raw.raw_payload, null, 2))}</pre></details>`).join("");
  const dialog = modal(`流水 #${entry.id}`, `<div class="cards"><div class="metric"><span>类型</span><strong>${esc(typeNames[entry.ledger_type] || entry.ledger_type)}</strong></div><div class="metric"><span>流入</span><strong>${money(entry.incoming)}</strong></div><div class="metric"><span>流出</span><strong>${money(entry.outgoing)}</strong></div><div class="metric"><span>投影版本</span><strong>${entry.projection_version}</strong></div></div><div class="actions"><button data-action="edit-tags" data-id="${entry.id}" data-version="${entry.projection_version}">编辑标签</button></div><section><h3>事实账单</h3>${table(["Fact", "时间", "方向", "金额", "原始账户", "交易方", ""], factRows)}</section><section><h3>相关审查</h3>${reviews || '<p class="muted">没有人工审查。</p>'}</section><section><h3>原始证据</h3>${raws || '<p class="muted">没有原始明细。</p>'}</section>`);
  bindPage(dialog);
}

async function editTags(ledgerId, version) {
  const renderVersion = state.renderVersion;
  const [views, detail] = await Promise.all([
    request("/paam/tag/v1/view/list"),
    request(`/paam/ledger/v1/entry/detail/${ledgerId}`),
  ]);
  if (renderVersion !== state.renderVersion) return;
  if (!views.length) return toast("请先创建标签维度", true);
  const current = Object.fromEntries(detail.entry.tags.map((item) => [item.view_system_name, item.tag_system_name]));
  const dialog = modal("编辑最终流水标签", `<form data-form="tag-assignment" data-ledger="${ledgerId}" data-version="${version}" class="stack">${views.map((view) => `<label>${esc(view.name)}<select name="${esc(view.system_name)}">${view.tags.map((tag) => `<option value="${esc(tag.system_name)}" ${(current[view.system_name] || "unclassified") === tag.system_name ? "selected" : ""}>${esc(tag.name)}</option>`).join("")}</select></label>`).join("")}<label>修改原因<input name="reason" value="用户修订标签"></label><div class="actions"><button class="primary">保存标签</button></div></form>`);
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
    page: state.params.get("page") || "1",
    page_size: "50",
  });
  if (state.params.get("status")) query.set("status", state.params.get("status"));
  const result = await request(`/paam/review/v1/case/page?${query}`);
  const rows = result.items.map((item) => `<tr><td>${item.id}</td><td><strong>${esc(item.review_type)}</strong><br><small>${esc(item.title)}</small></td><td>${esc(statusNames[item.status] || item.status)}</td><td>${esc(statusNames[item.allocation_status] || item.allocation_status)}</td><td>${item.lines.length}</td><td>${item.version}</td><td><button data-action="review-detail" data-id="${item.id}">详情</button></td></tr>`);
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const paging = `<div class="pagination"><span>共 ${result.total} 条 · 第 ${result.page}/${pages} 页</span><button data-action="review-page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="review-page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
  return `<section class="panel"><div class="section-head"><h2>审查事项</h2><button class="primary" data-action="new-review">新建财务审查</button></div><form class="toolbar" data-form="review-filter"><label>状态<select name="status"><option value="">全部</option>${["PENDING","CONFIRMED","REJECTED","REVOKED"].map((value) => `<option value="${value}" ${result.status === value ? "selected" : ""}>${esc(statusNames[value] || value)}</option>`).join("")}</select></label><button>筛选</button></form>${rows.length ? table(["ID", "类型", "状态", "分配", "Fact 数", "版本", ""], rows) : '<div class="empty-state">没有符合条件的 Review。</div>'}${paging}</section>`;
}

async function showReview(id) {
  const renderVersion = state.renderVersion;
  const item = await request(`/paam/review/v1/case/detail/${id}`);
  if (renderVersion !== state.renderVersion) return;
  const lines = item.lines.map((line) => `<tr><td>${line.bill_id}</td><td>${esc(line.role)}</td><td>${money({amount_value:line.amount_value,amount_scale:line.amount_scale,currency_code:line.currency_code})}</td><td>${esc(line.party)}</td></tr>`);
  const history = item.history.map((event) => `<details><summary>v${event.version} · ${esc(event.operation)} · ${date(event.created_time)}</summary><p>${esc(event.reason || "无说明")}</p><pre>${esc(JSON.stringify({request:event.request,before:event.before,after:event.after}, null, 2))}</pre></details>`).join("");
  let actions = "";
  if (["AA","LOAN_BORROW","LOAN_LEND","REFUND","TRANSFER","FX_EXCHANGE","DUPLICATE"].includes(item.review_type)) {
    if (item.status === "PENDING") actions = `<button data-action="edit-review" data-id="${item.id}">编辑</button><button class="primary" data-action="review-transition" data-kind="confirm" data-id="${item.id}" data-version="${item.version}">确认</button>`;
    if (item.status === "CONFIRMED") actions = `<button data-action="review-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销</button>`;
    if (item.status === "REVOKED") actions = `<button data-action="edit-review" data-id="${item.id}">编辑</button><button class="primary" data-action="review-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复</button>`;
  } else if (item.review_type === "ACCOUNT") {
    if (item.status === "CONFIRMED") actions = `<button data-action="account-transition" data-kind="revoke" data-id="${item.id}" data-version="${item.version}">撤销账户修正</button>`;
    if (item.status === "REVOKED") actions = `<button data-action="account-transition" data-kind="restore" data-id="${item.id}" data-version="${item.version}">恢复账户修正</button>`;
  } else if (item.review_type === "FACT_CONFLICT") {
    if (item.status === "PENDING") actions = `<button data-action="conflict-resolve" data-id="${item.id}" data-version="${item.version}">解决冲突</button><button data-action="conflict-transition" data-kind="dismiss" data-id="${item.id}" data-version="${item.version}">忽略</button>`;
    if (item.status === "REJECTED") actions = `<button data-action="conflict-transition" data-kind="reopen" data-id="${item.id}" data-version="${item.version}">重新打开</button>`;
  }
  const dialog = modal(`Review #${item.id}`, `<div class="section-head"><div><h3>${esc(item.review_type)} · ${esc(item.title)}</h3><small>${esc(statusNames[item.status] || item.status)} · 版本 ${item.version}</small></div><div class="actions">${actions}</div></div><h3>当前成员</h3>${lines.length ? table(["Fact", "角色", "分配金额", "对象"], lines) : '<p class="muted">当前没有已接受的 Fact。</p>'}<h3>类型结果</h3><pre>${esc(JSON.stringify(item.result, null, 2))}</pre><h3>完整历史</h3>${history || '<p class="muted">没有历史。</p>'}`);
  bindPage(dialog);
}

function newReview() {
  const roleHelp = "每行格式：FactID:ROLE[:金额原子值[:对象]]。例如 12:TRANSFER_OUT";
  const dialog = modal("新建财务审查", `<form data-form="new-review" class="stack"><label>审查类型<select name="review_type"><option>AA</option><option>LOAN_BORROW</option><option>LOAN_LEND</option><option>REFUND</option><option>TRANSFER</option><option>FX_EXCHANGE</option><option>DUPLICATE</option></select></label><label>标题<input name="title" maxlength="160"></label><label>Fact 与角色<textarea name="lines" required placeholder="12:TRANSFER_OUT\n13:TRANSFER_IN"></textarea><small>${roleHelp}</small></label><label>说明<input name="reason" value="人工创建财务审查"></label><div class="actions"><button class="primary">创建待确认 Review</button></div></form>`);
  bindPage(dialog);
}

async function editReview(id) {
  const item = await request(`/paam/review/v1/case/detail/${id}`);
  if (!["PENDING", "REVOKED"].includes(item.status)) throw new Error("只有待确认或已撤销 Review 可以编辑");
  const lines = item.lines.map((line) => [
    line.bill_id,
    line.role,
    line.amount_value || "",
    line.party || "",
  ].join(":").replace(/:+$/, "")).join("\n");
  const dialog = modal(`编辑 Review #${item.id}`, `<form data-form="edit-review" data-id="${item.id}" data-version="${item.version}" class="stack"><label>标题<input name="title" value="${esc(item.title)}" maxlength="160"></label><label>Fact 与角色<textarea name="lines" required>${esc(lines)}</textarea><small>每行格式：FactID:ROLE[:金额原子值[:对象]]</small></label><label>类型结果（JSON）<textarea name="result">${esc(JSON.stringify(item.result, null, 2))}</textarea></label><label>修改说明<input name="reason" value="人工修订待确认审查"></label><div class="actions"><button class="primary">保存修订</button></div></form>`);
  bindPage(dialog);
}

function parseReviewLines(value) {
  return String(value).split(/\r?\n/).filter(Boolean).map((row) => {
    const [billId, role, amount, party = ""] = row.split(":");
    const item = { bill_id: Number(billId), role: role?.trim(), party: party.trim() };
    if (amount?.trim()) item.amount_value = Number(amount);
    return item;
  });
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
  $$('button[data-page], a[data-page]', root).forEach((button) => button.onclick = () => route(button.dataset.page));
  $$('[data-action="import-step"]', root).forEach((button) => button.onclick = () => {
    showImportStep(Number(button.dataset.step));
  });
  $$('[data-action="detail-preview"]', root).forEach((button) => button.onclick = () => {
    openPreviewDrawer(Number(button.dataset.document));
  });
  $('[data-action="reload"]', root)?.addEventListener("click", render);
  $('[data-action="clear-summary"]', root)?.addEventListener("click", () => route("summary"));
  $('[data-action="clear-ledger"]', root)?.addEventListener("click", () => route("ledger"));
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
    const params = new URLSearchParams(state.params); params.set("page", button.dataset.value); route("reviews", params);
  });
  $$('[data-action="history-page"]', root).forEach((button) => button.onclick = () => {
    const form = $('[data-form="history-filter"]');
    if (form) refreshHistoryResults(form, Number(button.dataset.value));
  });
  $$('[data-action="detail"]', root).forEach((button) => button.onclick = () => showDetail(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="ledger-toggle"]', root).forEach((button) => button.onclick = () => toggleLedgerCard(button));
  $$('[data-action="review-detail"]', root).forEach((button) => button.onclick = () => showReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-review"]', root).forEach((button) => button.onclick = () => editReview(button.dataset.id).catch((error) => toast(error.message, true)));
  $$('[data-action="edit-tags"]', root).forEach((button) => button.onclick = () => editTags(button.dataset.id, Number(button.dataset.version)).catch((error) => toast(error.message, true)));
  $$('[data-action="account"]', root).forEach((button) => button.onclick = () => editAccount(button));
  $('[data-action="new-review"]', root)?.addEventListener("click", newReview);
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
  $('[data-form="review-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("page", "1"); route("reviews", params); });
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
  $('[data-form="new-review"]', root)?.addEventListener("submit", submitReview);
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
    await jsonRequest(`/paam/tag/v1/assignment/set/${form.dataset.ledger}`, "PUT", { tag_state: Object.fromEntries(data), expected_projection_version: Number(form.dataset.version), reason, idempotency_key: idempotencyKey });
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
async function submitReview(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  try {
    const lines = parseReviewLines(data.get("lines"));
    await jsonRequest("/paam/review/v1/case/create", "POST", { review_type: data.get("review_type"), title: data.get("title"), result: {}, lines, reason: data.get("reason"), idempotency_key: idempotencyKey });
    closeDialogs(); toast("待确认 Review 已创建"); await render();
  } catch (error) { endSubmit(form); showFormError(form, error); }
}
async function submitReviewUpdate(event) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  const idempotencyKey = beginSubmit(form); if (!idempotencyKey) return;
  try {
    const result = JSON.parse(String(data.get("result") || "{}"));
    await jsonRequest(`/paam/review/v1/case/update/${form.dataset.id}`, "PUT", { expected_version: Number(form.dataset.version), title: data.get("title"), result, lines: parseReviewLines(data.get("lines")), reason: data.get("reason"), idempotency_key: idempotencyKey });
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
if (!location.hash) route("summary"); else readRoute();

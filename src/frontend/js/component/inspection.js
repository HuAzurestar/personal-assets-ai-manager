import { request } from "../api/client.js";
import { esc, money, date as when, typeNames, statusNames } from "../util/core.js";

const names = { fact: "事实流水", ledger: "账本流水", review: "审查记录", file: "导入文件" };
const sources = { 0: "来源未识别", 1: "手工录入", 101: "支付宝", 102: "微信支付", 201: "建设银行", 202: "农业银行", 203: "招商银行" };
const behavior = { 0: "事实交易", 1: "借款与还款" };
const operations = { 0: "创建审查", 1: "修改审查", 2: "撤销审查", 3: "恢复审查" };
const fileStates = { 0: "待处理", 1: "已导入", 2: "部分导入", 3: "导入失败" };
const rowStates = { 0: "状态未知", 1: "已接受", 2: "已跳过", 3: "待处理异常" };
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
function timeBasis(value) { return /(?:Z|[+-]\d{2}:\d{2})$/i.test(String(value || "")) ? "UTC+8" : "原记录时间"; }
function amount(item) { return `${money(item)} ${item.currency_code}`; }
function fields(items, technical = false) {
  return `<dl class="inspection-fields">${items.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value === "" || value == null ? "未提供" : value)}${technical && value != null && value !== "" ? `<button type="button" class="inspection-copy" data-copy="${esc(value)}" aria-label="复制${esc(label)}">复制</button>` : ""}</dd></div>`).join("")}</dl>`;
}
function group(title, body, { open = true, wide = false, technical = false } = {}) {
  return `<details class="inspection-group ${wide ? "inspection-wide" : ""}" ${technical ? "data-technical" : "data-business"} ${open ? "open" : ""}><summary>${esc(title)}</summary><div class="inspection-group-body">${body}</div></details>`;
}
function metrics(items) {
  return `<dl class="inspection-metrics">${items.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}
function relation(kind, id, title, note = "", value = "") {
  return `<details class="inspection-related" data-kind="${kind}" data-id="${id}"><summary><span><strong>${esc(title)}</strong><small>${esc(note)}</small></span>${value ? `<strong class="inspection-money">${esc(value)}</strong>` : ""}</summary><div class="inspection-related-body"></div></details>`;
}
function technical(item, extras = []) {
  return group("技术信息与定位", fields([["记录编号", item.id], ...extras, [`创建时间（${timeBasis(item.created_time)}）`, when(item.created_time)], [`更新时间（${timeBasis(item.updated_time)}）`, when(item.updated_time)]], true), { open: false, technical: true });
}
function evidencePayload(row) {
  let payload = row.raw_payload;
  if (payload) {
    try { payload = JSON.stringify(JSON.parse(payload), null, 2); } catch { /* Preserve original evidence if malformed. */ }
  }
  return `<details data-technical class="inspection-evidence"><summary>原始证据与来源行定位</summary>${fields([["来源交易号", row.source_reference], ["处理说明", row.issue_message], ["异常代码", row.issue_code]], true)}${payload ? `<pre class="inspection-raw">${esc(payload)}</pre>` : '<p class="inspection-empty">未提供原始字段</p>'}</details>`;
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
  return metrics([...sums.values()].map(row => [`${row.currency_code} · ${direction(row.direction)}`, amount(row)]));
}

// Pagination is presentation-only for complete relationship collections; PO lists stay server-paged.
class Presentation {
  constructor() { this.collections = []; }
  collection(rows, renderer, label = "条记录") {
    if (!rows.length) return '<p class="inspection-empty">暂无关联记录</p>';
    const index = this.collections.push({ rows, renderer, label, page: 0, size: 20 }) - 1;
    return `<div data-collection="${index}"></div>`;
  }
  mount(root, inline = false) {
    root.querySelectorAll("[data-collection]").forEach(host => {
      const item = this.collections[Number(host.dataset.collection)];
      const render = () => {
        const start = item.page * item.size;
        if (item.rows.length <= 20) {
          host.innerHTML = `<div class="inspection-record-list">${item.rows.map(item.renderer).join("")}</div>`;
          if (inline) promoteRelations(host);
          return;
        }
        host.innerHTML = `<div class="inspection-record-list">${item.rows.slice(start, start + item.size).map(item.renderer).join("")}</div><nav class="inspection-pagination" aria-label="关联明细分页"><span>显示 ${Math.min(start + 1, item.rows.length)}–${Math.min(start + item.size, item.rows.length)} / ${item.rows.length} ${esc(item.label)}</span><button data-rel-prev ${!item.page ? "disabled" : ""}>上一页</button><button data-rel-next ${start + item.size >= item.rows.length ? "disabled" : ""}>下一页</button><label>每页 <select aria-label="关联明细每页条数">${[20, 50, 100].map(size => `<option ${size === item.size ? "selected" : ""}>${size}</option>`).join("")}</select></label></nav>`;
        host.querySelector("[data-rel-prev]").onclick = () => { item.page--; render(); host.scrollIntoView({ block: "nearest" }); host.querySelector("[data-rel-next]").focus({ preventScroll: true }); };
        host.querySelector("[data-rel-next]").onclick = () => { item.page++; render(); host.scrollIntoView({ block: "nearest" }); host.querySelector("[data-rel-prev]").focus({ preventScroll: true }); };
        host.querySelector("select").onchange = event => { item.size = Number(event.target.value); item.page = 0; render(); host.querySelector("select").focus({ preventScroll: true }); };
        if (inline) promoteRelations(host);
      };
      render();
    });
  }
}

function promoteRelations(root) {
  root.querySelectorAll(".inspection-related").forEach(child => {
    const button = document.createElement("button");
    button.className = "inspection-promote";
    button.dataset.promote = "";
    button.dataset.kind = child.dataset.kind;
    button.dataset.id = child.dataset.id;
    button.innerHTML = child.querySelector("summary").innerHTML;
    child.replaceWith(button);
  });
}

function allocationSection(presentation, allocations, facts, ledgers, reviews, context = {}) {
  const factMap = new Map(facts.map(row => [row.id, row]));
  const ledgerMap = new Map(ledgers.map(row => [row.id, row]));
  const reviewMap = new Map(reviews.map(row => [row.id, row]));
  const active = allocations.filter(row => reviewMap.get(row.review_case_id)?.status === 0);
  const history = allocations.filter(row => reviewMap.get(row.review_case_id)?.status !== 0);
  const render = row => {
    const fact = factMap.get(row.transaction_fact_id);
    const ledger = ledgerMap.get(row.ledger_entry_id);
    const review = reviewMap.get(row.review_case_id);
    return `<article class="inspection-allocation"><div class="inspection-allocation-head"><span>${review?.status === 0 ? "当前生效" : review?.status === 1 ? "历史 · 已撤销" : "有效性待核实"}</span><strong>${esc(amount(row))}</strong></div><div class="inspection-allocation-grid">${relation("fact", row.transaction_fact_id, context.kind === "fact" && context.id === row.transaction_fact_id ? "本笔事实" : businessTitle(fact || {}), `来源事实 · ${when(fact?.occurred_time)}`)}${relation("review", row.review_case_id, context.kind === "review" && context.id === row.review_case_id ? "本次审查" : reviewTitle(review || {}, fact ? [fact] : []), `所属审查 · ${behavior[review?.behavior_type] || "类型未识别"}`)}${relation("ledger", row.ledger_entry_id, context.kind === "ledger" && context.id === row.ledger_entry_id ? "本条账本结果" : typeNames[ledger?.entry_type] || "账本分类未识别", `分配去向 · ${direction(ledger?.entry_direction)}`)}</div></article>`;
  };
  const primaryHistory = !active.length && history.length > 0 && ["review", "ledger"].includes(context.kind);
  return (primaryHistory ? "" : group(`当前分配 · ${active.length} 条`, presentation.collection(active, render), { wide: true }))
    + (history.length ? group(`历史分配 · ${history.length} 条（不计入当前金额）`, presentation.collection(history, render), { open: primaryHistory, wide: true }) : "");
}

function describe(kind, data) {
  const p = new Presentation();
  let item, title, subtitle, hero = "", body = "", actions = "";
  if (kind === "fact") {
    item = data.transaction_fact;
    title = businessTitle(item);
    subtitle = `${when(item.occurred_time)} · ${direction(item.cash_direction)} · ${timeBasis(item.occurred_time)}`;
    hero = amount(item);
    const reviewIds = new Set(data.reviews.filter(row => row.status === 0).map(row => row.id));
    const active = data.allocations.filter(row => reviewIds.has(row.review_case_id) && row.currency_code === item.currency_code);
    const allocated = active.reduce((sum, row) => sum + row.amount, 0);
    if (!Number.isSafeInteger(allocated)) throw new Error("分配合计超出安全精度");
    body = metrics([["当前有效分配", amount({ ...item, amount: allocated })], ["与事实金额差额", amount({ ...item, amount: item.amount - allocated })], ["有效审查", reviewIds.size], ["来源文件", new Set(data.import_evidence.map(row => row.transaction_import_file_id)).size]])
      + '<div class="inspection-grid">'
      + group("交易信息", fields([["交易方", item.counterparty_name], ["来源账户", readableAccount(item.account_code)], ["对手方账户", item.counterparty_account_ref], ["原始摘要", item.summary]]))
      + group(`来源证据 · ${data.import_evidence.length} 行`, p.collection(data.import_evidence, row => `${relation("file", row.transaction_import_file_id, row.filename, `${sources[row.source_type] || "来源未识别"} · 第 ${row.source_row_number} 行 · ${rowStates[row.row_status] || "状态未识别"}`)}${evidencePayload(row)}`))
      + allocationSection(p, data.allocations, [item], data.ledgers, data.reviews, { kind, id: item.id })
      + technical(item, [["事实身份键", item.fact_key], ["来源账户原始标识", item.account_code]]) + "</div>";
  } else if (kind === "ledger") {
    item = data.ledger_entry;
    title = businessTitle(data.facts[0] || item);
    const effective = data.reviews.some(row => row.status === 0);
    subtitle = `${typeNames[item.entry_type] || "分类未识别"} · ${effective ? "当前生效" : "历史记录 · 不计入当前账本"} · ${direction(item.entry_direction)}`;
    hero = amount(item);
    const fact = data.facts[0];
    body = metrics([["来源事实金额", fact ? amount(fact) : "未提供"], ["本条分配金额", data.allocations[0] ? amount(data.allocations[0]) : "未提供"], ["占来源事实", fact?.amount > 0 && fact.currency_code === item.currency_code ? `${(item.amount / fact.amount * 100).toFixed(2)}%` : "不适用"]])
      + '<div class="inspection-grid">'
      + group("账本信息", fields([[`发生时间（${timeBasis(item.occurred_time)}）`, when(item.occurred_time)], ["本方账户", readableAccount(item.account_code)], ["对手方账户", item.counterparty_account_ref], ["经济分类", typeNames[item.entry_type]]]))
      + group("分类标签", item.tags.length ? fields(item.tags.map(tag => [tag.view_name, tag.tag_name])) : '<p class="inspection-empty">暂无标签</p>')
      + allocationSection(p, data.allocations, data.facts, [item], data.reviews, { kind, id: item.id })
      + group("来源文件与证据", p.collection(data.facts, row => relation("fact", row.id, businessTitle(row), "展开来源事实，查看文件与原始行定位", amount(row))))
      + technical(item, [["账本账户原始标识", item.account_code]]) + "</div>";
    actions = `<button data-action="edit-ledger-account" data-id="${item.id}">编辑账户</button><button data-action="edit-tags" data-id="${item.id}">编辑标签</button>`;
  } else if (kind === "review") {
    item = data;
    title = reviewTitle(item, item.facts);
    subtitle = `${behavior[item.behavior_type] || "类型未识别"} · ${statusNames[item.status] || "状态未识别"}`;
    body = metrics([["涉及事实", item.facts.length], ["账本结果", item.ledger_entries.length], ["分配关系", item.allocations.length], [`最近变更（${timeBasis(item.updated_time)}）`, when(item.updated_time)]])
      + `<p class="inspection-note">${item.status === 0 ? "以下金额为本审查的有效账本结果" : "以下金额为历史结果，不计入当前账本"}，按币种精度和方向分别统计。</p>`
      + totals(item.ledger_entries, "entry_direction") + '<div class="inspection-grid">'
      + allocationSection(p, item.allocations, item.facts, item.ledger_entries, [item], { kind, id: item.id })
      + group(`变更历史 · ${item.history.length} 次`, p.collection(item.history, row => `<article class="inspection-history"><strong>${esc(operations[row.operation] || "操作未识别")}</strong><small>${esc(when(row.created_time))} · ${esc(row.actor === "system" ? "系统" : row.actor || "操作者未提供")}</small><p>${esc(row.reason === "ensure exact accepted-fact coverage" ? "系统建立默认分配，确保事实金额完整入账" : row.reason || "未填写原因")}</p></article>`), { open: false, wide: true })
      + technical(item, [["原始审查标题", item.title]]) + "</div>";
    actions = `<button data-action="economic-review-transition" data-kind="${item.status === 0 ? "revoke" : "restore"}" data-id="${item.id}">${item.status === 0 ? "撤销并恢复默认交易" : "恢复审查"}</button>`;
  } else {
    item = data.import_file;
    title = `${sources[item.source_type] || "来源未识别"}账单`;
    subtitle = `${fileStates[item.status] || "状态未识别"} · ${formats[item.file_format] || "格式未识别"} · ${when(item.period_start)} 至 ${when(item.period_end)}`;
    const summary = data.relation_summary;
    body = metrics([["来源总行数", item.total_count], ["成功处理行数", item.success_count], ["跳过行数", item.skip_count], ["异常行数", item.issue_count], ["去重关联事实", data.facts.length]])
      + '<div class="inspection-grid">'
      + group("文件信息", fields([["原始文件名", item.filename], [`覆盖开始（${timeBasis(item.period_start)}）`, when(item.period_start)], [`覆盖结束（${timeBasis(item.period_end)}）`, when(item.period_end)], [`导入时间（${timeBasis(item.created_time)}）`, when(item.created_time)]]))
      + group("当前关联摘要", metrics([["有效审查", summary.review_count], ["有效分配", summary.allocation_count], ["有效账本结果", summary.ledger_count]]) + totals(summary.totals, "entry_direction") + '<p class="inspection-note">仅统计本文件去重事实对应的有效分配；其他文件可能关联同一事实，不宜跨文件直接相加。</p>')
      + group(`关联事实 · ${data.facts.length} 笔`, p.collection(data.facts, row => relation("fact", row.id, businessTitle(row), `${when(row.occurred_time)} · ${direction(row.cash_direction)} · ${row.counterparty_name || "交易方未提供"}`, amount(row)), "笔事实"), { wide: true })
      + technical(item, [["原始文件名", item.filename], ["批次码", item.batch_code], ["SHA-256", item.sha256]]) + "</div>";
  }
  return { title, subtitle, hero, body, actions, presentation: p };
}

async function load(kind, id) {
  if (kind !== "file") return request(`${endpoints[kind]}${id}`);
  const [detail, facts] = await Promise.all([request(`${endpoints.file}${id}`), request(`${endpoints.file}${id}/transaction_fact/list`)]);
  return { ...detail, facts: facts.items };
}

let current = null;
let backgroundOverflow = null;
export async function openInspection(kind, id, bindActions) {
  if (current?.dialog.isConnected && current.dialog.open) return current.navigate(kind, Number(id));
  const opener = document.activeElement;
  const rail = [...document.querySelectorAll('#page-content .detail-primary[data-action]')].filter(button => actionKinds[button.dataset.action]).map(button => ({ kind: actionKinds[button.dataset.action], id: Number(button.dataset.id), title: button.querySelector("strong")?.textContent || button.textContent, note: [button.closest("tr")?.querySelector("td")?.textContent, button.querySelector("small")?.textContent].filter(Boolean).join(" · "), amount: button.closest("tr")?.querySelector(".money")?.textContent || "" }));
  const dialog = document.createElement("dialog");
  dialog.className = "detail-view-drawer inspection-workspace";
  dialog.setAttribute("aria-labelledby", "inspection-title");
  dialog.innerHTML = '<div class="inspection-shell"><aside class="inspection-rail" aria-label="当前列表页"><h2>当前列表页</h2><div data-rail></div></aside><div class="inspection-main"><header class="inspection-header"><div class="inspection-controls"><button data-inspect-back disabled>返回上层</button><button data-inspect-prev>上一条</button><button data-inspect-next>下一条</button><button data-inspect-full aria-pressed="false">全屏查看</button><button data-close>返回列表</button></div><div class="inspection-heading" tabindex="-1"><div><span data-kind-label></span><h2 id="inspection-title">正在加载…</h2><p data-inspect-subtitle></p></div><strong data-inspect-hero></strong></div><div class="inspection-tools"><button data-expand aria-pressed="false">展开全部业务信息</button><details class="inspection-action-menu"><summary>操作</summary><div data-inspect-actions></div></details><span class="inspection-feedback" role="status" aria-live="polite"></span></div></header><div class="detail-view-drawer-body inspection-body" tabindex="0" aria-label="详情内容"></div></div></div>';
  document.body.append(dialog);
  if (backgroundOverflow === null) backgroundOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  dialog.showModal();
  const railRoot = dialog.querySelector("[data-rail]");
  railRoot.innerHTML = rail.map(row => `<button data-rail-kind="${row.kind}" data-rail-id="${row.id}"><strong>${esc(row.title)}</strong><small>${esc(row.note)}</small><span>${esc(row.amount)}</span></button>`).join("");
  const body = dialog.querySelector(".inspection-body");
  const stack = [];
  let selected = null, version = 0, full = false;
  const cache = new Map();
  const get = (nextKind, nextId) => {
    const key = `${nextKind}:${nextId}`;
    if (!cache.has(key)) cache.set(key, load(nextKind, nextId).catch(error => { cache.delete(key); throw error; }));
    return cache.get(key);
  };
  async function navigate(nextKind, nextId, record = true) {
    const ticket = ++version;
    if (record && selected) stack.push({ ...selected, scroll: body.scrollTop });
    selected = { kind: nextKind, id: nextId };
    dialog.querySelector("[data-kind-label]").textContent = names[nextKind];
    dialog.querySelector("#inspection-title").textContent = "正在加载…";
    dialog.querySelector("[data-inspect-subtitle]").textContent = "";
    dialog.querySelector("[data-inspect-hero]").textContent = "";
    dialog.querySelector("[data-inspect-actions]").innerHTML = "";
    dialog.querySelector(".inspection-action-menu").hidden = true;
    dialog.querySelector("[data-inspect-back]").disabled = !stack.length;
    const index = rail.findIndex(row => row.kind === nextKind && row.id === nextId);
    dialog.querySelector("[data-inspect-prev]").disabled = index <= 0;
    dialog.querySelector("[data-inspect-next]").disabled = index < 0 || index >= rail.length - 1;
    railRoot.querySelectorAll("button").forEach(button => button.setAttribute("aria-current", String(button.dataset.railKind === nextKind && Number(button.dataset.railId) === nextId)));
    body.innerHTML = '<p role="status">正在加载详情…</p>';
    try {
      const data = await get(nextKind, nextId);
      if (ticket !== version || !dialog.isConnected) return;
      const view = describe(nextKind, data);
      dialog.querySelector("#inspection-title").textContent = view.title;
      dialog.querySelector("[data-inspect-subtitle]").textContent = view.subtitle;
      dialog.querySelector("[data-inspect-hero]").textContent = view.hero;
      const actions = dialog.querySelector("[data-inspect-actions]");
      actions.innerHTML = view.actions;
      dialog.querySelector(".inspection-action-menu").hidden = !view.actions;
      bindActions(actions);
      body.innerHTML = view.body;
      view.presentation.mount(body);
      body.scrollTop = 0;
      const expand = dialog.querySelector("[data-expand]");
      expand.setAttribute("aria-pressed", "false");
      expand.textContent = "展开全部业务信息";
      dialog.querySelector(".inspection-heading").focus({ preventScroll: true });
    } catch (error) {
      if (ticket !== version || !dialog.isConnected) return;
      dialog.querySelector("#inspection-title").textContent = "详情暂时无法加载";
      body.innerHTML = `<p role="alert">${esc(error.message)}</p><button data-inspect-retry>重试</button>`;
    }
  }
  current = { dialog, navigate };
  const closeOnRoute = () => dialog.close();
  dialog.addEventListener("close", () => {
    version++;
    window.removeEventListener("hashchange", closeOnRoute);
    dialog.remove();
    if (current?.dialog === dialog) current = null;
    if (!document.querySelector(".inspection-workspace[open]")) {
      document.body.style.overflow = backgroundOverflow ?? "";
      backgroundOverflow = null;
    }
    if (opener?.isConnected && !document.querySelector("dialog[open]")) opener.focus({ preventScroll: true });
  });
  window.addEventListener("hashchange", closeOnRoute);
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.querySelector("[data-inspect-full]").onclick = event => { full = !full; dialog.classList.toggle("inspection-full", full); event.target.setAttribute("aria-pressed", String(full)); event.target.textContent = full ? "恢复分栏" : "全屏查看"; };
  dialog.querySelector("[data-expand]").onclick = event => {
    const open = event.target.getAttribute("aria-pressed") !== "true";
    body.querySelectorAll("details[data-business]").forEach(section => { section.open = open; });
    event.target.setAttribute("aria-pressed", String(open));
    event.target.textContent = open ? "收起业务信息" : "展开全部业务信息";
  };
  dialog.querySelector("[data-inspect-back]").onclick = async () => { const previous = stack.pop(); if (previous) { await navigate(previous.kind, previous.id, false); body.scrollTop = previous.scroll; } };
  for (const [selector, delta] of [["[data-inspect-prev]", -1], ["[data-inspect-next]", 1]]) dialog.querySelector(selector).onclick = () => { const index = rail.findIndex(row => row.kind === selected.kind && row.id === selected.id); const row = rail[index + delta]; if (row) navigate(row.kind, row.id); };
  dialog.addEventListener("click", async event => {
    const railButton = event.target.closest("[data-rail-id]");
    if (railButton) navigate(railButton.dataset.railKind, Number(railButton.dataset.railId));
    const promote = event.target.closest("[data-promote]");
    if (promote) navigate(promote.dataset.kind, Number(promote.dataset.id));
    if (event.target.closest("[data-inspect-retry]")) navigate(selected.kind, selected.id, false);
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      try { await navigator.clipboard.writeText(copy.dataset.copy); dialog.querySelector(".inspection-feedback").textContent = "已复制"; }
      catch { dialog.querySelector(".inspection-feedback").textContent = "复制失败，请选中文字后复制"; }
    }
    const retry = event.target.closest("[data-related-retry]");
    if (retry) { const related = retry.closest(".inspection-related"); related.dataset.loaded = ""; related.open = false; related.open = true; }
  });
  dialog.addEventListener("toggle", async event => {
    const related = event.target;
    if (!related.matches(".inspection-related") || !related.open || related.dataset.loaded) return;
    related.dataset.loaded = "loading";
    const target = related.querySelector(".inspection-related-body");
    target.innerHTML = '<p role="status">正在加载关联信息…</p>';
    try {
      const data = await get(related.dataset.kind, Number(related.dataset.id));
      if (!target.isConnected) return;
      const view = describe(related.dataset.kind, data);
      // One inline level is enough to inspect the object. Further traversal promotes it
      // into the workspace and records a return point instead of growing nested panels.
      target.innerHTML = `<div class="inspection-inline-heading"><strong>${esc(view.title)}</strong><span>${esc(view.subtitle)}</span><strong>${esc(view.hero)}</strong><button data-promote data-kind="${related.dataset.kind}" data-id="${related.dataset.id}">转为主详情，查看全部关联</button></div>${view.body}`;
      view.presentation.mount(target, true);
      related.dataset.loaded = "true";
    } catch (error) { related.dataset.loaded = "error"; target.innerHTML = `<p role="alert">${esc(error.message)}</p><button data-related-retry>重试关联信息</button>`; }
  }, true);
  return navigate(kind, Number(id), false);
}

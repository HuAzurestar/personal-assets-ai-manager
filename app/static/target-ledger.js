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
const state = { page: "summary", params: new URLSearchParams(), importPlan: null, renderVersion: 0 };

async function request(url, options = {}) {
  const response = await fetch(url, options).catch(() => {
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
  return `<div class="pagination"><span>共 ${result.total} 条 · 第 ${result.page}/${pages} 页</span><button data-action="page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><button data-action="page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
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
  ["import", "导入", "事实与原始证据"],
  ["tags", "标签", "标签维度"],
  ["reviews", "审查", "统一 Review"],
];
$("#app").innerHTML = `<div class="workspace target-shell"><aside class="sidebar"><div class="brand"><img src="/static/personal-assets-ai-manager.svg" alt=""><div class="brand-text"><strong>个人账本</strong><small>PIRC-9 目标模型</small></div></div><nav aria-label="主导航">${nav.map(([id, label, title]) => `<button data-page="${id}" title="${title}"><span class="nav-icon">•</span><span class="nav-label">${label}</span></button>`).join("")}</nav><div class="sidebar-foot"><small>本地 SQLite · 事实与审查分层</small></div></aside><main class="workspace-main" id="content" tabindex="-1"><header class="page-header"><div><div class="eyebrow">PIRC-9 LEDGER</div><h1 id="title"></h1><p id="help"></p></div><button class="primary" data-page="import">导入账单</button></header><div id="page-content" aria-live="polite"></div></main></div>`;

const pageInfo = {
  summary: ["收支概览", "只读取热投影；不加载原始文本和历史。"],
  ledger: ["实际流水", "事实与已确认 Review 合成的最终账本。"],
  import: ["导入事实", "原始记录保留，确认后生成不可变 Fact。"],
  tags: ["标签管理", "按维度管理标签；在卡片内即可快速添加。"],
  reviews: ["统一审查", "财务、标签、账户与事实冲突共用一套历史模型。"],
};

function route(page, params = new URLSearchParams()) {
  location.hash = `${page}${params.toString() ? `?${params}` : ""}`;
}
function readRoute() {
  const [page, query = ""] = location.hash.slice(1).split("?");
  state.page = pageInfo[page] ? page : "summary";
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
  const rows = result.items.map((entry) => `<tr><td>${entry.id}</td><td>${date(entry.start_time)}</td><td><strong>${esc(entry.title || "未命名")}</strong><div class="status-line"><span class="badge neutral">${esc(typeNames[entry.ledger_type] || entry.ledger_type)}</span><small>${esc(statusNames[entry.allocation_status] || entry.allocation_status)}</small></div></td><td><div class="money-pair"><span class="income">入 ${money(entry.incoming)}</span><span>出 ${money(entry.outgoing)}</span></div></td><td>${esc(entry.in_account_code)} / ${esc(entry.out_account_code)}</td><td>${tags(entry)}</td><td><button data-action="detail" data-id="${entry.id}">详情</button></td></tr>`);
  const tagOptions = views.flatMap((view) => view.tags.map((tag) => [
    `${view.system_name}:${tag.system_name}`, `${view.name}：${tag.name}`,
  ]));
  return `<section class="panel"><form class="toolbar" data-form="ledger-filter"><label class="grow">搜索<input name="q" value="${esc(state.params.get("q") || "")}" placeholder="标题、交易方或摘要"></label><label>业务类型<select name="ledger_type"><option value="">全部</option>${Object.entries(typeNames).map(([value, label]) => `<option value="${value}" ${state.params.get("ledger_type") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>标签<select name="tag"><option value="">全部</option>${tagOptions.map(([value, label]) => `<option value="${esc(value)}" ${state.params.get("tag") === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label><label>开始<input type="date" name="date_from" value="${esc(state.params.get("date_from") || "")}"></label><label>结束<input type="date" name="date_to" value="${esc(state.params.get("date_to") || "")}"></label><button>筛选</button><button type="button" data-action="clear-ledger">清除</button></form>${rows.length ? table(["ID", "时间", "流水", "现金流", "入/出账户", "标签", ""], rows) : '<div class="empty-state">没有符合条件的最终流水</div>'}${pager(result)}</section>`;
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

async function importPage() {
  const history = await request("/paam/import/v1/batch/list");
  const rows = history.map((item) => `<tr><td>${item.id}</td><td>${esc(item.filename)}</td><td>${esc(item.source_type)}</td><td>${item.imported_count}/${item.row_count}</td><td>${esc(item.status)}</td><td>${date(item.imported_at)}</td><td><button data-action="batch-rows" data-id="${item.id}">查看行</button></td></tr>`);
  return `<section class="panel"><h2>导入新账单</h2><form data-form="import-preview" class="stack"><label>账单文件<input type="file" name="files" multiple required accept=".csv,.xls,.xlsx,.zip,.pdf"></label><label>指定来源（可留空自动识别）<select name="source_type"><option value="">自动识别</option><option value="alipay">支付宝</option><option value="wechat">微信</option><option value="ccb">建设银行</option><option value="abc">农业银行</option><option value="cmb">招商银行</option></select></label><label>ZIP/PDF 密码（仅本次请求）<input type="password" name="password"></label><div class="actions"><button class="primary">预览导入</button></div></form><div id="import-preview"></div></section><section class="panel"><h2>导入历史</h2>${rows.length ? table(["ID", "文件", "来源", "成功/总数", "状态", "时间", ""], rows) : '<div class="empty-state">还没有导入记录</div>'}</section>`;
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
  root.innerHTML = `<div class="review-card"><h3>预览结果</h3><div class="status-line">${Object.entries(plan.counts || {}).map(([name, value]) => `<span class="badge neutral">${esc(name)} ${value}</span>`).join("")}</div>${plan.can_confirm ? '<p class="success">预览已通过，可以原子写入事实层。</p>' : '<p class="error">请先处理账号匹配或交易歧义；不可修复的错误需重新导出文件。</p>'}${errors ? `<ul class="error-list">${errors}</ul>` : ""}<form data-form="import-revise" class="stack">${accountFields ? `<details><summary>核对或调整账号匹配</summary><div class="stack inset">${accountFields}</div></details>` : ""}${decisions ? `<fieldset><legend>交易匹配决策</legend><div class="stack">${decisions}</div></fieldset>` : ""}<div class="actions"><button type="submit">重新计算预览</button><button type="button" class="primary" data-action="confirm-import" ${plan.can_confirm ? "" : "disabled"}>确认写入事实层</button></div></form><details><summary>查看详细计划</summary><pre class="raw-json">${esc(JSON.stringify(plan.documents || plan, null, 2))}</pre></details></div>`;
  bindPage(root);
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

function bindPage(root) {
  $$('[data-page]', root).forEach((button) => button.onclick = () => route(button.dataset.page));
  $('[data-action="reload"]', root)?.addEventListener("click", render);
  $('[data-action="clear-summary"]', root)?.addEventListener("click", () => route("summary"));
  $('[data-action="clear-ledger"]', root)?.addEventListener("click", () => route("ledger"));
  $$('[data-action="page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params); params.set("page", button.dataset.value); route("ledger", params);
  });
  $$('[data-action="review-page"]', root).forEach((button) => button.onclick = () => {
    const params = new URLSearchParams(state.params); params.set("page", button.dataset.value); route("reviews", params);
  });
  $$('[data-action="detail"]', root).forEach((button) => button.onclick = () => showDetail(button.dataset.id).catch((error) => toast(error.message, true)));
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
  $$('[data-action="batch-rows"]', root).forEach((button) => button.onclick = async () => {
    try { const rows = await request(`/paam/import/v1/batch/row/list?batch_id=${button.dataset.id}`); modal(`导入批次 #${button.dataset.id}`, `<pre class="raw-json">${esc(JSON.stringify(rows, null, 2))}</pre>`); } catch (error) { toast(error.message, true); }
  });
  $('[data-action="confirm-import"]', root)?.addEventListener("click", confirmImport);
  $('[data-form="summary-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); route("summary", new URLSearchParams([...data].filter(([, value]) => value))); });
  $('[data-form="ledger-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("page", "1"); route("ledger", params); });
  $('[data-form="review-filter"]', root)?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const params = new URLSearchParams([...data].filter(([, value]) => value)); params.set("page", "1"); route("reviews", params); });
  $('[data-form="import-preview"]', root)?.addEventListener("submit", async (event) => { event.preventDefault(); try { await previewImport(event.currentTarget); } catch (error) { showFormError(event.currentTarget, error); } });
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
  try { const result = await jsonRequest(`/paam/import/v1/preview/confirm/${state.importPlan.token}`, "POST", { version: state.importPlan.version }); state.importPlan = null; toast(`导入完成：${result.bill_fact_ids?.length || 0} 条新事实`); await render(); } catch (error) { if (button) button.disabled = false; const form = button?.closest("form"); if (form) showFormError(form, error); else toast(error.message, true); }
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

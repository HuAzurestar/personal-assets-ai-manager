import { reviewTools } from './review.js';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(
    value,
  );
const date = (value) =>
  String(value || "")
    .replace("T", " ")
    .slice(0, 16);
const sourceName = (value) =>
  ({ alipay: "支付宝", wechat: "微信", manual: "手工" })[value] ||
  value ||
  "手工";
const statusNames = {
  pending: "待复核",
  evidence_insufficient: "需补充核验",
  legacy_duplicate_needs_review: "旧记录待复核",
  deferred: "稍后处理",
  ignored: "已忽略",
  duplicate_rejected: "已拒绝重复建议",
  duplicate_excluded: "已保留一笔",
  personal_transfer_grouped: "个人转移已确认",
  third_party_transfer_grouped: "他人转移已确认",
  transfer_grouped: "转移已确认",
};
const statusName = (s) => statusNames[s] || "旧版处理记录";
const actionable = (c) =>
  [
    "pending",
    "evidence_insufficient",
    "legacy_duplicate_needs_review",
  ].includes(c.status);
const state = {
  page: "summary",
  params: new URLSearchParams(),
  routes: {},
  views: [],
  bills: [],
  candidates: [],
  selected: new Set(),
  epoch: 0,
  importVersion: 0,
  pendingImport: null,
};
function message(error) {
  const raw = String(error?.message || error);
  const translations = {
    "Failed to fetch": "连接失败，请检查本地服务后重试。",
    "Tag view name already exists": "这个分类维度名称已存在。",
    "Tag name already exists in this view": "此维度中已有同名标签。",
    "ZIP password is required or invalid": "ZIP 密码缺失或不正确。",
    "date_from must be before date_to": "起始日期不能晚于结束日期。",
    "amount_min must not exceed amount_max": "最小金额不能大于最大金额。",
    "The unclassified tag is protected": "未分类标签由系统保留，不能修改。",
    "Candidate has already been handled; undo it before applying another decision":
      "这条候选已处理，请先撤销再作决定。",
  };
  return translations[raw] || raw;
}
async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error("连接失败，请检查本地服务后重试。");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail
            .map((d) => `${d.loc?.slice(1).join(" / ") || "输入"}：${d.msg}`)
            .join("；")
        : detail || `请求失败（${response.status}），请重试。`,
    );
  }
  return data;
}
const jsonRequest = (url, method, data) =>
  request(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
function toast(text, undo = null) {
  $(".toast")?.remove();
  const node = document.createElement("div");
  node.className = "toast";
  node.setAttribute("role", "status");
  node.textContent = text;
  if (undo) {
    const action = document.createElement("button");
    action.textContent = "撤销本次处理";
    action.onclick = async () => {
      action.disabled = true;
      try {
        await undo();
        node.remove();
      } catch (error) {
        action.disabled = false;
        toast(message(error));
      }
    };
    node.append(action);
  }
  document.body.append(node);
  setTimeout(() => node.remove(), undo ? 20000 : 4500);
}
const pageInfo = {
  summary: ["收支概览", "看清收支，也能追溯每一笔。"],
  data: ["流水账本", "查找、分类与核对，都从这里开始。"],
  tags: ["标签管理", "用消费类别、使用场景等维度整理账目。"],
  candidates: ["候选复核", "对照原始记录，再作决定。所有处理均可撤销。"],
  database: ["数据库观察", "本机只读诊断 · 原始记录和审计均保留。"],
  settings: ["设置", "按你的习惯调整外观，即选即生效。"],
};
const icon = (path) =>
  `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
const navItems = [
  // Settings shares the navigation on desktop and mobile.
  [
    "summary",
    "概览",
    "汇总",
    icon(
      '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    ),
  ],
  [
    "data",
    "流水",
    "导入与数据",
    icon(
      '<path d="M6 3h13v18H6a3 3 0 0 1 0-6h13M6 3a3 3 0 0 0-3 3v12M8 7h7M8 11h5"/>',
    ),
  ],
  [
    "tags",
    "标签",
    "标签管理",
    icon('<path d="M3 3h8l10 10-8 8L3 11Z"/><circle cx="7.5" cy="7.5" r="1"/>'),
  ],
  [
    "candidates",
    "复核",
    "重复与转移候选",
    icon(
      '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 11 3 3 5-6M8 18h8"/>',
    ),
  ],
  [
    "database",
    "数据库",
    "数据库观察",
    icon(
      '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
    ),
  ],
];
navItems.push([
  "settings",
  "设置",
  "设置",
  icon(
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
  ),
]);
$("#app").innerHTML =
  `<div class="workspace"><aside class="sidebar"><div class="brand"><img src="/static/personal-assets-ai-manager.svg" alt=""><div class="brand-text"><strong>个人账本</strong><small>个人账本与资产管家</small></div></div><nav aria-label="主导航">${navItems.map(([key, label, title, icon]) => `<button data-page="${key}" aria-label="${title}" title="${title}"><span class="nav-icon" aria-hidden="true">${icon}</span><span class="nav-label">${label}</span></button>`).join("")}</nav><div class="sidebar-foot"><button id="nav-toggle" class="quiet" aria-label="收缩侧栏">收缩</button><small>本地存储 · 数据由你掌握</small></div></aside><main class="workspace-main" id="content" tabindex="-1"><header class="page-header"><div><div class="eyebrow">PERSONAL LEDGER</div><h1 id="page-title"></h1><p id="page-help"></p></div><button class="primary" data-action="import">＋ 导入账单</button></header><div id="page-content" aria-live="polite"></div></main></div>`;
try {
  $(".workspace").dataset.collapsed =
    localStorage.getItem("paam-sidebar-collapsed") || "false";
} catch {
  /* storage may be disabled */
}
function collapseLabel() {
  const collapsed = $(".workspace").dataset.collapsed === "true";
  $("#nav-toggle").textContent = collapsed ? "展开" : "收缩";
  $("#nav-toggle").setAttribute(
    "aria-label",
    collapsed ? "展开侧栏" : "收缩侧栏",
  );
}
collapseLabel();
function urlFor(page, params) {
  return `#${page}${params.toString() ? `?${params}` : ""}`;
}
function navigate(page, params = null) {
  state.routes[state.page] = state.params.toString();
  const next = urlFor(
    page,
    new URLSearchParams(params ?? state.routes[page] ?? ""),
  );
  if (location.hash === next) render();
  else location.hash = next;
}
function updateParams(values) {
  const params = new URLSearchParams(state.params);
  for (const [key, value] of Object.entries(values)) {
    params.delete(key);
    if (value !== "" && value != null) params.set(key, value);
  }
  navigate(state.page, params);
}
function readRoute() {
  const [page, query = ""] = location.hash.slice(1).split("?");
  state.page = pageInfo[page] ? page : "summary";
  state.params = new URLSearchParams(query);
  state.routes[state.page] = state.params.toString();
  state.selected.clear();
  render();
}
window.addEventListener("hashchange", readRoute);
const button = (label, action, attrs = "", primary = false) =>
  `<button type="button" ${primary ? 'class="primary"' : ""} data-action="${action}" ${attrs}>${label}</button>`;
function empty(title, desc = "", action = "") {
  return `<div class="empty"><h2>${esc(title)}</h2><p>${esc(desc)}</p>${action}</div>`;
}
function select(name, label, options, current) {
  return `<label>${esc(label)}<select name="${name}">${options.map(([value, text]) => `<option value="${esc(value)}" ${String(current ?? "") === String(value) ? "selected" : ""}>${esc(text)}</option>`).join("")}</select></label>`;
}
function input(name, label, type = "text", value = "", attrs = "") {
  return `<label>${esc(label)}<input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;
}
function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function pager(result, key = "page") {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  return `<div class="pagination"><span class="range">共 ${result.total} 条 · ${result.total ? (result.page - 1) * result.page_size + 1 : 0}–${Math.min(result.total, result.page * result.page_size)}</span><button data-action="page-step" data-key="${key}" data-step="-1" ${result.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${result.page} / ${pages} 页</span><button data-action="page-step" data-key="${key}" data-step="1" ${result.page >= pages ? "disabled" : ""}>下一页</button></div>`;
}
async function render({ preservePosition = false } = {}) {
  const scroll = window.scrollY;
  const tableScrolls = $$(".table-wrap").map((el) => ({
    top: el.scrollTop,
    left: el.scrollLeft,
  }));
  const focused = document.activeElement;
  const focusAction = focused?.dataset?.action;
  const focusId = focused?.dataset?.id;
  const epoch = ++state.epoch;
  const page = state.page;
  const params = new URLSearchParams(state.params);
  const root = $("#page-content");
  $("#page-title").textContent = pageInfo[page][0];
  $("#page-help").textContent = pageInfo[page][1];
  document.title = `${pageInfo[page][0]} · 个人账本`;
  $$("[data-page]").forEach((b) => {
    b.classList.toggle("active", b.dataset.page === page);
    if (b.dataset.page === page) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  root.innerHTML = '<div class="busy" role="status">正在加载…</div>';
  root.setAttribute("aria-busy", "true");
  try {
    const result = await {
      summary: summaryPage,
      data: dataPage,
      tags: tagsPage,
      candidates: candidatesPage,
      database: databasePage,
      settings: settingsPage,
    }[page](params);
    if (epoch !== state.epoch) return;
    root.innerHTML = result.html;
    Object.assign(state, result.cache || {});
    review.decorate(page, root);
    if (preservePosition)
      $$(".table-wrap").forEach((el, i) => {
        el.scrollTop = tableScrolls[i]?.top || 0;
        el.scrollLeft = tableScrolls[i]?.left || 0;
      });
    if (preservePosition && focusAction && focusId) {
      $$("[data-action][data-id]", root)
        .find(
          (b) => b.dataset.action === focusAction && b.dataset.id === focusId,
        )
        ?.focus({ preventScroll: true });
    }
    window.scrollTo({
      top: preservePosition ? scroll : 0,
      behavior: "instant",
    });
  } catch (error) {
    if (epoch === state.epoch)
      root.innerHTML = `<section class="panel"><div class="error" role="alert">${esc(message(error))}</div><div class="actions">${button("重新加载", "retry")}</div></section>`;
  } finally {
    if (epoch === state.epoch) root.removeAttribute("aria-busy");
  }
}
async function summaryPage(params) {
  const filter = new URLSearchParams();
  for (const key of ["date_from", "date_to"])
    if (params.get(key)) filter.set(key, params.get(key));
  const [d, all, excluded, candidates] = await Promise.all([
    request(`/api/dashboard?${filter}`),
    request("/api/transactions?scope=all&page_size=1"),
    request("/api/transactions?scope=excluded&page_size=1"),
    request("/api/candidates/page?status=needs_review&page_size=1"),
  ]);
  const max = Math.max(1, ...d.trend.map((t) => Math.abs(t.spending)));
  return {
    cache: { pendingCandidateCount: candidates.total, summary: d },
    html: `<form data-form="filters" class="toolbar">${input("date_from", "起始日期", "date", params.get("date_from"))}${input("date_to", "结束日期", "date", params.get("date_to"))}<button>应用日期</button>${button("全部日期", "clear-filters")}</form><div class="cards"><button class="metric accent" data-action="drill" data-direction=""><span>净收支</span><strong>${money(d.net)}</strong><small>所选日期内 · 点击查看流水</small></button><button class="metric" data-action="drill" data-direction="income"><span>收入</span><strong>${money(d.income)}</strong><small>已扣除确认的退款抵扣</small></button><button class="metric" data-action="drill" data-direction="expense"><span>支出</span><strong>${money(Math.abs(d.spending))}</strong><small>不含重复与已确认转移</small></button><button class="metric" data-action="drill" data-direction=""><span>参与汇总的流水</span><strong>${d.effective_count ?? d.bill_count}</strong><small>所选日期内的有效记录</small></button></div><div class="two-col"><section class="panel"><div class="section-head"><h2>待复核</h2><span class="badge ${candidates.total ? "warn" : ""}">${candidates.total} 条</span></div><p class="muted">${candidates.total ? "有可能重复或转移的记录，等你核对。" : "当前没有待复核的候选。"}</p>${button(candidates.total ? "开始复核" : "查看复核记录", "review", "", candidates.total > 0)}</section><section class="panel"><h2>原始记录完整保留</h2><p class="muted">累计保存 ${all.total} 条流水，其中 ${excluded.total} 条已排除汇总。复核不会删除原始记录。</p>${button("查看所有流水", "all-ledger")}</section></div><section class="panel"><div class="section-head"><h2>每日收支</h2><small>按日核对 · 收支均为人民币</small></div>${
      d.trend.length
        ? table(
            ["日期", "收入", "支出", "净收支"],
            d.trend.map(
              (t) =>
                `<tr><td><button class="quiet" data-action="day" data-day="${t.day}">${t.day}</button></td><td class="money income">${money(t.income)}</td><td class="money">${money(Math.abs(t.spending))}<span class="trend-bar" style="width:${Math.round((Math.abs(t.spending) / max) * 100)}%"></span></td><td class="money">${money(t.net)}</td></tr>`,
            ),
          )
        : empty(
            "还没有收支记录",
            "导入一份账单，就能开始整理。",
            button("导入第一份账单", "import", "", true),
          )
    }</section>`,
  };
}
function tagsMarkup(bill) {
  const tags = (bill.view_tags || []).filter(
    (t) => t.tag_system_name !== "unclassified",
  );
  return (
    tags
      .map(
        (t) =>
          `<span class="tag" title="${esc(t.view_name)}">${esc(t.tag_name)}</span>`,
      )
      .join("") || '<span class="muted">未分类</span>'
  );
}
function excludedMarkup(bill) {
  return bill.aggregate_excluded
    ? `<span class="badge warn">${bill.duplicate_of_id ? "重复已排除" : bill.transfer_group_id ? "转移不计收支" : "不计入汇总"}</span>`
    : "";
}
async function dataPage(params) {
  const query = new URLSearchParams(params);
  if (!query.has("page_size")) query.set("page_size", "20");
  const [views, result] = await Promise.all([
    request("/api/tag-views"),
    request(`/api/transactions?${query}`),
  ]);
  const p = (key) => params.get(key) || "";
  const advanced =
    [
      "date_from",
      "date_to",
      "amount_min",
      "amount_max",
      "account",
      "source",
      "sort_by",
      "sort_order",
    ].filter(
      (k) =>
        p(k) &&
        !(k === "sort_by" && p(k) === "occurred_at") &&
        !(k === "sort_order" && p(k) === "desc"),
    ).length + params.getAll("tag").length;
  const filters = `<form data-form="filters" id="transaction-filters"><div class="toolbar"><label class="grow">搜索流水<input name="q" value="${esc(p("q"))}" placeholder="交易方或备注"></label>${select(
    "scope",
    "记录范围",
    [
      ["effective", "参与汇总"],
      ["all", "所有原始流水"],
      ["excluded", "已排除汇总"],
    ],
    p("scope") || "effective",
  )}${select(
    "direction",
    "收支",
    [
      ["", "全部"],
      ["income", "收入"],
      ["expense", "支出"],
      ["transfer", "转移"],
    ],
    p("direction"),
  )}<button class="primary">查询</button>${button("清空", "clear-filters")}</div><details class="filter-panel" ${advanced ? "open" : ""}><summary>更多筛选与排序${advanced ? ` · ${advanced} 项条件` : ""}</summary><div class="filter-grid">${input("date_from", "起始日期", "date", p("date_from"))}${input("date_to", "结束日期", "date", p("date_to"))}${input("amount_min", "最小金额（绝对值）", "number", p("amount_min"), 'min="0" step="0.01"')}${input("amount_max", "最大金额（绝对值）", "number", p("amount_max"), 'min="0" step="0.01"')}${input("account", "账户名称", "text", p("account"))}${select(
    "source",
    "来源",
    [
      ["", "全部"],
      ["alipay", "支付宝"],
      ["wechat", "微信"],
      ["manual", "手工"],
    ],
    p("source"),
  )}${views
    .map((v) =>
      select(
        `tag:${v.system_name}`,
        v.name,
        [
          ["", "全部"],
          ...v.tags
            .filter((t) => !t.archived)
            .map((t) => [t.system_name, t.name]),
        ],
        params
          .getAll("tag")
          .find((t) => t.startsWith(v.system_name + ":"))
          ?.split(":")[1],
      ),
    )
    .join("")}${select(
    "sort_by",
    "排序依据",
    [
      ["occurred_at", "交易时间"],
      ["amount", "金额"],
    ],
    p("sort_by") || "occurred_at",
  )}${select(
    "sort_order",
    "顺序",
    [
      ["desc", "从大到小 / 从新到旧"],
      ["asc", "从小到大 / 从旧到新"],
    ],
    p("sort_order") || "desc",
  )}${select(
    "page_size",
    "每页",
    [
      [20, "20 条"],
      [50, "50 条"],
      [100, "100 条"],
    ],
    p("page_size") || 20,
  )}</div><button>应用筛选</button></details></form>`;
  const rows = result.items.map(
    (b) =>
      `<tr><td><input type="checkbox" data-select-bill="${b.id}" aria-label="选择 ${esc(b.merchant)} 的流水 ${b.id}"></td><td><strong>${esc(b.merchant)}</strong><small>${esc(b.note)}</small>${excludedMarkup(b)}</td><td>${date(b.occurred_at)}<small>${sourceName(b.source_type)} · ${esc(b.account_name)}</small></td><td class="money ${b.amount > 0 ? "income" : "expense"}">${money(b.amount)}</td><td>${tagsMarkup(b)}</td><td><button data-action="bill-detail" data-id="${b.id}">详情</button> <button data-action="assign" data-id="${b.id}">标签</button></td></tr>`,
  );
  const mobile = result.items
    .map(
      (b) =>
        `<article class="transaction-card"><div class="section-head"><label class="inline-check"><input type="checkbox" data-select-bill="${b.id}" aria-label="选择 ${esc(b.merchant)} 的流水 ${b.id}"><strong>${esc(b.merchant)}</strong></label><strong class="${b.amount > 0 ? "income" : ""}">${money(b.amount)}</strong></div><p class="muted">${date(b.occurred_at)} · ${sourceName(b.source_type)} · ${esc(b.account_name)}</p>${excludedMarkup(b)}${tagsMarkup(b)}<div class="actions"><button data-action="bill-detail" data-id="${b.id}">详情</button><button data-action="assign" data-id="${b.id}">选择标签</button></div></article>`,
    )
    .join("");
  return {
    cache: { views, bills: result.items, selected: new Set() },
    html: `<section class="panel">${filters}<div id="bill-selection" class="selection-bar" hidden><strong></strong> ${button("批量修改标签", "bulk-tags")}</div><div class="section-head"><h2>流水明细 <small>${result.total} 条</small></h2><span class="muted">${p("scope") === "all" ? "含已排除的原始记录" : p("scope") === "excluded" ? "以下记录不计入收支汇总" : "以下记录参与收支汇总"}</span></div><div id="transaction-result">${result.total ? `<div class="desktop-table">${table(['<input type="checkbox" id="select-all-bills" aria-label="选择本页所有流水">', "交易方 / 备注", "时间 / 账户", "金额", "标签", "操作"], rows)}</div><div class="mobile-cards">${mobile}</div>` : empty("没有符合条件的流水", "可以清空筛选，或导入新的账单。", button("清空筛选", "clear-filters"))}${pager(result)}</div></section>`,
  };
}
async function tagsPage(params) {
  const views = await request("/api/tag-views?include_archived=true");
  const show = params.get("archived") === "true";
  return {
    cache: { views },
    html: `<div class="toolbar">${button("＋ 新建分类维度", "new-view", "", true)}<label class="inline-check"><input type="checkbox" id="show-archived" ${show ? "checked" : ""}>显示已归档</label></div><p class="muted">例如“消费类别”下的餐饮、交通；每个维度选一个标签，不同维度可组合。</p><div class="tag-grid">${views
      .filter((v) => show || !v.archived)
      .map(
        (v) =>
          `<section class="panel"><div class="section-head"><h2>${esc(v.name)} ${v.archived ? '<span class="badge neutral">已归档</span>' : ""}</h2><div>${button("重命名", "edit-view", `data-id="${v.id}"`)} ${button(v.archived ? "恢复" : "归档", "archive-view", `data-id="${v.id}"`)}</div></div>${v.tags
            .filter((t) => show || !t.archived)
            .map(
              (t) =>
                `<div class="tag-row"><strong>${esc(t.name)} ${t.archived ? '<span class="badge neutral">已归档</span>' : ""}</strong>${t.is_unclassified ? "<small>系统保留</small>" : `${button("重命名", "edit-tag", `data-view="${v.id}" data-id="${t.id}"`)}${button(t.archived ? "恢复" : "归档", "archive-tag", `data-view="${v.id}" data-id="${t.id}"`)}`}</div>`,
            )
            .join(
              "",
            )}${!v.archived ? `<div class="actions">${button("＋ 添加标签", "new-tag", `data-view="${v.id}"`)}</div>` : ""}</section>`,
      )
      .join("")}</div>`,
  };
}
function evidence(b, index) {
  return `<div class="evidence"><strong>${String.fromCharCode(65 + index)} · ${esc(b.merchant)}</strong><div class="amount">${money(b.amount)}</div><p>${esc(b.account_name || "未提供账户")} · ${esc(b.direction)}</p><p class="muted">${date(b.occurred_at)} · ${sourceName(b.source_type)}</p><p class="muted">批次 ${b.import_batch_id || "—"} · 流水 ${esc(b.source_reference || b.id)}</p>${excludedMarkup(b)}</div>`;
}
function candidateActions(c) {
  if (!actionable(c))
    return c.undo_available
      ? button(
          c.status === "deferred" ? "继续处理" : "撤销处理",
          "candidate-undo",
          `data-id="${c.id}" data-candidate-undo="${c.id}"`,
        )
      : "";
  if (c.candidate_type === "duplicate")
    return (
      (c.member_bills?.length ? c.member_bills : [c.bill, c.related_bill])
        .map((b, i) =>
          button(
            `保留 ${String.fromCharCode(65 + i)}`,
            "candidate-action",
            `data-id="${c.id}" data-decision="resolve_duplicate" data-retained="${b.id}" title="仅此笔参与汇总，同组其他原始记录仍保留"`,
          ),
        )
        .join("") +
      button(
        "不是重复",
        "candidate-action",
        `data-id="${c.id}" data-decision="reject_duplicate" title="不是重复（拒绝建议，全部计入）"`,
      ) +
      button(
        "稍后处理",
        "candidate-action",
        `data-id="${c.id}" data-decision="deferred"`,
      )
    );
  const members = c.member_bills?.length
    ? c.member_bills
    : [c.bill, c.related_bill];
  const sufficient =
    c.status !== "evidence_insufficient" &&
    members[0].account_name !== members[1].account_name;
  return (
    button(
      "个人转移",
      "candidate-action",
      `data-id="${c.id}" data-decision="confirm_personal_transfer" ${sufficient ? "" : 'disabled title="需要两个不同账户的证据"'}`,
    ) +
    button(
      "建立往来事项",
      "candidate-matter",
      `data-id="${c.id}"`,
    ) +
    button(
      "忽略建议",
      "candidate-action",
      `data-id="${c.id}" data-decision="ignored"`,
    ) +
    button(
      "稍后处理",
      "candidate-action",
      `data-id="${c.id}" data-decision="deferred"`,
    )
  );
}
async function candidatesPage(params) {
  const query = new URLSearchParams(params);
  if (!query.has("page_size")) query.set("page_size", "20");
  const result = await request(`/api/candidates/page?${query}`);
  return {
    cache: { candidates: result.items, selected: new Set() },
    html: `<section class="panel"><form data-form="filters" class="toolbar">${select(
      "status",
      "状态",
      [
        ["", "所有状态"],
        ["needs_review", "待复核"],
        ["deferred", "稍后处理"],
        ["transfer_grouped", "已确认转移"],
        ["duplicate_excluded", "已确认重复"],
        ["duplicate_rejected", "已拒绝重复建议"],
        ["ignored", "已忽略"],
      ],
      params.get("status"),
    )}${select(
      "candidate_type",
      "类型",
      [
        ["", "所有类型"],
        ["duplicate", "重复"],
        ["transfer", "转移"],
      ],
      params.get("candidate_type"),
    )}<button>筛选</button>${button("清空", "clear-filters")}</form><div class="selection-bar" id="candidate-selection" hidden><strong></strong><div class="actions">${button("批量忽略", "candidate-batch", 'data-decision="ignored"')}${button("批量稍后", "candidate-batch", 'data-decision="deferred"')}${button("批量个人转移", "candidate-batch", 'data-decision="confirm_personal_transfer"')}${button("批量他人转移", "candidate-batch", 'data-decision="confirm_third_party_transfer"')}</div></div><div id="candidate-result">${result.items.map((c) => `<article class="candidate-card"><div class="section-head"><label class="inline-check">${actionable(c) ? `<input type="checkbox" data-select-candidate="${c.id}" aria-label="选择候选 ${c.id}">` : ""}<h3>${c.candidate_type === "duplicate" ? "可能重复的记录" : "可能的转移"} <span class="badge ${actionable(c) ? "warn" : "neutral"}">${statusName(c.status)}</span></h3></label>${button("查看详情", "candidate-detail", `data-id="${c.id}" data-candidate-detail="${c.id}"`)}</div><div class="evidence-grid">${(c.member_bills?.length ? c.member_bills : [c.bill, c.related_bill]).map(evidence).join("")}</div><p class="muted">${esc(c.reason)} · 匹配强度 ${Math.round(c.confidence * 100)}%，仅供参考。</p><p class="muted">${esc(c.aggregation_effect)}</p>${actionable(c) ? `<p class="muted">${c.candidate_type === "duplicate" ? "保留一笔后，同组其他记录不计入汇总；不是重复则全部保留统计。" : "确认转移后，两笔不计入收支汇总；原始记录保留。"}</p>` : ""}<div class="actions">${candidateActions(c)}</div></article>`).join("") || empty("没有符合条件的候选", "已处理记录可通过上方状态筛选找回。")}${pager(result)}</div></section>`,
  };
}
const themes = [
  {
    id: "jade",
    name: "01 清爽青绿",
    desc: "清透浅底，适合日常查看和整理。",
    bg: "#f6f9f7",
    text: "#213b36",
    accent: "#167561",
    line: "#dce5e0",
    button: "#fff",
  },
  {
    id: "blue",
    name: "02 克制蓝灰",
    desc: "中性规整，熟悉的工作台观感。",
    bg: "#f4f6fa",
    text: "#26354b",
    accent: "#3864a3",
    line: "#dae0eb",
    button: "#fff",
  },
  {
    id: "paper",
    name: "03 暖白纸感",
    desc: "柔和暖白，接近个人手账。",
    bg: "#fbf7ef",
    text: "#493e30",
    accent: "#886034",
    line: "#e7dfd1",
    button: "#fff",
  },
  {
    id: "focus",
    name: "04 深色专注",
    desc: "深色背景与清晰层级，默认主题。",
    bg: "#202b2d",
    text: "#e4ece8",
    accent: "#91d7be",
    line: "#425453",
    button: "#153b2d",
  },
];
async function settingsPage() {
  const active = document.documentElement.dataset.theme;
  return {
    html: `<section class="panel"><h2>外观主题</h2><p class="muted">点击卡片立即切换，无需另行保存。选择保存在当前浏览器，不影响账目数据。</p><div class="theme-grid">${themes.map((t) => `<button type="button" class="theme-card" data-action="theme" data-theme-choice="${t.id}" aria-pressed="${active === t.id}" aria-label="使用${t.name}" style="--preview-bg:${t.bg};--preview-text:${t.text};--preview-accent:${t.accent};--preview-line:${t.line};--preview-button:${t.button}"><span class="theme-sample" aria-hidden="true"><span>流水账本</span><strong>¥2,480.00</strong><span class="sample-row"><span>午餐 · 餐饮</span><span>− ¥28.00</span></span><span class="sample-action">导入账单</span></span><span class="theme-description"><strong>${t.name}<span class="theme-selected">${active === t.id ? "已使用" : ""}</span></strong><small>${t.desc}</small></span></button>`).join("")}</div><p class="muted" style="margin:18px 0 0">卡片内金额为外观示例。</p></section>`,
  };
}
function syncThemeChoices() {
  const active = document.documentElement.dataset.theme;
  $$("[data-theme-choice]").forEach((b) => {
    const selected = b.dataset.themeChoice === active;
    b.setAttribute("aria-pressed", String(selected));
    $(".theme-selected", b).textContent = selected ? "已使用" : "";
  });
}
window.addEventListener("paam-theme-change", syncThemeChoices);
async function databasePage(params) {
  const catalog = await request("/api/database/tables");
  const name = params.get("table") || "bills";
  const valid = catalog.tables.some((t) => t.name === name)
    ? name
    : catalog.tables[0]?.name;
  if (!valid) return { html: empty("暂无数据表") };
  const result = await request(
    `/api/database/tables/${encodeURIComponent(valid)}?page=${params.get("page") || 1}&page_size=25`,
  );
  return {
    html: `<section class="panel"><form class="toolbar" data-form="filters">${select(
      "table",
      "数据表",
      catalog.tables.map((t) => [t.name, `${t.name}（${t.row_count} 行）`]),
      valid,
    )}<button>查看</button></form><p class="muted">只读查看 · 单元格最多显示 600 字符 · 原始内容不受影响</p><details><summary>字段与索引（${result.columns.length} 个字段）</summary>${table(
      ["字段", "类型", "主键"],
      result.columns.map(
        (c) =>
          `<tr><td>${esc(c.name)}</td><td>${esc(c.type)}</td><td>${c.primary_key ? "是" : "—"}</td></tr>`,
      ),
    )}<p class="muted">${result.indexes.map((i) => `${esc(i.name)} (${esc(i.columns.join(", "))})`).join("；") || "无额外索引"}</p></details><h2>行内容</h2>${
      result.rows.length
        ? table(
            result.columns.map((c) => esc(c.name)),
            result.rows.map(
              (row) =>
                `<tr>${result.columns.map((c) => `<td>${esc(row[c.name])}</td>`).join("")}</tr>`,
            ),
          )
        : empty("这张表还没有记录")
    }${pager(result)}</section>`,
  };
}
let dialogId = 0;
function modal(title, body, { wide = false, id = "" } = {}) {
  const d = document.createElement("dialog");
  d.className = wide ? "wide" : "";
  d.id = id || `dialog-${++dialogId}`;
  d.setAttribute("aria-labelledby", `${d.id}-title`);
  d.innerHTML = `<div class="dialog-head"><h2 id="${d.id}-title">${esc(title)}</h2><button class="quiet" data-close aria-label="关闭${esc(title)}">关闭</button></div><div class="dialog-body">${body}</div>`;
  document.body.append(d);
  $("[data-close]", d).onclick = () => d.close();
  d.addEventListener("close", () => d.remove());
  d.showModal();
  return d;
}
function errorIn(d, error) {
  let node = $(".form-error", d);
  if (!node) {
    node = document.createElement("div");
    node.className = "form-error error";
    node.setAttribute("role", "alert");
    $(".dialog-body", d).prepend(node);
  }
  node.textContent = message(error);
}
async function formSave(d, operation) {
  const controls = $$("button,input,select", d);
  const disabled = controls.map((c) => c.disabled);
  $$(".form-error", d).forEach((node) => node.remove());
  controls.forEach((c) => (c.disabled = true));
  d.addEventListener("cancel", preventCancel);
  try {
    await operation();
    return true;
  } catch (e) {
    errorIn(d, e);
    return false;
  } finally {
    if (d.isConnected) controls.forEach((c, i) => (c.disabled = disabled[i]));
    d.removeEventListener("cancel", preventCancel);
  }
}
function preventCancel(event) {
  event.preventDefault();
}
async function assignTags(ids) {
  const epoch = state.epoch;
  const bill = state.bills.find((b) => b.id === ids[0]);
  const [views, audits] = await Promise.all([request("/api/tag-views"), request(`/api/bills/${ids[0]}/tags`)]);
  const expectedAudit = audits.find(a => !a.superseded)?.id || 0;
  if (epoch !== state.epoch) return;
  const bulk = ids.length > 1;
  const d = modal(
    bulk ? `修改 ${ids.length} 条流水的标签` : "选择标签",
    `<form><p class="muted">${bulk ? "只修改你选中的维度，其余标签保持不变。" : "每个分类维度选择一个标签。"}</p>${views.map((v) => select(v.system_name, v.name, [...(bulk ? [["", "保持原标签"]] : []), ...v.tags.filter((t) => !t.archived).map((t) => [t.system_name, t.name])], bulk ? "" : bill?.tag_state?.[v.system_name] || "unclassified")).join("")}<div class="actions"><button type="button" data-cancel>取消</button><button class="primary">保存标签</button></div></form>`,
  );
  $("[data-cancel]", d).onclick = () => d.close();
  $("form", d).onsubmit = async (e) => {
    e.preventDefault();
    const tag_state = Object.fromEntries(
      [...new FormData(e.currentTarget)].filter(([, v]) => v),
    );
    if (bulk && !Object.keys(tag_state).length)
      return errorIn(d, "请至少选择一个要修改的分类维度。");
    await formSave(d, async () => {
      await jsonRequest(
        bulk
          ? "/api/transactions/bulk-tag-state"
          : `/api/transactions/${ids[0]}/tag-state`,
        "PUT",
        bulk ? { bill_ids: ids, tag_state, merge: true, expected_revisions: Object.fromEntries(state.bills.filter(b => ids.includes(b.id)).map(b => [b.id, b.tag_revision_id])) } : { tag_state, expected_audit_id: expectedAudit },
      );
      d.close();
      toast("标签已保存");
      await render({ preservePosition: true });
    });
  };
}
async function editDefinition(kind, viewId, tagId) {
  const view = state.views.find((v) => v.id === viewId);
  const tag = view?.tags.find((t) => t.id === tagId);
  const title = {
    "new-view": "新建分类维度",
    "edit-view": "重命名分类维度",
    "new-tag": "添加标签",
    "edit-tag": "重命名标签",
  }[kind];
  const current =
    kind === "edit-view" ? view.name : kind === "edit-tag" ? tag.name : "";
  const d = modal(
    title,
    `<form>${input("name", "名称", "text", current, 'required maxlength="120" autocomplete="off"')}<p class="muted">${kind.includes("view") ? "例如：消费类别、使用场景。新维度自带“未分类”。" : "例如：餐饮、交通、旅行。"}</p><div class="actions"><button class="primary">保存</button></div></form>`,
  );
  $("form", d).onsubmit = async (e) => {
    e.preventDefault();
    const name = new FormData(e.currentTarget).get("name").trim();
    if (!name) return errorIn(d, "请输入名称，不能仅包含空格。");
    const url =
      kind === "new-view"
        ? "/api/tag-views"
        : kind === "edit-view"
          ? `/api/tag-views/${viewId}`
          : kind === "new-tag"
            ? `/api/tag-views/${viewId}/tags`
            : `/api/tag-views/${viewId}/tags/${tagId}`;
    await formSave(d, async () => {
      await jsonRequest(url, kind.startsWith("new") ? "POST" : "PATCH", {
        name,
      });
      d.close();
      toast("已保存");
      await render();
    });
  };
}
async function archiveDefinition(viewId, tagId) {
  const view = state.views.find((v) => v.id === viewId);
  const item = tagId ? view.tags.find((t) => t.id === tagId) : view;
  const archived = !item.archived;
  const d = modal(
    archived ? "归档确认" : "恢复确认",
    `<p>${esc(item.name)}</p><p class="muted">${archived ? "归档后不再作为新分类选项，历史记录仍保留。可在“显示已归档”中恢复。" : "恢复后可继续用于流水分类。"}</p><div class="actions">${button(archived ? "确认归档" : "确认恢复", "confirm-definition", "", true)}</div>`,
  );
  $("[data-action=confirm-definition]", d).onclick = () =>
    formSave(d, async () => {
      await jsonRequest(
        `/api/tag-views/${viewId}${tagId ? `/tags/${tagId}` : ""}`,
        "PATCH",
        { archived },
      );
      d.close();
      toast(archived ? "已归档" : "已恢复");
      await render();
    });
}
async function billDetail(id) {
  const epoch = state.epoch;
  const bill = state.bills.find((b) => b.id === id);
  const origin = await request(`/api/transactions/${id}/source`);
  if (epoch !== state.epoch) return;
  const detailDialog = modal(
    "流水详情",
    `<div class="section-head"><h2>${esc(bill.merchant)}</h2><strong>${money(bill.amount)}</strong></div>${excludedMarkup(bill)}<dl><dt>交易时间</dt><dd>${date(bill.occurred_at)}</dd><dt>账户</dt><dd>${esc(bill.account_name)}</dd><dt>来源</dt><dd>${sourceName(bill.source_type)}</dd><dt>备注</dt><dd>${esc(bill.note) || "—"}</dd><dt>标签</dt><dd>${tagsMarkup(bill)}</dd><dt>原始文件</dt><dd>${esc(origin.artifact?.filename || "手工记录")}</dd><dt>原始流水号</dt><dd>${esc(origin.origin?.source_reference || "—")}</dd></dl><details open><summary>原始字段</summary><pre>${esc(JSON.stringify(origin.raw_fields, null, 2))}</pre></details>`,
  );
  await review.billActions(detailDialog, bill);
}
async function candidateDetail(id) {
  const epoch = state.epoch;
  const detail = await request(`/api/candidates/${id}/detail`);
  if (epoch !== state.epoch) return;
  const c = detail.candidate;
  const d = modal(
    `候选 ${id} 详情`,
    `<p><span class="badge">${statusName(c.status)}</span> ${esc(detail.match_basis)}</p><p class="notice">${esc(c.aggregation_effect)}</p><div class="evidence-grid">${detail.members.map((m, i) => `<section>${evidence(m.bill, i)}<p class="muted">文件：${esc(m.source.batch_filename || "手工记录")}</p><details open><summary>原始字段（默认展开）</summary><pre>${esc(JSON.stringify(m.raw_fields, null, 2))}</pre></details></section>`).join("")}</div><div class="actions">${candidateActions(c)}</div><details><summary>处理历史</summary><pre>${esc(JSON.stringify(detail.actions, null, 2))}</pre></details>`,
    { wide: true, id: "candidate-detail-dialog" },
  );
  $("[data-close]", d).setAttribute(
    "aria-label",
    "关闭候选详情，不改变候选或流水状态",
  );
}
function confirmReview(text) {
  return new Promise((resolve) => {
    const d = modal(
      "确认复核影响",
      `<p class="notice">${esc(text)}</p><p class="muted">原始流水不会删除。处理记录可在复核页找到并撤销。</p><div class="actions"><button type="button" data-review-cancel>返回核对</button><button type="button" class="primary" data-review-confirm>确认处理</button></div>`,
    );
    let accepted = false;
    $("[data-review-cancel]", d).onclick = () => d.close();
    $("[data-review-confirm]", d).onclick = () => {
      accepted = true;
      d.close();
    };
    d.addEventListener("close", () => resolve(accepted), { once: true });
    $("[data-review-cancel]", d).focus();
  });
}
async function candidateDecision(button) {
  const d = button.closest("dialog");
  const id = Number(button.dataset.id);
  const c = state.candidates.find((c) => c.id === id);
  const batch = button.dataset.action === "candidate-batch";
  const ids = batch ? [...state.selected] : [id];
  const decision = button.dataset.decision;
  if (batch && !ids.length) return;
  if (
    batch &&
    decision.includes("transfer") &&
    state.candidates.some(
      (c) => ids.includes(c.id) && c.candidate_type !== "transfer",
    )
  )
    throw new Error("请只选择转移候选；重复记录需要逐组选择保留项。");
  if (
    batch ||
    decision === "resolve_duplicate" ||
    decision?.includes("transfer")
  ) {
    const effect =
      decision === "resolve_duplicate"
        ? `将保留流水 ${button.dataset.retained} 参与汇总，同组其他记录不计入收支。`
        : decision?.includes("transfer")
          ? `将 ${ids.length} 组记录确认为${decision.includes("third_party") ? "他人转移 / 代收代付" : "本人账户间转移"}，相关流水不再计入收支汇总。`
          : `将对 ${ids.length} 组候选执行“${decision === "ignored" ? "忽略建议" : "稍后处理"}”，本次不改变收支统计。`;
    if (!(await confirmReview(effect))) return;
  }
  let recordedAction = null;
  const operation = async () => {
    if (button.dataset.action === "candidate-undo")
      await request(`/api/candidates/${id}/undo?expected_action_id=${c.current_action_id}`, { method: "POST" });
    else if (batch)
      await jsonRequest("/api/candidates/batch", "POST", {
        items: ids.map((candidate_id) => { const c = state.candidates.find(item => item.id === candidate_id); return { candidate_id, action: decision, expected_action_id: c.current_action_id, expected_member_ids: c.member_bills.map(b => b.id) }; }),
      });
    else
      recordedAction = await jsonRequest(`/api/candidates/${id}`, "POST", {
        action: decision,
        expected_action_id: c?.current_action_id,
        expected_member_ids: c?.member_bills.map(b => b.id),
        ...(button.dataset.retained
          ? { retained_bill_id: Number(button.dataset.retained) }
          : {}),
      });
    d?.close();
    await render({ preservePosition: true });
    toast(
      button.dataset.action === "candidate-undo"
        ? c?.status === "deferred"
          ? "已恢复待处理，可以继续复核"
          : "已撤销，原始状态已恢复"
        : "处理已保存，可在复核记录中撤销",
      !batch && button.dataset.action !== "candidate-undo"
        ? async () => {
            await request(`/api/candidates/${id}/undo?expected_action_id=${recordedAction.current_action_id}`, { method: "POST" });
            await render({ preservePosition: true });
            toast("已撤销本次处理");
          }
        : null,
    );
  };
  if (d) await formSave(d, operation);
  else await operation();
}
function importStep(d, index) {
  $$(".steps span", d).forEach((node, i) => {
    node.classList.toggle("active", i === index);
    if (i === index) node.setAttribute("aria-current", "step");
    else node.removeAttribute("aria-current");
  });
}
function invalidateImport(d) {
  importStep(d, 0);
  $$(".form-error", d).forEach((node) => node.remove());
  state.importVersion++;
  state.pendingImport = null;
  $("#import-preview", d).innerHTML =
    '<p class="muted">文件或平台已变化，请重新预览。</p>';
  $("#import-password", d).value = "";
  $("#password-label", d).hidden = ![
    $("#import-files", d),
    $("#import-folder", d),
  ].some((x) =>
    [...x.files].some((f) => f.name.toLowerCase().endsWith(".zip")),
  );
}
function openImport() {
  state.pendingImport = null;
  state.importVersion++;
  const d = modal(
    "导入账单",
    `<div class="steps"><span class="active">1 选择文件</span><span>2 核对预览</span><span>3 导入结果</span></div><p class="muted">选择平台后上传它导出的账单；支持 CSV、XLS、XLSX 和密码 ZIP。</p>${select(
      "provider",
      "账单平台",
      [
        ["alipay", "支付宝"],
        ["wechat", "微信"],
      ],
      "alipay",
    )}<div class="import-files"><label>选择账单文件<input type="file" id="import-files" multiple accept=".csv,.xls,.xlsx,.zip"></label><label>或选择文件夹<input type="file" id="import-folder" webkitdirectory multiple></label></div><label id="password-label" hidden>ZIP 密码（仅本次请求使用）<input type="password" id="import-password" autocomplete="off"></label><div class="actions">${button("预览账单", "preview-import", "", true)}</div><div id="import-preview" aria-live="polite"></div>`,
    { wide: true, id: "import-dialog" },
  );
  $$("input[type=file]", d).forEach(
    (input) =>
      (input.onchange = () => {
        const other =
          input.id === "import-files"
            ? $("#import-folder", d)
            : $("#import-files", d);
        other.value = "";
        invalidateImport(d);
      }),
  );
  $("select", d).onchange = () => invalidateImport(d);
  d.addEventListener("close", () => {
    state.importVersion++;
    state.pendingImport = null;
    $("#import-password", d).value = "";
  });
}
async function previewImport(d) {
  $$(".form-error", d).forEach((node) => node.remove());
  const version = ++state.importVersion;
  state.pendingImport = null;
  const files = [
    ...$("#import-files", d).files,
    ...$("#import-folder", d).files,
  ];
  if (!files.length) throw new Error("请先选择账单文件或文件夹。");
  if (files.length > 100)
    throw new Error("一次最多预览 100 个文件，请分批选择。");
  if (files.some((file) => file.size > 25 * 1024 * 1024))
    throw new Error("单个文件不能超过 25 MB，请拆分账单后导入。");
  const source = $("select[name=provider]", d).value;
  const target = $("#import-preview", d);
  target.innerHTML = '<p class="busy">正在解析账单…</p>';
  const password = $("#import-password", d).value;
  $("#import-password", d).value = "";
  try {
    const payload = {
      files: await Promise.all(
        files.map(async (f) => ({
          filename: f.name,
          content_base64: await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = () =>
              reject(new Error("文件读取失败，请重新选择。"));
            reader.readAsDataURL(f);
          }),
        })),
      ),
    };
    const result = await request(`/api/imports/${source}/batch/preview`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(password ? { "X-Import-Password": password } : {}),
      },
      body: JSON.stringify(payload),
    });
    if (version !== state.importVersion || !d.isConnected) return;
    const eligible = result.files
      .map((f, i) => (f.ok && !f.duplicate ? i : -1))
      .filter((i) => i >= 0);
    const needsPassword = eligible.some((i) =>
      files[i].name.toLowerCase().endsWith(".zip"),
    );
    state.pendingImport = {
      version,
      source,
      payload: {
        files: eligible.map((i) => payload.files[i]),
        batch_token: result.batch_token,
      },
      needsPassword,
    };
    importStep(d, 1);
    target.innerHTML = `<h3>核对预览 · ${sourceName(source)}</h3>${result.files
      .map(
        (f) =>
          `<article class="import-file"><h3>${esc(f.filename)} <span class="badge ${f.ok && !f.duplicate ? "" : "warn"}">${f.duplicate ? "已导入，将跳过" : f.ok ? `${f.preview.row_count} 条` : "无法导入"}</span></h3>${f.error ? `<p class="error">${esc(message(f.error))}</p>` : ""}${
            f.preview
              ? `${f.preview.issues?.length ? `<p class="error" role="alert">${f.preview.issues.length} 条数据存在问题；确认后保留原始证据，待修正后入账。</p>${f.preview.issues.slice(0, 20).map(i => `<p>第 ${i.row_number} 行：${esc(i.error)}</p>`).join('')}` : ''}<small>前 ${f.preview.preview_rows.length} 条原始字段预览 · 请核对时间、金额及收支方向</small>${table(
                  ["时间", "交易方", "金额", "收支", "备注"],
                  f.preview.preview_rows.map(
                    (r) =>
                      `<tr><td>${esc(r["交易时间"])}</td><td>${esc(r["交易方"])}</td><td>${esc(r["金额"])}</td><td>${esc(r["收支"])}</td><td>${esc(r["备注"])}</td></tr>`,
                  ),
                )}`
              : ""
          }</article>`,
      )
      .join(
        "",
      )}<p class="muted">将导入 ${eligible.length} 个有效文件，其他文件不会提交。</p>${needsPassword ? '<p class="notice">预览密码已清空。确认前请在上方再次输入 ZIP 密码。</p>' : ""}<div class="actions">${button(`确认导入 ${eligible.length} 个文件`, "confirm-import", `id="confirm-import" ${eligible.length ? "" : "disabled"}`, true)}</div>`;
  } catch (error) {
    if (version === state.importVersion && d.isConnected)
      target.innerHTML = `<p class="error" role="alert">${esc(message(error))}</p>`;
  }
}
async function confirmImport(d) {
  const pending = state.pendingImport;
  if (!pending || pending.version !== state.importVersion)
    throw new Error("文件已变化，请重新预览。");
  const password = $("#import-password", d).value;
  if (pending.needsPassword && !password)
    throw new Error("请再次输入 ZIP 密码后确认导入。");
  $("#import-password", d).value = "";
  await formSave(d, async () => {
    const result = await request(`/api/imports/${pending.source}/batch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(password ? { "X-Import-Password": password } : {}),
      },
      body: JSON.stringify(pending.payload),
    });
    state.pendingImport = null;
    importStep(d, 2);
    const imported = result.files.filter((f) => f.status === "imported");
    const count = imported.reduce(
      (n, f) => n + (f.import_batch?.imported_count || 0),
      0,
    );
    const issueCount = imported.reduce((n, f) => n + (f.import_batch?.issue_count || 0), 0);
    $("#import-preview", d).innerHTML =
      `<div class="success" role="status">本次导入 ${count} 条流水，${imported.length} 个文件成功。</div>${result.files.map((f) => `<article class="import-file"><strong>${esc(f.filename)}</strong><p class="${f.status === "imported" ? "muted" : "error"}">${f.status === "imported" ? `已导入 ${f.import_batch.imported_count} 条流水` : esc(message(f.error || "文件未导入，请重新预览。"))}</p></article>`).join("")}<div class="actions">${button("查看流水", "import-done", "", true)}${button("查看候选", "import-review")}</div>`;
    if (issueCount) $('#import-preview', d).insertAdjacentHTML('afterbegin', `<p class="error" role="alert">另有 ${issueCount} 条待核验记录，未计入金额。请到“复核 → 数据问题”修正。</p>`);
  });
}
function selectionChanged() {
  const isBill = state.page === "data";
  const selector = isBill ? "[data-select-bill]" : "[data-select-candidate]";
  $$(selector).forEach(
    (x) =>
      (x.checked = state.selected.has(
        Number(isBill ? x.dataset.selectBill : x.dataset.selectCandidate),
      )),
  );
  const bar = $(isBill ? "#bill-selection" : "#candidate-selection");
  if (bar) {
    bar.hidden = state.selected.size === 0;
    $("strong", bar).textContent =
      `已选择 ${state.selected.size} ${isBill ? "条流水" : "组候选"}`;
    if (!$("[data-action=cancel-selection]", bar)) {
      bar.insertAdjacentHTML(
        "beforeend",
        button("取消选择", "cancel-selection"),
      );
    }
  }
  const all = $("#select-all-bills");
  if (all) {
    all.checked =
      state.selected.size === state.bills.length && state.bills.length > 0;
    all.indeterminate = state.selected.size > 0 && !all.checked;
  }
}
function markFiltersDirty(event) {
  const form = event.target.closest('[data-form="filters"]');
  if (!form || form.querySelector(".filter-pending")) return;
  const note = document.createElement("p");
  note.className = "filter-pending notice";
  note.setAttribute("role", "status");
  note.textContent = "筛选条件已修改，尚未应用。点击查询 / 应用后更新结果。";
  form.append(note);
}
document.addEventListener("input", markFiltersDirty);
document.addEventListener("change", (event) => {
  markFiltersDirty(event);
  const el = event.target;
  if (el.matches("[data-select-bill],[data-select-candidate]")) {
    const id = Number(el.dataset.selectBill || el.dataset.selectCandidate);
    el.checked ? state.selected.add(id) : state.selected.delete(id);
    selectionChanged();
  }
  if (el.id === "select-all-bills") {
    state.selected = new Set(el.checked ? state.bills.map((b) => b.id) : []);
    selectionChanged();
  }
  if (el.id === "show-archived")
    updateParams({ archived: el.checked ? "true" : "", page: "" });
});
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches("[data-form=filters]")) return;
  event.preventDefault();
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form)) {
    if (!value) continue;
    if (key.startsWith("tag:"))
      params.append("tag", `${key.slice(4)}:${value}`);
    else params.set(key, value);
  }
  if (
    params.get("direction") === "transfer" &&
    params.get("scope") === "effective"
  )
    params.set("scope", "all");
  navigate(state.page, params);
});
document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-page]");
  if (nav) {
    navigate(nav.dataset.page);
    return;
  }
  const toggle = event.target.closest("#nav-toggle");
  if (toggle) {
    const root = $(".workspace");
    root.dataset.collapsed = String(root.dataset.collapsed !== "true");
    try {
      localStorage.setItem("paam-sidebar-collapsed", root.dataset.collapsed);
    } catch {}
    collapseLabel();
    return;
  }
  const b = event.target.closest("[data-action]");
  if (!b || b.disabled || b.dataset.busy === "true") return;
  const a = b.dataset.action;
  const id = Number(b.dataset.id);
  const d = b.closest("dialog");
  if (a === "confirm-definition") return;
  b.dataset.busy = "true";
  b.setAttribute("aria-busy", "true");
  try {
    if (a === "retry") await render();
    else if (a === "theme") {
      const saved = window.paamTheme.set(b.dataset.themeChoice);
      syncThemeChoices();
      toast(
        saved
          ? "主题已切换并保存"
          : "主题已切换；浏览器禁止保存，刷新后可能恢复默认。",
      );
    } else if (a === "import") openImport();
    else if (a === "preview-import") await previewImport(d);
    else if (a === "confirm-import") await confirmImport(d);
    else if (a === "import-done" || a === "import-review") {
      d.close();
      navigate(
        a === "import-done" ? "data" : "candidates",
        a === "import-done" ? "scope=all" : "status=needs_review",
      );
    } else if (a === "cancel-selection") {
      state.selected.clear();
      selectionChanged();
    } else if (a === "clear-filters")
      navigate(state.page, new URLSearchParams());
    else if (a === "page-step")
      updateParams({
        page: Math.max(
          1,
          Number(state.params.get("page") || 1) + Number(b.dataset.step),
        ),
      });
    else if (a === "review")
      navigate(
        "candidates",
        state.pendingCandidateCount ? "status=needs_review" : "",
      );
    else if (a === "all-ledger") navigate("data", "scope=all");
    else if (a === "day")
      navigate(
        "data",
        new URLSearchParams({
          date_from: b.dataset.day,
          date_to: b.dataset.day,
        }),
      );
    else if (a === "drill") {
      await review.drill(b.dataset.metric || ({ income: 'income', expense: 'spending' }[b.dataset.direction]) || 'net');
    } else if (a === 'review-matters') await review.matters();
    else if (a === 'new-matter') await review.editor(null, state.bills.filter(bill => state.selected.has(bill.id)));
    else if (a === 'review-refunds') await review.refunds();
    else if (a === 'review-issues') await review.issues();
    else if (a === 'candidate-matter') { d?.close(); await review.editor(null, state.candidates.find(c => c.id === id).member_bills); }
    else if (a === "assign") await assignTags([id]);
    else if (a === "bulk-tags") await assignTags([...state.selected]);
    else if (a === "bill-detail") await billDetail(id);
    else if (["new-view", "edit-view", "new-tag", "edit-tag"].includes(a))
      await editDefinition(
        a,
        a.includes("view") ? id : Number(b.dataset.view),
        a === "edit-tag" ? id : null,
      );
    else if (a === "archive-view" || a === "archive-tag")
      await archiveDefinition(
        a === "archive-view" ? id : Number(b.dataset.view),
        a === "archive-tag" ? id : null,
      );
    else if (a === "candidate-detail") await candidateDetail(id);
    else if (
      ["candidate-action", "candidate-undo", "candidate-batch"].includes(a)
    )
      await candidateDecision(b);
  } catch (error) {
    if (d?.isConnected) errorIn(d, error);
    else {
      let node = $("#action-error");
      if (!node) {
        node = document.createElement("div");
        node.id = "action-error";
        node.className = "error";
        node.setAttribute("role", "alert");
        $("#page-content").prepend(node);
      }
      node.textContent = message(error);
      node.scrollIntoView({ block: "center" });
    }
  } finally {
    if (b.isConnected) {
      delete b.dataset.busy;
      b.removeAttribute("aria-busy");
    }
  }
});
const review = reviewTools({ $, $$, esc, money, date, request, jsonRequest, modal, input, select, table, formSave, confirmReview, render, state, toast });
if (!location.hash) history.replaceState(null, "", "#summary");
readRoute();

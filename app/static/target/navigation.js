import { $, $$, esc } from "./core.js";

export const moduleMeta = {
  details: {
    label: "明细",
    caption: "查找与追溯",
    page: "ledger",
  },
  accounts: {
    label: "账户",
    caption: "月度收支",
    page: "summary",
  },
  workbench: {
    label: "工作台",
    caption: "导入与审查",
    page: "reviews",
  },
};

const pageModules = {
  ledger: "details",
  economy: "details",
  "ledger-reviews": "details",
  "ledger-imports": "details",
  "ledger-tags": "details",
  "legacy-ledger": "workbench",
  summary: "accounts",
  reviews: "workbench",
  import: "workbench",
  "import-history": "workbench",
  tags: "workbench",
};

const canonicalPages = {
  details: "ledger",
  accounts: "summary",
  workbench: "reviews",
};

const detailViews = {
  economics: "economy",
  reviews: "ledger-reviews",
  imports: "ledger-imports",
  tags: "ledger-tags",
};

export function pageModule(page) {
  return pageModules[page] || "details";
}

export function canonicalHash(page, params = new URLSearchParams()) {
  const module = pageModule(page);
  const query = new URLSearchParams(params);
  if (module === "workbench") {
    query.set("task", page);
    query.delete("detail");
  } else if (module === "details") {
    query.delete("task");
    const detail = Object.entries(detailViews).find(([, detailPage]) => detailPage === page)?.[0];
    if (detail) query.set("detail", detail);
    else query.delete("detail");
  } else {
    query.delete("task");
    query.delete("detail");
  }
  return `${module}${query.toString() ? `?${query}` : ""}`;
}

export function parseHash(hash, validPages) {
  const [rawPage, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  let page = rawPage;
  if (canonicalPages[rawPage]) {
    if (rawPage === "workbench") page = params.get("task") || canonicalPages[rawPage];
    else if (rawPage === "details") page = detailViews[params.get("detail")] || canonicalPages[rawPage];
    else page = canonicalPages[rawPage];
  }
  if (!validPages.has(page)) page = "ledger";
  if (pageModule(page) !== "workbench") params.delete("task");
  if (pageModule(page) !== "details") params.delete("detail");
  return { page, params };
}

export function shellMarkup() {
  const nav = Object.entries(moduleMeta).map(([id, item], index) => `
    <button type="button" data-module="${id}" data-page="${item.page}" aria-pressed="false">
      <span>0${index + 1}</span><strong>${esc(item.label)}</strong><small>${esc(item.caption)}</small>
    </button>`).join("");
  return `<div class="module-shell target-shell">
    <header class="module-topbar">
      <a class="module-brand" href="#details" aria-label="个人账本首页">
        <img src="/static/personal-assets-ai-manager.svg" alt="">
        <span><strong>个人账本</strong><small>账目工作区</small></span>
      </a>
      <nav class="module-nav" aria-label="主要模块">${nav}</nav>
      <div class="topbar-actions">
        <span class="connection"><i></i><span data-live-label>本地账本已连接</span></span>
        <button type="button" class="primary compact" data-page="import">导入账单</button>
      </div>
    </header>
    <main class="module-content" id="content" tabindex="-1">
      <div class="domain-note"><span>账目形成</span><strong>事实流水 → 审查 → 经济流水</strong><p>事实保留来源，审查负责解释，经济流水是最终阅读和统计结果。</p></div>
      <header class="module-heading page-header">
        <div><span class="eyebrow" id="section-kicker">LEDGER</span><h1 id="title"></h1><p id="help"></p></div>
        <div class="page-actions" id="page-actions"></div>
      </header>
      <div id="page-content" aria-live="polite"></div>
    </main>
  </div>`;
}

export function syncNavigation(page) {
  const active = pageModule(page);
  $$('[data-module]').forEach((button) => {
    const selected = button.dataset.module === active;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("#section-kicker").textContent = active === "details"
    ? "DETAILS"
    : active === "accounts" ? "MONTHLY OVERVIEW" : "WORKBENCH";
}

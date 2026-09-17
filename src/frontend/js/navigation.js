import { $, $$, esc } from "./util/core.js";

export const moduleMeta = {
  details: {
    label: "明细",
    caption: "查找与追溯",
    page: "ledger",
  },
  overview: {
    label: "概览",
    caption: "账本汇总",
    page: "summary",
  },
  workbench: {
    label: "工作台",
    caption: "导入与审查",
    page: "import",
  },
};

const secondaryMeta = {
  details: [
    ["ledger", "事实流水", "Transaction Fact"],
    ["economy", "账本流水", "Ledger"],
    ["ledger-reviews", "审查记录", "Review"],
    ["ledger-imports", "导入文件", "Import File"],
    ["ledger-tags", "标签管理", "Tag"],
  ],
  overview: [
    ["summary", "概览", "Ledger Summary"],
  ],
  workbench: [
    ["import", "导入 / 上传", "预览并写入事实层"],
    ["reviews", "账单审查", "配置 Fact 与 Ledger"],
  ],
};

const pageModules = {
  ledger: "details",
  economy: "details",
  "ledger-reviews": "details",
  "ledger-imports": "details",
  "ledger-tags": "details",
  summary: "overview",
  reviews: "workbench",
  import: "workbench",
  "import-history": "workbench",
};

const canonicalPages = {
  details: "ledger",
  overview: "summary",
  // Preserve old bookmarks while making Overview the canonical module name.
  accounts: "summary",
  workbench: "import",
};

const detailViews = {
  economics: "economy",
  reviews: "ledger-reviews",
  imports: "ledger-imports",
  tags: "ledger-tags",
};

const pagePaths = {
  ledger: "details/transaction-fact",
  economy: "details/ledger",
  "ledger-reviews": "details/review",
  "ledger-imports": "details/import-file",
  "ledger-tags": "details/tag",
  summary: "overview",
  import: "workbench/import",
  reviews: "workbench/review",
  "import-history": "workbench/import/history",
};

const pathPages = Object.fromEntries(Object.entries(pagePaths).map(([page, path]) => [path, page]));
pathPages["workbench/review/create"] = "reviews";

export function pageModule(page) {
  return pageModules[page] || "details";
}

export function canonicalHash(page, params = new URLSearchParams()) {
  const query = new URLSearchParams(params);
  query.delete("task");
  query.delete("detail");
  const path = pagePaths[page] || pagePaths.ledger;
  return `${path}${query.toString() ? `?${query}` : ""}`;
}

export function parseHash(hash, validPages) {
  const [rawPage, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  let page = pathPages[rawPage] || rawPage;
  if (!pathPages[rawPage] && canonicalPages[rawPage]) {
    if (rawPage === "workbench") page = params.get("task") || canonicalPages[rawPage];
    else if (rawPage === "details") page = detailViews[params.get("detail")] || canonicalPages[rawPage];
    else page = canonicalPages[rawPage];
  }
  if (!validPages.has(page)) page = "ledger";
  params.delete("task");
  params.delete("detail");
  return { page, params };
}

export function shellMarkup() {
  const nav = Object.entries(moduleMeta).map(([id, item], index) => `
    <button type="button" data-module="${id}" data-page="${item.page}" aria-pressed="false">
      <span>0${index + 1}</span><strong>${esc(item.label)}</strong><small>${esc(item.caption)}</small>
    </button>`).join("");
  return `<div class="module-shell target-shell">
    <header class="module-topbar">
      <a class="module-brand" href="#details/transaction-fact" aria-label="个人账本首页">
        <img src="/asset/personal-assets-ai-manager.svg" alt="">
        <span><strong>个人账本</strong><small>账目工作区</small></span>
      </a>
      <nav class="module-nav" aria-label="主要模块">${nav}</nav>
      <div class="topbar-actions">
        <span class="connection"><i></i><span data-live-label>本地账本已连接</span></span>
        <button type="button" class="primary compact" data-page="import">导入账单</button>
      </div>
    </header>
    <div class="module-subbar">
      <nav class="secondary-nav" id="secondary-nav" aria-label="当前模块功能"></nav>
      <div class="domain-note"><span>账目形成</span><strong>事实流水 → 审查 → 经济流水</strong><p>事实保留来源，审查负责解释，经济流水是最终阅读和统计结果。</p></div>
    </div>
    <main class="module-content" id="content" tabindex="-1">
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
  const selectedPage = page === "import-history" ? "import" : page;
  const secondaryNavigation = $("#secondary-nav");
  if (secondaryNavigation) {
    secondaryNavigation.dataset.activeModule = active;
    secondaryNavigation.closest(".module-subbar")?.setAttribute("data-active-module", active);
    secondaryNavigation.innerHTML = secondaryMeta[active].map(([id, label, caption]) => `
      <button type="button" data-page="${id}" class="${id === selectedPage ? "active" : ""}" aria-pressed="${id === selectedPage}">
        <strong>${esc(label)}</strong><small>${esc(caption)}</small>
      </button>`).join("");
  }
  $("#section-kicker").textContent = active === "details"
    ? "DETAILS"
    : active === "overview" ? "LEDGER OVERVIEW" : "WORKBENCH";
}

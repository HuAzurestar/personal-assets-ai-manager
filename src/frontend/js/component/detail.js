import { esc } from "../util/core.js";

export function detailTabs(active, items) {
  return `<nav class="detail-tabs" aria-label="明细类型">${items.map(([page, label, note]) => `
    <button type="button" data-page="${page}" class="${page === active ? "active" : ""}" aria-pressed="${page === active}"><strong>${esc(label)}</strong><small>${esc(note)}</small></button>`).join("")}</nav>`;
}

export function detailList({ tabs, active, toolbar = "", title, description, total, headers, rows, footer = "" }) {
  return `${detailTabs(active, tabs)}<article class="list-surface detail-list-surface">
    ${toolbar ? `<div class="detail-list-toolbar">${toolbar}</div>` : ""}
    <div class="list-context"><div><strong>${esc(title)}</strong><span>${esc(description)}</span></div><small>共 ${total} 条</small></div>
    <div class="table-scroll"><table class="reusable-table detail-data-table"><thead><tr>${headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}" class="empty-row">暂无符合条件的数据</td></tr>`}</tbody></table></div>
    ${footer ? `<footer class="list-footer">${footer}</footer>` : ""}
  </article>`;
}

export function detailPager(result, pageId) {
  const pageIndex = result.page_index ?? result.page;
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const start = result.total ? (pageIndex - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.total, pageIndex * result.page_size);
  const sizes = [10, 20, 50, 100];
  return `<div class="pagination ledger-pagination">
    <label class="page-size">每页<select data-action="detail-page-size" data-page-id="${pageId}">${sizes.map((size) => `<option value="${size}" ${result.page_size === size ? "selected" : ""}>${size} 条</option>`).join("")}</select></label>
    <div class="page-buttons"><button data-action="detail-page" data-page-id="${pageId}" data-value="${pageIndex - 1}" ${pageIndex <= 1 ? "disabled" : ""}>上一页</button><span>第 ${pageIndex} / ${pages} 页</span><button data-action="detail-page" data-page-id="${pageId}" data-value="${pageIndex + 1}" ${pageIndex >= pages ? "disabled" : ""}>下一页</button></div>
    <span class="range">${start}–${end} / ${result.total}</span>
  </div>`;
}

export function detailFields(items) {
  return `<dl class="ledger-review-basis">${items.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></div>`).join("")}</dl>`;
}

export function detailSection(title, content, emptyText = "暂无关联内容") {
  return `<section class="drawer-section"><h3>${esc(title)}</h3>${content || `<p class="muted">${esc(emptyText)}</p>`}</section>`;
}

export function detailRelationRows(items, render) {
  return items.map((item) => `<div class="drawer-review-row">${render(item)}</div>`).join("");
}

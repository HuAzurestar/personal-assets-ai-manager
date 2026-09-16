export function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((item) => `<th>${item}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
export function pager(result) {
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
    <label class="page-size">每页<select data-action="ledger-page-size"><option value="10" ${result.page_size === 10 ? "selected" : ""}>10 条</option><option value="20" ${result.page_size === 20 ? "selected" : ""}>20 条</option><option value="50" ${result.page_size === 50 ? "selected" : ""}>50 条</option><option value="100" ${result.page_size === 100 ? "selected" : ""}>100 条</option></select></label>
    <div class="page-buttons"><button data-action="page" data-value="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""} aria-label="上一页">‹</button>${numbers.join("")}<button data-action="page" data-value="${result.page + 1}" ${result.page >= pages ? "disabled" : ""} aria-label="下一页">›</button></div>
    <span class="range">${start}–${end} / ${result.total}</span>
  </div>`;
}

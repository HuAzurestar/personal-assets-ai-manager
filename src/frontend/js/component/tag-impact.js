import { esc } from "../util/core.js";

const dispositions = {
  KEEP: "旧有效值 → 新输出延续",
  REVIEW_REQUIRED: "新输出使用默认值 → 待人工核对",
  RETAIN_INACTIVE: "旧输出停用 → 原标签保留",
};

// Paginate the complete frozen preview in memory. Browsing never changes the
// financial intent, preview digest, selected Facts or the publication scope.
export function mountTagImpact(host, effect) {
  if (!host) return;
  const mappings = effect.mappings || [];
  const labels = new Map((effect.mapping_labels || []).map(row => [row.tag_id, row]));
  const views = new Map(mappings.map(row => [row.view_id, labels.get(row.tag_id)?.view_name || `View #${row.view_id}`]));
  let page = 1, size = 50, disposition = "", view = "";
  host.innerHTML = `<div class="actions"><label>变化类型<select data-impact-kind data-preview-readonly><option value="">全部变化</option>${Object.entries(dispositions).map(([code, name]) => `<option value="${code}">${esc(name)}</option>`).join("")}</select></label>
    <label>标签视图<select data-impact-view data-preview-readonly><option value="">全部视图</option>${[...views].map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join("")}</select></label>
    <label>每页<select data-impact-size data-preview-readonly><option>20</option><option selected>50</option><option>100</option></select></label></div>
    <p data-impact-count role="status"></p><div data-impact-rows></div>
    <div class="actions"><button type="button" data-impact-prev data-preview-readonly>上一页</button><button type="button" data-impact-next data-preview-readonly>下一页</button></div>`;
  // These are read controls nested in the command form, not intent edits.
  host.addEventListener("input", event => event.stopPropagation());
  host.addEventListener("change", event => event.stopPropagation());
  const find = selector => host.querySelector(selector);
  const render = () => {
    const filtered = mappings.map((row, index) => ({ row, index })).filter(({ row }) =>
      (!disposition || row.disposition === disposition) && (!view || String(row.view_id) === view));
    const pages = Math.max(1, Math.ceil(filtered.length / size));
    page = Math.min(Math.max(page, 1), pages);
    const start = (page - 1) * size;
    find("[data-impact-count]").textContent = `完整影响共 ${mappings.length} 项；当前筛选 ${filtered.length} 项；第 ${page} / ${pages} 页（本页 ${filtered.length ? start + 1 : 0}–${Math.min(start + size, filtered.length)}）`;
    find("[data-impact-rows]").innerHTML = filtered.slice(start, start + size).map(({ row, index }) => {
      const label = labels.get(row.tag_id);
      const target = row.new_output ? `新解释 ${row.new_output.review_index + 1} / 现金行 ${row.new_output.allocation_index + 1}` : "保留停用原项";
      return `<p data-tag-row-index="${index}">旧 Ledger ${row.old_ledger_id ? `#${row.old_ledger_id}` : "无唯一来源"} → ${esc(target)} · ${esc(label?.view_name || `View #${row.view_id}`)} / ${esc(label?.tag_name || `Tag #${row.tag_id}`)} · ${esc(dispositions[row.disposition] || row.disposition)}</p>`;
    }).join("") || "<p>当前筛选没有影响项；完整预览未被删减。</p>";
    find("[data-impact-prev]").disabled = page <= 1;
    find("[data-impact-next]").disabled = page >= pages;
  };
  find("[data-impact-kind]").onchange = event => { disposition = event.target.value; page = 1; render(); };
  find("[data-impact-view]").onchange = event => { view = event.target.value; page = 1; render(); };
  find("[data-impact-size]").onchange = event => { size = Number(event.target.value); page = 1; render(); };
  find("[data-impact-prev]").onclick = () => { page--; render(); };
  find("[data-impact-next]").onclick = () => { page++; render(); };
  render();
}

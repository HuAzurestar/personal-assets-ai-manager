import { request } from "../api/client.js";
import { esc } from "../util/core.js";

export const input = (name, label, value = "", extra = "") => `<label>${esc(label)}<input name="${name}" value="${esc(value)}" ${extra}></label>`;
export const select = (name, label, choices, value = "") => `<label>${esc(label)}<select name="${name}">${choices.map(choice => {
  const [code, title] = Array.isArray(choice) ? choice : [choice, choice];
  return `<option value="${esc(code)}" ${String(code) === String(value) ? "selected" : ""}>${esc(title)}</option>`;
}).join("")}</select></label>`;

export function workbenchDialog(title, body) {
  const node = document.createElement("dialog");
  node.className = "wide";
  node.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button type="button" data-workbench-close>关闭</button></div><div class="dialog-body">${body}</div>`;
  node.querySelector("[data-workbench-close]").onclick = () => node.close();
  node.addEventListener("close", () => node.remove());
  document.body.append(node);
  node.showModal();
  return node;
}

export function writeFailure(host, error) {
  const unknown = !error.status || error.status >= 500 || error.code === "RESULT_UNKNOWN";
  host.querySelector("[role=status]").textContent = unknown
    ? "提交结果未知。请查询当前对象和审查记录，不要重发命令。"
    : `${error.code || "操作失败"}：${error.message}；重新读取并预览后再提交。`;
  return unknown;
}

// Exactly one bounded read at a time. Search continuation scans candidates,
// including an empty hit batch, and never invents a total or parallel-prefetches.
export function mountPicker(host, { url, searchKeys = [], describe, selected = () => false, choose, signal, filter }) {
  let page = 1, cursor = null, result, busy = false, generation = 0;
  host.innerHTML = `<div class="actions"><input data-picker-word maxlength="128" aria-label="字面搜索" placeholder="字面搜索（不支持通配符）"><button type="button" data-picker-search>重新查找</button></div><p role="status"></p><div data-picker-items></div><div class="actions"><button type="button" data-picker-prev>上一页</button><span data-picker-count></span><button type="button" data-picker-next>下一页 / 继续扫描</button></div>`;
  const word = host.querySelector("[data-picker-word]");
  word.disabled = !searchKeys.length;
  const read = async (restart = false, previous = false) => {
    if (busy) return;
    const requestedPage = restart ? 1 : previous ? page - 1 : result ? page + 1 : page;
    const requestedCursor = restart ? null : result?.next_cursor;
    const issued = ++generation;
    busy = true;
    host.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const params = new URLSearchParams({ page_size: "20" });
    const searching = !!word.value.trim() && !!searchKeys.length;
    if (searching) {
      params.set("query", JSON.stringify(searchKeys.map(key => ({ key, word: word.value.trim() }))));
      // Multiple query terms are AND; callers normally choose one field.
      if (requestedCursor) params.set("cursor", requestedCursor);
    } else params.set("page_index", String(requestedPage));
    const predicate = typeof filter === "function" ? filter() : filter;
    if (predicate) params.set("filter", JSON.stringify(predicate));
    try {
      const next = await request(`${url}/${searching ? "search" : "list"}?${params}`, { signal });
      if (!host.isConnected || signal?.aborted || issued !== generation) return;
      result = next;
      page = requestedPage;
      host.querySelector("[data-picker-items]").innerHTML = next.items.map(row => `<article class="review-ledger-row"><span>${esc(describe(row))}</span><button type="button" data-picker-id="${row.id ?? row.transaction_id}">${selected(row) ? "移除选择" : "选择"}</button></article>`).join("") || "<p>本批没有匹配项。</p>";
      host.querySelector("[data-picker-count]").textContent = searching
        ? `本批扫描 ${next.scanned_count} 个候选，命中 ${next.items.length}，总数未知`
        : `第 ${next.page_index} 页，共 ${next.total} 项`;
      host.querySelectorAll("[data-picker-id]").forEach(button => {
        button.onclick = () => {
          choose(next.items.find(row => String(row.id ?? row.transaction_id) === button.dataset.pickerId));
          button.textContent = selected(next.items.find(row => String(row.id ?? row.transaction_id) === button.dataset.pickerId)) ? "移除选择" : "选择";
        };
      });
      host.querySelector("[role=status]").textContent = "";
    } catch (error) {
      if (error.name !== "AbortError" && host.isConnected) host.querySelector("[role=status]").textContent = error.message;
    } finally {
      busy = false;
      if (host.isConnected) {
        host.querySelectorAll("button").forEach(button => { button.disabled = false; });
        host.querySelector("[data-picker-prev]").disabled = searching || page <= 1;
        host.querySelector("[data-picker-next]").disabled = !result || (searching ? !result.has_more : result.page_index * result.page_size >= result.total);
      }
    }
  };
  // Editing a search condition invalidates the continuation until a fresh read.
  word.oninput = () => { ++generation; result = null; cursor = null; host.querySelector("[data-picker-next]").disabled = true; };
  host.querySelector("[data-picker-search]").onclick = () => read(true);
  host.querySelector("[data-picker-prev]").onclick = () => read(false, true);
  host.querySelector("[data-picker-next]").onclick = () => read();
  return read(true);
}

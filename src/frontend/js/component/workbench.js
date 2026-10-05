import { request, isUnknownWrite } from "../api/client.js";
import { esc } from "../util/core.js";
import { financialIssueMessage, financialStateLabel } from '../util/financial-copy.js';
import { candidateScan, scanControls } from "../util/candidate-scan.js";
import { mountLocalPicker } from './local-choice.js';

// The API label is derived from masked public identity and current ownership.
// Keep stable IDs and status as secondary disambiguators, never infer a merge.
export const metadataLabel = row => `${row.display_label || [row.institution || row.source_namespace, row.name, row.source_identity || row.reference].filter(Boolean).join(" · ") || "未命名对象"} · ${financialStateLabel('metadata',row.status)} · #${row.id}`;

export const input = (name, label, value = "", extra = "") => `<label>${esc(label)}<input name="${name}" value="${esc(value)}" ${extra}></label>`;
export const select = (name, label, choices, value = "") => `<label>${esc(label)}<select name="${name}">${choices.map(choice => {
  const [code, title] = Array.isArray(choice) ? choice : [choice, choice];
  return `<option value="${esc(code)}" ${String(code) === String(value) ? "selected" : ""}>${esc(title)}</option>`;
}).join("")}</select></label>`;

export function namedChoice(name, label, { value = '', text = '尚未选择', pick = '选择', pickAttribute = '', clear = '' } = {}) {
  return `<div class="stack" data-named-choice="${name}"><span>${esc(label)}</span><input type="hidden" name="${name}" value="${esc(value)}"><span data-choice-label>${esc(text)}</span><div class="actions"><button type="button" data-choice-pick ${pickAttribute}>${esc(pick)}</button>${clear ? `<button type="button" data-choice-clear>${esc(clear)}</button>` : ''}</div></div>`;
}

export function setNamedChoice(host, name, value, text, notify = true) {
  const field = host.querySelector(`[data-named-choice="${name}"]`);
  field.querySelector('input').value = value;
  field.querySelector('[data-choice-label]').textContent = text;
  if (notify) field.querySelector('input').dispatchEvent(new Event('change', { bubbles: true }));
}

export function refreshLocalChoice(host, name, choices, placeholder) {
  const previous = host.querySelector(`[name="${name}"]`).value;
  const valid = choices.has(previous);
  setNamedChoice(host, name, valid ? previous : '', valid ? choices.get(previous) : previous ? '引用已移除，请重新选择' : placeholder, false);
  return !!previous && !valid;
}

export function bindLocalChoice(host, name, { choices, title, signal }) {
  host.querySelector(`[data-named-choice="${name}"] [data-choice-pick]`).onclick = () => {
    if (!host.isConnected || signal?.aborted) return;
    const dialog = workbenchDialog(title, '<div data-local-picker></div>');
    const abort = () => {if (dialog.open) dialog.close();};
    signal?.addEventListener('abort', abort, {once: true});
    dialog.addEventListener('close', () => signal?.removeEventListener('abort', abort), {once: true});
    mountLocalPicker(dialog.querySelector('[data-local-picker]'), {choices, signal,
      choose: (id, label) => {
        if (host.isConnected && !signal?.aborted) setNamedChoice(host, name, id, label);
        dialog.close();
      }});
  };
}

// Public masked metadata selection. IDs remain transport values, not editable
// user-facing fields; selection and an explicit zero/clear action are distinct.
export function bindNamedChoice(host, name, { url, title, signal, describe = metadataLabel, searchKeys = ['display_label'],
  allowZero = false, zeroLabel = '尚未选择', pickerAttribute = 'data-choice-picker', initialize = true,
  load = id => request(`${url}/${id}`, { signal }), changed = () => {}, filter, canChange = () => true }) {
  const field = host.querySelector(`[data-named-choice="${name}"]`);
  const apply = (id, text, row) => {
    if (!host.isConnected || signal?.aborted || !canChange()) return;
    setNamedChoice(host, name, id, text); changed(row);
  };
  field.querySelector('[data-choice-pick]').onclick = () => {
    if (!host.isConnected || signal?.aborted || !canChange()) return;
    const dialog = workbenchDialog(title, `<div ${pickerAttribute}></div>`);
    const abort = () => {if (dialog.open) dialog.close();};
    signal?.addEventListener('abort', abort, {once: true});
    dialog.addEventListener('close', () => signal?.removeEventListener('abort', abort), {once: true});
    mountPicker(dialog.querySelector(`[${pickerAttribute}]`), { url, searchKeys, signal, describe, filter,
      choose: row => { apply(row.id ?? row.transaction_id, describe(row), row); dialog.close(); } });
  };
  const clear = field.querySelector('[data-choice-clear]');
  if (clear) clear.onclick = () => apply(allowZero ? 0 : '', zeroLabel, null);
  const initial = field.querySelector('input').value;
  if (initialize && initial && initial !== '0') load(initial).then(row => {
    if (host.isConnected && !signal?.aborted && field.querySelector('input').value === initial)
      setNamedChoice(host, name, initial, describe(row), false);
  }).catch(error => {
    if (host.isConnected && !signal?.aborted && error.name !== 'AbortError' && field.querySelector('input').value === initial)
      field.querySelector('[data-choice-label]').textContent = `对象名称读取失败：${error.message}；请重新选择`;
  });
}

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
  const unknown = isUnknownWrite(error);
  host.querySelector("[role=status]").textContent = unknown
    ? "提交结果未知。请查询当前对象和审查记录，不要重发命令。"
    : error.code === "WRITE_BUSY"
      ? "本次未提交，输入已保留。稍后重新预览，再由你确认提交；不会自动重发。"
      : `${financialIssueMessage(error)}；本次未提交，重新读取并预览后再提交。`;
  return unknown;
}

// List pages stay explicit; text search uses the same serial, cancellable
// automatic scan as Fact/Flow/Review, including zero-hit candidate batches.
export function mountPicker(host, { url, searchKeys = [], describe, selected = () => false, choose, signal, filter, actions = () => [] }) {
  let page = 1, result, controller, busy = false, generation = 0;
  const identity = row => row.id ?? row.transaction_id;
  const scan = candidateScan(url, 'picker', identity, { signal });
  host.innerHTML = `<div class="actions"><input data-picker-word maxlength="128" aria-label="字面搜索" placeholder="字面搜索（不支持通配符）"><button type="button" data-picker-search>重新查找</button></div><p role="status"></p><div data-picker-items></div><div class="actions"><button type="button" data-picker-prev>上一页</button><span data-picker-count></span><button type="button" data-picker-next>下一页</button></div><div data-picker-scan></div>`;
  const word = host.querySelector("[data-picker-word]");
  word.disabled = !searchKeys.length;
  function stop() { generation++; controller?.abort(); scan.reset(); busy = false; }
  signal?.addEventListener('abort', stop, { once: true });
  host.closest('dialog')?.addEventListener('close', stop, { once: true });
  function controls(searching) {
    host.querySelector('[data-picker-prev]').hidden = searching;
    host.querySelector('[data-picker-next]').hidden = searching;
    host.querySelector('[data-picker-prev]').disabled = busy || page <= 1;
    host.querySelector('[data-picker-next]').disabled = busy || !result || result.page_index * result.page_size >= result.total;
    host.querySelector('[data-picker-search]').disabled = busy;
  }
  function paint(next, searching) {
    result = next;
    const extra = next.items.map(actions);
    host.querySelector("[data-picker-items]").innerHTML = next.items.map((row,index) => `<article class="picker-list-row"><span>${esc(describe(row))}</span><div class="picker-row-actions"><button type="button" data-picker-id="${identity(row)}">${selected(row) ? "移除选择" : "选择"}</button>${extra[index].map((action,key) => `<button type="button" data-picker-action="${index}:${key}">${esc(action.label)}</button>`).join('')}</div></article>`).join("")
      || `<p>${searching && next.has_more ? '尚无命中；空批次不代表扫描结束。' : '没有匹配项。'}</p>`;
    host.querySelector("[data-picker-count]").textContent = searching ? '' : `第 ${next.page_index} 页，共 ${next.total} 项`;
    host.querySelectorAll("[data-picker-id]").forEach(button => {
      button.onclick = () => {
        const row = next.items.find(row => String(identity(row)) === button.dataset.pickerId);
        choose(row); button.textContent = selected(row) ? "移除选择" : "选择";
      };
    });
    host.querySelectorAll('[data-picker-action]').forEach(button => {
      button.onclick = () => {
        if (!host.isConnected || signal?.aborted) return;
        const [index,key] = button.dataset.pickerAction.split(':').map(Number);
        extra[index][key].choose(next.items[index]);
      };
    });
    host.querySelector('[data-picker-scan]').innerHTML = searching ? scanControls('picker', next) : '';
    controls(searching);
    if (searching) scan.bind(host, value => paint(value, true));
  }
  const read = async (restart = false, previous = false) => {
    if (busy && !restart) return;
    const requestedPage = restart ? 1 : previous ? page - 1 : result ? page + 1 : page;
    stop(); controller = new AbortController();
    const issued = generation;
    busy = true; host.querySelector('[role=status]').textContent = '正在读取';
    const params = new URLSearchParams({ page_size: "20" });
    const searching = !!word.value.trim() && !!searchKeys.length;
    controls(searching);
    if (searching) {
      params.set("query", JSON.stringify(searchKeys.map(key => ({ key, word: word.value.trim() }))));
      // Multiple query terms are AND; callers normally choose one field.
    } else params.set("page_index", String(requestedPage));
    const predicate = typeof filter === "function" ? filter() : filter;
    if (predicate) params.set("filter", JSON.stringify(predicate));
    try {
      const next = searching ? await scan.read(params, location.hash)
        : await request(`${url}/list?${params}`, { signal: controller.signal });
      if (!host.isConnected || signal?.aborted || issued !== generation) return;
      page = requestedPage;
      paint(next, searching);
      host.querySelector("[role=status]").textContent = "";
    } catch (error) {
      if (issued === generation && error.name !== "AbortError" && host.isConnected && !signal?.aborted) host.querySelector("[role=status]").textContent = error.message;
    } finally {
      if (issued === generation) { busy = false; if (host.isConnected && !signal?.aborted) controls(searching); }
    }
  };
  // Editing aborts an in-flight read and removes its candidates. Selection is
  // owned by the caller and survives; late responses cannot restore old hits.
  word.oninput = () => {
    stop(); result = null;
    host.querySelector('[data-picker-items]').innerHTML = '';
    host.querySelector('[data-picker-scan]').innerHTML = '';
    host.querySelector('[data-picker-count]').textContent = '';
    host.querySelector('[role=status]').textContent = '条件已改变，请重新查找';
    controls(!!word.value.trim() && !!searchKeys.length);
  };
  host.querySelector("[data-picker-search]").onclick = () => read(true);
  host.querySelector("[data-picker-prev]").onclick = () => read(false, true);
  host.querySelector("[data-picker-next]").onclick = () => read();
  return read(true);
}

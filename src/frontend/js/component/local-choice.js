import { esc } from '../util/core.js';

// A local draft is already explicitly loaded, unlike a server candidate scan.
// One shared label map per scope; each consumer retains only its chosen token.
export function localChoiceMap(choices) {
  const result = new Map();
  for (const [key, label] of choices) {
    const id = String(key);
    if (result.has(id)) throw new Error('草稿选择身份不唯一');
    result.set(id, String(label));
  }
  return result;
}

export function localChoicePage(choices, word = '', page = 1) {
  const term = word.trim().normalize('NFC').toLowerCase();
  const matched = [...choices].filter(([, label]) => !term || label.normalize('NFC').toLowerCase().includes(term));
  const pages = Math.max(1, Math.ceil(matched.length / 20));
  const current = Math.min(pages, Math.max(1, page));
  return { items: matched.slice((current - 1) * 20, current * 20), total: matched.length, pages, page: current };
}

export function mountLocalPicker(host, { choices, choose, signal }) {
  let page = 1;
  host.innerHTML = '<div class="actions"><label>当前草稿字面搜索<input data-local-word maxlength="128"></label></div><p role="status"></p><div data-local-items></div><div class="actions"><button type="button" data-local-prev>上一页</button><span data-local-count></span><button type="button" data-local-next>下一页</button></div>';
  const word = host.querySelector('[data-local-word]');
  const paint = () => {
    if (!host.isConnected || signal?.aborted) return;
    const result = localChoicePage(choices(), word.value, page); page = result.page;
    host.querySelector('[data-local-items]').innerHTML = result.items.map(([id, label]) =>
      `<article class="picker-list-row"><span>${esc(label)}</span><button type="button" data-local-id="${esc(id)}">选择</button></article>`).join('') || '<p>当前草稿没有匹配项。</p>';
    host.querySelector('[data-local-count]').textContent = `仅当前草稿 · 共 ${result.total} 项 · 第 ${page} / ${result.pages} 页`;
    host.querySelector('[data-local-prev]').disabled = page <= 1;
    host.querySelector('[data-local-next]').disabled = page >= result.pages;
    host.querySelectorAll('[data-local-id]').forEach(button => {button.onclick = () => {
      if (!host.isConnected || signal?.aborted) return;
      const id = button.dataset.localId, label = choices().get(id);
      if (label === undefined) {
        paint(); host.querySelector('[role=status]').textContent = '对象已移除，请重新选择'; return;
      }
      choose(id, label);
    };});
  };
  word.oninput = () => {page = 1; paint();};
  host.querySelector('[data-local-prev]').onclick = () => {page--; paint();};
  host.querySelector('[data-local-next]').onclick = () => {page++; paint();};
  paint();
}

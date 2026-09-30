// Presentation state only. Never retains server versions, records or scan cursors.
const rows = '[data-rule-row], [data-tag-request-row], [data-fact-row], [data-economic-row], [data-review-row], [data-view-card], [data-model-card], .batch-card';
const focusable = 'button, a[href], input, select, textarea, summary, [tabindex="0"]';
const visible = (el) => typeof el.checkVisibility === 'function'
  ? el.checkVisibility({ checkVisibilityCSS: true })
  : el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';

function nodeKey(el) {
  if (el.nodeType !== 1) return '';
  if (el.id) return `id:${el.id}`;
  for (const name of ['data-rule-row', 'data-tag-request-row', 'data-fact-row', 'data-economic-row', 'data-review-row', 'data-view-card', 'data-model-card', 'data-ledger-key']) {
    if (el.hasAttribute(name)) return `${name}:${el.getAttribute(name)}`;
  }
  if (el.hasAttribute('data-action')) return `action:${el.dataset.action}:${el.dataset.id || ''}:${el.dataset.operation || ''}:${el.dataset.view || ''}`;
  if (el.name) return `name:${el.name}:${el.type === 'radio' ? el.value : ''}`;
  if (el.matches('input[type="checkbox"][value]')) return `checkbox:${el.value}`;
  if (el.matches('details')) return `details:${el.querySelector(':scope > summary')?.textContent.trim()}`;
  if (el.matches('a[href]')) return `href:${el.getAttribute('href')}`;
  return '';
}

function path(root, el) {
  if (!el || !root.contains(el)) return null;
  const parts = [];
  while (el !== root) {
    const key = nodeKey(el);
    const peers = [...el.parentElement.children].filter((item) => item.tagName === el.tagName && nodeKey(item) === key);
    parts.unshift({ tag: el.tagName, key, index: peers.indexOf(el) });
    if (key && (el.id || el.matches(rows))) {
      parts[0].global = true;
      break;
    }
    el = el.parentElement;
  }
  return parts;
}

function find(root, parts) {
  if (!parts) return null;
  return parts.reduce((parent, part) => parent && [...(part.global ? root.querySelectorAll(part.tag) : parent.children)]
    .filter((el) => el.tagName === part.tag && nodeKey(el) === part.key)[part.index], root);
}

export function preserveView(root, update, { restoreValues = true, restoreScroll = true } = {}) {
  const active = document.activeElement;
  const ownedFocus = root.contains(active);
  const activePath = path(root, active);
  const controls = [...root.querySelectorAll(focusable)].filter(visible);
  const index = controls.indexOf(active);
  const fallback = index < 0 ? [] : [...controls.slice(index + 1), ...controls.slice(0, index).reverse()].map((el) => path(root, el));
  const details = [...root.querySelectorAll('details')].map((el) => [path(root, el), el.open]);
  const fields = [...root.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="password"]), textarea, select')]
    .map((el) => ({ path: path(root, el), value: el.value, checked: el.checked,
      start: el.selectionStart, end: el.selectionEnd, direction: el.selectionDirection }));
  const scrolls = [root, ...root.querySelectorAll('*')].filter((el) => el.scrollTop || el.scrollLeft)
    .map((el) => [path(root, el), el.scrollTop, el.scrollLeft]);
  const x = window.scrollX, y = window.scrollY;
  const candidates = [...root.querySelectorAll(rows)].filter(visible);
  const first = candidates.findIndex((el) => el.getBoundingClientRect().bottom > 0);
  const anchors = first < 0 ? [] : [...candidates.slice(first), ...candidates.slice(0, first).reverse()]
    .map((el) => [path(root, el), el.getBoundingClientRect().top]);
  const result = update();
  for (const [key, open] of details) { const el = find(root, key); if (el) el.open = open; }
  for (const field of restoreValues ? fields : []) {
    const el = find(root, field.path);
    if (!el) continue;
    if (el.disabled) { if (el.type === 'checkbox' || el.type === 'radio') el.checked = false; continue; }
    if (el.type === 'checkbox' || el.type === 'radio') el.checked = field.checked;
    else if (el.tagName !== 'SELECT' || [...el.options].some((option) => option.value === field.value)) el.value = field.value;
    if (field.start != null) el.setSelectionRange(field.start, field.end, field.direction);
  }
  if (ownedFocus) {
    const usable = (el) => el && !el.disabled && visible(el);
    const original = find(root, activePath);
    const target = usable(original) ? original : fallback.map((key) => find(root, key)).find(usable);
    if (target) target.focus({ preventScroll: true });
    else { root.tabIndex = -1; root.focus({ preventScroll: true }); }
  }
  for (const [key, top, left] of restoreScroll ? scrolls : []) { const el = find(root, key); if (el) { el.scrollTop = top; el.scrollLeft = left; } }
  const anchor = anchors.map(([key, top]) => [find(root, key), top]).find(([el]) => el && visible(el));
  window.scrollTo({ left: x, top: anchor ? window.scrollY + anchor[0].getBoundingClientRect().top - anchor[1] : y, behavior: 'instant' });
  return result;
}

// Keyed updates for delegated-event regions. Reuse rows and controls instead of
// detaching them; server attributes (including disabled/version) remain authoritative.
export function patchMarkup(root, markup) {
  const template = document.createElement('template');
  template.innerHTML = markup;
  function children(parent, incoming) {
    const old = [...parent.childNodes];
    const used = new Set();
    let cursor = parent.firstChild;
    for (const next of [...incoming.childNodes]) {
      const key = nodeKey(next);
      const current = old.find((el) => !used.has(el) && el.nodeType === next.nodeType
        && el.nodeName === next.nodeName && nodeKey(el) === key);
      if (!current) {
        parent.insertBefore(next.cloneNode(true), cursor);
        continue;
      }
      used.add(current);
      if (current !== cursor) parent.insertBefore(current, cursor);
      if (current.nodeType === 1) {
        const open = current.matches('details') && current.open;
        for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
        for (const attr of [...next.attributes]) if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
        children(current, next);
        if (current.matches('details')) current.open = open;
      } else if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      cursor = current.nextSibling;
    }
    for (const el of old) if (!used.has(el)) el.remove();
  }
  children(root, template.content);
}

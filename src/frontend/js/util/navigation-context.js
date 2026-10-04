// Presentation state on each browser history entry, never financial data,
// editable form values, cached server records or executable commands.
const stateKey = 'paamView';
const rowAttributes = ['data-fact-row', 'data-economic-row', 'data-review-row',
  'data-import-file-row', 'data-ref-id', 'data-batch-row', 'data-rule-row',
  'data-tag-request-row'];
const rowSelector = rowAttributes.map(name => `[${name}]`).join(',');
const coordinate = value => Number.isFinite(value) && Math.abs(value) <= 1e9;
const safeId = value => typeof value === 'string' && /^[\w:-]{1,128}$/.test(value);

function rowKey(row) {
  const attribute = rowAttributes.find(name => row.hasAttribute(name));
  const value = attribute && row.getAttribute(attribute);
  return attribute && safeId(value) ? {attribute, value} : null;
}

export function captureNavigationView(root, route) {
  if (typeof route !== 'string' || route.length > 4096) return null;
  const visibleRows = [...root.querySelectorAll(rowSelector)].filter(row =>
    row.getClientRects().length && row.getBoundingClientRect().bottom > 0
    && row.getBoundingClientRect().top < window.innerHeight);
  const anchors = visibleRows.slice(0, 8).map(row => ({...rowKey(row), top:row.getBoundingClientRect().top}))
    .filter(anchor => anchor.attribute && coordinate(anchor.top));
  return {route, x:window.scrollX, y:window.scrollY, rootX:root.scrollLeft,
    rootY:root.scrollTop, anchors};
}

export function restoreNavigationView(root, view, route) {
  if (!view || typeof route !== 'string' || route.length > 4096 || view.route !== route || ![view.x, view.y, view.rootX, view.rootY].every(coordinate)
      || !Array.isArray(view.anchors) || view.anchors.length > 8) return false;
  root.scrollLeft = view.rootX;
  root.scrollTop = view.rootY;
  const anchor = view.anchors.find(item => rowAttributes.includes(item.attribute)
    && safeId(item.value) && coordinate(item.top)
    && root.querySelector(`[${item.attribute}="${item.value}"]`)?.getClientRects().length);
  const row = anchor && root.querySelector(`[${anchor.attribute}="${anchor.value}"]`);
  window.scrollTo({left:view.x, top:row ? window.scrollY + row.getBoundingClientRect().top - anchor.top : view.y,
    behavior:'instant'});
  return true;
}

export function navigationContext(root) {
  let mountedRoute, pending, frame;
  const remember = () => {
    if (!mountedRoute || mountedRoute !== location.hash || root.getAttribute('aria-busy') === 'true') return;
    const view = captureNavigationView(root, mountedRoute);
    if (!view) return;
    const existing = history.state && typeof history.state === 'object' ? history.state : {};
    try { history.replaceState({...existing, [stateKey]:view}, '', location.href); }
    catch { /* Navigation still works if the browser refuses history storage. */ }
  };
  const schedule = event => {
    if (event?.target?.closest?.('dialog')) return;
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = null; remember(); });
  };
  document.addEventListener('scroll', schedule, true);
  // Capture before a link/button changes the route or closes its detail dialog.
  document.addEventListener('click', remember, true);
  window.addEventListener('pagehide', remember);
  history.scrollRestoration = 'manual';
  return {
    remember,
    begin(route) { pending = {route, view:history.state?.[stateKey]}; },
    mounted(route, {restore = false} = {}) {
      if (route !== location.hash) return;
      mountedRoute = route;
      if (restore) {
        const view = pending?.route === route ? pending.view : null;
        if (!restoreNavigationView(root, view, route)) window.scrollTo({left:0, top:0, behavior:'instant'});
      }
      pending = null;
      // render() still owns aria-busy here; remember after its finally completes.
      schedule();
    },
  };
}

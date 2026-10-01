import { request } from "../api/client.js";

// A scan is not a COUNT-backed page. Empty hits retain the continuation and
// each explicit click consumes just one bounded candidate batch.
let controller, generation = 0, current;
export function stopFlowRead() {
  generation++; controller?.abort();
  if (current && current.route !== location.hash) current = null;
}
export function resetFlowSearch() { stopFlowRead(); current = null; }
export function flowReadBusy() { return !!current?.busy; }

export async function readFlowSearch(query, route) {
  stopFlowRead();
  controller = new AbortController();
  const ticket = generation;
  const signature = query.toString();
  if (current?.signature === signature && current.route === route) return current.result;
  const result = await request(`/paam/ledger/v1/flow/search?${query}`, { signal: controller.signal });
  if (ticket !== generation || route !== location.hash) throw new DOMException("obsolete scan", "AbortError");
  current = { signature, query: new URLSearchParams(query), route, result, busy: false };
  return result;
}

export function bindFlowSearch(root, paint) {
  const button = root.querySelector('[data-flow-continue]');
  if (!button || !current) return;
  button.onclick = async () => {
    const scan = current, ticket = generation;
    if (scan.busy || !scan.result.has_more) return;
    scan.busy = true; button.disabled = true;
    const query = new URLSearchParams(scan.query);
    query.set('cursor', scan.result.next_cursor);
    try {
      const result = await request(`/paam/ledger/v1/flow/search?${query}`, { signal: controller.signal });
      if (ticket !== generation || current !== scan || !root.isConnected || scan.route !== location.hash) return;
      const hits = new Map(scan.result.items.map(row => [row.id, row]));
      result.items.forEach(row => hits.set(row.id, row));
      scan.result = { ...result, items: [...hits.values()], scanned_count: scan.result.scanned_count + result.scanned_count };
      paint(scan.result);
    } catch (error) {
      if (ticket !== generation || !root.isConnected || error.name === 'AbortError') return;
      root.querySelector('[data-flow-scan-status]').textContent = `读取失败，保留已找到结果：${error.message}`;
    } finally {
      scan.busy = false;
      if (ticket === generation && button.isConnected) button.disabled = false;
    }
  };
}

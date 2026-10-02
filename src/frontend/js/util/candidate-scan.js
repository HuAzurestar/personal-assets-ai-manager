import { request } from "../api/client.js";

// One explicit click, one bounded candidate batch. Failed reads keep the cursor.
export function candidateScan(base, name, identity = row => row.id) {
  let controller, generation = 0, current;
  function stop() {
    generation++; controller?.abort();
    if (current && current.route !== location.hash) current = null;
  }
  function reset() { stop(); current = null; }
  const busy = () => !!current?.busy;
  async function read(query, route) {
    stop(); controller = new AbortController();
    const ticket = generation, signature = query.toString();
    if (current?.signature === signature && current.route === route) return current.result;
    const result = await request(`${base}/search?${query}`, { signal: controller.signal });
    if (ticket !== generation || route !== location.hash) throw new DOMException("obsolete scan", "AbortError");
    current = { signature, query: new URLSearchParams(query), route, result, busy: false };
    return result;
  }
  function bind(root, paint) {
    const button = root.querySelector(`[data-${name}-continue]`);
    if (!button || !current) return;
    button.onclick = async () => {
      const scan = current, ticket = generation;
      if (scan.busy || !scan.result.has_more) return;
      scan.busy = true; button.disabled = true;
      const query = new URLSearchParams(scan.query);
      query.set('cursor', scan.result.next_cursor);
      try {
        const result = await request(`${base}/search?${query}`, { signal: controller.signal });
        if (ticket !== generation || current !== scan || !root.isConnected || scan.route !== location.hash) return;
        const hits = new Map(scan.result.items.map(row => [identity(row), row]));
        result.items.forEach(row => hits.set(identity(row), row));
        scan.result = { ...result, items: [...hits.values()], scanned_count: scan.result.scanned_count + result.scanned_count };
        paint(scan.result);
      } catch (error) {
        if (ticket !== generation || !root.isConnected || error.name === 'AbortError') return;
        root.querySelector(`[data-${name}-scan-status]`).textContent = `读取失败，保留已找到结果：${error.message}`;
      } finally {
        scan.busy = false;
        if (ticket === generation && button.isConnected) button.disabled = false;
      }
    };
  }
  return { read, bind, stop, reset, busy };
}

import { request } from "../api/client.js";
import { esc } from "./core.js";

// GET-only serial search. Server batch boundaries are not user action boundaries.
// A phase pauses after bounded work; a result window is replaced only by an
// explicit, labelled next-window action. No cumulative candidate cutoff.
export function candidateScan(base, name, identity = row => row.id, options = {}) {
  const { maxBatches = 25, maxElapsedMs = 8000, maxItems = 200,
    maxBytes = 2 * 1024 * 1024, signal } = options;
  let controller, generation = 0, current, view;
  const bytes = items => new TextEncoder().encode(JSON.stringify(items)).length;
  const valid = (scan, ticket) => current === scan && ticket === generation
    && !signal?.aborted && scan.route === location.hash && view?.root.isConnected;
  const busy = () => !!current?.busy;
  function publish(scan) { if (view?.root.isConnected && scan.route === location.hash) view.paint(scan.result); }
  function interrupt(state, reason, paint = true) {
    generation++; controller?.abort();
    if (!current) return;
    current.busy = false;
    current.result = { ...current.result, scan_state: state, scan_reason: reason, scan_error: '' };
    if (paint) publish(current);
  }
  function stop(paint = false) {
    if (current && ['ready', 'running'].includes(current.result.scan_state)) interrupt('paused', '页面读取已停止，结果和游标保留', paint);
    else { generation++; controller?.abort(); if (current) current.busy = false; }
    if (current && current.route !== location.hash) current = null;
  }
  function reset() { stop(); current = null; view = null; }
  signal?.addEventListener('abort', reset, { once: true });
  async function read(query, route) {
    stop(); controller = new AbortController();
    const ticket = generation, signature = query.toString();
    if (current?.signature === signature && current.route === route) return current.result;
    current = null; view = null;
    const result = await request(`${base}/search?${query}`, { signal: controller.signal });
    if (ticket !== generation || signal?.aborted || route !== location.hash) throw new DOMException("obsolete scan", "AbortError");
    current = { signature, query: new URLSearchParams(query), route, busy: false,
      result: { ...result, matched_count: result.items.length, scan_window: 1,
        scan_state: result.has_more ? 'ready' : 'complete', scan_reason: '', scan_error: '', scan_replace: false } };
    return current.result;
  }
  async function run(replace = false) {
    const scan = current;
    if (!scan || scan.busy || !scan.result.has_more || signal?.aborted || !view?.root.isConnected || scan.route !== location.hash) return;
    const ticket = ++generation, started = Date.now();
    controller = new AbortController(); scan.busy = true;
    let replaceWindow = replace;
    // Do not discard the previous window until the next GET succeeds.
    scan.result = { ...scan.result, scan_state: 'running', scan_reason: '', scan_error: '', scan_replace: replace };
    publish(scan);
    let batches = 0;
    try {
      while (valid(scan, ticket) && scan.result.has_more) {
        const full = !replaceWindow && (scan.result.items.length >= maxItems || bytes(scan.result.items) >= maxBytes);
        if (full || batches >= maxBatches || Date.now() - started >= maxElapsedMs) {
          scan.result = { ...scan.result, scan_state: 'paused', scan_replace: full || replaceWindow,
            scan_reason: full ? '本段结果达到缓存预算；下一段将替换当前结果，请先审阅或选择' : '本阶段达到扫描预算，可从原游标继续' };
          break;
        }
        const query = new URLSearchParams(scan.query), cursor = scan.result.next_cursor;
        if (!cursor) throw new Error('继续游标缺失，请重新查找');
        query.set('cursor', cursor);
        const result = await request(`${base}/search?${query}`, { signal: controller.signal });
        if (!valid(scan, ticket)) return;
        if (result.has_more && (!result.next_cursor || result.next_cursor === cursor)) throw new Error('继续游标未推进，请重新查找');
        const oldItems = replaceWindow ? [] : scan.result.items;
        const hits = new Map(oldItems.map(row => [identity(row), row]));
        result.items.forEach(row => hits.set(identity(row), row));
        scan.result = { ...scan.result, ...result, items: [...hits.values()],
          matched_count: scan.result.matched_count + hits.size - oldItems.length,
          scanned_count: scan.result.scanned_count + result.scanned_count,
          scan_window: scan.result.scan_window + (replaceWindow ? 1 : 0), scan_replace: false,
          scan_state: result.has_more ? 'running' : 'complete' };
        replaceWindow = false;
        batches++; publish(scan);
        // Let input/pause/cancel events run even with a very fast local response.
        if (result.has_more) await new Promise(resolve => setTimeout(resolve, 0));
      }
    } catch (error) {
      if (!valid(scan, ticket) || error.name === 'AbortError') return;
      scan.result = { ...scan.result, scan_state: 'failed', scan_error: error.message,
        scan_reason: '读取失败，结果和原游标已保留；由你继续查找' };
    } finally {
      if (ticket === generation && current === scan) {
        scan.busy = false;
        if (valid(scan, ticket)) publish(scan);
      }
    }
  }
  function bind(root, paint) {
    const button = root.querySelector(`[data-${name}-continue]`);
    if (!button || !current) return;
    view = { root, paint };
    button.disabled = current.busy || !current.result.has_more;
    button.onclick = () => run(current?.result.scan_replace);
    const pause = root.querySelector(`[data-${name}-pause]`);
    if (pause) { pause.disabled = !current.busy; pause.onclick = () => interrupt('paused', '已暂停，结果和游标保留'); }
    const cancel = root.querySelector(`[data-${name}-cancel]`);
    if (cancel) { cancel.disabled = !current.busy; cancel.onclick = () => interrupt('cancelled', '已取消自动查找，结果和游标保留'); }
    if (current.result.scan_state === 'ready' && !current.scheduled) {
      const scan = current, ticket = generation; scan.scheduled = true;
      queueMicrotask(() => { if (valid(scan, ticket) && scan.result.scan_state === 'ready') run(); });
    }
  }
  return { read, bind, stop, reset, busy };
}

export function scanControls(name, result) {
  const running = result.scan_state === 'running';
  const state = result.scan_state === 'complete' || !result.has_more ? '本次扫描结束'
    : running || result.scan_state === 'ready' ? '正在自动查找' : result.scan_reason || '可继续查找';
  return `<div class="actions"><span role="status" data-${name}-scan-status>已扫描 ${result.scanned_count} 个候选；找到 ${result.matched_count ?? result.items.length} 项；总数未知。${esc(state)}${result.scan_error ? `：${esc(result.scan_error)}` : ''}${result.scan_window > 1 ? `；第 ${result.scan_window} 段当前显示 ${result.items.length} 项` : ''}</span><button type="button" data-${name}-continue ${running || !result.has_more ? 'disabled' : ''}>${result.scan_replace ? '继续下一段（替换当前结果）' : '继续查找'}</button><button type="button" data-${name}-pause ${running ? '' : 'disabled'}>暂停</button><button type="button" data-${name}-cancel ${running ? '' : 'disabled'}>取消自动查找</button></div>`;
}

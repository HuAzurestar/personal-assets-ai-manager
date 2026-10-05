import { resourceId } from './core.js';

// A user-requested scope read is all-or-nothing in the browser. Do not add the
// first successful pages to the selection if a later page fails or changes.
export async function completeImportScope(read, params, {signal, valid = () => true, now = () => Date.now(), timers = globalThis} = {}) {
  const query = new URLSearchParams(params), started = now(), rows = [], seen = new Set();
  query.set('page_size', '100');
  let total;
  const cancelledError = () => Object.assign(new Error('范围读取已取消或预览变化；未部分加入选择。'), {name:'AbortError'});
  const timeoutError = () => new Error('完整范围读取超过30秒；未部分加入选择，请缩小范围。');
  const guard = () => {
    if (signal?.aborted || !valid()) throw cancelledError();
    if (now() - started > 30000) throw timeoutError();
  };
  guard();
  const controller = new AbortController();
  let rejectRead;
  const interrupted = new Promise((_resolve, reject) => {rejectRead = reject;});
  const stop = error => {controller.abort(); rejectRead(error);};
  const abort = () => stop(cancelledError());
  signal?.addEventListener('abort', abort, {once:true});
  // Stop an in-flight GET too. Racing the response protects callers even if a
  // transport/mock ignores abort; a late response can never return a prefix.
  const timeout = timers.setTimeout(() => stop(timeoutError()), 30000);
  try {
    for (let page = 1; ; page++) {
      guard(); query.set('page_index', String(page));
      const result = await Promise.race([
        Promise.resolve().then(() => read(query, {signal:controller.signal})), interrupted]);
      guard();
      if (!Number.isSafeInteger(result.total) || result.total < 0 || result.total > 20000
          || result.page_index !== page || result.page_size !== 100 || !Array.isArray(result.items)
          || result.items.length !== Math.min(100, Math.max(0, result.total - (page - 1) * 100))
          || (total !== undefined && result.total !== total)) throw new Error('完整范围页数、总数或内容变化；未部分加入选择。');
      total = result.total;
      for (const row of result.items) {
        const key = `${resourceId(row.file_id)}:${resourceId(row.source_row_number)}`;
        if (seen.has(key)) throw new Error('范围包含重复行定位；未部分加入选择。');
        seen.add(key); rows.push(row);
      }
      if (page * 100 >= total) break;
    }
    return rows;
  } finally {
    timers.clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
    controller.abort();
  }
}

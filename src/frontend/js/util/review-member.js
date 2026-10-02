import { resourceId, reviewTypeNames } from './core.js';

export const currentReviewLabel = row => `${row.title || reviewTypeNames[row.type] || row.type} · Review #${row.id} · ${row.member_count} 个完整事实`;

const reject = message => { throw Object.assign(new Error(message), {code:'REVIEW_MEMBERS_CHANGED'}); };
export function validateMemberPage(page, index, size, expectedTotal) {
  if (!Number.isSafeInteger(page.total) || page.total < 0 || page.page_index !== index || page.page_size !== size
    || !Array.isArray(page.items) || page.items.length !== Math.max(0, Math.min(size, page.total - (index - 1) * size)))
    reject('事项成员读取不完整，请重新读取；尚未改变选择。');
  if (expectedTotal !== undefined && page.total !== expectedTotal) reject('事项成员数量已变化，请重新读取。');
  page.items.forEach(row => resourceId(row.transaction_id));
  return page;
}

// Read-only original membership. Selection is returned all-or-error; callers
// must not apply an individual page to the financial draft while loading.
export async function completeReviewMembers(review, {read, signal, valid = () => true, maxMembers = 2000, maxBytes = 2 * 1024 * 1024}) {
  const id = resourceId(review.id), check = () => {
    if (signal?.aborted || !valid()) throw Object.assign(new Error('已取消事项读取，选择未改变。'), {name:'AbortError'});
  };
  const header = async () => {
    check();
    const result = await read(`/paam/ledger/v1/review/list?${new URLSearchParams({page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:id})})}`);
    check();
    const current = result.items?.[0];
    if (result.total !== 1 || result.items.length !== 1 || current.id !== id || current.status !== 'CONFIRMED'
      || current.updated_time !== review.updated_time) reject('事项状态已变化或已停用，请重新读取；选择未改变。');
  };
  await header();
  let total, bytes = 0;
  const members = [], seen = new Set();
  for (let index = 1; total === undefined || members.length < total; index++) {
    check();
    const page = validateMemberPage(await read(`/paam/ledger/v1/review/${id}/fact/list?page_index=${index}&page_size=100`), index, 100, total);
    check(); total = page.total;
    if (total > maxMembers) throw Object.assign(new Error(`完整事项有 ${total} 个事实，超过本次 ${maxMembers} 个事实的选择上限；未部分选择。`), {code:'DETAIL_LIMIT'});
    bytes += new TextEncoder().encode(JSON.stringify(page.items)).length;
    if (bytes > maxBytes) throw Object.assign(new Error('完整事项超过本次读取容量，未部分选择。'), {code:'DETAIL_LIMIT'});
    for (const fact of page.items) {
      const fid = resourceId(fact.transaction_id);
      if (seen.has(fid)) reject('事项成员重复，请重新读取；选择未改变。');
      if (!fact.current_reviews?.some(row => row.id === id && row.status === 'CONFIRMED' && row.updated_time === review.updated_time))
        reject('事项已不再是该事实的当前解释，请重新读取；选择未改变。');
      seen.add(fid); members.push(fact);
    }
  }
  await header(); check();
  return members;
}

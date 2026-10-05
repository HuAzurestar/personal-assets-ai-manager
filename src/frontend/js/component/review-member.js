import { request } from '../api/client.js';
import { esc, date, money, resourceId } from '../util/core.js';
import { currentReviewLabel, validateMemberPage, completeReviewMembers } from '../util/review-member.js';
import { workbenchDialog } from './workbench.js';
import { openInspection } from './inspection.js';

export function openReviewMembers(summary, {signal, canSelect = () => false, select = () => {}} = {}) {
  const id = resourceId(summary.id), route = location.hash;
  const node = workbenchDialog(`查看原事项 Review #${id}`, `<section data-review-members>
    <p data-member-header></p><details><summary>原成员、当前归属与操作边界</summary><p>此 Review 的原始完整成员停用后仍保留；每行另标当前归属。查看不改草稿。选择整组后仍须服务端预览、核对整体替代影响并明确发布。</p>
    <p>完整选择最多 2000 个事实；发布还有独立的原默认及受影响 Review 组预算，能选择不等于能发布。</p></details>
    <p role="status" data-member-status></p><div class="actions review-member-tools">
      <button type="button" data-member-prev>上一页</button><span data-member-count></span><button type="button" data-member-next>下一页</button>
      <button type="button" data-member-refresh>重新读取事项</button>
      <button type="button" data-member-original title="查看原事项完整现金和数量结果（只读）">原现金 / 数量</button>
      <button type="button" data-select-review-group title="选择完整事项进行替代或拆并；尚不发布" disabled>选择完整事项</button></div><div data-member-items></div></section>`);
  const local = new AbortController(), status = node.querySelector('[data-member-status]');
  let generation = 0, reading = false, selecting = false, current, result, page = 1, activeRead;
  const valid = () => node.isConnected && node.open && !local.signal.aborted && !signal?.aborted && location.hash === route;
  const stop = () => {local.abort(); activeRead?.abort(); if (node.open) node.close();};
  signal?.addEventListener('abort', stop, {once:true});
  node.addEventListener('close', () => {local.abort(); activeRead?.abort(); signal?.removeEventListener('abort',stop);}, {once:true});
  const controls = () => {
    node.querySelector('[data-member-prev]').disabled = reading || selecting || page <= 1;
    node.querySelector('[data-member-next]').disabled = reading || selecting || !result || page * 20 >= result.total;
    node.querySelector('[data-member-refresh]').disabled = reading || selecting;
    node.querySelector('[data-select-review-group]').disabled = reading || selecting || current?.status !== 'CONFIRMED' || !result?.total || !canSelect();
  };
  const readPage = async (index, refresh = false) => {
    if (reading || selecting || !valid()) return;
    activeRead?.abort(); activeRead = new AbortController();
    const ticket = ++generation; reading = true; controls(); status.textContent = '正在读取原事项成员…';
    try {
      if (refresh || !current) {
        const header = await request(`/paam/ledger/v1/review/list?${new URLSearchParams({page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:id})})}`, {signal:activeRead.signal});
        if (!valid() || ticket !== generation) return;
        if (header.total !== 1 || header.items.length !== 1) throw new Error('原事项不存在，选择未改变。');
        current = header.items[0];
      }
      const next = validateMemberPage(await request(`/paam/ledger/v1/review/${id}/fact/list?page_index=${index}&page_size=20`, {signal:activeRead.signal}), index, 20, refresh ? undefined : result?.total);
      if (!valid() || ticket !== generation) return;
      page = index; result = next;
      node.querySelector('[data-member-header]').textContent = `${current.title || '原事项'} · ${current.status === 'CONFIRMED' ? '当前有效' : '已停用（历史原成员）'} · Review #${id}`;
      node.querySelector('[data-member-count]').textContent = `第 ${page} 页 · 原成员共 ${next.total} 项`;
      node.querySelector('[data-member-items]').innerHTML = next.items.map(fact => `<article class="review-member-row"><strong>${esc(fact.summary)} · ${esc(money(fact))} ${esc(fact.cash_direction)} · Fact #${fact.transaction_id}</strong>
        <span>${esc(date(fact.occurred_time))} · 当前归属：${esc((fact.current_reviews || []).map(currentReviewLabel).join('；') || '无有效解释')}</span></article>`).join('') || '<p>此事项没有现金事实成员。</p>';
      status.textContent = current.status === 'CONFIRMED' ? '原成员只读；整组选择前重新核对状态。' : '历史成员可查看，已停用事项不能作为当前事项整组选择。';
    } catch (error) {if (valid() && error.name !== 'AbortError') {current = null; status.textContent = `${error.code || '读取失败'}：${error.message}`;}}
    finally {if (valid() && ticket === generation) {reading = false; controls();}}
  };
  node.querySelector('[data-member-prev]').onclick = () => readPage(page - 1);
  node.querySelector('[data-member-next]').onclick = () => readPage(page + 1);
  node.querySelector('[data-member-refresh]').onclick = () => readPage(1,true);
  node.querySelector('[data-member-original]').onclick = () => {
    if (valid()) openInspection('review',id,() => {},{readOnly:true,signal:local.signal}).catch(error => {
      if (valid()) status.textContent = `原事项读取失败：${error.message}`;
    });
  };
  node.querySelector('[data-select-review-group]').onclick = async () => {
    if (!valid() || reading || selecting || current?.status !== 'CONFIRMED' || !canSelect()) return;
    selecting = true; controls(); status.textContent = '正在完整读取并核对事项；尚未改变选择…';
    activeRead?.abort(); activeRead = new AbortController();
    let expired = false;
    const timeout = setTimeout(() => {expired = true; activeRead.abort();},30000);
    try {
      const members = await completeReviewMembers(current, {signal:activeRead.signal, valid:() => valid() && canSelect(),
        read:url => request(url,{signal:activeRead.signal})});
      if (!valid() || !canSelect()) return;
      select(members); node.close();
    } catch (error) {
      if (valid()) status.textContent = expired ? '完整事项读取超时，选择未改变；请重新读取。' : `${error.code || '读取已取消'}：${error.message}`;
    } finally {clearTimeout(timeout); selecting = false; if (valid()) controls();}
  };
  readPage(1,true);
  return node;
}

export function openCurrentReviews(fact, options) {
  const reviews = fact.current_reviews || [];
  if (reviews.length === 1) return openReviewMembers(reviews[0], options);
  const node = workbenchDialog(`Fact #${resourceId(fact.transaction_id)} 的当前事项`, '<p>以下为当前读取快照；查看成员时重新读取状态。原系统默认身份不是当前人工事项。</p><div data-current-groups></div>');
  const abort = () => {if (node.open) node.close();};
  options.signal?.addEventListener('abort',abort,{once:true});
  node.addEventListener('close',() => options.signal?.removeEventListener('abort',abort),{once:true});
  const host = node.querySelector('[data-current-groups]');
  reviews.forEach(review => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = currentReviewLabel(review);
    button.onclick = () => {node.close(); openReviewMembers(review,options);}; host.append(button);
  });
  if (!reviews.length) host.textContent = '无有效解释，不能推断原默认已经恢复。';
  return node;
}

import {request} from '../api/client.js';
import {esc, money, date, resourceId} from '../util/core.js';
import {accountCorrectionIntent, accountCorrectionMarkup} from '../util/account-correction.js';
import {namedChoice, bindNamedChoice, metadataLabel, mountPicker, workbenchDialog} from '../component/workbench.js';
import {financialStateLabel} from '../util/financial-copy.js';

const base = '/paam/ledger/v1/review';

export async function mountAccountCorrection(host, params, {signal, publication}) {
  const ledgerId = resourceId(params.get('correct_ledger')), route = location.hash;
  const valid = () => host.isConnected && !signal.aborted && location.hash === route;
  host.innerHTML = '<div class="busy">正在读取指定流水及完整原事项…</div>';
  const detail = await request(`/paam/ledger/v1/flow/${ledgerId}`, {signal});
  if (!valid()) return;
  const flow = detail.ledger_entry, factId = resourceId(detail.allocations[0].transaction_id);
  if (!detail.active) {
    host.innerHTML = '<section class="panel"><h1>原流水已停用</h1><p>只能查看历史内容；请返回流水列表读取当前解释，不能用旧流水重新提交更正。</p><button type="button" data-correction-back>返回上一页</button></section>';
    host.querySelector('[data-correction-back]').onclick = () => history.back();
    return;
  }
  const original = await request(`${base}/${resourceId(detail.reviews[0].id)}`, {signal});
  if (!valid()) return;
  if (original.status !== 'CONFIRMED') throw new Error('原事项已停用；请返回读取当前流水。');
  const labels = new Map([[0,'来源未识别']]), keepers = new Map();
  if (detail.account.ref) labels.set(detail.account.ref.id,metadataLabel(detail.account.ref));
  let duplicateIds = [...new Set(original.allocations.filter(row => original.ledger_entries.some(
    ledger => ledger.id === row.ledger_id && ledger.economic_type === 'DUPLICATE')).map(row => row.transaction_id))];
  host.innerHTML = `<section class="panel account-correction-shell" data-account-correction><h1>更正流水来源</h1>
    <p><strong>${esc(detail.summary || '所选流水')} · ${esc(money(flow))} ${esc(flow.cash_currency_code)} · ${flow.cash_direction === 'IN' ? '收入' : '支出'}</strong> · ${esc(date(flow.occurred_time))}</p>
    <p>当前来源：${esc(labels.get(flow.account_ref_id) || `来源卡 #${flow.account_ref_id}`)}。只更正本条流水；原事项的其他拆分、数量证据和归因由服务端完整保留，后续有效数量依赖会一并预览。</p>
    <button type="button" data-correction-back>返回上一页（保留列表位置）</button>
    <form class="stack" data-account-only><section><h2>1. 选择新来源</h2>${namedChoice('account_ref_id','新来源卡',{
      value:'',text:'请选择，不默认改动',pick:'选择来源卡',clear:'明确设为来源未识别'})}</section>
      <section data-correction-keepers hidden><h2>重复证据：重新明确保留对象</h2><p>原输出没有可推断的保留交易。此处仅重新核对原重复证据；不会把真实转账自动排除。</p><div data-correction-keeper-items></div></section>
      <section data-review-step="preview"><h2>2. 核对完整影响</h2><p role="status" data-review-status>选择新来源，再预览；现在没有写入。</p><div data-review-impact></div>
        <div class="actions"><button type="button" data-review-preview>预览来源更正（不入账）</button><button type="submit" data-review-command disabled>确认来源更正</button></div></section>
      <section><button type="button" data-correction-current data-preview-readonly>读取当前状态（不重发）</button><div data-correction-current-result aria-live="polite"></div></section>
    </form></section>`;
  host.querySelector('[data-correction-back]').onclick = () => history.back();
  const form = host.querySelector('form'), currentResult = form.querySelector('[data-correction-current-result]');
  let currentReading = false, published = false;
  const queryCurrent = async () => {
    if (!valid() || currentReading || invalidate.state().writing) return;
    currentReading = true;
    const button = form.querySelector('[data-correction-current]'); button.disabled = true;
    currentResult.textContent = '只读查询原流水及该交易的当前完整事项…';
    try {
      const [old, candidates] = await Promise.all([request(`/paam/ledger/v1/flow/${ledgerId}`,{signal}),
        request(`/paam/ledger/v1/candidate/list?${new URLSearchParams({page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:factId})})}`,{signal})]);
      if (!valid()) return;
      if (candidates.total !== 1 || candidates.items.length !== 1) throw new Error('当前交易无法完整定位，未判定提交结果。');
      const current = await Promise.all(candidates.items[0].current_reviews.map(row => request(`${base}/${resourceId(row.id)}`,{signal})));
      if (!valid()) return;
      currentResult.innerHTML = `<p>原流水：${old.active ? '仍有效' : '已停用，内容保留'}。以下为当前只读状态；不会自动重发，也不据此解锁未知提交。</p>${current.map(row => `<section class="panel"><h3>${esc(row.title || '当前事项')} · ${esc(financialStateLabel('review',row.status))} · #${row.id}</h3>
        <ul class="account-correction-list">${row.ledger_entries.map(ledger => `<li>${esc(money(ledger))} ${esc(ledger.cash_currency_code)} · ${ledger.cash_direction === 'IN' ? '收入' : '支出'} · ${esc(labels.get(ledger.account_ref_id) || (ledger.account_ref_id ? `来源卡 #${ledger.account_ref_id}` : '来源未识别'))} · 流水 #${ledger.id}</li>`).join('')}</ul></section>`).join('') || '<p>当前没有有效事项，不能猜测默认已恢复。</p>'}`;
    } catch (error) {if (valid() && error.name !== 'AbortError') currentResult.textContent = `查询失败：${error.message}；提交结果仍须核对，未重发。`;}
    finally {currentReading = false; if (valid()) button.disabled = false;}
  };
  const updateKeepers = ids => {
    duplicateIds = [...new Set(ids.map(id => resourceId(id)))];
    for (const id of keepers.keys()) if (!duplicateIds.includes(id)) keepers.delete(id);
    const section = form.querySelector('[data-correction-keepers]'); section.hidden = !duplicateIds.length;
    section.querySelector('[data-correction-keeper-items]').innerHTML = duplicateIds.map(id => `<p>重复交易 #${id} → <span data-correction-kept="${id}">${esc(keepers.has(id) ? `${keepers.get(id).summary} · 交易 #${keepers.get(id).transaction_id}` : '尚未选择保留交易')}</span> <button type="button" data-correction-pick="${id}">查找保留交易</button></p>`).join('');
    section.querySelectorAll('[data-correction-pick]').forEach(button => {
      button.onclick = () => {
        if (!valid() || published || invalidate.state().writing || invalidate.state().uncertain) return;
        const id = resourceId(button.dataset.correctionPick), picker = workbenchDialog('选择仍计现金的交易','<div data-correction-kept-picker></div>');
        mountPicker(picker.querySelector('[data-correction-kept-picker]'), {url:'/paam/ledger/v1/candidate',searchKeys:['summary'],signal,
          describe:row => `${row.summary} · ${money(row)} ${row.cash_currency_code} · ${row.cash_direction} · ${date(row.occurred_time)} · 交易 #${row.transaction_id}`,
          choose:row => {
            if (valid() && !published && !invalidate.state().writing && !invalidate.state().uncertain) {
              keepers.set(id,row);section.querySelector(`[data-correction-kept="${id}"]`).textContent = `${row.summary} · ${money(row)} · 交易 #${row.transaction_id}`;invalidate();
            }
            picker.close();
          }});
      };
    });
  };
  const invalidate = publication(form, () => {
    if (published) throw new Error('本次更正已发布；请从当前流水重新进入，不重发旧命令。');
    return accountCorrectionIntent(ledgerId,form.querySelector('[name="account_ref_id"]').value,flow.account_ref_id,duplicateIds,keepers);
  },new Map(),async result => {
    published = true; form.querySelector('[data-review-command]').disabled = true;
    form.querySelector('[data-review-status]').textContent = `来源更正已发布；完整新事项 ${result.created_reviews.map(row => `#${row.id}`).join('、')}。旧证据保留，请读取当前状态或返回列表。`;
  },new Map(),() => new Map(),{
    confirmation:'请核对指定来源、整组现金与数量、后续依赖及标签影响，再确认来源更正。',
    render:async plan => {
      if (plan.blocking_issues.length) {
        const required = plan.blocking_issues.find(row => row.code === 'ACCOUNT_CORRECTION_KEEPER_REQUIRED');
        if (required?.details?.duplicate_transaction_ids && valid()) updateKeepers(required.details.duplicate_transaction_ids);
        return accountCorrectionMarkup(plan,new Map(),labels);
      }
      const rows = await Promise.all(plan.new_reviews.map(row => request(`${base}/${resourceId(row.source_review_id)}`,{signal})));
      if (!valid()) return '';
      return accountCorrectionMarkup(plan,new Map(rows.map(row => [row.id,row])),labels);
    }});
  bindNamedChoice(form,'account_ref_id',{url:'/paam/ledger/v1/account-ref',title:'选择新来源卡',signal,
    canChange:() => valid() && !published && !invalidate.state().writing && !invalidate.state().uncertain,
    allowZero:true,zeroLabel:'明确设为来源未识别',initialize:false,filter:{key:'status',op:'=',val:'ACTIVE'},
    changed:row => {if (row) labels.set(row.id,metadataLabel(row));}});
  updateKeepers(duplicateIds);
  form.querySelector('[data-correction-current]').onclick = queryCurrent;
}

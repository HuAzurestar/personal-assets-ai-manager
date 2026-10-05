import {esc, money, date, quantityDecimal, resourceId, typeNames} from './core.js';
import {financialStateLabel, financialIssueMessage, financialScopeNote} from './financial-copy.js';

export function accountCorrectionIntent(ledgerId, target, originalRef, duplicateIds, keepers) {
  if (target === '') throw new Error('请明确选择新来源卡，或明确设为来源未识别。');
  const ref = resourceId(target, {allowZero:true});
  if (ref === originalRef) throw new Error('来源未改变，无需发布更正。');
  if (duplicateIds.some(id => !keepers.has(id))) throw new Error('请为每份重复证据选择仍计现金的保留交易。');
  return {account_corrections:[{ledger_id:resourceId(ledgerId),account_ref_id:ref}],
    correction_duplicates:duplicateIds.map(id => ({transaction_id:resourceId(id),kept_transaction_id:resourceId(keepers.get(id).transaction_id)}))};
}

const requireComplete = condition => {if (!condition) throw new Error('完整原组与更正预览不一致或状态已改变；请重新读取，不能确认。');};
const quantityState = (row, unit) => row.quantity_state === 'KNOWN'
  ? `${quantityDecimal(row.quantity, unit)} ${unit}`
  : financialStateLabel('quantity',row.quantity_state);

// Join immutable originals for presentation only. All effects come from the
// server; no client-derived financial projection or command outputs are sent.
export function accountCorrectionMarkup(plan, originals, labels) {
  const issues = plan.blocking_issues || [];
  if (issues.length) return issues.map(row => `<p class="error">${esc(financialIssueMessage(row))}</p>`).join('');
  const drafts = plan.new_reviews || [];
  requireComplete(drafts.length > 0);
  const positions = new Map();
  const groups = drafts.map(draft => {
    const old = originals.get(draft.source_review_id);
    const expected = plan.expected_reviews.find(row => row.review_id === draft.source_review_id);
    requireComplete(old && expected && old.status === expected.status && old.status === 'CONFIRMED'
      && old.updated_time === expected.updated_time && draft.case_code === 'ACCOUNT_CORRECTION');
    old.positions.forEach(row => positions.set(row.id,row));
    const flows = new Map(old.ledger_entries.map(row => [row.id,row]));
    const allocations = new Map(old.allocations.map(row => [row.ledger_id,row]));
    requireComplete(draft.copied_ledger_ids.length === flows.size && draft.allocations.length === flows.size
      && new Set(draft.copied_ledger_ids).size === flows.size && draft.legs.length === old.position_legs.length
      && draft.position_allocations.length === old.position_allocations.length);
    const changes = new Map(draft.account_changes.map(row => [row.ledger_id,row]));
    const cash = draft.allocations.map((row,index) => {
      const ledgerId = draft.copied_ledger_ids[index], previous = flows.get(ledgerId), allocation = allocations.get(ledgerId), change = changes.get(ledgerId);
      requireComplete(previous && allocation && allocation.transaction_id === row.transaction_id
        && previous.cash_amount === row.cash_amount && previous.economic_type === row.economic_type
        && (change ? change.allocation_index === index && change.before_account_ref_id === previous.account_ref_id
          && change.after_account_ref_id === row.account_ref_id : previous.account_ref_id === row.account_ref_id));
      const source = id => labels.get(id) || (id ? `来源卡 #${id}` : '来源未识别');
      return `<li data-correction-cash="${ledgerId}"><strong>${esc(money(previous))} ${esc(previous.cash_currency_code)} · ${previous.cash_direction === 'IN' ? '收入' : '支出'} · ${esc(typeNames[previous.economic_type])}</strong>
        <span>${esc(date(previous.occurred_time))} · 交易 #${row.transaction_id}</span><span>${change ? `${esc(source(previous.account_ref_id))} → ${esc(source(row.account_ref_id))}` : `${esc(source(row.account_ref_id))}（未更正）`}</span></li>`;
    }).join('');
    const legs = draft.legs.map(row => {
      const previous = old.position_legs.find(leg => leg.id === row.copied_leg_id), position = positions.get(row.existing_position_id);
      requireComplete(previous && position && previous.position_id === row.existing_position_id
        && previous.leg_amount === row.leg_amount && previous.leg_direction === row.leg_direction
        && previous.type === row.type && previous.occurred_time === row.occurred_time && previous.basis === row.basis
        && previous.source_position_leg_id === row.source);
      return `<li><strong>${esc(position.title)} · ${row.leg_direction === 'IN' ? '增加' : '减少'} ${quantityDecimal(row.leg_amount,position.unit_code)} ${esc(position.unit_code)}</strong>
        <span>${esc(date(row.occurred_time))} · ${esc(row.basis)}${row.replacement_source_leg_id ? ' · 来源重连至本次复制的增加证据' : ' · 原来源对应关系保留'}</span></li>`;
    }).join('');
    const links = draft.position_allocations.map(row => {
      const ledgerId = draft.copied_ledger_ids[row.allocation_index], legId = draft.legs[row.leg_index]?.copied_leg_id;
      requireComplete(old.position_allocations.some(link => link.ledger_id === ledgerId && link.position_leg_id === legId
        && link.cash_amount === row.cash_amount && link.cash_currency_code === row.cash_currency_code));
      return `<li>${esc(money(row))} ${esc(row.cash_currency_code)} · 原现金拆分 ${row.allocation_index+1} → 数量证据 ${row.leg_index+1}（归因，不增加现金）</li>`;
    }).join('');
    return `<section class="panel"><h3>${esc(old.title || '原事项')} · ${changes.size ? '指定来源更正' : '数量依赖整组延续'}</h3>
      ${old.type === 'NORMAL_TRANSACTION' ? '<p>原系统默认保留为历史；更正结果为新的人工解释，不再生成系统默认。</p>' : ''}
      ${cash ? `<ul class="account-correction-list">${cash}</ul>` : '<p>本组没有现金，不生成额外流水。</p>'}
      ${legs ? `<details><summary>数量证据 ${draft.legs.length} 条（金额及数量保持）</summary><ul class="account-correction-list">${legs}</ul></details>` : ''}
      ${links ? `<details><summary>款项归因 ${draft.position_allocations.length} 条（完整保留）</summary><ul>${links}</ul></details>` : ''}
      <details><summary>技术关系（只读）</summary><p>原 Review #${old.id} 整组停用并保留内容；原 Ledger ${draft.copied_ledger_ids.map(id => `#${id}`).join('、')} 对应新的不可变输出。旧数量腿与归因按原 ID 仍可查看。</p></details></section>`;
  }).join('');
  const quantities = (plan.position_changes || []).map(row => {
    const position = positions.get(row.position_id); requireComplete(position);
    return `<p data-correction-quantity>${esc(position.title)}：${esc(quantityState(row.before,position.unit_code))} → ${esc(quantityState(row.after,position.unit_code))}（不是余额或估值）</p>`;
  }).join('');
  const mappings = plan.tag_effect.mappings || [], needsReview = mappings.filter(row => row.disposition === 'REVIEW_REQUIRED').length;
  return `<section data-correction-business><h2>更正结果预览（尚未发布）</h2><p>只改变明确指定的来源；现金金额、方向、币种和时间保持。完整复制 ${drafts.length} 个事项，旧证据不删除。</p>
    <p data-financial-scope-note>${esc(financialScopeNote)}</p>
    ${quantities}${needsReview ? `<p>有 ${needsReview} 项标签含义不能判为唯一等义，新输出使用默认标签、待人工核对；旧标签保留。数量来源 ID 重连也可能影响标签继承。</p>` : '<p>标签按服务端的完整等义规则处理；旧标签保留。</p>'}
    ${groups}${mappings.length ? '<details><summary>完整标签影响（只读）</summary><div data-tag-impact></div></details>' : ''}</section>`;
}

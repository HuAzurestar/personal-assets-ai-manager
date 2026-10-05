import { esc, money, date, quantityDecimal, resourceId } from '../util/core.js';
import { importIssueMessage } from '../util/import-decision.js';
import { financialStateLabel, importPlanScopeNote } from '../util/financial-copy.js';

// This is disclosure of the server's frozen projection, never a client-side
// financial planner. Local paging retains every record in the supplied plan.
const rowName = row => `文件 #${row.file_id} 第 ${row.source_row_number} 行`;
const targetName = target => !target ? '未配对' : target.kind === 'FACT'
  ? `Fact #${target.transaction_id}` : rowName(target);
const modes = {AUTO:'可靠来源规则', NEW:'明确新增真实现金', LINK_EXISTING:'只补同源证据', DUPLICATE:'跨源重复，不计现金'};
const amount = (value, code) => esc(money({amount:value, currency_code:code}));
const source = id => id == null ? '按可靠来源创建／复用' : id === 0 ? '明确待绑定' : `来源卡 #${id}`;
const ranges = rows => rows.map(row => `文件 #${row.file_id} 第 ${row.row_start}–${row.row_end} 行（${row.row_count} 行）`).join('；');
const budget = item => `选择 ${item.selected_rows} 行 · 解释组 ${item.review_groups} · Fact ${item.facts} · 输出 ${item.outputs} · 关系 ${item.position_links} · 标签／规则影响 ${item.tag_changes}`;

export function validateImportPlan(plan, selected) {
  const expected = new Set(selected.map(row => `${resourceId(row.file_id)}:${resourceId(row.source_row_number)}`));
  if (!expected.size || expected.size !== selected.length || expected.size > 20000
      || plan.selected_count !== expected.size || plan.selected_rows.length !== expected.size
      || plan.cross_batch_atomic !== false || plan.execution_policy.max_batch_rows !== 1000
      || plan.execution_policy.serial !== true || plan.execution_policy.retain_committed !== true
      || plan.execution_policy.automatic_post_replay !== false
      || ['CANCEL','FAILURE','STALE_PREVIEW','RESULT_UNKNOWN'].some(code => !plan.execution_policy.stop_on.includes(code))) {
    throw new Error('处理计划范围或执行边界不完整；不能把它当作已核对的计划。');
  }
  const whole = new Set(plan.selected_rows.map(row => `${resourceId(row.file_id)}:${resourceId(row.source_row_number)}`));
  if (whole.size !== expected.size || [...whole].some(key => !expected.has(key))) throw new Error('处理计划选择范围不一致。');
  const seen = new Set();
  const visit = row => {
    const key = `${resourceId(row.file_id)}:${resourceId(row.source_row_number)}`;
    if (!expected.has(key) || seen.has(key)) throw new Error('处理计划有遗漏、重复或范围外记录。');
    seen.add(key);
  };
  for (const item of plan.batches) {
    if (!item.preview.selected_rows.length || item.preview.selected_rows.length > 1000
        || item.preview.budget.selected_rows !== item.preview.selected_rows.length) throw new Error('单批选择范围不完整或超限。');
    item.preview.selected_rows.forEach(visit);
  }
  plan.blocked.forEach(item => item.selected_rows.forEach(visit));
  if (seen.size !== expected.size || (plan.can_confirm && (plan.blocked.length || plan.batches.some(item => !item.preview.can_confirm)))) {
    throw new Error('完整范围或阻断状态不一致。');
  }
  return plan;
}

function pageList(host, rows, render, label, signal, bind = () => {}) {
  let page = 0;
  const live = () => host.isConnected && !signal?.aborted;
  const paint = () => {
    if (!live()) return;
    const start = page * 20, items = rows.slice(start, start + 20);
    host.innerHTML = `<div data-plan-list-items>${items.map(render).join('') || '<p>无记录。</p>'}</div>
      <nav class="actions" aria-label="${esc(label)}"><button type="button" data-plan-prev ${page === 0 ? 'disabled' : ''}>上一页</button>
      <span data-plan-list-count>${esc(label)}：${rows.length ? start + 1 : 0}–${Math.min(start + 20, rows.length)} / ${rows.length}</span>
      <button type="button" data-plan-next ${start + 20 >= rows.length ? 'disabled' : ''}>下一页</button></nav>`;
    host.querySelector('[data-plan-prev]').onclick = () => {if (live() && page > 0) {page--; paint();}};
    host.querySelector('[data-plan-next]').onclick = () => {if (live() && (page + 1) * 20 < rows.length) {page++; paint();}};
    bind(host.querySelector('[data-plan-list-items]'), items);
  };
  paint();
}

function section(label, name) {
  return `<details><summary>${esc(label)}</summary><div data-plan-${name}></div></details>`;
}

function pairMarkup(pair) {
  const value = pair.comparison, hint = pair.duplicate_hint;
  return `<article class="picker-list-row import-plan-record"><div><strong>${esc(rowName(pair.row))} → ${esc(targetName(pair.target))}</strong>
    <p>${esc(modes[pair.resolution])} · ${amount(value.amount, value.currency_code)} · ${esc(value.cash_direction || '方向未知')} · ${esc(value.occurred_time ? date(value.occurred_time) : '时间未知')}</p>
    <p>${pair.source_labels_masked.map(esc).join(' → ')} · ${value.exact_match ? '核心精确一致' : '不代表已经配对'}</p>
    <small>风险范围：${esc(financialStateLabel('importRisk',hint.state))} · ${hint.candidate_count == null ? '候选数未知，不能当作零' : `${hint.candidate_count} 个候选（只限已核验范围）`}
    ${pair.reason_codes.length ? ` · ${esc(pair.reason_codes.join('、'))}` : ''}</small></div></article>`;
}

function showBatch(host, item, signal) {
  const value = item.preview, effect = value.effects, counts = value.counts, tag = effect.tag_effect;
  host.innerHTML = `<section class="panel" data-plan-batch-detail><h3>第 ${item.batch_index + 1} 批：完整核对</h3>
    <p>${esc(ranges(item.row_ranges))}</p><p>${esc(budget(value.budget))}</p>
    <p>真实新增 ${counts.new_real_fact} · 新重复 Fact ${counts.new_duplicate_fact} · 仅补证据 ${counts.evidence_only} · 跳过 ${counts.skipped} · 无效 ${counts.invalid} · 未解决 ${counts.unresolved}</p>
    <p>${value.can_confirm ? esc(importPlanScopeNote) : '本批被阻断，不能提交。'}</p>
    ${effect.by_currency.map(row => `<p>单位 ${esc(row.currency_code)}：新增真实流入 ${amount(row.cash_in_amount,row.currency_code)}／流出 ${amount(row.cash_out_amount,row.currency_code)}；重复排除流入 ${amount(row.excluded_in_amount,row.currency_code)}／流出 ${amount(row.excluded_out_amount,row.currency_code)}。不跨币加总。</p>`).join('')}
    <p>新输出 ${tag.new_output_count} · 默认标签赋值 ${tag.projected_assignment_count} · 影响视图 ${tag.affected_view_ids.map(id => `#${id}`).join('、') || '无'} · 规则 ${tag.affected_rule_ids.map(id => `#${id}`).join('、') || '无'}（包括停用规则的扫描前提）。</p>
    ${tag.default_assignments.map(row => `<p>默认标签：${esc(row.view_name_masked)} #${row.view_id} → ${esc(row.tag_name_masked)} #${row.tag_id}</p>`).join('')}
    ${section('全部来源配对与风险', 'pairs')}${section('拟建原始默认输出（未生成 ID）', 'defaults')}
    ${section('拟建重复输出（正金额、零计量、无数量腿）', 'duplicates')}
    ${section('本批重复解释的整组撤销范围', 'revoke')}${section('所有未解决问题', 'issues')}
    ${section('既有解释完整内容与状态', 'reviews')}</section>`;
  pageList(host.querySelector('[data-plan-pairs]'), value.pairs, pairMarkup, '来源配对', signal);
  pageList(host.querySelector('[data-plan-defaults]'), effect.new_original_defaults, row =>
    `<p>${esc(rowName(row.row))} → 原默认拟建输出 ${row.output_index + 1} · ${amount(row.amount,row.currency_code)} · ${esc(row.cash_direction)} · ${esc(date(row.occurred_time))} · ${esc(source(row.account_ref_id))} · ${esc(row.source_label_masked)} · ${esc(financialStateLabel('plannedReview',row.after_status))}</p>`, '拟建默认', signal);
  const duplicate = effect.new_duplicate_reviews.flatMap(review => review.allocations.map(row => ({...row, review_index:review.review_index})));
  pageList(host.querySelector('[data-plan-duplicates]'), duplicate, row =>
    `<p>拟建重复解释 ${row.review_index + 1} · ${esc(rowName(row.row))} → 保留 ${esc(targetName(row.kept_target))} · DUPLICATE ${amount(row.amount,row.currency_code)} ${esc(row.cash_direction)} · ${esc(source(row.account_ref_id))}</p>`, '拟建重复', signal);
  pageList(host.querySelector('[data-plan-revoke]'), item.duplicate_revoke_scope, row => `<p>${esc(rowName(row))}：原默认停用；撤销本批 DUP 解释会整组恢复这些原默认，不支持只撤销其中一行。</p>`, '整组撤销范围', signal);
  pageList(host.querySelector('[data-plan-issues]'), value.issues, row => `<p class="error">${esc(rowName(row))}：${esc(row.code)} · ${esc(importIssueMessage(row.code))}</p>`, '未解决问题', signal);
  pageList(host.querySelector('[data-plan-reviews]'), effect.before_after_review_states, (row,index) =>
    `<article class="panel"><h4>${esc(row.before.title)} · Review #${row.before.id} · ${esc(financialStateLabel('review',row.before.status))} → ${esc(financialStateLabel('plannedReview',row.after_status))}</h4>
      <p>${esc(row.before.type)} · 更新 ${esc(row.before.updated_time)}</p>
      <button type="button" data-plan-review="${index}">查看完整原现金、关系与数量</button></article>`, '既有解释', signal,
    (list, items) => list.querySelectorAll('[data-plan-review]').forEach(button => {
      button.onclick = () => showReview(host.querySelector('[data-plan-reviews]'), items[Number(button.dataset.planReview)].before, signal);
    }));
}

function showReview(host, review, signal) {
  host.querySelector('[data-plan-review-detail]')?.remove();
  host.insertAdjacentHTML('beforeend', `<section class="panel" data-plan-review-detail><h4>原 Review #${review.id} 的完整不可变内容</h4>
    ${section('现金输出', 'ledger')}${section('Fact → 现金关系', 'allocation')}${section('数量腿', 'legs')}
    ${section('现金 → 数量关系', 'links')}${section('数量对象', 'positions')}</section>`);
  const detail = host.querySelectorAll('[data-plan-review-detail]');
  const panel = detail[detail.length - 1];
  pageList(panel.querySelector('[data-plan-ledger]'), review.ledger_entries, row => `<p>Ledger #${row.id} · ${esc(row.economic_type)} · ${amount(row.cash_amount,row.cash_currency_code)} ${esc(row.cash_direction)} · ${esc(date(row.occurred_time))} · ${esc(source(row.account_ref_id))}</p>`, '原现金', signal);
  pageList(panel.querySelector('[data-plan-allocation]'), review.allocations, row => `<p>关系 #${row.id}：Fact #${row.transaction_id} → Ledger #${row.ledger_id} · ${amount(row.cash_amount,row.cash_currency_code)}</p>`, '第一段关系', signal);
  pageList(panel.querySelector('[data-plan-legs]'), review.position_legs, row => `<p>数量腿 #${row.id} → 对象 #${row.position_id} · ${esc(row.type)} ${esc(row.leg_direction)} ${esc(quantityDecimal(row.leg_amount,row.unit_code))} ${esc(row.unit_code)} · 来源腿 #${row.source_position_leg_id} · ${esc(date(row.occurred_time))} · ${esc(row.basis)}</p>`, '原数量腿', signal);
  pageList(panel.querySelector('[data-plan-links]'), review.position_allocations, row => `<p>关系 #${row.id}：Ledger #${row.ledger_id} → 数量腿 #${row.position_leg_id} · ${amount(row.cash_amount,row.cash_currency_code)}</p>`, '第二段关系', signal);
  pageList(panel.querySelector('[data-plan-positions]'), review.positions, row => `<p>对象 #${row.id} · ${esc(row.title)} · ${esc(row.description)} · ${esc(row.type)} · ${esc(financialStateLabel('position',row.status))} · ${esc(row.usage_scenario)} · 个人 #${row.party_id} · ${esc(row.counterparty)} · ${esc(row.unit_code)}</p>`, '原数量对象', signal);
}

export function mountImportPlan(host, plan, {signal} = {}) {
  const issues = plan.batches.flatMap(item => item.preview.issues || []);
  host.innerHTML = `<section class="panel" data-import-operation><h2>完整处理计划（只读预览）</h2>
    <p>本次明确选择 ${plan.selected_count} 行 · 可规划 ${plan.batches.length} 批 · 阻断关联组 ${plan.blocked.length} 个。</p>
    <p>每批最多1000来源行，另受完整解释／关系／标签预算约束；关联组不拆散。各批不是一个大事务。</p>
    <p>取消、失败、前提变化或结果未知时停止后续；此前成功批保留，不回滚整次操作。结果未知先查持久状态，不自动重发 POST；继续须你的明确动作并重新核对剩余计划。</p>
    <p>${plan.can_confirm ? `计划无阻断；${esc(importPlanScopeNote)}本预览本身不会提交账务。` : '存在阻断或未解决事项；不能自动跳过后执行其余记录。'}</p>
    <p data-plan-issue-summary>所选完整范围未解决 ${issues.length} 行，其中新增现金风险待处理 ${issues.filter(row=>row.code === 'IMPORT_REVIEW_REQUIRED').length} 行；预算阻断组与行问题分别计算，不以零阻断组冒称可入账。</p>
    <div data-plan-all-issues></div><div data-plan-batches></div><div data-plan-blocked></div><div data-plan-selected-detail></div></section>`;
  pageList(host.querySelector('[data-plan-all-issues]'),issues,row=>`<p class="error">${esc(rowName(row))}：${esc(row.code)} · ${esc(importIssueMessage(row.code))}</p>`,'全部未解决来源行',signal);
  const detail = host.querySelector('[data-plan-selected-detail]');
  pageList(host.querySelector('[data-plan-batches]'), plan.batches, (item,index) =>
    `<article class="picker-list-row import-plan-record"><div><strong>第 ${item.batch_index + 1} 批 · ${item.preview.selected_rows.length} 行</strong>
      <p>${esc(ranges(item.row_ranges))}</p><small>${esc(budget(item.preview.budget))}</small></div>
      <button type="button" data-plan-batch="${index}">完整核对</button></article>`, '全部计划批次', signal,
    (list,items) => list.querySelectorAll('[data-plan-batch]').forEach(button => {
      button.onclick = () => {if (host.isConnected && !signal?.aborted) showBatch(detail,items[Number(button.dataset.planBatch)],signal);};
    }));
  pageList(host.querySelector('[data-plan-blocked]'), plan.blocked, (item,index) =>
    `<article class="picker-list-row import-plan-record"><div><strong class="error">${esc(item.issue.code)} · ${esc(item.issue.dimension)} ${item.issue.count}／${item.issue.limit}</strong>
      <p>${esc(ranges(item.row_ranges))}</p><small>${esc(budget(item.budget))}。请明确排除或重新核对完整关联组；不会静默跳过。</small></div>
      <button type="button" data-plan-block="${index}">全部阻断行</button></article>`, '全部阻断组', signal,
    (list,items) => list.querySelectorAll('[data-plan-block]').forEach(button => {
      button.onclick = () => pageList(detail,items[Number(button.dataset.planBlock)].selected_rows,row => `<p>${esc(rowName(row))}</p>`,'完整阻断范围',signal);
    }));
}

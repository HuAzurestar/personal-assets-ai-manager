import { request, jsonRequest } from "../api/client.js";
import { esc, date, money, quantityDecimal, quantityAmount, typeNames, reviewTypeNames, resourceId } from "../util/core.js";
import { input, select, workbenchDialog, writeFailure, mountPicker, namedChoice, bindNamedChoice, setNamedChoice, bindLocalChoice, refreshLocalChoice } from "../component/workbench.js";
import { positionFields, bindPartyPicker } from "./position.js";
import { mountTagImpact } from "../component/tag-impact.js";
import { positionTarget, linkTarget } from '../util/draft-reference.js';
import { dateTimeField, dateTimeValue } from '../component/date-time-field.js';
import { localChoiceMap } from '../component/local-choice.js';
import { reviewScene, incompatibleSceneInputs } from '../util/review-scene.js';
import { currentReviewLabel } from '../util/review-member.js';
import { openCurrentReviews, openReviewMembers } from '../component/review-member.js';
import { mountAccountCorrection } from './account-correction.js';
import { mountSourceCompletion } from './source-completion.js';
import { financialStateLabel, financialIssueMessage, financialScopeNote } from '../util/financial-copy.js';

const base = "/paam/ledger/v1/review";
const cases = [["NORMAL", "普通收支"], ["REFUND", "退款"], ["SHARED_PAYMENT", "共同费用 / AA"], ["INTERNAL_TRANSFER", "真实内部转账"], ["BORROW_REPAY", "借出、借入、收回、偿还"], ["DUPLICATE", "同一交易的重复证据"], ["POS_OPENING", "对象期初数量"], ["POS_POSITION_OPEN", "对象增加（可无现金）"], ["POS_POSITION_SETTLE", "对象减少（显式来源）"], ["POS_CREDIT_PURCHASE", "信用消费"], ["POS_CREDIT_REPAY", "信用还本"]];
let controller;
export function stopReviewRead() { controller?.abort(); }
const ids = values => values.length ? values.map(id => `#${id}`).join("、") : "无";

function previewMarkup(plan, facts = new Map(), positions = new Map(), labels = new Map()) {
  // Format only the complete server plan; never sum across units or derive
  // financial outputs, coverage, approval or quantity state in the browser.
  const cash = (amount, fact) => fact ? `${money({cash_amount:amount,cash_currency_code:fact.cash_currency_code})} ${fact.cash_currency_code}`
    : `${amount} 最小单位（单位信息未读取）`;
  const quantity = (amount, position) => position ? `${quantityDecimal(amount,position.unit_code)} ${position.unit_code}`
    : `${amount} 最小单位（单位信息未读取）`;
  const quantityState = (state, position) => state.quantity_state === 'KNOWN' && state.quantity != null
    ? quantity(state.quantity,position) : financialStateLabel('quantity',state.quantity_state);
  const source = id => labels.get(`account_ref_id:${id}`) || (id ? `来源卡 #${id}` : '来源未识别');
  const direction = code => code === 'IN' ? '收入' : code === 'OUT' ? '支出' : '方向信息未读取';
  const mappings = plan.tag_effect.mappings || [];
  const reviewRequired = mappings.filter(row=>row.disposition === 'REVIEW_REQUIRED').length;
  return `<section data-review-business><h3>业务结果预览（尚未发布）</h3>
    ${plan.blocking_issues.map(issue=>`<p class="error">${esc(financialIssueMessage(issue))}</p>`).join('')}
    <p data-financial-scope-note>${esc(financialScopeNote)}</p>
    ${plan.new_reviews.map(row=>`<section class="panel"><h4>${esc(row.title || cases.find(([code])=>code === row.case_code)?.[1] || reviewTypeNames[row.type] || row.type)}</h4>
      ${row.allocations.length ? `<ul class="account-correction-list">${row.allocations.map(allocation=>{
        const fact = facts.get(allocation.transaction_id), duplicate = allocation.economic_type === 'DUPLICATE';
        return `<li data-review-cash-result><strong>${duplicate ? '重复证据金额（不增加现金）' : direction(fact?.cash_direction)} · ${esc(cash(allocation.cash_amount,fact))} · ${esc(typeNames[allocation.economic_type] || allocation.economic_type)}</strong>
          <span>${esc(fact?.summary || `交易 #${allocation.transaction_id}`)} · ${esc(source(allocation.account_ref_id))}${fact ? ` · ${esc(date(fact.occurred_time))}` : ''}</span></li>`;
      }).join('')}</ul>` : '<p>本事项不新建现金输出。</p>'}
      ${row.new_positions.map(position=>`<p>新数量对象：${esc(position.title)} · ${esc(financialStateLabel('positionType',position.type,false))} · ${esc(financialStateLabel('positionUsage',position.usage_scenario,false))} · ${esc(labels.get(`party_id:${position.party_id}`) || `个人 #${position.party_id}`)} · ${esc(position.counterparty)} · ${esc(position.unit_code)}</p>`).join('')}
      ${row.legs.length ? `<ul class="account-correction-list">${row.legs.map(leg=>{
        const position = leg.existing_position_id ? positions.get(leg.existing_position_id) : row.new_positions[leg.new_position_index];
        return `<li><strong>${esc(position?.title || (leg.existing_position_id ? `对象 #${leg.existing_position_id}` : `新对象 ${leg.new_position_index+1}`))} · ${leg.leg_direction === 'IN' ? '增加' : leg.leg_direction === 'OUT' ? '减少' : '方向未知'} ${esc(quantity(leg.leg_amount,position))}</strong><span>${esc(date(leg.occurred_time))} · ${esc(leg.basis)}（数量证据，不是另一笔现金）</span></li>`;
      }).join('')}</ul>` : ''}</section>`).join('')}
    ${!plan.new_reviews.length ? '<p>不新建现金输出；仅调整所选原解释状态，原内容和证据保留。</p>' : ''}
    ${plan.position_changes.length ? `<section><h4>有据数量变化（不是余额或估值）</h4>${plan.position_changes.map(change=>{
      const position = change.position_id ? positions.get(change.position_id) : plan.new_reviews[change.new_review_index]?.new_positions[change.new_position_index];
      return `<p data-review-quantity-change>${esc(position?.title || (change.position_id ? `对象 #${change.position_id}` : `新对象 ${change.new_position_index+1}`))}：${esc(quantityState(change.before,position))} → ${esc(quantityState(change.after,position))}</p>`;
    }).join('')}</section>` : ''}
    <p>将整体停用 ${plan.impact.conflicting_review_ids.length} 个冲突事项，恢复 ${plan.impact.restored_default_review_ids.length} 个原始系统默认；旧内容保留。</p>
    ${plan.impact.dependent_position_leg_ids.length ? `<p class="error">${plan.impact.dependent_position_leg_ids.length} 条后续数量证据可能因来源失效而需要核对，不能当作有据数量或零。</p>` : ''}
    <p>标签按完整等义规则处理，旧标签保留；${reviewRequired} 项需人工核对，${plan.tag_effect.invalidated_request_count ?? 0} 项旧建议将失效。</p>
    ${plan.coverage.length ? `<details data-review-coverage><summary>金额覆盖核对（${plan.coverage.length} 个事实，不代表业务已核对正确）</summary>${plan.coverage.map(row=>{
      const fact = facts.get(row.transaction_id);
      return `<p>${esc(fact?.summary || `交易 #${row.transaction_id}`)}：事实 ${esc(cash(row.cash_amount,fact))}／生效覆盖 ${esc(cash(row.effective_cash_amount,fact))}</p>`;
    }).join('')}</details>` : ''}
    <details data-review-technical><summary>完整技术关系与标签影响（只读）</summary>
      <p>整体停用冲突 Review：${ids(plan.impact.conflicting_review_ids)}；恢复原始系统默认：${ids(plan.impact.restored_default_review_ids)}</p>
      <p>账户来源：${ids(plan.impact.affected_account_ref_ids)}；对象：${ids(plan.impact.affected_position_ids)}；可能失效的后续腿：${ids(plan.impact.dependent_position_leg_ids)}</p>
      <p>标签影响 Ledger：${ids(plan.impact.tag_ledger_ids)}；涉及 ${(plan.tag_effect.affected_views || []).length} 个视图、${(plan.tag_effect.affected_rule_ids || []).length} 条规则。旧标签保留，异步请求随账务状态失效。</p>
      <p>将失效的建议：${plan.tag_effect.invalidated_request_count ?? 0}；扫描状态：${esc(plan.tag_effect.scan_state || 'NOT_NEEDED')}。只有完整含义相同且新旧各唯一的输出延续标签。</p>
      ${plan.new_reviews.map(row=>`<section><h4>${esc(row.case_code)} → ${esc(reviewTypeNames[row.type] || row.type)} · ${esc(row.title)}</h4>
        ${row.allocations.map(allocation=>`<p>Fact #${allocation.transaction_id} → ${esc(typeNames[allocation.economic_type] || allocation.economic_type)} · ${esc(cash(allocation.cash_amount,facts.get(allocation.transaction_id)))} · ${esc(source(allocation.account_ref_id))}</p>`).join('')}
        ${row.new_positions.map((position,index)=>`<p>新对象 ${index+1}：${esc(position.title)} · ${esc(financialStateLabel('positionType',position.type))} · ${esc(financialStateLabel('positionUsage',position.usage_scenario))} · ${esc(labels.get(`party_id:${position.party_id}`) || `个人 #${position.party_id}`)} · ${esc(position.counterparty)} · ${esc(position.unit_code)}</p>`).join('')}
        ${row.legs.map((leg,index)=>`<p>数量腿 ${index+1} → ${leg.existing_position_id ? `对象 #${leg.existing_position_id}` : `新对象 ${leg.new_position_index+1}`}：${esc(leg.type)} · ${esc(leg.leg_direction)} ${esc(quantity(leg.leg_amount,leg.existing_position_id ? positions.get(leg.existing_position_id) : row.new_positions[leg.new_position_index]))} · ${esc(labels.get(`source:${leg.source}`) || `来源腿 #${leg.source}`)} · ${esc(date(leg.occurred_time))} · ${esc(leg.basis)}</p>`).join('')}
        ${row.position_allocations.map(link=>`<p>现金行 ${link.allocation_index+1} → 数量腿 ${link.leg_index+1}：${esc(money(link))} ${esc(link.cash_currency_code)}（款项归因，不是额外现金）</p>`).join('')}</section>`).join('')}
      ${plan.coverage.map(row=>`<p>Fact #${row.transaction_id}：事实 ${row.cash_amount}／生效覆盖 ${row.effective_cash_amount}（同币种最小单位）</p>`).join('')}
      ${mappings.length ? '<section class="panel" data-tag-mappings><h4>新旧标签对照</h4><div data-tag-impact></div></section>' : ''}
    </details></section>`;
}

// Preview and command share exactly the same frozen intent. Editing cancels
// the approval; command retries are never automatic, even after a lost reply.
function bindPublication(form, build, facts, completed, positions = new Map(), labels = () => new Map(), options = {}) {
  let generation = 0, plan, frozen, writing = false, uncertain = false, previewing = false;
  const previewButton = form.querySelector("[data-review-preview]");
  const submit = form.querySelector("[data-review-command]");
  const status = form.querySelector("[data-review-status]");
  const invalidate = () => {
    ++generation; plan = null; frozen = null; submit.disabled = true;
    status.textContent = uncertain ? "提交结果未知，请查询当前对象；不得重发。" : "输入已变更，需要重新预览。";
  };
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  let disabledBeforeWrite = new Set();
  const setEditing = disabled => {
    if (disabled) disabledBeforeWrite = new Set();
    form.querySelectorAll("input, textarea, select, button").forEach(node => {
      if (node.hasAttribute?.("data-preview-readonly")) return;
      if (disabled && node.disabled) disabledBeforeWrite.add(node);
      node.disabled = disabled || disabledBeforeWrite.has(node);
    });
  };
  previewButton.onclick = async () => {
    if (writing || uncertain || previewing) return;
    const issued = ++generation; plan = null; submit.disabled = true; previewing = true; previewButton.disabled = true;
    try {
      const intent = build();
      const next = await jsonRequest(`${base}/preview`, "POST", intent);
      if (!form.isConnected || issued !== generation) return;
      const markup = await (options.render ? options.render(next) : previewMarkup(next, facts, positions, labels()));
      if (!form.isConnected || issued !== generation) return;
      plan = next; frozen = intent;
      form.querySelector("[data-review-impact]").innerHTML = markup;
      if (next.tag_effect.mappings?.length) mountTagImpact(form.querySelector("[data-tag-impact]"), next.tag_effect);
      status.textContent = next.blocking_issues.length ? "有阻塞问题，不能提交。" : (options.confirmation || "请核对现金、数量、整体冲突和默认恢复后确认。");
      submit.disabled = !!next.blocking_issues.length;
      // Scene pages keep narrow-screen actions within reach; bring the actual
      // complete server impact into view before the user can confirm it.
      form.querySelector('[data-review-step="preview"]')?.scrollIntoView({block:'start'});
    } catch (error) { if (form.isConnected && issued === generation) status.textContent = `${financialIssueMessage(error)}；未取得可确认预览。`; }
    finally { previewing = false; if (form.isConnected) previewButton.disabled = uncertain; }
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (!plan || !frozen || writing || uncertain || submit.disabled) return;
    writing = true; setEditing(true);
    try {
      const result = await jsonRequest(`${base}/command`, "POST", { ...frozen, expected_reviews: plan.expected_reviews, preview_digest: plan.preview_digest });
      plan = null; frozen = null;
      status.textContent = "解释已发布并生效；不代表业务已核对正确。正在读取当前对象…";
      await completed(result);
    } catch (error) {
      plan = null; frozen = null;
      uncertain = writeFailure({ querySelector: () => status }, error);
      if (!uncertain) { setEditing(false); submit.disabled = true; }
    } finally { writing = false; }
  };
  invalidate.state = () => ({writing, uncertain, previewing});
  return invalidate;
}

export async function transitionReview(id, activate, reload) {
  id = resourceId(id);
  const row = await request(`${base}/${id}`);
  const duplicateLedgerIds = new Set(row.ledger_entries.filter(flow => flow.economic_type === "DUPLICATE").map(flow => flow.id));
  const duplicates = activate ? row.allocations.filter(allocation => duplicateLedgerIds.has(allocation.ledger_id)) : [];
  const excluded = [...new Map(duplicates.map(allocation => [allocation.transaction_id, allocation])).values()];
  const kept = new Map();
  const node = workbenchDialog(`${activate ? "激活" : "停用"}原 Review #${id}`, `<form class="stack"><p>仅改变原组状态；原现金、数量腿和关系 ID 保留。服务端将核对全部直接冲突、原始默认恢复和来源依赖。</p>
    ${excluded.length ? `<section><h3>重新核对重复证据的保留对象</h3><p>旧输出未保存可推断的保留关系。请为每份重复证据明确选择仍计量的交易；不按同额自动选择。</p>
      ${excluded.map(allocation => `<p>重复 Fact #${allocation.transaction_id} · ${esc(money(allocation))} → <span data-activation-kept="${allocation.transaction_id}">尚未选择保留对象</span> <button type="button" data-activation-pick="${allocation.transaction_id}">查找保留交易</button></p>`).join("")}</section>` : ""}
    <p role="status" data-review-status></p><div data-review-impact></div><button type="button" data-review-preview>预览整体影响</button><button type="submit" data-review-command disabled>确认当前预览</button></form>`);
  const form = node.querySelector("form");
  const invalidate = bindPublication(form, () => {
    if (kept.size !== excluded.length) throw new Error("请先为每份重复证据选择保留交易，再预览最终现金状态。");
    return {
      [activate ? "activate_review_ids" : "deactivate_review_ids"]: [id],
      activation_duplicates: excluded.map(allocation => ({ transaction_id: allocation.transaction_id,
        kept_transaction_id: kept.get(allocation.transaction_id).transaction_id })),
    };
  }, new Map(), async () => { node.close(); await reload(); }, new Map(row.positions.map(position => [position.id, position])));
  form.querySelectorAll("[data-activation-pick]").forEach(button => {
    button.onclick = () => {
      const factId = resourceId(button.dataset.activationPick);
      const picker = workbenchDialog("明确保留的真实交易", '<div data-kept-picker></div>');
      mountPicker(picker.querySelector("[data-kept-picker]"), {
        url: "/paam/ledger/v1/candidate", searchKeys: ["summary"],
        describe: fact => `${fact.summary} · ${money(fact)} · ${fact.cash_direction} · ${date(fact.occurred_time)} · Fact #${fact.transaction_id}`,
        choose: fact => {
          kept.set(factId, fact);
          form.querySelector(`[data-activation-kept="${factId}"]`).textContent = `${fact.summary} · ${money(fact)} · Fact #${fact.transaction_id}`;
          invalidate(); picker.close();
        },
      });
    };
  });
}

export async function mountReviewWorkbench(root, params, completed) {
  stopReviewRead(); controller = new AbortController();
  const signal = controller.signal, route = location.hash;
  const host = root.querySelector("[data-review-workflow]");
  if (!host) return;
  if (params.has('complete_source')) return mountSourceCompletion(host, params, {signal, publication:bindPublication});
  if (params.has('correct_ledger')) return mountAccountCorrection(host, params, {signal, publication:bindPublication});
  let sceneCode = params.get('case_code') || 'NORMAL';
  reviewScene(sceneCode);
  const preselected = new Set((params.get("facts") || "").split(",").filter(Boolean).map(id => resourceId(id)));
  if (preselected.size && !reviewScene(sceneCode).cash) throw new Error('期初数量场景不能携带现金事实，请移除 facts 条件');
  const facts = new Map(), selected = new Set(), positions = new Map();
  host.innerHTML = `<section class="panel review-scene-shell"><h1>不可变账务审查</h1><p>Fact 不可改；每个所选 Fact 必须完整解释。现金与对象数量分区，停用保留原始内容，不造剩余默认项、不按同名猜债。</p>
    <div class="review-scene-links"><a href="#workbench/position">查询对象与显式来源腿</a> · <a href="#workbench/account">查询来源卡</a></div>
    <form class="stack review-scene-form" data-immutable-review>
      <section data-review-step="select"><h2>1. 选择完整事实</h2><details data-scene-cash-selection open><summary data-scene-selection-summary>选择或调整完整事实</summary><div data-fact-picker></div><p data-selected-facts>未选择现金 Fact（纯数量场景允许为空）。</p></details><div data-current-review-selection></div><p data-scene-no-cash hidden>本场景只登记有据数量，不新增现金事实。</p></section>
      <section data-review-step="edit"><h2>2. 配置业务解释</h2><div class="review-scene-head">${select("case_code", "业务场景", cases, sceneCode)}${input("title", "审查说明", "", 'maxlength="160"')}</div><p data-scene-hint></p>
      <div data-scene-phase hidden>${select("phase", "共同费用阶段（只用于 AA）", [["ADVANCE_OUT", "我垫付：资产增加 / 现金流出"], ["COLLECT_IN", "我收回：资产减少 / 现金流入"], ["RECEIVE_IN", "他人垫付：负债增加 / 现金流入"], ["PAY_OUT", "我偿还：负债减少 / 现金流出"]], "ADVANCE_OUT")}</div>
      <details data-scene-cash><summary data-scene-cash-summary>现金分配与来源 · 展开改绑或拆分</summary><div data-cash-rows></div><button type="button" data-add-cash>增加现金拆分</button></details>
      <section data-scene-quantity hidden><h3>独立对象与有据数量</h3><p>可选已有对象，或在本次发布中创建新对象。OUT 必须指定该对象原始 IN 来源腿；不会自动 FIFO。</p>
      <div class="actions"><button type="button" data-find-position>查找已有对象</button><button type="button" data-new-position>本次发布新建对象</button></div>
      <div data-position-choices></div><div data-position-drafts></div><div data-leg-rows></div><button type="button" data-add-leg>增加数量腿</button></section>
      <section data-scene-link hidden><h3>现金 → 数量腿的款项归因</h3><p>必须显式选择现金拆分行和数量腿。金额来自现金币种；第二段不新增现金。</p><div data-link-rows></div><button type="button" data-add-link>增加款项归因</button></section>
      <details data-scene-duplicate hidden><summary>高级：明确重复证据 B → 保留 A</summary><p>B 不再计现金，原始证据保留；A 与 B 必须已核对不同来源卡、同金额、币种、方向和时间。</p><div data-duplicate-rows></div><button type="button" data-add-duplicate>增加重复证据 B → 保留 A</button></details>
      </section><section data-review-step="preview"><h2>3. 核对整体影响并发布</h2><p role="status" data-review-status></p><div data-review-impact></div><div class="actions"><button type="button" data-review-preview>服务端预览完整变更</button><button type="submit" data-review-command disabled>确认当前预览并发布</button></div></section></form></section>`;
  const form = host.querySelector("form"), cashRoot = form.querySelector("[data-cash-rows]"), legRoot = form.querySelector("[data-leg-rows]"), draftRoot = form.querySelector("[data-position-drafts]"), linkRoot = form.querySelector("[data-link-rows]"), duplicateRoot = form.querySelector("[data-duplicate-rows]");
  const value = (node, name) => node.querySelector(`[name="${name}"]`).value;
  const nodes = (node, selector) => [...node.querySelectorAll(selector)];
  const fail = error => { form.querySelector("[data-review-status]").textContent = financialIssueMessage(error); };
  let draftSerial = 0, selectingGroup = false, currentGroupSignature = '';
  const refReads = new Map();
  const readRef = id => {
    if (!refReads.has(String(id))) refReads.set(String(id), request(`/paam/ledger/v1/account-ref/${id}`, {signal})
      .catch(error => {refReads.delete(String(id)); throw error;}));
    return refReads.get(String(id));
  };
  let targetChoices = new Map(), cashChoices = new Map(), legChoices = new Map(), factChoices = new Map();
  const positionOptions = () => [...positions.values()].map(row => [`existing:${row.id}`, `${row.title} · ${row.unit_code} · #${row.id}`])
    .concat(nodes(draftRoot, '[data-position-draft]').map(row => [`new:${row.dataset.draftId}`, `新对象 ${value(row, 'title') || '未命名'} · ${value(row, 'unit_code')}`]));
  const refreshReferences = () => {
    if (selectingGroup) return;
    targetChoices = localChoiceMap(positionOptions());
    nodes(legRoot, '[data-leg-row]').forEach((row, index) => {
      row.querySelector('h3').textContent = `数量腿 ${index + 1}`;
      row.querySelector('[data-named-choice="source"]').hidden = value(row, 'leg_direction') === 'IN';
      if (refreshLocalChoice(row, 'target', targetChoices, '选择数量对象'))
        setNamedChoice(row, 'source', 0, '对象已改变，请重新选择原始来源（IN 无需来源）', false);
    });
    cashChoices = localChoiceMap(nodes(cashRoot, '[data-cash-row]').map((row, index) => {
      const fact = facts.get(resourceId(value(row, 'transaction_id')));
      row.querySelector('h3').textContent = `现金拆分 ${index + 1} · ${fact.summary || '未填写摘要'} · Fact #${fact.transaction_id}`;
      return [row.dataset.draftId, `现金 ${index + 1} · ${fact.summary} · ${fact.cash_direction} ${value(row, 'cash_amount')} ${fact.cash_currency_code}`];
    }));
    legChoices = localChoiceMap(nodes(legRoot, '[data-leg-row]').map((row, index) => [row.dataset.draftId,
      `数量腿 ${index + 1} · ${targetChoices.get(value(row, 'target')) || '尚未选择对象'} · ${value(row, 'leg_direction')} ${value(row, 'leg_amount') || '未填数量'}`]));
    nodes(linkRoot, '[data-link-row]').forEach(row => {
      refreshLocalChoice(row, 'allocation_ref', cashChoices, '选择现金拆分项');
      refreshLocalChoice(row, 'leg_ref', legChoices, '选择数量腿');
    });
    factChoices = localChoiceMap([...selected].map(id => [id, `${facts.get(id).summary} · ${money(facts.get(id))} · Fact #${id}`]));
    nodes(duplicateRoot, '[data-duplicate-row]').forEach(row => {
      if (refreshLocalChoice(row, 'transaction_id', factChoices, '选择本次排除的事实'))
        setNamedChoice(row, 'account_ref_id', '', '请重新选择 B 的可靠来源卡', false);
    });
    form.querySelector('[data-scene-selection-summary]').textContent = `已选 ${selected.size} 个完整事实 · 展开调整范围`;
    if (selected.size === 1) {
      const fact = facts.get([...selected][0]);
      form.querySelector('[data-scene-selection-summary]').textContent += ` · ${fact.summary} · ${money(fact)} ${fact.cash_direction}`;
    }
    form.querySelector('[data-scene-cash-summary]').textContent = `现金分配与来源 · ${cashRoot.children.length} 项拆分 · 展开改绑或拆分`;
    form.querySelector('[data-scene-duplicate] summary').textContent = `高级：明确重复证据 B → 保留 A（${duplicateRoot.children.length} 项）`;
    const groups = new Map();
    for (const id of selected) for (const review of facts.get(id).current_reviews || [])
      if (review.type !== 'NORMAL_TRANSACTION') groups.set(review.id,review);
    // Editing unrelated inputs must not replace a button between pointerdown
    // and click when the focused field dispatches its blur/change event.
    const signature = JSON.stringify([...groups.values()]);
    if (signature === currentGroupSignature) return;
    currentGroupSignature = signature;
    const currentHost = form.querySelector('[data-current-review-selection]');
    currentHost.replaceChildren();
    for (const review of groups.values()) {
      const button = document.createElement('button'); button.type = 'button';
      button.dataset.currentReview = review.id; button.dataset.previewReadonly = '';
      button.textContent = `当前事项：${currentReviewLabel(review)} · 查看原成员`;
      button.onclick = () => openReviewMembers(review,groupOptions()); currentHost.append(button);
    }
  };
  const article = (parent, kind, content) => {
    const node = document.createElement("article"); node.className = "panel stack"; node.dataset[kind] = ""; node.dataset.draftId = `draft-${++draftSerial}`;
    node.innerHTML = `${content}<div class="actions"><button type="button" data-remove>移除此行</button>${['positionDraft', 'cashRow', 'legRow'].includes(kind) ? '<button type="button" data-move-up>上移</button><button type="button" data-move-down>下移</button>' : ''}</div>`;
    node.querySelector("[data-remove]").onclick = () => {
      node.remove();
      refreshReferences(); invalidate();
    };
    node.querySelector('[data-move-up]')?.addEventListener('click', () => {node.previousElementSibling?.before(node); refreshReferences(); invalidate();});
    node.querySelector('[data-move-down]')?.addEventListener('click', () => {node.nextElementSibling?.after(node); refreshReferences(); invalidate();});
    parent.append(node); invalidate(); return node;
  };
  const refreshPositionChoices = () => {
    form.querySelector("[data-position-choices]").textContent = [...positions.values()].map(row => `#${row.id} ${row.title} / ${row.type} / ${row.unit_code} / ${row.status}`).join("；") || "尚未载入已有对象";
    refreshReferences();
  };
  const addCash = (fact, amount = fact.cash_amount) => {
    const index = nodes(cashRoot, "[data-cash-row]").length + 1;
    const scene = reviewScene(sceneCode);
    if (!scene.cash) return fail(new Error('本场景不接受现金事实或拆分'));
    const defaultType = scene.defaultType;
const row = article(cashRoot, "cashRow", `<h3>现金拆分行 ${index} · Fact #${fact.transaction_id}</h3><p>${esc(fact.summary)} · ${esc(fact.cash_direction)} · ${esc(date(fact.occurred_time))} · ${esc(fact.cash_currency_code)}（方向／币种／时间不可改）</p>
      <input type="hidden" name="transaction_id" value="${fact.transaction_id}">${select("economic_type", "经济分类", scene.economicTypes.map(key => [key, typeNames[key]]), defaultType)}
      ${input("cash_amount", "分配金额", quantityDecimal(amount, fact.cash_currency_code), 'inputmode="decimal" required')}${namedChoice('account_ref_id', '具体本方来源卡', {value: fact.account_ref_id, text: fact.account_ref_id ? '正在读取来源名称…' : '来源未识别', pickAttribute: 'data-pick-ref', clear: '明确来源未知'})}`);
    bindNamedChoice(row, 'account_ref_id', {url: '/paam/ledger/v1/account-ref', title: '选择具体来源卡', signal,
      allowZero: true, zeroLabel: '来源未识别', pickerAttribute: 'data-ref-picker', load: readRef});
    refreshReferences();
  };
  const readPosition = async id => {
    id = resourceId(id);
    const row = await request(`/paam/financial/v1/position/${id}`, { signal });
    if (!host.isConnected || route !== location.hash) return;
    positions.set(row.id, row); refreshPositionChoices(); invalidate(); return row;
  };
  const build = () => {
    if (form.elements.case_code.value !== sceneCode) throw new Error('请先完成场景切换，再预览');
    if (Object.keys(incompatibleSceneInputs(sceneCode, draftCounts())).length) throw new Error('存在不适用于当前场景的输入，请重新核对');
    const cashRows = nodes(cashRoot, '[data-cash-row]'), draftRows = nodes(draftRoot, '[data-position-draft]'), legRows = nodes(legRoot, '[data-leg-row]');
    const allocations = cashRows.map(row => {
      const transaction_id = resourceId(value(row, "transaction_id")), fact = facts.get(transaction_id);
      return { transaction_id, economic_type: value(row, "economic_type"), cash_amount: quantityAmount(value(row, "cash_amount"), fact.cash_currency_code), account_ref_id: resourceId(value(row, "account_ref_id"), { allowZero: true }) };
    });
    for (const id of selected) {
      const sum = allocations.filter(row => row.transaction_id === id).reduce((sum, row) => sum + row.cash_amount, 0);
      if (!Number.isSafeInteger(sum) || sum !== facts.get(id).cash_amount) throw new Error(`Fact #${id} 需要完整解释：${sum} / ${facts.get(id).cash_amount} 最小单位`);
    }
    const new_positions = draftRows.map(row => Object.fromEntries(["title", "description", "type", "usage_scenario", "counterparty", "unit_code"].map(key => [key, key === "unit_code" ? value(row, key).trim().toUpperCase() : value(row, key)]).concat([["party_id", resourceId(value(row, "party_id"))]])));
    const legs = legRows.map(row => {
      const target = positionTarget(value(row, 'target'), draftRows);
      const position = target.existing_position_id ? positions.get(target.existing_position_id) : new_positions[target.new_position_index];
      if (!position) throw new Error('数量腿需先选择明确对象');
      return { ...target, type: value(row, "type"), leg_amount: quantityAmount(value(row, "leg_amount"), position.unit_code), leg_direction: value(row, "leg_direction"), occurred_time: dateTimeValue(row, 'occurred_time'), source: resourceId(value(row, "source"), { allowZero: true }), basis: value(row, "basis") };
    });
    const position_allocations = nodes(linkRoot, "[data-link-row]").map(row => {
      const {allocation_index, leg_index} = linkTarget(value(row, 'allocation_ref'), value(row, 'leg_ref'), cashRows, legRows);
      const cash_currency_code = facts.get(allocations[allocation_index].transaction_id).cash_currency_code;
      return { allocation_index, leg_index, cash_currency_code, cash_amount: quantityAmount(value(row, "cash_amount"), cash_currency_code) };
    });
    const duplicate_transactions = nodes(duplicateRoot, "[data-duplicate-row]").map(row => ({ transaction_id: resourceId(value(row, "transaction_id")), kept_transaction_id: resourceId(value(row, "kept_transaction_id")) }));
    const account_bindings = nodes(duplicateRoot, "[data-duplicate-row]").map(row => ({ transaction_id: resourceId(value(row, "transaction_id")), account_ref_id: resourceId(value(row, "account_ref_id")) }));
    const case_code = form.elements.case_code.value;
    const core = { allocations, new_positions, legs, position_allocations };
    const review = { case_code, title: form.elements.title.value, account_bindings, duplicate_transactions,
      ...(case_code.startsWith("POS_") ? core : { parameters: { ...core, ...(case_code === "SHARED_PAYMENT" ? { phase: form.elements.phase.value } : {}) } }) };
    return { new_reviews: [review] };
  };
  const invalidate = bindPublication(form, build, facts, completed, positions, () => new Map(nodes(form, '[data-named-choice]').map(field => {
    const name = field.dataset.namedChoice, input = field.querySelector('input');
    return [`${name}:${input.value}`, field.querySelector('[data-choice-label]').textContent];
  })));
  form.addEventListener('input', refreshReferences);
  form.addEventListener('change', refreshReferences);
  form.querySelector("[data-add-cash]").onclick = () => {
    const fact = facts.get([...selected][0]); if (!fact) return fail(new Error("先选择现金 Fact；纯数量场景无需现金行"));
    if (selected.size === 1) return addCash(fact);
    const node = workbenchDialog("选择要增加拆分的现金 Fact", `${select("fact", "明确 Fact", [...selected].map(id => [id, `#${id} ${facts.get(id).summary}`]), fact.transaction_id)}<button type="button" data-add-selected>增加拆分</button>`);
    node.querySelector("[data-add-selected]").onclick = () => {
      try { addCash(facts.get(resourceId(value(node, "fact")))); node.close(); } catch (error) { fail(error); }
    };
  };
  form.querySelector("[data-find-position]").onclick = () => {
    if (!reviewScene(sceneCode).quantity) return fail(new Error('本场景不接受数量对象'));
    const node = workbenchDialog("分页选择对象", '<div data-position-picker></div>');
    mountPicker(node.querySelector("[data-position-picker]"), { url: "/paam/financial/v1/position", searchKeys: ["title"], signal,
      describe: row => `#${row.id} ${row.title} / ${row.type} / ${row.unit_code} / ${row.status}`,
      choose: row => { readPosition(row.id).then(() => node.close()).catch(fail); },
    });
  };
  form.querySelector("[data-new-position]").onclick = () => {
    if (!reviewScene(sceneCode).quantity) return fail(new Error('本场景不接受数量对象'));
    const row = article(draftRoot, "positionDraft", `<h3>本次新对象 ${nodes(draftRoot, "[data-position-draft]").length + 1}</h3>${positionFields()}`);
    bindPartyPicker(row, signal); refreshReferences();
  };
  form.querySelector("[data-add-leg]").onclick = () => {
    if (!reviewScene(sceneCode).quantity) return fail(new Error('本场景不接受数量腿'));
    const targets = positionOptions();
    if (!targets.length) return fail(new Error("先读取已有对象或填写本次新对象"));
    const opening = form.elements.case_code.value === "POS_OPENING";
    const row = article(legRoot, "legRow", `<h3>数量腿 ${nodes(legRoot, "[data-leg-row]").length + 1}</h3>${namedChoice('target', '明确对象', {value: targets.length === 1 ? targets[0][0] : '', text: targets.length === 1 ? targets[0][1] : '尚未选择数量对象', pick: '选择数量对象'})}${select("type", "数量证据类型", ["MOVEMENT", "OPENING"], opening ? "OPENING" : "MOVEMENT")}${select("leg_direction", "数量方向", ["IN", "OUT"], form.elements.case_code.value.includes("SETTLE") || form.elements.case_code.value === "POS_CREDIT_REPAY" ? "OUT" : "IN")}
      ${input("leg_amount", "精确数量（按对象单位）", "", 'inputmode="decimal" required')}${dateTimeField('occurred_time', '数量发生时间')}${namedChoice('source', '原始 IN 数量证据（OUT 必须明确选择）', {value: 0, text: 'IN 无需来源；OUT 请查找该对象有效的原始 IN 腿', pickAttribute: 'data-find-source', pick: '查找原始来源腿'})}${input("basis", "数量依据 / 第三人代还说明", "", 'maxlength="2000"')}`);
    const resetSource = () => {setNamedChoice(row, 'source', 0, 'IN 无需来源；OUT 请重新选择原始来源', false); invalidate();};
    row.querySelector('[name="target"]').onchange = resetSource;
    row.querySelector('[name="leg_direction"]').onchange = resetSource;
    bindLocalChoice(row, 'target', {title: '选择当前草稿数量对象', choices: () => targetChoices, signal});
    row.querySelector("[data-find-source]").onclick = () => {
      const target = value(row, "target");
      if (!target.startsWith("existing:")) return fail(new Error("新对象没有已发布来源腿；OUT 需选择已有对象"));
      let id;
      try { id = resourceId(target.split(":")[1]); } catch (error) { return fail(error); }
      const node = workbenchDialog(`对象 #${id} 的原始来源腿`, '<div data-source-picker></div>');
      mountPicker(node.querySelector("[data-source-picker]"), { url: `/paam/financial/v1/position/${id}/leg`, searchKeys: ["basis"], signal,
        describe: leg => `${leg.basis || "依据未填写"} / ${leg.leg_direction} ${quantityDecimal(leg.leg_amount, leg.unit_code)} ${leg.unit_code} / ${date(leg.occurred_time)} / Review #${leg.review_id} ${leg.review.status} / Leg #${leg.id}`,
        choose: leg => {
          if (leg.leg_direction !== "IN" || leg.review.status !== "CONFIRMED") return fail(new Error("只能选择该对象当前有效的原始 IN 来源腿"));
          setNamedChoice(row, 'source', leg.id, `${leg.basis || '依据未填写'} · IN ${quantityDecimal(leg.leg_amount, leg.unit_code)} ${leg.unit_code} · ${date(leg.occurred_time)} · Leg #${leg.id}`); node.close();
        },
      });
    };
    refreshReferences();
  };
  form.querySelector("[data-add-link]").onclick = () => {
    if (!reviewScene(sceneCode).link) return fail(new Error('本场景不接受款项归因'));
    const row = article(linkRoot, "linkRow", `${namedChoice('allocation_ref', '明确现金拆分项', {pick: '选择现金拆分项'})}${namedChoice('leg_ref', '明确数量腿', {pick: '选择数量腿'})}${input("cash_amount", "归因现金金额（币种取自现金行）", "", 'inputmode="decimal" required')}`);
    bindLocalChoice(row, 'allocation_ref', {title: '选择当前草稿现金拆分项', choices: () => cashChoices, signal});
    bindLocalChoice(row, 'leg_ref', {title: '选择当前草稿数量腿', choices: () => legChoices, signal});
    refreshReferences();
  };
  form.querySelector("[data-add-duplicate]").onclick = () => {
    if (!reviewScene(sceneCode).duplicate) return fail(new Error('本场景不接受重复证据'));
    const row = article(duplicateRoot, 'duplicateRow', `${namedChoice('transaction_id', '重复证据 B（本次所选事实）', {pick: '选择本次排除的事实'})}${namedChoice('kept_transaction_id', '保留计现金 A', {pick: '查找保留交易', pickAttribute: 'data-pick-kept'})}${namedChoice('account_ref_id', '明确 B 来源卡', {pick: '查找 B 来源卡', pickAttribute: 'data-pick-duplicate-ref'})}`);
    bindLocalChoice(row, 'transaction_id', {title: '选择当前草稿排除的事实', choices: () => factChoices, signal});
    bindNamedChoice(row, 'kept_transaction_id', {url: '/paam/ledger/v1/candidate', title: '明确保留的真实交易', signal,
      initialize: false, searchKeys: ['summary'], pickerAttribute: 'data-kept-picker',
      describe: fact => `${fact.summary} · ${money(fact)} · ${fact.cash_direction} · ${date(fact.occurred_time)} · Fact #${fact.transaction_id}`});
    bindNamedChoice(row, 'account_ref_id', {url: '/paam/ledger/v1/account-ref', title: '明确 B 来源卡', signal, pickerAttribute: 'data-ref-picker'});
    row.querySelector('[name="transaction_id"]').onchange = () => setNamedChoice(row, 'account_ref_id', '', '请重新选择 B 来源卡');
    refreshReferences();
  };
  const choose = fact => {
    if (!reviewScene(sceneCode).cash) return fail(new Error('本场景不接受现金事实'));
    let id;
    try { id = resourceId(fact.transaction_id); } catch (error) { return fail(error); }
    if (selected.has(id)) {
      selected.delete(id); nodes(cashRoot, "[data-cash-row]").filter(row => resourceId(value(row, "transaction_id")) === id).forEach(row => row.remove());
    } else {
      if (selected.size >= 2000) return fail(new Error('本次最多选择2000个完整事实，请明确缩小范围。'));
      facts.set(id, fact); selected.add(id); addCash(fact);
    }
    form.querySelector("[data-selected-facts]").textContent = `选择 ${selected.size} 个完整 Fact：${ids([...selected])}`; refreshReferences(); invalidate();
  };
  function groupOptions() {
    return {signal,canSelect:() => host.isConnected && route === location.hash && !signal.aborted
      && reviewScene(sceneCode).cash && !form.querySelector('[data-review-preview]').disabled,
      select:members => {
        const union = new Set([...selected,...members.map(fact => resourceId(fact.transaction_id))]);
        if (union.size > 2000) throw new Error('加入整组后超过2000个事实，选择未改变；请先明确缩小范围。');
        // Preflight every value used by cash construction before applying the
        // first member. Invalid legacy units/IDs must not leave half a group.
        for (const fact of members) {
          resourceId(fact.account_ref_id,{allowZero:true});
          if (fact.cash_amount <= 0) throw new Error('事项包含无效现金金额，选择未改变。');
          quantityDecimal(fact.cash_amount,fact.cash_currency_code);
        }
        selectingGroup = true;
        try {
          for (const fact of members) {
            const id = resourceId(fact.transaction_id); facts.set(id,fact);
            if (!selected.has(id)) {selected.add(id); addCash(fact);}
          }
        } finally {selectingGroup = false;}
        form.querySelector('[data-selected-facts]').textContent = `选择 ${selected.size} 个完整 Fact：${ids([...selected])}`;
        refreshReferences(); invalidate();
      }};
  }
  let pickerMounted = false;
  const ensureFactPicker = async () => {
    if (pickerMounted || !reviewScene(sceneCode).cash) return;
    pickerMounted = true;
    await mountPicker(form.querySelector("[data-fact-picker]"), { url: "/paam/ledger/v1/candidate", searchKeys: ["summary"], signal,
    describe: fact => `Fact #${fact.transaction_id} ${fact.summary} / ${money(fact)} ${fact.cash_direction} / ${financialStateLabel('coverage',fact.coverage.state,false)} / ${financialStateLabel('default',fact.coverage.default_identity_state,false)} / ${fact.current_reviews.length === 1 ? `当前事项：${currentReviewLabel(fact.current_reviews[0])}` : `当前有效事项 ${fact.current_reviews.length} 个`}`,
    selected: fact => selected.has(fact.transaction_id), choose,
    actions: fact => fact.current_reviews.length ? [{label:`查看当前事项（${fact.current_reviews.length}）`,choose:row => openCurrentReviews(row,groupOptions())}] : [],
    });
  };
  function draftCounts() {
    return {facts:selected.size, cash:cashRoot.children.length, drafts:draftRoot.children.length,
      legs:legRoot.children.length, links:linkRoot.children.length, duplicates:duplicateRoot.children.length};
  }
  function applyScene(code) {
    const previousDefault = reviewScene(sceneCode).defaultType;
    const scene = reviewScene(code), discarded = incompatibleSceneInputs(code, draftCounts());
    if (discarded.facts) selected.clear();
    for (const [key, area] of Object.entries({cash:cashRoot, drafts:draftRoot, legs:legRoot, links:linkRoot, duplicates:duplicateRoot}))
      if (discarded[key]) area.replaceChildren();
    sceneCode = code;
    for (const key of ['cash', 'quantity', 'link', 'phase', 'duplicate']) form.querySelector(`[data-scene-${key}]`).hidden = !scene[key];
    form.querySelector('[data-scene-cash]').open = scene.quantity;
    form.querySelector('[data-scene-cash-selection]').hidden = !scene.cash;
    if (!selected.size) form.querySelector('[data-scene-cash-selection]').open = true;
    form.querySelector('[data-scene-no-cash]').hidden = scene.cash;
    form.querySelector('[data-scene-duplicate]').open = code === 'DUPLICATE' || duplicateRoot.children.length > 0;
    form.elements.phase.disabled = !scene.phase;
    if (!scene.phase) form.elements.phase.value = 'ADVANCE_OUT';
    form.querySelector('[data-scene-hint]').textContent = scene.hint;
    nodes(cashRoot, '[name="economic_type"]').forEach(field => {
      const current = field.value !== previousDefault && scene.economicTypes.includes(field.value) ? field.value : scene.defaultType;
      field.replaceChildren(...scene.economicTypes.map(key => new Option(typeNames[key], key, false, key === current)));
    });
    form.querySelector('[data-selected-facts]').textContent = `选择 ${selected.size} 个完整 Fact：${ids([...selected])}`;
    refreshReferences(); invalidate();
    form.querySelector('[data-review-impact]').replaceChildren();
  }
  form.elements.case_code.onchange = () => {
    const next = form.elements.case_code.value, discarded = incompatibleSceneInputs(next, draftCounts());
    const apply = () => {applyScene(next); ensureFactPicker().catch(fail);};
    if (!Object.keys(discarded).length) return apply();
    const names = {facts:'个完整事实', cash:'个现金拆分', drafts:'个新对象', legs:'条数量腿', links:'条款项归因', duplicates:'条重复证据'};
    const node = workbenchDialog('确认业务场景切换', `<div data-scene-switch><p>新场景不适用的草稿将移除：${Object.entries(discarded).map(([key,count]) => `${count} ${names[key]}`).join('、')}。尚未发布，不改持久账务；旧预览已经失效。</p><button type="button" data-scene-cancel>保留原场景和输入</button><button type="button" data-scene-apply>移除不适用输入并切换</button></div>`);
    let accepted = false;
    const abort = () => {if (node.open) node.close();};
    signal.addEventListener('abort', abort, {once:true});
    node.addEventListener('close', () => {
      signal.removeEventListener('abort', abort);
      if (!accepted) form.elements.case_code.value = sceneCode;
    }, {once:true});
    node.querySelector('[data-scene-cancel]').onclick = () => node.close();
    node.querySelector('[data-scene-apply]').onclick = () => {accepted = true; apply(); node.close();};
  };
  applyScene(sceneCode);
  await ensureFactPicker();
  for (const id of preselected) {
    const page = await request(`/paam/ledger/v1/candidate/list?${new URLSearchParams({ page_size: "20", filter: JSON.stringify({ key: "id", op: "=", val: id }) })}`, { signal });
    if (!host.isConnected || route !== location.hash) return;
    if (page.items[0]) choose(page.items[0]);
  }
  if (preselected.size && selected.size) form.querySelector('[data-scene-cash-selection]').open = false;
  if (params.get("position")) await readPosition(params.get("position"));
}

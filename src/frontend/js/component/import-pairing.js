import {request} from '../api/client.js';
import {esc,money,date,resourceId} from '../util/core.js';
import {workbenchDialog} from './workbench.js';
import {importRowIdentity,importRowLabel,importIntentChoice,importIntentNames,openImportChoice} from './import-choice.js';
import {mountImportDraftRows} from './import-bulk.js';
import {readImportBinding} from './import-binding.js';

const actions = {SAME_SOURCE:'LINK_EXISTING',CROSS_SOURCE:'DUPLICATE'};
const timeKey = value => {
  const found = typeof value === 'string' && /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  if (!found) throw new Error('配对时间不是完整UTC时间');
  return `${found[1]}.${(found[2] || '').padEnd(6,'0')}Z`;
};
const sameCore = (a,b) => Number.isSafeInteger(a.amount) && a.amount > 0 && a.amount === b.amount
  && typeof a.currency_code === 'string' && !!a.currency_code && a.currency_code === b.currency_code
  && ['IN','OUT'].includes(a.cash_direction) && a.cash_direction === b.cash_direction
  && timeKey(a.occurred_time) === timeKey(b.occurred_time);
const locator = row => {
  if (typeof row?.file_id !== 'number' || typeof row?.source_row_number !== 'number') throw new Error('来源行定位不精确');
  resourceId(row.file_id);resourceId(row.source_row_number);
  return importRowIdentity(row);
};
const mutable = item => item.row.classification !== 'PROCESSED' && item.row.persisted_row_status !== 1
  && !item.row.existing_transaction_id && item.row.classification !== 'EXISTING'
  && (![2,3].includes(item.row.persisted_row_status) || item.choice.recheck === true);
const cloneScope = selected => new Map([...selected].map(([key,item]) => [key,structuredClone(item)]));

// Also catch in-place changes to a frozen range, not just a new Map reference.
export function sameImportPairingScope(frozen,current) {
  if (!(current instanceof Map) || current.size !== frozen.size) return false;
  for (const [key,item] of frozen) {
    const next = current.get(key);
    if (!next || JSON.stringify(item) !== JSON.stringify(next)) return false;
  }
  return true;
}

export function validateImportPairing(result,selected,kind,digest,time) {
  if (!Object.hasOwn(actions,kind) || !selected.size || selected.size > 20000 || typeof time !== 'string' || !time
      || result?.source_preview_digest !== digest || result.expected_updated_time !== time || result.kind !== kind
      || result.selected_count !== selected.size || !Array.isArray(result.items) || result.items.length !== selected.size)
    throw new Error('批配对核验范围、策略或前提不一致；未应用草稿');
  const seen = new Set();
  for (const item of result.items) {
    const key = locator(item.row), source = selected.get(key);
    if (!source || locator(source.row) !== key || seen.has(key) || typeof item.source_label_masked !== 'string'
        || !Array.isArray(item.reason_codes) || item.reason_codes.some(code=>typeof code !== 'string' || !code)
        || !(item.candidate_count === null || Number.isSafeInteger(item.candidate_count) && item.candidate_count >= 0))
      throw new Error('批配对核验不完整或来源行重复');
    seen.add(key);
    if (item.state === 'SUGGESTED') {
      const suggestion = item.suggestion;
      if (!suggestion || item.candidate_count !== 1 || item.reason_codes.length || suggestion.resolution !== actions[kind]
          || typeof suggestion.summary_masked !== 'string' || typeof suggestion.source_label_masked !== 'string'
          || !Array.isArray(suggestion.current_review_summaries) || suggestion.current_review_summaries.some(review=>typeof review.title !== 'string')
          || !mutable(source) || source.choice.decision !== 'ACCEPT' || (source.choice.resolution || 'AUTO') !== 'AUTO'
          || !sameCore(source.row.parsed,suggestion)) throw new Error('具名配对建议与本行核心或意图不一致');
      // Shared strict target validation; this is not consent or a mutation.
      importIntentChoice(source.row,source.choice,actions[kind],suggestion.target,false);
      if (suggestion.target.kind === 'ROW') {
        const anchor = selected.get(locator(suggestion.target));
        if (!anchor || !mutable(anchor) || anchor.row.classification !== 'NEW' || anchor.row.issue_codes?.length
            || anchor.choice.decision !== 'ACCEPT' || !['AUTO','NEW'].includes(anchor.choice.resolution || 'AUTO')
            || !sameCore(anchor.row.parsed,suggestion)) throw new Error('本批锚点已移除、改变或不再是真实新行');
      }
    } else if (item.suggestion !== null
        || !(item.state === 'NO_MATCH' && item.candidate_count === 0 && !item.reason_codes.length
          || item.state === 'AMBIGUOUS' && item.candidate_count > 1 && item.reason_codes.length
          || item.state === 'EXCEPTION' && item.reason_codes.length)) throw new Error('批配对状态、计数和例外不一致');
  }
  return result;
}

export function pairingTargetLabel(suggestion,files=[]) {
  const target = suggestion.target;
  const identity = target.kind === 'FACT' ? `Fact #${target.transaction_id}`
    : `${files.find(file=>file.file_id === target.file_id)?.filename || `文件 #${target.file_id}`} 第 ${target.source_row_number} 行（本批真实锚点）`;
  return `${suggestion.summary_masked || '摘要未知'} · ${suggestion.source_label_masked} · ${identity}`;
}

// All-or-nothing local projection. A selected suggestion is still only a
// draft: saving, full financial preview and lock-time validation remain needed.
export function projectImportPairing(selected,result,confirmed,kind,digest,time,manual=new Map(),files=[]) {
  const working = new Map(selected);
  for (const [key,item] of manual) {
    if (!selected.has(key) || key !== locator(item.row) || !mutable(selected.get(key))) throw new Error('人工草稿不属于可修改的冻结范围');
    const choice = importIntentChoice(item.row,item.choice,item.choice.resolution || 'AUTO',item.choice.target,item.choice.acknowledge_new_risk);
    working.set(key,{...item,choice});
  }
  validateImportPairing(result,working,kind,digest,time);
  const suggestions = new Map(result.items.filter(item=>item.state === 'SUGGESTED').map(item=>[locator(item.row),item.suggestion]));
  const updated = new Map(working);
  for (const key of confirmed) {
    if (!suggestions.has(key) || manual.has(key)) throw new Error('确认范围包含未展示或已变更的配对');
    const previous = working.get(key), suggestion = suggestions.get(key);
    updated.set(key,{...previous,choice:importIntentChoice(previous.row,previous.choice,suggestion.resolution,suggestion.target,false),
      targetLabel:pairingTargetLabel(suggestion,files),refLabel:suggestion.resolution === 'LINK_EXISTING' ? null : previous.refLabel});
  }
  // Never turn a proposed/manual B into a ROW keeper or accept a pair chain.
  for (const item of updated.values()) {
    if (item.choice.decision !== 'ACCEPT' || item.choice.target?.kind !== 'ROW') continue;
    const anchor = updated.get(locator(item.choice.target));
    if (!anchor || !mutable(anchor) || anchor.row.classification !== 'NEW' || anchor.row.issue_codes?.length
        || anchor.choice.decision !== 'ACCEPT' || !['AUTO','NEW'].includes(anchor.choice.resolution || 'AUTO')
        || !sameCore(item.row.parsed,anchor.row.parsed)) throw new Error('配对锚点已移除或成为另一配对来源；本次草稿全部不应用');
  }
  return {selected:updated,modified:manual.size+confirmed.size,unchanged:selected.size-manual.size-confirmed.size};
}

const reasons = {ROWS_ALREADY_PROCESSED:'已接受行只读',ROW_RECHECK_REQUIRED:'旧跳过／问题状态需先明确重查',
  USER_INTENT_RETAINED:'保留原明确意图或跳过决定',AUTO_IDENTITY_RETAINED:'已有可靠匹配，保持自动来源规则',
  SOURCE_IDENTITY_REQUIRED:'完整来源身份不能证明',IDENTITY_AMBIGUOUS:'有多个候选或同文件目标碰撞，需人工核对',
  PAIR_TARGET_REQUIRES_REAL_ANCHOR:'目标本身也待配对，须先明确真实锚点',INVALID_EVIDENCE_TARGET:'不是合法的本批真实锚点',
  INVALID_DUPLICATE:'目标不是不同可靠来源的完整真实现金',ACCOUNT_BINDING_CONFLICT:'来源归属与完整身份冲突',
  FACT_CONFLICT:'来源会计核心冲突'};

export function openImportPairing({selected,current=()=>selected,files,token,digest,time,signal,valid,apply}) {
  const frozen = cloneScope(selected), manual = new Map(), confirmed = new Set();
  let working = new Map(frozen), result, generation=0, busy=false, reader, page=0;
  const dialog = workbenchDialog('具名批配对（仅修改草稿）', `<div class="import-choice"><p>冻结 ${frozen.size} 行、${new Set([...frozen.values()].map(item=>item.row.file_id)).size} 个文件。逐一具名映射，不广播同一个Fact；唯一建议不会自动确认。</p><details><summary>完整已选来源范围</summary><div data-pairing-scope></div></details><label>配对策略<select data-pairing-kind><option value="SAME_SOURCE">同源补证据，不新增现金</option><option value="CROSS_SOURCE">跨源重复，新B保留证据但不计现金</option></select></label><p data-pairing-effect></p><div class="actions"><button type="button" data-pairing-read>核验完整具名配对（不保存、不入账）</button><button type="button" data-pairing-stop disabled>停止核验</button></div><p role="status" data-pairing-status></p><p data-pairing-count></p><div class="actions"><label>显示<select data-pairing-filter><option value="ALL">全部行</option><option value="SUGGESTED">唯一具名建议</option><option value="OTHER">待人工处理／保留原草稿</option></select></label><button type="button" data-pairing-all disabled>选择完整范围内全部具名建议</button><button type="button" data-pairing-none disabled>清除建议选择</button></div><div data-pairing-items></div><div class="actions"><button type="button" data-pairing-prev disabled>上一页</button><span data-pairing-page></span><button type="button" data-pairing-next disabled>下一页</button></div><label><input type="checkbox" data-pairing-ack>已核对选中的具名映射及人工草稿，明确确认上述处理意图；仍需保存核验与完整金融预览</label><label data-pairing-retain hidden><input type="checkbox" data-pairing-retain-ack>其余行保留原选择和决定，不自动跳过入账；歧义和例外仍需处理</label><button type="button" data-pairing-apply disabled>确认应用具名配对草稿</button></div>`);
  dialog.classList.add('import-choice-dialog','import-pairing-dialog');
  const find = selector=>dialog.querySelector(selector), local = new AbortController();
  const alive = ()=>dialog.isConnected && !local.signal.aborted && valid();
  const scopeCurrent = ()=>alive() && sameImportPairingScope(frozen,current());
  const abort = ()=>{local.abort();reader?.abort();if(dialog.open) dialog.close();};
  signal.addEventListener('abort',abort,{once:true});
  dialog.addEventListener('close',()=>{local.abort();reader?.abort();signal.removeEventListener('abort',abort);},{once:true});
  mountImportDraftRows(find('[data-pairing-scope]'),[...frozen.values()],item=>importRowLabel(item.row,files),local.signal);
  const ackReset = ()=>{find('[data-pairing-ack]').checked=false;find('[data-pairing-retain-ack]').checked=false;};
  const kind = ()=>find('[data-pairing-kind]').value;
  const effect = ()=>{find('[data-pairing-effect]').textContent=kind()==='SAME_SOURCE'
    ? 'LINK只补证据，保留目标Fact的来源、解释与标签；删除本行新流水来源覆盖，不新增默认或现金。'
    : 'DUP在同一金融事务建立新B和原默认，并发布排除现金的解释；A须保留完整真实现金。来源和新现金风险不会由选择建议自动补齐。';};
  const controls = ()=>{
    const count = confirmed.size+manual.size;
    find('[data-pairing-read]').disabled=busy;
    find('[data-pairing-stop]').disabled=!busy;
    find('[data-pairing-all]').disabled=busy || !result?.items.some(item=>item.state==='SUGGESTED');
    find('[data-pairing-none]').disabled=busy || !confirmed.size;
    find('[data-pairing-apply]').disabled=busy || !result || !count;
    find('[data-pairing-retain]').hidden=!result || count===frozen.size;
    find('[data-pairing-count]').textContent=result
      ? `完整核验 ${result.selected_count} 行；唯一建议 ${result.items.filter(item=>item.state==='SUGGESTED').length} 行，已选映射 ${confirmed.size} 行，人工草稿 ${manual.size} 行，其余 ${frozen.size-count} 行保留。不是金融确认。`
      : `人工草稿 ${manual.size} 行；完整核验尚未完成，不能应用。`;
  };
  const clear = ()=>{result=null;confirmed.clear();ackReset();page=0;find('[data-pairing-items]').textContent='';find('[data-pairing-page]').textContent='';
    find('[data-pairing-prev]').disabled=true;find('[data-pairing-next]').disabled=true;controls();};
  const paint = ()=>{
    if (!alive() || !result) return;
    const filter = find('[data-pairing-filter]').value;
    const rows = result.items.filter(item=>filter==='ALL' || (filter==='SUGGESTED' ? item.state==='SUGGESTED' : item.state!=='SUGGESTED'));
    page=Math.min(page,Math.max(0,Math.ceil(rows.length/20)-1));
    const start = page*20;
    find('[data-pairing-items]').innerHTML=rows.slice(start,start+20).map(item=>{
      const key=locator(item.row), source=working.get(key), suggestion=item.suggestion, edited=manual.get(key);
      const target=suggestion ? `${pairingTargetLabel(suggestion,files)}\n${date(suggestion.occurred_time)} (${suggestion.occurred_time}) · ${suggestion.cash_direction} ${money(suggestion)}\n${suggestion.current_review_summaries.map(review=>review.title).join('；') || (suggestion.target.kind==='ROW' ? '本批拟建真实默认；实际ID由提交产生' : '当前无有效解释摘要')}`
        : edited ? `${importIntentNames[edited.choice.resolution || 'AUTO']} ${edited.targetLabel ? `→ ${edited.targetLabel}` : ''}\n人工已明确，仍需完整金融预览；不是已入账`
        : item.state==='NO_MATCH' ? '精确范围无候选；不证明不存在重复，不自动视为新交易'
        : `候选 ${item.candidate_count === null ? '未完成／不适用' : item.candidate_count}：${item.reason_codes.map(code=>reasons[code] || code).join('、')}`;
      return `<article class="import-pairing-row" data-pairing-row="${key}"><label class="import-pairing-select">${suggestion ? `<input type="checkbox" data-pairing-choice="${key}" aria-label="确认文件 #${item.row.file_id} 第 ${item.row.source_row_number} 行具名配对" ${confirmed.has(key) ? 'checked' : ''}>` : '<span>—</span>'}</label><div><small>本行</small><span>${esc(importRowLabel(source.row,files))}</span><small>${esc(item.source_label_masked)}</small></div><div><small>${suggestion ? '具名目标' : edited ? '人工草稿' : '待核对／保留'}</small><span>${esc(target)}</span></div><div><small>${suggestion ? esc(importIntentNames[suggestion.resolution]) : '原草稿与选择不会自动丢弃'}</small><button type="button" data-pairing-manual="${key}" ${mutable(source) ? '' : 'disabled'}>单独核对</button></div></article>`;
    }).join('') || '<p>此筛选没有行；完整范围未被排除。</p>';
    find('[data-pairing-page]').textContent=`${rows.length ? start+1 : 0}–${Math.min(start+20,rows.length)} / ${rows.length}（本地每页20行）`;
    find('[data-pairing-prev]').disabled=busy || page===0;find('[data-pairing-next]').disabled=busy || start+20>=rows.length;
    dialog.querySelectorAll('[data-pairing-choice]').forEach(node=>{node.onchange=()=>{
      if (!scopeCurrent() || busy) return;
      if(node.checked) confirmed.add(node.dataset.pairingChoice);else confirmed.delete(node.dataset.pairingChoice);
      ackReset();controls();
    };});
    dialog.querySelectorAll('[data-pairing-manual]').forEach(node=>{node.onclick=()=>{
      if (!scopeCurrent() || busy) return;
      const key=node.dataset.pairingManual, source=working.get(key);
      if(!mutable(source)) return;
      const child=openImportChoice({row:source.row,choice:source.choice,targetLabel:source.targetLabel,token,digest,files,
        selected:()=>working,signal:local.signal,valid:scopeCurrent,
        apply:(choice,targetLabel)=>{
          if(!scopeCurrent()) throw new Error('冻结范围已变化，人工草稿未应用');
          const next={...source,choice,targetLabel,refLabel:choice.resolution==='LINK_EXISTING' ? null : source.refLabel};
          manual.set(key,next);working=new Map(working);working.set(key,next);
          clear();find('[data-pairing-status]').textContent='人工草稿已暂存于本弹窗；须重新核验完整映射。关闭弹窗会丢弃，不会保存或入账。';
        }});
      local.signal.addEventListener('abort',()=>{if(child.open) child.close();},{once:true});
    };});
    controls();
  };
  find('[data-pairing-kind]').onchange=()=>{generation++;reader?.abort();busy=false;clear();effect();find('[data-pairing-status]').textContent='策略已变化，请重新核验完整范围';};
  find('[data-pairing-filter]').onchange=()=>{page=0;paint();};
  find('[data-pairing-prev]').onclick=()=>{if(!busy && page>0){page--;paint();}};
  find('[data-pairing-next]').onclick=()=>{if(!busy){page++;paint();}};
  find('[data-pairing-all]').onclick=()=>{if(!scopeCurrent() || busy || !result) return;result.items.filter(item=>item.state==='SUGGESTED').forEach(item=>confirmed.add(locator(item.row)));ackReset();paint();};
  find('[data-pairing-none]').onclick=()=>{if(!scopeCurrent() || busy) return;confirmed.clear();ackReset();paint();};
  find('[data-pairing-stop]').onclick=()=>reader?.abort();
  find('[data-pairing-read]').onclick=async()=>{
    if(!scopeCurrent() || busy) {find('[data-pairing-status]').textContent='冻结范围已变化，请关闭后重新核对';return;}
    const issued=++generation, strategy=kind(), readScope=working;
    reader=new AbortController();const issuedReader=reader, cancelRead=()=>issuedReader.abort();
    local.signal.addEventListener('abort',cancelRead,{once:true});clear();busy=true;controls();
    find('[data-pairing-status]').textContent='正在完整只读核验；不保存、不入账，也不会自动选择唯一建议';
    try {
      const next=await readImportBinding(readSignal=>request(`/paam/import/v1/preview/${token}/pairing-preview`,{method:'POST',signal:readSignal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_updated_time:time,preview_digest:digest,kind:strategy,
          choices:[...readScope.values()].map(item=>({...item.choice,file_id:item.row.file_id,source_row_number:item.row.source_row_number}))})}),
        {signal:issuedReader.signal,valid:()=>scopeCurrent() && issued===generation && working===readScope && kind()===strategy,label:'完整批配对核验'});
      if(!scopeCurrent() || issued!==generation || working!==readScope || kind()!==strategy) return;
      result=validateImportPairing(next,working,strategy,digest,time);page=0;paint();
      find('[data-pairing-status]').textContent='完整映射已展示，建议默认未选。可选择多个已核对具名建议；歧义项单独核对后重新核验。';
    } catch(error) {
      if(alive() && issued===generation) {clear();find('[data-pairing-status]').textContent=`${error.code || '批配对核验未完成'}：${error.message}；原选择不变，未保存或入账。`;}
    } finally {local.signal.removeEventListener('abort',cancelRead);if(alive() && issued===generation){busy=false;controls();if(result) paint();}}
  };
  find('[data-pairing-apply]').onclick=()=>{
    if(!alive() || busy || !result) return;
    try {
      if(!scopeCurrent()) throw new Error('冻结行、锚点或选择已变化；本次草稿全部不应用');
      if(!find('[data-pairing-ack]').checked) throw new Error('请明确确认选中的具名映射及人工草稿');
      const projection=projectImportPairing(frozen,result,confirmed,kind(),digest,time,manual,files);
      if(!projection.modified) throw new Error('尚未确认任何映射或人工草稿');
      if(projection.unchanged && !find('[data-pairing-retain-ack]').checked) throw new Error('请明确其余行保留原选择和决定，仍需处理例外');
      apply(projection.selected,{modified:projection.modified,unchanged:projection.unchanged});dialog.close();
    } catch(error){find('[data-pairing-status]').textContent=error.message;}
  };
  effect();controls();return dialog;
}

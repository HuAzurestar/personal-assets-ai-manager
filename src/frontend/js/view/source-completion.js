import {request} from '../api/client.js';
import {esc,money,date,resourceId,zonedISOString,typeNames} from '../util/core.js';
import {unitChoices,unitLabel} from '../util/unit-dictionary.js';
import {namedChoice,bindNamedChoice,metadataLabel,mountPicker,workbenchDialog} from '../component/workbench.js';
import {dateTimeRangeControl,bindDateTimeRanges} from '../component/date-time-range.js';
import {accountCorrectionMarkup} from '../util/account-correction.js';
import {financialStateLabel} from '../util/financial-copy.js';
import {sourceCompletionFilter,sourceCompletionPage,sourceCompletionSelection,sourceCompletionWhole,
  sourceCompletionIntent,sourceCompletionPlan,boundedSourceRead,sourceCompletionOriginals} from '../util/source-completion.js';

const base='/paam/ledger/v1';

export async function mountSourceCompletion(host, params, {signal,publication}) {
  const route=location.hash,valid=()=>host.isConnected && !signal.aborted && location.hash===route;
  let selected=new Map(),result,appliedScope,reading=false,currentReading=false,published=false,generation=0,selectionGeneration=0,readController;
  let duplicateIds=[];const keepers=new Map(),labels=new Map([[0,'来源未识别']]);
  host.innerHTML=`<section class="panel account-correction-shell source-completion-shell" data-source-completion><h1>补齐历史流水来源</h1>
    <p>只选择当前有效且来源未识别的流水，一次指定来源卡。不会按昵称、尾号或交易对手猜卡，也不会创建个人或集合。</p>
    <button type="button" data-source-back>返回上一页（保留列表位置）</button>
    <form class="stack" data-source-completion-form>
      <section><h2>1. 选择待补齐范围</h2><div class="source-completion-filter" data-source-filter>
        <label>收支方向<select name="direction"><option value="">全部方向</option><option value="IN">收入</option><option value="OUT">支出</option></select></label>
        <label>币种<select name="currency"><option value="">全部币种</option>${unitChoices('CURRENCY').map(row=>`<option value="${esc(row.code)}">${esc(row.code)} · ${esc(unitLabel(row.code))}</option>`).join('')}</select></label>
        ${dateTimeRangeControl()}<button type="button" data-source-search>查找待补齐流水</button>
      </div><p role="status" data-source-read-status></p><p data-source-count aria-live="polite">尚未读取范围。</p>
      <div class="actions"><button type="button" data-source-select-page disabled>选择本页</button><button type="button" data-source-select-all disabled>选择当前筛选全部（最多100条）</button><button type="button" data-source-clear>清空本次选择</button></div>
      <small>跨页选择保留；更换已应用筛选会清空旧选择。每次最多100条，整组与数量依赖超限时由预览明确阻断，不静默处理部分。预览只针对本次明确选择。</small>
      <div data-source-items></div><div class="actions"><button type="button" data-source-prev disabled>上一页</button><span data-source-page></span><button type="button" data-source-next disabled>下一页</button></div>
      <details><summary>本次已选流水（完整清单）</summary><div data-source-selected></div></details></section>
      <section><h2>2. 一次指定来源卡</h2>${namedChoice('account_ref_id','补齐至来源卡',{text:'请选择，不默认绑定',pick:'选择来源卡'})}</section>
      <section data-source-keepers hidden><h2>重复证据需要重新明确保留交易</h2><p>完整依赖组中的重复证据不能猜测保留对象；选择后重新预览，不会自动排除真实转账。</p><div data-source-keeper-items></div></section>
      <section data-review-step="preview"><h2>3. 核对整组影响并确认</h2><p role="status" data-review-status>尚未写入。选范围和来源卡后预览。</p><div data-review-impact></div>
        <div class="actions"><button type="button" data-review-preview>预览批量补齐（不入账）</button><button type="submit" data-review-command disabled>确认补齐所选来源</button></div></section>
      <section><button type="button" data-source-current data-preview-readonly>读取所选当前状态（不重发）</button><div data-source-current-result aria-live="polite"></div></section>
    </form></section>`;
  const form=host.querySelector('form'),readStatus=form.querySelector('[data-source-read-status]');
  const locked=()=>published || invalidate.state().writing || invalidate.state().uncertain;
  const scope=()=>({direction:form.elements.direction.value,currency:form.elements.currency.value,
    start:form.elements.date_from.value ? zonedISOString(form.elements.date_from.value) : '',
    end:form.elements.date_to.value ? zonedISOString(form.elements.date_to.value,true) : ''});
  const fingerprint=value=>JSON.stringify(value);
  const describe=row=>`${row.summary || '未命名交易'} · ${money(row)} ${row.cash_currency_code} · ${row.cash_direction==='IN' ? '收入' : '支出'} · ${date(row.occurred_time)}`;
  const paint=()=>{
    form.querySelector('[data-source-count]').textContent=`当前已应用筛选 ${result?.total ?? '未读取'} 条 · 本页 ${result?.items.length ?? 0} 条 · 本次明确已选 ${selected.size} 条（跨页）`;
    form.querySelector('[data-source-items]').innerHTML=(result?.items || []).map(row=>`<article class="source-completion-row" data-source-ledger="${row.id}">
      <input type="checkbox" data-source-select="${row.id}" aria-label="选择流水 ${row.id}" ${selected.has(row.id) ? 'checked' : ''} ${locked() || reading ? 'disabled' : ''}>
      <span><strong>${esc(row.summary || '未命名交易')}</strong><small>${esc(date(row.occurred_time))} · ${esc(typeNames[row.economic_type])} · 来源未识别</small></span>
      <span>${esc(money(row))} ${esc(row.cash_currency_code)}<small>${row.cash_direction==='IN' ? '收入' : '支出'}</small></span>
      <small>流水 #${row.id}<br>交易 #${row.transaction_id}</small></article>`).join('') || '<p>此范围没有待补齐流水，或尚未读取。</p>';
    const pages=result ? Math.max(1,Math.ceil(result.total/result.page_size)) : 1;
    form.querySelector('[data-source-page]').textContent=result ? `第 ${result.page_index} / ${pages} 页` : '';
    for (const [key,disabled] of [['prev',!result || result.page_index<=1],['next',!result || result.page_index>=pages],
      ['select-page',!result?.items.length],['select-all',!result?.total]])
      form.querySelector(`[data-source-${key}]`).disabled=locked() || reading || disabled;
    form.querySelector('[data-source-selected]').innerHTML=[...selected.values()].map(row=>`<p>${esc(describe(row))} · 流水 #${row.id} · 交易 #${row.transaction_id}</p>`).join('') || '<p>没有选中流水。</p>';
    form.querySelectorAll('[data-source-select]').forEach(input=>{input.onchange=()=>{
      if (!valid() || locked() || reading) {paint();return;}
      try {selected=sourceCompletionSelection(selected,[result.items.find(row=>row.id===resourceId(input.dataset.sourceSelect))],!input.checked);selectionChanged();}
      catch(error) {readStatus.textContent=error.message;paint();}
    };});
  };
  const selectionChanged=()=>{++selectionGeneration;duplicateIds=[];keepers.clear();form.querySelector('[data-source-keepers]').hidden=true;
    form.querySelector('[data-source-current-result]').textContent=currentReading ? '本次选择已变更，请重新读取当前状态；迟到结果不会覆盖新范围。' : '';
    invalidate();form.querySelector('[data-review-impact]').innerHTML='';paint();};
  const updateKeepers=ids=>{
    duplicateIds=[...new Set(ids.map(id=>resourceId(id)))];
    for (const id of keepers.keys()) if (!duplicateIds.includes(id)) keepers.delete(id);
    const section=form.querySelector('[data-source-keepers]');section.hidden=!duplicateIds.length;
    section.querySelector('[data-source-keeper-items]').innerHTML=duplicateIds.map(id=>`<p>重复交易 #${id} → <span data-source-kept="${id}">${esc(keepers.has(id) ? describe(keepers.get(id)) : '尚未选择保留交易')}</span> <button type="button" data-source-pick="${id}">查找保留交易</button></p>`).join('');
    section.querySelectorAll('[data-source-pick]').forEach(button=>{button.onclick=()=>{
      if (!valid() || locked() || reading) return;
      const id=resourceId(button.dataset.sourcePick),issued=selectionGeneration;
      const picker=workbenchDialog('明确保留的真实交易','<div data-source-kept-picker></div>');
      signal.addEventListener('abort',()=>{if (picker.open) picker.close();},{once:true});
      mountPicker(picker.querySelector('[data-source-kept-picker]'),{url:`${base}/candidate`,searchKeys:['summary'],signal,describe,
        choose:row=>{if (valid() && !locked() && !reading && issued===selectionGeneration && duplicateIds.includes(id)) {
          keepers.set(id,row);updateKeepers(duplicateIds);invalidate();}picker.close();}});
    };});
  };
  const invalidate=publication(form,()=>{
    if (published || reading) throw new Error('已发布或正在读取范围；不能提交旧选择。');
    return sourceCompletionIntent(selected,form.elements.account_ref_id.value,duplicateIds,keepers);
  },new Map(),async response=>{
    published=true;form.querySelector('[data-review-command]').disabled=true;
    form.querySelector('[data-review-status]').textContent=`所选 ${selected.size} 条来源补齐已发布，完整新事项 ${response.created_reviews.length} 个；旧证据保留。读取当前状态或返回，不重发。`;
    paint();
  },new Map(),()=>new Map(),{
    confirmation:'请核对本次选择、目标来源卡、完整现金/数量/标签与依赖影响，再确认补齐。',
    render:async plan=>{
      if (plan.blocking_issues.length) {
        const required=plan.blocking_issues.find(row=>row.code==='ACCOUNT_CORRECTION_KEEPER_REQUIRED');
        if (required?.details?.duplicate_transaction_ids && valid()) updateKeepers(required.details.duplicate_transaction_ids);
        return accountCorrectionMarkup(plan,new Map(),labels);
      }
      const originals=await sourceCompletionOriginals(plan,signal);
      if (!valid()) return '';
      sourceCompletionPlan(plan,selected,form.elements.account_ref_id.value);
      return `<p>本次明确选择 ${selected.size} 条流水，来源未识别 → ${esc(labels.get(resourceId(form.elements.account_ref_id.value)) || '所选来源卡')}。</p>${accountCorrectionMarkup(plan,originals,labels)}`;
    }});
  bindNamedChoice(form,'account_ref_id',{url:`${base}/account-ref`,title:'选择补齐来源卡',signal,initialize:false,
    canChange:()=>valid() && !locked() && !reading,filter:{key:'status',op:'=',val:'ACTIVE'},changed:row=>{if(row)labels.set(row.id,metadataLabel(row));}});

  async function read(page=1,whole=false) {
    if (!valid() || locked() || reading) return;
    let nextScope;
    try {nextScope=scope();sourceCompletionFilter(nextScope);}catch(error){readStatus.textContent=error.message;return;}
    const issued=++generation,signature=fingerprint(nextScope);reading=true;
    readController?.abort();readController=new AbortController();invalidate();paint();
    readStatus.textContent=whole ? '完整读取当前筛选（不入账）…' : '读取当前有效且来源未识别的流水…';
    try {
      const size=whole ? 100 : 20;
      const query=new URLSearchParams({page_index:String(page),page_size:String(size),filter:JSON.stringify(sourceCompletionFilter(nextScope)),sorter:JSON.stringify([{key:'id',direction:'asc'}])});
      const next=await boundedSourceRead(readSignal=>request(`${base}/flow/list?${query}`,{signal:readSignal}),readController.signal);
      if (!valid() || issued!==generation || fingerprint(scope())!==signature) return;
      sourceCompletionPage(next,page,size);
      const all=whole ? sourceCompletionWhole(next) : null;
      const scopeChanged=!appliedScope || fingerprint(appliedScope)!==signature;
      const baseSelection=scopeChanged ? new Map() : selected;
      const nextSelection=whole ? sourceCompletionSelection(baseSelection,all) : baseSelection;
      selected=nextSelection;appliedScope=nextScope;result=next;
      if (whole || scopeChanged) selectionChanged();
      readStatus.textContent=whole ? `已完整选择当前筛选 ${all.length} 条，没有写入。` : '只列出当前有效且来源未识别的流水；不会隐藏破损引用。';
    } catch(error) {if(valid() && issued===generation && error.name!=='AbortError') readStatus.textContent=`${error.code || '读取失败'}：${error.message}；原选择未变，没有应用部分范围。`;}
    finally {if(issued===generation){reading=false;if(valid())paint();}}
  }
  const cancelRead=()=>{if(locked())return;++generation;readController?.abort();reading=false;
    readStatus.textContent='筛选条件已变更；请重新查找。当前选择不会自动扩展到新范围。';paint();};
  form.querySelector('[data-source-filter]').addEventListener('input',cancelRead);
  form.querySelector('[data-source-filter]').addEventListener('change',cancelRead);
  bindDateTimeRanges(form,cancelRead);
  signal.addEventListener('abort',()=>readController?.abort(),{once:true});
  form.querySelector('[data-source-search]').onclick=()=>read();
  form.querySelector('[data-source-prev]').onclick=()=>read(result.page_index-1);
  form.querySelector('[data-source-next]').onclick=()=>read(result.page_index+1);
  form.querySelector('[data-source-select-all]').onclick=()=>read(1,true);
  form.querySelector('[data-source-select-page]').onclick=()=>{
    if(!valid() || locked() || reading || !result)return;
    try{selected=sourceCompletionSelection(selected,result.items);selectionChanged();readStatus.textContent='本页已加入本次明确选择，尚未写入。';}
    catch(error){readStatus.textContent=error.message;}
  };
  form.querySelector('[data-source-clear]').onclick=()=>{if(valid() && !locked() && !reading){selected=new Map();selectionChanged();}};
  form.querySelector('[data-source-current]').onclick=async()=>{
    if(!valid() || currentReading || invalidate.state().writing)return;
    const output=form.querySelector('[data-source-current-result]'),button=form.querySelector('[data-source-current]');
    if(!selected.size){output.textContent='没有本次选择；请先选择待补齐流水。';return;}
    currentReading=true;button.disabled=true;output.textContent='只读核对所选原流水及交易的当前事项…';
    const rows=[...selected.values()],issued=selectionGeneration;
    try{
      const state=await boundedSourceRead(async readSignal=>{
        const flows=[],reviews=new Map();let next=0;
        await Promise.all(Array.from({length:Math.min(6,rows.length)},async()=>{
          while(next<rows.length){const row=rows[next++];
            const [flow,candidates]=await Promise.all([request(`${base}/flow/${row.id}`,{signal:readSignal}),
              request(`${base}/candidate/list?${new URLSearchParams({page_size:'1',filter:JSON.stringify({key:'id',op:'=',val:row.transaction_id})})}`,{signal:readSignal})]);
            if(flow.ledger_entry.id!==row.id || candidates.total!==1 || candidates.items.length!==1 || candidates.items[0].transaction_id!==row.transaction_id)
              throw new Error('所选原流水与当前交易无法完整定位，未判定结果。');
            flows.push({row,active:flow.active});
            for(const current of candidates.items[0].current_reviews)reviews.set(resourceId(current.id),current);
          }
        }));
        if(reviews.size>100)throw new Error('当前完整事项超过读取预算；未返回部分状态。');
        const originals=reviews.size ? await sourceCompletionOriginals({new_reviews:[...reviews.keys()].map(id=>({source_review_id:id}))},readSignal) : new Map();
        return {flows,originals};
      },signal);
      if(!valid() || issued!==selectionGeneration)return;
      output.innerHTML=`<p>完整只读核对 ${state.flows.length} 条原流水；没有重发命令，也不据此解锁未知提交。</p>
        ${state.flows.sort((a,b)=>a.row.id-b.row.id).map(item=>`<p>流水 #${item.row.id} · ${esc(item.row.summary)}：${item.active ? '仍有效' : '已停用，内容保留'}</p>`).join('')}
        ${[...state.originals.values()].map(row=>`<section><h3>${esc(row.title || '当前事项')} · ${esc(financialStateLabel('review',row.status))}</h3><ul class="account-correction-list">${row.ledger_entries.map(flow=>`<li>${esc(money(flow))} ${esc(flow.cash_currency_code)} · ${flow.cash_direction==='IN' ? '收入' : '支出'} · ${esc(labels.get(flow.account_ref_id) || (flow.account_ref_id ? `来源卡 #${flow.account_ref_id}` : '来源未识别'))} · 流水 #${flow.id}</li>`).join('')}</ul></section>`).join('')}`;
    }catch(error){if(valid() && issued===selectionGeneration && error.name!=='AbortError')output.textContent=`查询失败：${error.message}；没有推断提交结果或重发。`;}
    finally{currentReading=false;if(valid())button.disabled=false;}
  };
  host.querySelector('[data-source-back]').onclick=()=>history.back();
  paint();await read();
}

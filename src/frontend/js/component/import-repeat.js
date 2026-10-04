import {request} from '../api/client.js';
import {esc,money,date,resourceId} from '../util/core.js';
import {workbenchDialog} from './workbench.js';
import {importRowIdentity,importIntentChoice} from './import-choice.js';
import {mountImportDraftRows} from './import-bulk.js';
import {readImportBinding} from './import-binding.js';
import {sameImportPairingScope} from './import-pairing.js';

const locator = row => {
  if (typeof row?.file_id !== 'number' || typeof row?.source_row_number !== 'number') throw new Error('来源行定位不精确');
  resourceId(row.file_id);resourceId(row.source_row_number);
  return importRowIdentity(row);
};
const timeKey = value => {
  const found=typeof value==='string' && /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  if(!found) throw new Error('重复组时间不是完整UTC时间');
  return `${found[1]}.${(found[2] || '').padEnd(6,'0')}Z`;
};
const sameCore = (a,b) => a && b && Number.isSafeInteger(a.amount) && a.amount>0 && a.amount===b.amount
  && typeof a.currency_code==='string' && !!a.currency_code && a.currency_code===b.currency_code
  && ['IN','OUT'].includes(a.cash_direction) && a.cash_direction===b.cash_direction
  && timeKey(a.occurred_time)===timeKey(b.occurred_time);
const mutable = item => item.row.classification==='NEW' && item.row.persisted_row_status!==1
  && !item.row.existing_transaction_id && !item.row.issue_codes?.length
  && (![2,3].includes(item.row.persisted_row_status) || item.choice.recheck===true)
  && (item.choice.resolution || 'AUTO')==='AUTO' && !item.choice.target;

export function validateImportRepeat(result,selected,digest,time) {
  if(!(selected instanceof Map) || !selected.size || selected.size>20000 || typeof time!=='string' || !time
      || result?.source_preview_digest!==digest || result.expected_updated_time!==time
      || result.selected_count!==selected.size || !Array.isArray(result.groups) || result.groups.length>10000
      || !Array.isArray(result.items) || result.items.length!==selected.size)
    throw new Error('整组核验范围或前提不一致；未应用草稿');
  const items=new Map(), grouped=new Map();
  for(const item of result.items) {
    const key=locator(item.row), source=selected.get(key);
    if(!source || locator(source.row)!==key || items.has(key) || !Array.isArray(item.reason_codes)
        || item.reason_codes.some(code=>typeof code!=='string' || !code)) throw new Error('整组核验不完整或来源重复');
    items.set(key,item);
  }
  for(const group of result.groups) {
    const keeper=locator(group.keeper_row), files=new Set(), members=[group.keeper_row,...(group.repeated_rows || [])];
    if(group.kind!=='SUSPECTED_EXPORT' || !Array.isArray(group.repeated_rows) || !group.repeated_rows.length
        || typeof group.source_label_masked!=='string' || typeof group.parsed?.summary!=='string') throw new Error('重复组建议格式不正确');
    for(const row of members) {
      const key=locator(row), source=selected.get(key);
      if(!source || grouped.has(key) || files.has(row.file_id) || row.file_id<group.keeper_row.file_id
          || row.file_id===group.keeper_row.file_id && row.source_row_number<group.keeper_row.source_row_number
          || !mutable(source) || !sameCore(source.row.parsed,group.parsed)) throw new Error('重复组必须完整、互不重叠且保留稳定首份；不能覆盖原明确意图');
      files.add(row.file_id);grouped.set(key,keeper);
    }
  }
  for(const [key,item] of items) {
    if(item.state==='GROUPED') {
      if(!grouped.has(key) || !item.keeper_row || locator(item.keeper_row)!==grouped.get(key) || item.reason_codes.length)
        throw new Error('重复组成员或保留行不一致');
    } else if(!['UNCHANGED','EXCEPTION'].includes(item.state) || grouped.has(key) || item.keeper_row!==null || !item.reason_codes.length)
      throw new Error('例外必须显式保留，不能静默加入重复组');
  }
  return result;
}

// Only explicitly selected complete groups change local intent. No POST write
// or consent derives from a recommendation; existing preflight remains required.
export function projectImportRepeat(selected,result,confirmed,acknowledge,digest,time) {
  validateImportRepeat(result,selected,digest,time);
  if(!(confirmed instanceof Set) || confirmed.size && acknowledge!==true) throw new Error('请明确确认重复导出和首份真实交易');
  const groups=new Map(result.groups.map(group=>[locator(group.keeper_row),group])), updated=new Map(selected);
  let modified=0;
  for(const key of confirmed) {
    const group=groups.get(key);
    if(!group) throw new Error('确认范围包含未展示的完整重复组');
    for(const row of [group.keeper_row,...group.repeated_rows]) {
      const member=locator(row), previous=selected.get(member), keeper=member===key;
      const choice=keeper ? importIntentChoice(previous.row,previous.choice,'NEW',null,true)
        : {...previous.choice,decision:'SKIP',resolution:'AUTO',target:null,acknowledge_new_risk:false};
      updated.set(member,{...previous,choice,targetLabel:null});modified++;
    }
  }
  return {selected:updated,modified,groups:confirmed.size,unchanged:selected.size-modified};
}

const reasons={ROWS_ALREADY_PROCESSED:'已接受行只读',ROW_RECHECK_REQUIRED:'旧跳过／问题行需先明确重查',
  USER_INTENT_RETAINED:'保留原明确处理意图',SOURCE_IDENTITY_REQUIRED:'完整本方身份不足，先按文件确认来源',
  REPEAT_SCOPE_INCOMPLETE:'筛选未包含完整组，请清除文件／分类筛选后核对',IDENTITY_AMBIGUOUS:'同文件内有多笔相同交易，不能自动保留一份',
  EXTERNAL_CANDIDATE_REQUIRES_REVIEW:'已有入账候选，不能再建首份现金；请单独核对或具名补证据',
  GROUP_REQUIRES_REVIEW:'组内有例外，整组保留原决定',NO_REPEAT_IN_SCOPE:'没有符合条件的重复导出组',
  CANONICAL_IDENTITY_RETAINED:'已有可靠交易号，沿用默认接受／补证据策略'};

export function openImportRepeat({selected,current=()=>selected,files,token,digest,time,signal,valid,apply}) {
  const frozen=new Map([...selected].map(([key,item])=>[key,structuredClone(item)])), confirmed=new Set();
  const label=row=>`${files.find(file=>file.file_id===row.file_id)?.filename || `文件 #${row.file_id}`} · 第 ${row.source_row_number} 行`;
  const dialog=workbenchDialog('处理重复导出的账单', `<div class="import-choice"><p>核对同一来源的重复导出：每组保留首份，其他份跳过并保留原证据。没有交易号时，仅相同字段不证明重复，需要你确认。</p><p>完整选择 ${frozen.size} 行，来自 ${new Set([...frozen.values()].map(item=>item.row.file_id)).size} 个文件；原明确意图和例外不会自动改动。</p><div class="actions"><button type="button" data-repeat-read>重新核对整组（不保存、不入账）</button><button type="button" data-repeat-stop disabled>停止核对</button></div><p role="status" data-repeat-status></p><p data-repeat-count></p><div class="actions"><button type="button" data-repeat-all disabled>选择全部建议组</button><button type="button" data-repeat-none disabled>清除组选择</button></div><div data-repeat-groups></div><div class="actions"><button type="button" data-repeat-prev disabled>上一页</button><span data-repeat-page></span><button type="button" data-repeat-next disabled>下一页</button></div><details><summary>未纳入建议组的行（保留原决定）</summary><div data-repeat-exceptions></div></details><details><summary>完整所选范围</summary><div data-repeat-scope></div></details><label><input type="checkbox" data-repeat-ack>我确认所选组是重复导出，首份确为真实交易；接受首份、跳过其余份。其余行保留原决定，已有入账候选另行处理。</label><button type="button" data-repeat-apply disabled>应用整组草稿，下一步核验导入</button></div>`);
  dialog.classList.add('import-choice-dialog');
  const find=selector=>dialog.querySelector(selector),local=new AbortController();
  let result,reader,busy=false,generation=0,page=0;
  const alive=()=>dialog.isConnected && !local.signal.aborted;
  const currentScope=()=>alive() && valid() && sameImportPairingScope(frozen,current());
  const abort=()=>{local.abort();reader?.abort();if(dialog.open) dialog.close();};
  signal.addEventListener('abort',abort,{once:true});
  dialog.addEventListener('close',()=>{local.abort();reader?.abort();signal.removeEventListener('abort',abort);},{once:true});
  mountImportDraftRows(find('[data-repeat-scope]'),[...frozen.values()],item=>label(item.row),local.signal);
  const controls=()=>{
    find('[data-repeat-read]').disabled=busy;find('[data-repeat-stop]').disabled=!busy;
    find('[data-repeat-all]').disabled=busy || !result?.groups.length;
    find('[data-repeat-none]').disabled=busy || !confirmed.size;
    find('[data-repeat-ack]').disabled=busy || !result?.groups.length;
    find('[data-repeat-apply]').disabled=busy || !confirmed.size || !find('[data-repeat-ack]').checked;
    const chosen=result?.groups.filter(group=>confirmed.has(locator(group.keeper_row))) || [];
    find('[data-repeat-count]').textContent=result ? `建议 ${result.groups.length} 组；已选 ${confirmed.size} 组：接受 ${chosen.length} 行，跳过 ${chosen.reduce((sum,group)=>sum+group.repeated_rows.length,0)} 行。未选组与例外保留原决定。` : '正在核对完整范围；建议不会自动选中。';
  };
  const paint=()=>{
    if(!alive() || !result) return;
    page=Math.min(page,Math.max(0,Math.ceil(result.groups.length/20)-1));
    const start=page*20;
    find('[data-repeat-groups]').innerHTML=result.groups.slice(start,start+20).map(group=>{
      const key=locator(group.keeper_row);
      return `<article class="preview-file-card"><label><input type="checkbox" data-repeat-group="${key}" ${confirmed.has(key)?'checked':''} ${busy?'disabled':''}>${esc(group.parsed.summary || '摘要未知')} · ${esc(money(group.parsed))} ${esc(group.parsed.cash_direction)}</label><small>${esc(group.source_label_masked)} · ${esc(date(group.parsed.occurred_time))}</small><p>接受：${esc(label(group.keeper_row))}</p><details><summary>跳过 ${group.repeated_rows.length} 份（保留原证据）</summary><div data-repeat-members="${key}"></div></details></article>`;
    }).join('') || '<p>没有可整组处理的建议。请查看未纳入建议组的原因；原决定保持不变。</p>';
    for(const group of result.groups.slice(start,start+20)) {
      const key=locator(group.keeper_row);
      mountImportDraftRows(find(`[data-repeat-members="${key}"]`),group.repeated_rows,label,local.signal);
    }
    dialog.querySelectorAll('[data-repeat-group]').forEach(node=>{node.onchange=()=>{
      if(!currentScope() || busy) return;
      if(node.checked) confirmed.add(node.dataset.repeatGroup);else confirmed.delete(node.dataset.repeatGroup);
      find('[data-repeat-ack]').checked=false;controls();
    };});
    find('[data-repeat-prev]').disabled=busy || page===0;
    find('[data-repeat-next]').disabled=busy || start+20>=result.groups.length;
    find('[data-repeat-page]').textContent=`${result.groups.length ? start+1 : 0}–${Math.min(start+20,result.groups.length)} / ${result.groups.length} 组`;
    controls();
  };
  find('[data-repeat-ack]').onchange=controls;
  find('[data-repeat-all]').onclick=()=>{if(!currentScope() || busy || !result) return;result.groups.forEach(group=>confirmed.add(locator(group.keeper_row)));find('[data-repeat-ack]').checked=false;paint();};
  find('[data-repeat-none]').onclick=()=>{if(!currentScope() || busy) return;confirmed.clear();find('[data-repeat-ack]').checked=false;paint();};
  find('[data-repeat-prev]').onclick=()=>{if(!busy && page>0){page--;paint();}};
  find('[data-repeat-next]').onclick=()=>{if(!busy){page++;paint();}};
  find('[data-repeat-stop]').onclick=()=>reader?.abort();
  find('[data-repeat-read]').onclick=async()=>{
    if(!currentScope() || busy) return;
    const issued=++generation;result=null;confirmed.clear();page=0;busy=true;find('[data-repeat-ack]').checked=false;
    find('[data-repeat-groups]').textContent='';find('[data-repeat-exceptions]').textContent='';controls();
    find('[data-repeat-status]').textContent='正在完整只读核对；没有保存决定或写入现金。';
    reader=new AbortController();const issuedReader=reader,cancel=()=>issuedReader.abort();
    local.signal.addEventListener('abort',cancel,{once:true});
    try {
      const next=await readImportBinding(readSignal=>request(`/paam/import/v1/preview/${token}/repeat-preview`,{method:'POST',signal:readSignal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_updated_time:time,preview_digest:digest,
          choices:[...frozen.values()].map(item=>({...item.choice,file_id:item.row.file_id,source_row_number:item.row.source_row_number}))})}),
        {signal:issuedReader.signal,valid:()=>currentScope() && issued===generation,label:'完整重复导出核对'});
      if(!currentScope() || issued!==generation) return;
      result=validateImportRepeat(next,frozen,digest,time);paint();
      const other=result.items.filter(item=>item.state!=='GROUPED');
      mountImportDraftRows(find('[data-repeat-exceptions]'),other,item=>`${label(item.row)}：${item.reason_codes.map(code=>reasons[code] || code).join('；')}`,local.signal);
      find('[data-repeat-status]').textContent=`完整核对 ${result.selected_count} 行；${other.length} 行未纳入建议组，保留原决定。请选择已核实的组并明确确认。`;
    } catch(error) {
      if(alive() && issued===generation) {result=null;find('[data-repeat-status]').textContent=`${error.code || '整组核对未完成'}：${error.message}；原决定不变，未保存或入账。`;}
    } finally {local.signal.removeEventListener('abort',cancel);if(alive() && issued===generation){busy=false;controls();if(result) paint();}}
  };
  find('[data-repeat-apply]').onclick=()=>{
    if(!alive() || busy || !result) return;
    try {
      if(!currentScope()) throw new Error('原选择、意图或预览已经改变，请关闭后重新核对；未应用草稿');
      const projection=projectImportRepeat(frozen,result,confirmed,find('[data-repeat-ack]').checked,digest,time);
      if(!projection.modified) throw new Error('请先选择已核实的完整组');
      apply(projection.selected,projection);dialog.close();
    } catch(error) {find('[data-repeat-status]').textContent=error.message;}
  };
  controls();void find('[data-repeat-read]').onclick();return dialog;
}

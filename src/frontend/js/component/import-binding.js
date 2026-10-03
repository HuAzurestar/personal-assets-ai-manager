import {request} from '../api/client.js';
import {resourceId} from '../util/core.js';
import {workbenchDialog,mountPicker,metadataLabel} from './workbench.js';
import {importRowIdentity,importRowLabel} from './import-choice.js';
import {mountImportDraftRows} from './import-bulk.js';

export function validateImportBinding(result, selected, ref, digest, time) {
  if (ref !== null) {
    if (typeof ref !== 'number') throw new Error('来源策略定位不精确');
    resourceId(ref,{allowZero:true});
  }
  if (typeof time !== 'string' || !time || !selected.size || selected.size > 20000
      || result?.source_preview_digest !== digest || result.expected_updated_time !== time
      || result.account_ref_id !== ref || result.selected_count !== selected.size || !Array.isArray(result.items)
      || result.items.length !== selected.size) throw new Error('来源绑定核验范围或目标不一致，未应用草稿');
  const seen = new Set();
  for (const item of result.items) {
    if (typeof item.row?.file_id !== 'number' || typeof item.row?.source_row_number !== 'number') throw new Error('来源行定位不精确');
    resourceId(item.row.file_id);resourceId(item.row.source_row_number);
    const key = importRowIdentity(item.row);
    if (!selected.has(key) || importRowIdentity(selected.get(key).row) !== key || seen.has(key) || typeof item.applicable !== 'boolean'
        || !['RELIABLE','UNKNOWN'].includes(item.source_state) || !Array.isArray(item.reason_codes)
        || item.reason_codes.some(code => typeof code !== 'string' || !code)
        || item.applicable === !!item.reason_codes.length) throw new Error('来源绑定核验不完整或例外状态不一致');
    seen.add(key);
  }
  return result;
}

export function projectImportBinding(selected,result,ref,label,digest,time) {
  validateImportBinding(result,selected,ref,digest,time);
  const updated = new Map(selected);
  for (const item of result.items) {
    if (!item.applicable) continue;
    const key = importRowIdentity(item.row), previous = selected.get(key);
    // Even a malformed positive backend result cannot bind an existing Fact
    // or a local LINK intent. Never alter decisions, risk consent or pair targets.
    if (['PROCESSED','EXISTING'].includes(previous.row.classification) || previous.row.persisted_row_status === 1
        || previous.row.existing_transaction_id
        || previous.choice.resolution === 'LINK_EXISTING') throw new Error('已接受或补证据行不能覆盖来源');
    updated.set(key,{...previous,refLabel:ref ? label : null,choice:{...previous.choice,account_ref_id:ref,
      ...(previous.choice.target ? {target:{...previous.choice.target}} : {})}});
  }
  return updated;
}

// Bound the complete readonly request, including a transport that ignores abort.
// A partial/late/failed check cannot make any row applicable or change choices.
export async function readImportBinding(read,{signal,valid=()=>true,timers=globalThis,label='完整来源核验'}={}) {
  const controller = new AbortController();
  let reject;
  const interrupted = new Promise((_resolve,no) => {reject=no;});
  const cancelled = () => Object.assign(new Error(`${label}已取消或预览变化`),{name:'AbortError'});
  const stop = error => {controller.abort();reject(error);};
  const abort = () => stop(cancelled());
  if (signal?.aborted || !valid()) throw cancelled();
  signal?.addEventListener('abort',abort,{once:true});
  const timeout = timers.setTimeout(() => stop(new Error(`${label}超过30秒；原选择不变`)),30000);
  try {
    const result = await Promise.race([Promise.resolve().then(() => read(controller.signal)),interrupted]);
    if (signal?.aborted || !valid()) throw cancelled();
    return result;
  } finally {timers.clearTimeout(timeout);signal?.removeEventListener('abort',abort);controller.abort();}
}

const reasons = {ROWS_ALREADY_PROCESSED:'已接受行只读',EXISTING_ACCOUNT_READ_ONLY:'既有事实或补证据不能覆盖来源',
  ROW_RECHECK_REQUIRED:'旧跳过／问题状态须先明确重查',ACCOUNT_BINDING_CONFLICT:'完整来源身份或同一来源组绑定冲突',
  ACCOUNT_NOT_ACTIVE:'来源卡／集合／个人已停用',ACCOUNT_RELATION_BROKEN:'来源归属关系破损',REFERENCE_NOT_FOUND:'来源卡已不存在',
  ROW_INVALID:'会计核心不完整',FACT_CONFLICT:'来源事实核心冲突',IDENTITY_AMBIGUOUS:'身份不唯一'};

export function openImportBinding({selected,files,token,digest,time,signal,valid,apply}) {
  const frozen = new Map([...selected].map(([key,item]) => [key,{...item,choice:{...item.choice,
    ...(item.choice.target ? {target:{...item.choice.target}} : {})}}]));
  const dialog = workbenchDialog('批量来源绑定（仅修改草稿）',`<div class="import-choice"><p>已选 ${frozen.size} 行，${new Set([...frozen.values()].map(item=>item.row.file_id)).size} 个文件。逐行核对完整来源身份，不按尾号合并；不修改原Fact或当前解释。</p><details><summary>全部已选来源行</summary><div data-binding-scope></div></details><p data-binding-target>尚未选择本次来源策略</p><div class="actions"><button type="button" data-binding-auto>可靠来源自动匹配</button><button type="button" data-binding-zero>明确待绑定0</button></div><details open data-binding-picker-section><summary>选择具名来源卡</summary><div data-binding-picker></div></details><button type="button" data-binding-read disabled>核验全部已选行（不保存、不入账）</button><p role="status" data-binding-status></p><p data-binding-count></p><div data-binding-exceptions></div><label data-binding-exclude hidden><input type="checkbox" data-binding-exclude-ack>已核对例外，仅修改适用范围；例外保留原选择和决定，不自动排除入账</label><label data-binding-unknown hidden><input type="checkbox" data-binding-unknown-ack>明确为未知来源指定新流水归属；这不补造可靠身份或合并事实</label><button type="button" data-binding-apply disabled>确认应用已核验的来源草稿</button></div>`);
  dialog.classList.add('import-choice-dialog');
  const find = selector => dialog.querySelector(selector), local = new AbortController();
  let ref, label='', result, readController, generation=0, busy=false;
  const alive = () => dialog.isConnected && !local.signal.aborted && valid();
  const abort = () => {local.abort();readController?.abort();if(dialog.open) dialog.close();};
  signal.addEventListener('abort',abort,{once:true});
  dialog.addEventListener('close',()=>{local.abort();readController?.abort();signal.removeEventListener('abort',abort);},{once:true});
  mountImportDraftRows(find('[data-binding-scope]'),[...frozen.values()],item=>importRowLabel(item.row,files),local.signal);
  const controls = () => {
    find('[data-binding-read]').disabled = busy || ref === undefined;
    find('[data-binding-apply]').disabled = busy || !result?.items.some(item=>item.applicable);
  };
  const choose = (id,text) => {
    if(!alive()) return;
    generation++;readController?.abort();busy=false;ref=id;label=text;result=null;
    find('[data-binding-target]').textContent = `本次策略：${text}。只改变新流水来源草稿，不改变处理意图或新现金风险确认。`;
    find('[data-binding-exclude-ack]').checked=false;find('[data-binding-unknown-ack]').checked=false;
    find('[data-binding-exclude]').hidden=true;find('[data-binding-unknown]').hidden=true;
    find('[data-binding-status]').textContent='策略已选择，须核验完整范围后再应用';
    find('[data-binding-count]').textContent='';find('[data-binding-exceptions]').textContent='';
    find('[data-binding-picker-section]').open=false;controls();
  };
  find('[data-binding-auto]').onclick=()=>choose(null,'可靠来源自动匹配／否则待绑定');
  find('[data-binding-zero]').onclick=()=>choose(0,'明确待绑定0');
  mountPicker(find('[data-binding-picker]'),{url:'/paam/ledger/v1/account-ref',searchKeys:['display_label'],
    describe:metadataLabel,signal:local.signal,choose:item=>choose(resourceId(item.id),metadataLabel(item))});
  find('[data-binding-read]').onclick=async()=>{
    if(!alive() || busy || ref === undefined) return;
    const issued=++generation, target=ref;
    readController=new AbortController();
    const cancelRead=()=>readController?.abort();local.signal.addEventListener('abort',cancelRead,{once:true});
    busy=true;result=null;find('[data-binding-exclude-ack]').checked=false;find('[data-binding-unknown-ack]').checked=false;
    find('[data-binding-count]').textContent='';find('[data-binding-exceptions]').textContent='';
    find('[data-binding-exclude]').hidden=true;find('[data-binding-unknown]').hidden=true;
    find('[data-binding-status]').textContent='正在只读核验全部已选行；未修改草稿或入账';controls();
    try {
      const next = await readImportBinding(readSignal=>request(`/paam/import/v1/preview/${token}/binding-preview`,{
        method:'POST',signal:readSignal,headers:{'Content-Type':'application/json'},body:JSON.stringify({
          expected_updated_time:time,preview_digest:digest,account_ref_id:target,choices:[...frozen.values()].map(item=>({...item.choice,
            file_id:item.row.file_id,source_row_number:item.row.source_row_number}))})}),
        {signal:readController.signal,valid:()=>alive() && issued===generation && ref===target});
      if(!alive() || issued!==generation || ref!==target) return;
      result=validateImportBinding(next,frozen,target,digest,time);
      const exceptions=result.items.filter(item=>!item.applicable), unknown=result.items.filter(item=>item.applicable && item.source_state==='UNKNOWN');
      find('[data-binding-count]').textContent=`完整核验 ${result.selected_count} 行；可修改 ${result.selected_count-exceptions.length} 行，例外 ${exceptions.length} 行，适用行中来源身份未知 ${unknown.length} 行。仍需保存与完整金融预览。`;
      find('[data-binding-exceptions]').textContent='';
      if(exceptions.length) mountImportDraftRows(find('[data-binding-exceptions]'),exceptions,
        item=>`${importRowLabel(frozen.get(importRowIdentity(item.row)).row,files)}：${item.reason_codes.map(code=>reasons[code] || code).join('、')}`,local.signal);
      find('[data-binding-exclude]').hidden=!exceptions.length;
      find('[data-binding-unknown]').hidden=!(ref>0 && unknown.length);
      find('[data-binding-status]').textContent='全部范围已核验；请确认应用范围和例外。已接受／补证据不会改来源。';
    } catch(error) {
      if(alive() && issued===generation) {result=null;find('[data-binding-count]').textContent='';find('[data-binding-exceptions]').textContent='';
        find('[data-binding-status]').textContent=`${error.code || '来源核验未完成'}：${error.message}；原选择不变，未保存或入账。`;
        find('[data-binding-exclude]').hidden=true;find('[data-binding-unknown]').hidden=true;}
    } finally {local.signal.removeEventListener('abort',cancelRead);if(alive() && issued===generation){busy=false;controls();}}
  };
  find('[data-binding-apply]').onclick=()=>{
    if(!alive() || busy || !result) return;
    try {
      if(result.items.some(item=>!item.applicable) && !find('[data-binding-exclude-ack]').checked) throw new Error('请先明确核对并排除本次修改的例外');
      if(ref>0 && result.items.some(item=>item.applicable && item.source_state==='UNKNOWN') && !find('[data-binding-unknown-ack]').checked) throw new Error('请明确未知来源的手工归属边界');
      apply(projectImportBinding(frozen,result,ref,label,digest,time),{
        modified:result.items.filter(item=>item.applicable).length,
        exceptions:result.items.filter(item=>!item.applicable).length});dialog.close();
    } catch(error){find('[data-binding-status]').textContent=error.message;}
  };
  return dialog;
}

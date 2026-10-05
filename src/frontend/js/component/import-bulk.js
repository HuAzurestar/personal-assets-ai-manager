import {esc, resourceId} from '../util/core.js';
import {workbenchDialog} from './workbench.js';
import {importIntentChoice, importRowIdentity, importRowLabel} from './import-choice.js';

export const importBulkNames = {ACCEPT:'接受（保留已选意图）', SKIP:'跳过（问题行仍保留问题）',
  RECHECK:'重查旧跳过／问题行（保留决定）', NEW:'确为另一笔新交易，会新增真实现金', AUTO:'恢复可靠来源自动匹配'};

export function importRangeFilter(file, start, end) {
  if (!file) throw new Error('指定行号区间必须先选择一个文件');
  resourceId(file);
  const first = resourceId(start), after = resourceId(end);
  if (first >= after) throw new Error('区间必须满足起始行 < 结束行（不含）');
  return {key:'source_row_number',op:'between',val:{start:first,end:after}};
}

// This projection changes local drafts only, never predicts accounting effects.
// Exceptions remain selected with their original choices; only the explicitly
// acknowledged applicable subset is modified. Save and financial preview still
// run the authoritative identity, complete group and risk validation.
export function projectImportBulk(selected, action, acknowledge = false) {
  if (!Object.hasOwn(importBulkNames, action)) throw new Error('不支持批量广播配对目标，请逐一核对具名配对');
  if (!selected.size || selected.size > 20000) throw new Error('批量操作需要明确选择1–20000行');
  if (action === 'NEW' && acknowledge !== true) throw new Error('请明确确认：本次意图将新增真实现金');
  const updates = new Map(), exceptions = [];
  for (const [key, item] of selected) {
    const row = item.row;
    if (key !== importRowIdentity(row)) throw new Error('选择范围行定位不一致');
    resourceId(row.file_id); resourceId(row.source_row_number);
    let reason;
    if (row.classification === 'PROCESSED' || row.persisted_row_status === 1) reason = '已接受行只读，不能重新修改';
    else if (action === 'RECHECK' && ![2,3].includes(row.persisted_row_status)) reason = '不是旧跳过／问题状态，无需重查';
    else if (['ACCEPT','NEW','AUTO'].includes(action) && [2,3].includes(row.persisted_row_status) && !item.choice.recheck)
      reason = '旧跳过／问题状态需先明确重查';
    else if (action === 'NEW' && (row.classification === 'EXISTING' || !Number.isSafeInteger(row.parsed.amount)
      || row.parsed.amount <= 0 || !row.parsed.occurred_time || !row.parsed.currency_code || !['IN','OUT'].includes(row.parsed.cash_direction)))
      reason = '既有事实或会计核心不完整，不能按新交易修改';
    if (reason) {exceptions.push({key,row,reason});continue;}
    let choice = {...item.choice,file_id:row.file_id,source_row_number:row.source_row_number};
    let targetLabel = item.targetLabel;
    if (action === 'SKIP') {
      choice = {...choice,decision:'SKIP',resolution:'AUTO',target:null,acknowledge_new_risk:false};targetLabel = null;
    } else if (action === 'RECHECK') choice.recheck = true;
    else if (action === 'ACCEPT') choice.decision = 'ACCEPT';
    else {choice = importIntentChoice(row, choice, action, null, acknowledge);targetLabel = null;}
    if (choice.target) choice.target = {...choice.target};
    updates.set(key,{...item,choice,targetLabel});
  }
  return {updates,exceptions,total:selected.size,action};
}

export function mountImportDraftRows(host, rows, describe, signal) {
  let page = 0;
  const paint = () => {
    if (signal.aborted || !host.isConnected) return;
    const start = page * 20;
    host.innerHTML = rows.slice(start,start+20).map(item => `<article class="import-bulk-record">${esc(describe(item))}</article>`).join('')
      + `<div class="actions"><button type="button" data-bulk-prev ${page ? '' : 'disabled'}>上一页</button><span>${rows.length ? start+1 : 0}–${Math.min(start+20,rows.length)} / ${rows.length}</span><button type="button" data-bulk-next ${start+20 < rows.length ? '' : 'disabled'}>下一页</button></div>`;
    host.querySelector('[data-bulk-prev]').onclick = () => {if (page > 0) {page--;paint();}};
    host.querySelector('[data-bulk-next]').onclick = () => {if (start+20 < rows.length) {page++;paint();}};
  };
  paint();
}

export function openImportBulk({selected, files, signal, valid, apply}) {
  // Freeze the named range, not a later mutable page or a broadcast Fact ID.
  const frozen = new Map([...selected].map(([key,item]) => [key,{...item,choice:{...item.choice,
    ...(item.choice.target ? {target:{...item.choice.target}} : {})}}]));
  const dialog = workbenchDialog('批量修改已选行（尚未入账）', `<div class="import-choice"><p>本次范围：已选 ${frozen.size} 行，${new Set([...frozen.values()].map(item => item.row.file_id)).size} 个文件。以下完整范围可分页核对；不会广播重复目标。</p><details><summary>全部已选来源行</summary><div data-bulk-scope></div></details><label>批量操作<select data-bulk-action>${Object.entries(importBulkNames).map(([value,label]) => `<option value="${value}">${esc(label)}</option>`).join('')}</select></label><p data-bulk-effect></p><label data-bulk-risk><input type="checkbox" data-bulk-risk-ack>我已核对本范围确为新交易；会新增真实现金，不是补证据或排除重复</label><p data-bulk-count></p><section data-bulk-exceptions></section><label data-bulk-exclude><input type="checkbox" data-bulk-exclude-ack>仅修改适用行，明确排除以下例外于本次修改；例外仍选中并保留原决定，不会自动排除入账</label><p role="status" data-bulk-status></p><button type="button" data-bulk-apply>确认修改草稿（仍需保存核验与完整入账预览）</button></div>`);
  dialog.classList.add('import-choice-dialog');
  const local = new AbortController(), find = selector => dialog.querySelector(selector);
  const abort = () => {local.abort();if (dialog.open) dialog.close();};
  signal.addEventListener('abort',abort,{once:true});
  dialog.addEventListener('close',() => {local.abort();signal.removeEventListener('abort',abort);},{once:true});
  const alive = () => dialog.isConnected && !local.signal.aborted && valid();
  mountImportDraftRows(find('[data-bulk-scope]'),[...frozen.values()],item => importRowLabel(item.row,files),local.signal);
  const effects = {ACCEPT:'仅设为接受，保留已选配对和风险状态；普通接受不确认新现金风险。',
    SKIP:'设为跳过并清除配对／新现金确认；问题行不伪装为有效事实。',
    RECHECK:'仅重查旧跳过／问题行，保留原接受或跳过决定和意图；不是自动接受。',
    NEW:'明确另建真实交易，清除原配对；实际新增Fact数量和现金金额以服务器完整预览为准，不能按来源行数推算。',
    AUTO:'清除手工配对和新现金确认，恢复可靠来源规则；疑似或未核对风险仍须处理。'};
  const paint = () => {
    const action = find('[data-bulk-action]').value;
    find('[data-bulk-risk]').hidden = action !== 'NEW';
    find('[data-bulk-effect]').textContent = effects[action];
    const projection = projectImportBulk(frozen,action,action === 'NEW');
    find('[data-bulk-count]').textContent = `可修改草稿 ${projection.updates.size} 行 · 例外 ${projection.exceptions.length} 行；不代表金融校验通过`;
    find('[data-bulk-exclude]').hidden = !projection.exceptions.length;
    find('[data-bulk-exceptions]').textContent = '';
    if (projection.exceptions.length) mountImportDraftRows(find('[data-bulk-exceptions]'),projection.exceptions,
      item => `${importRowLabel(item.row,files)}：${item.reason}`,local.signal);
    find('[data-bulk-apply]').disabled = !projection.updates.size;
  };
  find('[data-bulk-action]').onchange = () => {find('[data-bulk-risk-ack]').checked = false;find('[data-bulk-exclude-ack]').checked = false;find('[data-bulk-status]').textContent = '';paint();};
  find('[data-bulk-apply]').onclick = () => {
    if (!alive()) {find('[data-bulk-status]').textContent = '预览或选择已改变，请关闭并重新核对';return;}
    try {
      const result = projectImportBulk(frozen,find('[data-bulk-action]').value,find('[data-bulk-risk-ack]').checked);
      if (!result.updates.size) throw new Error('没有可修改的行');
      if (result.exceptions.length && !find('[data-bulk-exclude-ack]').checked) throw new Error('请先核对并明确排除本次修改的例外');
      apply(result);dialog.close();
    } catch (error) {find('[data-bulk-status]').textContent = error.message;}
  };
  paint();return dialog;
}

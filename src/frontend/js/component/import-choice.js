import { request } from '../api/client.js';
import { esc, money, date, resourceId } from '../util/core.js';
import { workbenchDialog } from './workbench.js';
import { mountLocalPicker } from './local-choice.js';

export const importRowIdentity = row => `${row.file_id}:${row.source_row_number}`;
export const importIntentNames = {AUTO:'可靠来源自动匹配', NEW:'确为另一笔新交易', LINK_EXISTING:'同源补证据，不新增现金', DUPLICATE:'跨源重复，保留证据并排除现金'};
const paired = resolution => ['LINK_EXISTING', 'DUPLICATE'].includes(resolution);

export function importRowLabel(row, files = []) {
  const filename = files.find(file => file.file_id === row.file_id)?.filename || `文件 #${row.file_id}`;
  const core = row.parsed;
  return `${filename} 第 ${row.source_row_number} 行 · ${core.summary || '摘要未知'} · ${core.occurred_time ? `${date(core.occurred_time)} (${core.occurred_time})` : '时间未知'} · ${core.cash_direction || '方向未知'} ${core.amount == null ? '金额未知' : money(core)}`;
}

export function importAnchorChoices(selected, row, files) {
  const result = new Map();
  for (const [key, item] of selected) {
    const choice = item.choice, core = item.row.parsed;
    if (key === importRowIdentity(row) || item.row.classification !== 'NEW' || choice.decision !== 'ACCEPT'
      || !['AUTO','NEW'].includes(choice.resolution || 'AUTO')) continue;
    if (['occurred_time','cash_direction','amount','currency_code'].every(field => core[field] != null && core[field] === row.parsed[field]))
      result.set(key, importRowLabel(item.row, files));
  }
  return result;
}

export function importIntentChoice(row, previous, resolution, target, acknowledge) {
  if (!Object.hasOwn(importIntentNames, resolution)) throw new Error('请选择有效处理意图');
  if (paired(resolution) !== !!target) throw new Error('补证据或跨源重复必须明确选择目标');
  if (target) {
    const keys = target.kind === 'FACT' ? ['kind','transaction_id'] : ['kind','file_id','source_row_number'];
    if (Object.keys(target).length !== keys.length || Object.keys(target).some(key => !keys.includes(key))) throw new Error('目标字段与类型不一致');
    if (target.kind === 'FACT') {
      if (typeof target.transaction_id !== 'number') throw new Error('事实目标必须使用精确数值ID');
      resourceId(target.transaction_id);
    }
    else if (target.kind === 'ROW') {
      if (typeof target.file_id !== 'number' || typeof target.source_row_number !== 'number') throw new Error('来源行目标必须使用精确数值定位');
      resourceId(target.file_id); resourceId(target.source_row_number);
      if (importRowIdentity(target) === importRowIdentity(row)) throw new Error('不能以本行作为自己的目标');
    } else throw new Error('目标类型不正确');
  }
  if (resolution === 'NEW' && row.duplicate_hint?.state !== 'NONE_IN_SCOPE' && acknowledge !== true)
    throw new Error('疑似或未完成核对的新现金需要明确风险确认');
  const choice = {...previous, file_id:row.file_id, source_row_number:row.source_row_number,
    decision:'ACCEPT', resolution, target:target ? {...target} : null,
    acknowledge_new_risk:resolution === 'NEW' && acknowledge === true};
  if (resolution === 'LINK_EXISTING') delete choice.account_ref_id;
  return choice;
}

// Exact candidates are paged server-side. A single suggestion is never chosen
// implicitly, and stale/failed reads cannot restore an old selectable target.
function mountFactMatches(host, {token, digest, row, resolution, signal, valid, choose}) {
  let page = 1, requestedPage = 1, busy = false, controller, generation = 0, result;
  host.innerHTML = '<p role="status" data-match-status></p><div data-match-items></div><div class="actions"><button type="button" data-match-prev>上一页</button><span data-match-count></span><button type="button" data-match-next>下一页</button><button type="button" data-match-retry>重新读取候选</button></div>';
  const find = selector => host.querySelector(selector);
  const stop = () => { generation++; controller?.abort(); };
  signal.addEventListener('abort', stop, {once:true});
  const alive = issued => host.isConnected && !signal.aborted && valid() && issued === generation;
  const controls = () => {
    find('[data-match-prev]').disabled = busy || page <= 1;
    find('[data-match-next]').disabled = busy || !result || page * result.page_size >= result.total;
    find('[data-match-retry]').disabled = busy;
  };
  const read = async requested => {
    stop(); controller = new AbortController(); const issued = generation;
    requestedPage = requested;
    busy = true; result = null; find('[data-match-items]').textContent = ''; controls();
    find('[data-match-status]').textContent = '正在核对精确候选；不会自动判重或选择';
    const abort = () => controller?.abort(); signal.addEventListener('abort', abort, {once:true});
    try {
      const params = new URLSearchParams({preview_digest:digest, file_id:row.file_id, source_row_number:row.source_row_number,
        kind:resolution === 'LINK_EXISTING' ? 'SAME_SOURCE' : 'CROSS_SOURCE', page_index:requested, page_size:20});
      const next = await request(`/paam/import/v1/preview/${token}/match/list?${params}`, {signal:controller.signal, cache:'no-store'});
      if (!alive(issued)) return;
      result = next; page = next.page_index;
      find('[data-match-items]').innerHTML = next.items.map(item => `<article class="picker-list-row"><span>${esc(item.summary_masked)} · ${esc(date(item.occurred_time))} (${esc(item.occurred_time)}) · ${esc(item.cash_direction)} ${esc(money(item))}<br>${esc(item.source_label_masked)} · Fact #${item.transaction_id}<br>${esc(item.current_review_summaries.map(review => review.title).join('；'))}${item.reason_codes.length ? `<br>不可选择：${esc(item.reason_codes.join('、'))}` : ''}</span><button type="button" data-match-id="${item.transaction_id}" ${item.eligible_actions.includes(resolution) ? '' : 'disabled'}>选择此目标</button></article>`).join('') || '<p>当前精确范围没有候选；不证明所有账单都没有重复。</p>';
      find('[data-match-count]').textContent = `第 ${page} 页，共 ${next.total} 个精确候选`;
      find('[data-match-status]').textContent = '请比较本行与目标的时间、金额和来源后明确选择';
      host.querySelectorAll('[data-match-id]').forEach(button => { button.onclick = () => {
        if (!alive(issued)) return;
        const item = next.items.find(value => String(value.transaction_id) === button.dataset.matchId);
        if (!item?.eligible_actions.includes(resolution)) return;
        choose({kind:'FACT',transaction_id:item.transaction_id}, `${item.summary_masked} · ${item.source_label_masked} · Fact #${item.transaction_id}`);
      }; });
    } catch (error) {
      if (alive(issued) && error.name !== 'AbortError') find('[data-match-status]').textContent = `${error.code || '候选读取失败'}：${error.message}；没有自动选择，请重新核对`;
    } finally { signal.removeEventListener('abort', abort); if (alive(issued)) {busy = false; controls();} }
  };
  find('[data-match-prev]').onclick = () => read(page - 1);
  find('[data-match-next]').onclick = () => read(page + 1);
  find('[data-match-retry]').onclick = () => read(requestedPage);
  void read(1);
}

export function openImportChoice({row, choice, targetLabel, token, digest, files, selected, signal, valid, apply}) {
  const dialog = workbenchDialog('核对本行处理意图（尚未入账）', `<div class="import-choice"><p data-intent-source>${esc(importRowLabel(row, files))}</p><label>处理意图<select data-import-resolution>${Object.entries(importIntentNames).map(([key,label]) => `<option value="${key}" ${(choice.resolution || 'AUTO') === key ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label><p data-intent-effect></p><label data-new-risk><input type="checkbox" data-new-risk-ack ${choice.acknowledge_new_risk ? 'checked' : ''}>我已核对，确为另一笔交易；将新增真实现金，不是补证据或排除重复</label><section data-intent-pair><label>目标范围<select data-import-target-kind><option value="FACT">已接受的具名事实</option><option value="ROW" ${choice.target?.kind === 'ROW' ? 'selected' : ''}>本次已选的新真实锚点</option></select></label><p data-intent-target></p><div data-intent-picker></div></section><p role="status" data-intent-status></p><div class="actions"><button type="button" data-intent-apply>确认本行意图（仍需保存核验和入账预览）</button></div></div>`);
  dialog.classList.add('import-choice-dialog');
  const find = selector => dialog.querySelector(selector);
  const local = new AbortController();
  let pickerController, target = choice.target ? {...choice.target} : null, label = targetLabel || '', resolution = choice.resolution || 'AUTO';
  const abort = () => {local.abort(); pickerController?.abort(); if (dialog.open) dialog.close();};
  signal.addEventListener('abort', abort, {once:true});
  dialog.addEventListener('close', () => {local.abort(); pickerController?.abort(); signal.removeEventListener('abort', abort);}, {once:true});
  const alive = () => dialog.isConnected && !local.signal.aborted && valid();
  const targetText = () => {
    find('[data-intent-target]').textContent = target ? (label || (target.kind === 'FACT' ? `Fact #${target.transaction_id}（请重新读取具名目标核对）` : `文件 #${target.file_id} 第 ${target.source_row_number} 行`)) : '尚未选择目标；唯一候选也不会自动确认';
    find('[data-intent-apply]').disabled = paired(resolution) && !target;
  };
  const paint = () => {
    pickerController?.abort(); pickerController = new AbortController();
    find('[data-new-risk]').hidden = resolution !== 'NEW';
    find('[data-intent-pair]').hidden = !paired(resolution);
    find('[data-intent-effect]').textContent = resolution === 'NEW' ? '本行将建立新事实及原默认，并增加真实现金；可靠键冲突仍由服务器拒绝。'
      : resolution === 'LINK_EXISTING' ? '只补本行证据；保留目标Fact的来源、当前解释和标签，不新建现金，也不覆盖来源卡。'
      : resolution === 'DUPLICATE' ? '本行建立新B及保留的原默认，并在同一事务发布排除现金的DUP解释；目标A必须仍是真实现金。'
      : '只沿可靠来源身份自动匹配；疑似或未知风险仍须人工核对，不按相同金额自动合并。';
    find('[data-intent-picker]').textContent = ''; targetText();
    if (!paired(resolution)) return;
    const choose = (value, text) => {if (!alive()) return; target = value; label = text; targetText();};
    if (find('[data-import-target-kind]').value === 'FACT') {
      mountFactMatches(find('[data-intent-picker]'), {token, digest, row, resolution, signal:pickerController.signal, valid:alive, choose});
    } else mountLocalPicker(find('[data-intent-picker]'), {signal:pickerController.signal,
      choices:() => importAnchorChoices(selected(), row, files), choose:(id, text) => {
        const [file_id, source_row_number] = id.split(':').map(value => resourceId(value));
        choose({kind:'ROW',file_id,source_row_number}, text);
      }});
  };
  find('[data-import-resolution]').onchange = () => {
    resolution = find('[data-import-resolution]').value; target = null; label = '';
    find('[data-new-risk-ack]').checked = false; paint();
  };
  find('[data-import-target-kind]').onchange = () => {target = null; label = ''; paint();};
  find('[data-intent-apply]').onclick = () => {
    if (!alive()) {find('[data-intent-status]').textContent = '预览或选择已改变；请关闭并重新核对'; return;}
    try {
      if (target?.kind === 'ROW' && !importAnchorChoices(selected(), row, files).has(importRowIdentity(target))) throw new Error('锚点已移除或不再是真实新行，请重新选择');
      const next = importIntentChoice(row, choice, resolution, target, find('[data-new-risk-ack]').checked);
      apply(next, label); dialog.close();
    } catch (error) {find('[data-intent-status]').textContent = error.message;}
  };
  paint();
  return dialog;
}

import {resourceId} from '../util/core.js';
import {validateImportPlan} from './import-plan.js';

// Presentation only: the caller must still classify unknown writes first.
// A known rollback must not look like an unexplained English error or invite
// replay of the already committed children under the previous approval.
export function importKnownFailureMessage(error) {
  const reason = error.code === 'STALE_PREVIEW'
    ? '账户、来源或账务前提已变化，原计划已失效；本批未提交'
    : error.code === 'WRITE_BUSY'
      ? '数据库正忙，或本批处理超过写入预算；本批未提交，请稍后再核验'
      : error.message || '操作已停止';
  return `${reason}（${error.code || '操作已停止'}）；已完成批保留，未提交范围的选择不因失败变成未保存。请点击“核验导入”（保存选择并重新核验），核对完整处理计划并再次明确批准；不会自动重发。`;
}

const key = row => `${id(row?.file_id)}:${id(row?.source_row_number)}`;
const id = (value, zero = false) => {
  if (typeof value !== 'number') throw new Error('入账响应对象定位不精确');
  return resourceId(value,{allowZero:zero});
};
const digest = value => {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw new Error('入账响应摘要不完整');
};
const time = value => {
  const match = typeof value === 'string' && value.match(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/);
  if (!match || !Number.isFinite(Date.parse(value))) throw new Error('入账响应时间不完整');
  return `${match[1]}.${(match[2] || '').padEnd(6,'0')}Z`;
};

export function validateImportApproval(result, plan, current) {
  digest(result?.preview_digest);digest(result?.operation_preview_digest);
  if (result.token !== current.token || result.preview_digest !== current.preview_digest
      || result.operation_preview_digest !== plan.operation_preview_digest
      || result.batch_count !== plan.batches.length || result.selected_count !== plan.selected_count
      || time(result.updated_time) <= time(current.updated_time)) throw new Error('批准响应与完整计划不一致；没有发送金融确认');
  return result;
}

// A malformed successful financial response is still unknown, not a reason to
// replay the child. Only a complete exact scope advances the serial executor.
export function validateImportChild(result, batch, index, total, operation, previous, proofs) {
  digest(result?.preview_digest);digest(result?.operation_preview_digest);
  if (result.operation_preview_digest !== operation || result.next_batch_index !== index + 1
      || result.complete !== (index + 1 === total)
      || time(result.preview_updated_time) <= time(previous.updated_time)
      || !Array.isArray(result.processed_rows) || result.processed_rows.length !== batch.preview.selected_rows.length
      || !Array.isArray(result.files) || result.files.length > 100) throw new Error('本批响应未完整核实；保持结果未知');
  const expected = new Set(batch.preview.selected_rows.map(key)), seen = new Set(), rowIds = new Set(), fileIds = new Set();
  const newFacts = new Set(), duplicates = new Set();
  let skipped = 0, invalid = 0, evidence = 0;
  for (const row of result.processed_rows) {
    const locator = key(row);
    if (!expected.has(locator) || seen.has(locator) || rowIds.has(id(row.row_id))
        || ![1,2,3].includes(row.row_status) || !['NEW_REAL','EVIDENCE_ONLY','DUPLICATE_ZERO','NONE'].includes(row.resolution_effect)) throw new Error('本批响应来源范围或状态不完整');
    seen.add(locator);rowIds.add(row.row_id);fileIds.add(row.file_id);
    for (const field of ['transaction_id','created_review_id','created_ledger_id','duplicate_kept_transaction_id']) id(row[field],true);
    for (const field of ['effective_review_ids','effective_ledger_ids']) {
      if (!Array.isArray(row[field]) || row[field].length > 4000 || new Set(row[field]).size !== row[field].length) throw new Error('本批响应有效输出不完整');
      row[field].forEach(value=>id(value));
    }
    if (row.row_status === 1) {
      if (!row.transaction_id || row.resolution_effect === 'NONE'
          || (row.resolution_effect !== 'EVIDENCE_ONLY' && (!row.effective_review_ids.length || !row.effective_ledger_ids.length))) throw new Error('已接受来源缺少真实关联');
      if (!!row.created_review_id !== !!row.created_ledger_id) throw new Error('原默认响应不完整');
      if (row.created_review_id) newFacts.add(row.transaction_id);
      if (row.resolution_effect === 'DUPLICATE_ZERO') {
        if (!row.created_review_id || !row.duplicate_kept_transaction_id || row.duplicate_kept_transaction_id === row.transaction_id) throw new Error('重复排除响应缺少原默认或真实目标');
        duplicates.add(row.transaction_id);
      }
      if (row.resolution_effect === 'EVIDENCE_ONLY') evidence++;
    } else {
      if (row.transaction_id || row.created_review_id || row.created_ledger_id || row.duplicate_kept_transaction_id
          || row.effective_review_ids.length || row.effective_ledger_ids.length || row.resolution_effect !== 'NONE') throw new Error('未接受来源不应返回现金关联');
      if (row.row_status === 2) skipped++;else invalid++;
    }
  }
  const files = new Set();
  const hashes = proofs && new Map(proofs.map(file=>[file.file_id,file.sha256]));
  for (const file of result.files) {
    id(file.file_id);digest(file.sha256);time(file.updated_time);
    if (files.has(file.file_id) || ![0,1,2,3].includes(file.status)
        || (hashes && hashes.get(file.file_id) !== file.sha256)) throw new Error('本批文件进度或原文件校验定位不完整');
    files.add(file.file_id);
    for (const field of ['accepted','skipped','invalid','remaining']) if (!Number.isSafeInteger(file[field]) || file[field] < 0) throw new Error('本批文件进度不精确');
  }
  if ([...fileIds].some(value=>!files.has(value))) throw new Error('本批缺少文件进度');
  for (const field of ['new_fact_count','linked_existing_count','manual_linked_count','duplicate_fact_count','skipped_count','invalid_count','remaining_count']) {
    if (!Number.isSafeInteger(result[field]) || result[field] < 0) throw new Error('本批结果计数不精确');
  }
  if (result.new_fact_count !== newFacts.size || result.duplicate_fact_count !== duplicates.size
      || result.skipped_count !== skipped || result.invalid_count !== invalid
      || result.linked_existing_count > evidence || result.manual_linked_count > evidence) throw new Error('本批结果计数与完整范围不一致');
  return result;
}

export function createImportExecution({plan, current, approve, confirm, stop, prepare, committed, failed, progress = () => {}, valid = () => true, unknown, timers = globalThis}) {
  // Freeze the human-disclosed complete plan. No persistent queue or receipt.
  const frozen = structuredClone(plan), initial = {...current};
  validateImportPlan(frozen,frozen.selected_rows);
  digest(frozen.operation_preview_digest);digest(initial.preview_digest);time(initial.updated_time);
  if (!frozen.can_confirm || !frozen.batches.length || frozen.source_preview_digest !== initial.preview_digest
      || frozen.batches.some((batch,index)=>batch.batch_index !== index)) throw new Error('完整计划尚不可批准');
  frozen.batches.forEach(batch=>digest(batch.preview.batch_preview_digest));
  const state = {phase:'READY',completed_batches:0,completed_rows:0,total_batches:frozen.batches.length,
    remaining_rows:frozen.selected_rows.length,stop_requested:false,in_flight:false};
  let started = false, approved = false, guard = initial, stopPromise;
  const notify = () => progress({...state});
  const revoke = () => {
    if (!approved) return Promise.resolve();
    if (!stopPromise) {
      let timer;
      stopPromise=Promise.race([Promise.resolve().then(()=>stop({operation_preview_digest:frozen.operation_preview_digest})),
        new Promise((_,reject)=>{timer=globalThis.setTimeout(()=>reject(new Error('停止确认等待超时')),10000);})])
        .catch(error=>{state.stop_error=error.message;}).finally(()=>globalThis.clearTimeout(timer));
    }
    return stopPromise;
  };
  const halt = () => {state.stop_requested=true;notify();return revoke();};
  const waitFinancial = async send => {
    let timer;
    try {
      return await Promise.race([Promise.resolve().then(send),new Promise((_,reject)=>{
        timer=timers.setTimeout(()=>reject(Object.assign(new Error('等待本批响应超过35秒；结果未知，先核对持久状态'),
          {code:'RESULT_UNKNOWN',status:503})),35000);
      })]);
    } finally {if (timer != null) timers.clearTimeout(timer);}
  };
  return {state,stop:halt,async run() {
    if (started) throw new Error('本计划执行已开始；不能重复批准或重放');
    started=true;
    let stage='APPROVE';
    try {
      if (!valid() || state.stop_requested) {state.phase='STOPPED';notify();return state;}
      state.phase='APPROVING';notify();
      guard=validateImportApproval(await approve({expected_updated_time:initial.updated_time,preview_digest:initial.preview_digest,
        selected_rows:frozen.selected_rows,operation_preview_digest:frozen.operation_preview_digest}),frozen,initial);
      approved=true;
      for (const [index,batch] of frozen.batches.entries()) {
        if (!valid() || state.stop_requested) break;
        stage='PREPARE';state.phase='PREPARING';notify();
        await prepare(batch.preview.selected_rows,{...state});
        if (!valid() || state.stop_requested) break;
        stage='CONFIRM';state.phase='CONFIRMING';state.in_flight=true;notify();
        // Never abort a financial transport on route change/stop: cancellation
        // is not rollback. Wait for known response or retain unknown context.
        const response=await waitFinancial(()=>confirm({expected_updated_time:guard.updated_time,preview_digest:guard.preview_digest,
          selected_rows:batch.preview.selected_rows,batch_preview_digest:batch.preview.batch_preview_digest,
          operation_preview_digest:frozen.operation_preview_digest,batch_index:index}));
        stage='VALIDATE_FINANCIAL';
        const result=validateImportChild(response,batch,index,frozen.batches.length,frozen.operation_preview_digest,guard,initial.files);
        state.in_flight=false;state.completed_batches++;state.completed_rows+=batch.preview.selected_rows.length;
        state.remaining_rows-=batch.preview.selected_rows.length;
        guard={updated_time:result.preview_updated_time,preview_digest:result.preview_digest};
        stage='LOCAL_RESULT';await committed(result,batch.preview.selected_rows,{...state});notify();
      }
      if (state.remaining_rows) {state.phase='STOPPED';state.stop_requested=true;await revoke();}
      else state.phase='COMPLETE';
    } catch (error) {
      const uncertain=stage === 'VALIDATE_FINANCIAL' || (stage === 'CONFIRM' && unknown(error));
      state.phase=uncertain ? 'UNKNOWN' : 'FAILED';state.stop_requested=true;state.in_flight=false;
      await revoke();await failed(error,{...state,unknown:uncertain,stage});
    }
    notify();return state;
  }};
}

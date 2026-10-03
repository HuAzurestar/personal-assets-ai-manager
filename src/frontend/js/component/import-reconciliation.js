import {resourceId, money} from '../util/core.js';
import {importRowIdentity} from './import-choice.js';

const resolutions = ['AUTO','NEW','LINK_EXISTING','DUPLICATE'];
const states = ['NOT_PERSISTED','UNPROCESSED','SKIPPED','INVALID','ACCEPTED','EVIDENCE_LINKED',
  'DUPLICATE_EXCLUDED','CURRENT_STATE_CHANGED','UNRESOLVED'];
const exactId = (value,zero=false) => {
  if (typeof value !== 'number') throw new Error('核对对象定位不精确');
  return resourceId(value,{allowZero:zero});
};
const locator = row => {exactId(row?.file_id);exactId(row?.source_row_number);return importRowIdentity(row);};
const stamp = text => {
  const found = typeof text === 'string' && text.match(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/);
  if (!found || !Number.isFinite(Date.parse(text))) throw new Error('核对时间不完整');
  return `${found[1]}.${(found[2] || '').padEnd(6,'0')}Z`;
};
const amount = value => {if (!Number.isSafeInteger(value) || value <= 0 || value > 9000000000000) throw new Error('核对金额不精确');};
const core = fact => [fact.amount,fact.currency_code,fact.cash_direction,stamp(fact.occurred_time)].join('|');

export function importReconciliationInput(retained) {
  if (!Array.isArray(retained?.rows) || !retained.rows.length || retained.rows.length > 1000
      || !Array.isArray(retained.intents) || retained.intents.length !== retained.rows.length
      || !Array.isArray(retained.files) || retained.files.length > 100) throw new Error('本批安全定位上下文不完整；不能推断配对目标');
  const keys = new Set(retained.rows.map(locator)), seen = new Set(), fileIds = new Set();
  if (keys.size !== retained.rows.length) throw new Error('核对范围包含重复来源行');
  const rows = retained.intents.map(intent => {
    const key = locator(intent);
    if (!keys.has(key) || seen.has(key) || !resolutions.includes(intent.resolution)
        || ![undefined,null,'ACCEPT','SKIP'].includes(intent.decision)) throw new Error('核对意图与原发送范围不一致');
    seen.add(key);fileIds.add(intent.file_id);
    const target = intent.target;
    if (target) {
      if (!['LINK_EXISTING','DUPLICATE'].includes(intent.resolution)) throw new Error('非配对意图不能带目标');
      if (target.kind === 'FACT') exactId(target.transaction_id);
      else if (target.kind === 'ROW') {locator(target);fileIds.add(target.file_id);}
      else throw new Error('核对目标类型不明确');
    }
    return {file_id:intent.file_id,source_row_number:intent.source_row_number,resolution:intent.resolution,
      decision:intent.decision ?? null,target:target ? {...target} : null};
  });
  const proofs = new Map();
  for (const file of retained.files) {
    exactId(file.file_id);
    if (proofs.has(file.file_id) || typeof file.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(file.sha256)) throw new Error('原文件校验定位不完整');
    proofs.set(file.file_id,{file_id:file.file_id,sha256:file.sha256});
  }
  if ([...fileIds].some(id => !proofs.has(id))) throw new Error('缺少来源或ROW目标的原文件校验定位');
  return {rows,files:[...fileIds].map(id=>proofs.get(id))};
}

export function validateImportReconciliation(result,input) {
  if (result?.current_state_only !== true || typeof result.fully_observed !== 'boolean'
      || !Array.isArray(result.items) || result.items.length !== input.rows.length
      || !Array.isArray(result.facts) || result.facts.length > 2000
      || !Array.isArray(result.outputs) || result.outputs.length > 4000) throw new Error('核对未返回完整当前状态；保持结果未知');
  stamp(result.observed_at);
  const intents = new Map(input.rows.map(row=>[locator(row),row])), seen = new Set(), facts = new Map(), current = new Map();
  for (const fact of result.facts) {
    exactId(fact.id);amount(fact.amount);stamp(fact.occurred_time);
    if (facts.has(fact.id) || typeof fact.currency_code !== 'string' || !fact.currency_code
        || !['IN','OUT'].includes(fact.cash_direction)) throw new Error('核对Fact不完整或重复');
    facts.set(fact.id,fact);
  }
  const allocations = new Set(), ledgers = new Set();
  for (const output of result.outputs) {
    for (const field of ['transaction_id','allocation_id','review_id','ledger_id']) exactId(output[field]);
    exactId(output.account_ref_id,true);amount(output.cash_amount);stamp(output.occurred_time);
    const fact = facts.get(output.transaction_id);
    if (!fact || allocations.has(output.allocation_id) || ledgers.has(output.ledger_id)
        || output.cash_currency_code !== fact.currency_code || output.cash_direction !== fact.cash_direction
        || !['CONFIRMED','REVOKED'].includes(output.review_status)
        || !['TRANSACTION','ACCOUNT_TRANSFER','ASSET_LIABILITY','DUPLICATE'].includes(output.economic_type)) throw new Error('核对输出引用不完整或重复');
    allocations.add(output.allocation_id);ledgers.add(output.ledger_id);
    if (output.review_status === 'CONFIRMED') {
      if (!current.has(output.transaction_id)) current.set(output.transaction_id,[]);
      current.get(output.transaction_id).push(output);
    }
  }
  for (const item of result.items) {
    const key=locator(item.row), intent=intents.get(key);
    if (!intent || seen.has(key) || item.resolution !== intent.resolution || !states.includes(item.state)
        || typeof item.fully_observed !== 'boolean' || ![null,0,1,2,3].includes(item.row_status)
        || !Array.isArray(item.reason_codes) || item.reason_codes.some(code=>typeof code !== 'string' || !code)) throw new Error('核对范围或意图不一致');
    seen.add(key);exactId(item.row_id,true);exactId(item.transaction_id,true);exactId(item.target_transaction_id,true);
    if (item.row_status === 1 ? !item.row_id || !facts.has(item.transaction_id) : item.transaction_id !== 0) throw new Error('核对来源关联不完整');
    if (intent.target?.kind === 'FACT' && item.target_transaction_id
        && intent.target.transaction_id !== item.target_transaction_id) throw new Error('核对目标与原发送目标不一致');
    if (item.fully_observed && !['SKIPPED','INVALID','ACCEPTED','EVIDENCE_LINKED','DUPLICATE_EXCLUDED','CURRENT_STATE_CHANGED'].includes(item.state)) throw new Error('未核实状态不能解除未知');
    if (item.fully_observed && item.row_status === 1 && ['LINK_EXISTING','DUPLICATE'].includes(intent.resolution)
        && (!intent.target || !facts.has(item.target_transaction_id))) throw new Error('缺失配对目标时不能解除未知');
    if (item.state === 'ACCEPTED' && !['AUTO','NEW'].includes(intent.resolution)) throw new Error('配对意图不能降级为普通接受');
    if ((item.state === 'SKIPPED' && item.row_status !== 2) || (item.state === 'INVALID' && item.row_status !== 3)
        || (['ACCEPTED','EVIDENCE_LINKED','DUPLICATE_EXCLUDED','CURRENT_STATE_CHANGED'].includes(item.state) && item.row_status !== 1)) throw new Error('核对状态与持久来源不一致');
    if (item.state === 'EVIDENCE_LINKED' && (intent.resolution !== 'LINK_EXISTING'
        || !intent.target || item.transaction_id !== item.target_transaction_id)) throw new Error('原目标补证据未核实');
    if (item.state === 'DUPLICATE_EXCLUDED') {
      const b=facts.get(item.transaction_id),a=facts.get(item.target_transaction_id),bs=current.get(b?.id) || [],as=current.get(a?.id) || [];
      if (intent.resolution !== 'DUPLICATE' || !intent.target || !a || !b || a.id === b.id || core(a) !== core(b)
          || !bs.length || !as.length || bs.some(value=>value.economic_type !== 'DUPLICATE')
          || as.some(value=>value.economic_type === 'DUPLICATE')
          || bs.reduce((sum,value)=>sum+value.cash_amount,0) !== b.amount
          || as.reduce((sum,value)=>sum+value.cash_amount,0) !== a.amount
          || new Set(bs.map(value=>value.account_ref_id)).size !== 1 || new Set(as.map(value=>value.account_ref_id)).size !== 1
          || !bs[0].account_ref_id || !as[0].account_ref_id || bs[0].account_ref_id === as[0].account_ref_id) throw new Error('两边当前重复排除效果未完整核实');
    }
  }
  if (result.fully_observed !== result.items.every(item=>item.fully_observed)) throw new Error('核对完整状态不一致');
  return result;
}

const stateNames={NOT_PERSISTED:'尚无持久结果，不证明未提交',UNPROCESSED:'尚无最终处理状态',SKIPPED:'当前已跳过',INVALID:'当前有问题',
  ACCEPTED:'当前已接受',EVIDENCE_LINKED:'当前关联原目标，仅补证据',DUPLICATE_EXCLUDED:'当前B排除、A保留真实现金',
  CURRENT_STATE_CHANGED:'当前效果与原意图有变化，不等于首次提交失败',UNRESOLVED:'目标或来源尚无法核实'};
export const reconciliationRowLabel = item => `文件 #${item.row.file_id} 第 ${item.row.source_row_number} 行：${stateNames[item.state]} · 来源行 #${item.row_id} · Fact #${item.transaction_id}`
  + (item.target_transaction_id ? ` · 原明确目标 Fact #${item.target_transaction_id}` : '')
  + (item.reason_codes.includes('KEEPER_LOCATED_FROM_CLIENT_CONTEXT') ? '；A目标来自客户端保留的明确意图，不是数据库首次配对回执' : '')
  + (item.reason_codes.length ? `；${item.reason_codes.join(' / ')}` : '');
export const reconciliationOutputLabel = item => `Fact #${item.transaction_id} · 原Review #${item.review_id} ${item.review_status === 'CONFIRMED' ? '当前有效' : '已停用历史'}（${item.review_status}） · Ledger #${item.ledger_id} · ${item.economic_type} · ${item.cash_direction} ${money({cash_amount:item.cash_amount,cash_currency_code:item.cash_currency_code})} · ${item.occurred_time} · 来源卡 #${item.account_ref_id}`;

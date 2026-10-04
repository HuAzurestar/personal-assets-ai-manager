import {request} from '../api/client.js';
import {resourceId, currencyPrecision} from './core.js';
import {accountCorrectionIntent} from './account-correction.js';

export const sourceCompletionLimit = 100;
const fail = text => {throw new Error(text);};

export function sourceCompletionFilter({direction='',currency='',start='',end=''} = {}) {
  const expression = [{key:'active',op:'=',val:true},{key:'account_ref_id',op:'=',val:0}];
  if (direction) {
    if (!['IN','OUT'].includes(direction)) fail('收支方向无效。');
    expression.push({key:'cash_direction',op:'=',val:direction});
  }
  if (currency) {
    currencyPrecision(currency);
    expression.push({key:'cash_currency_code',op:'=',val:currency.trim().toUpperCase()});
  }
  for (const [key,value,op] of [['start',start,'>='],['end',end,'<']]) {
    if (!value) continue;
    if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value)))
      fail(`${key === 'start' ? '开始' : '结束'}时间须为明确时区的有效时间。`);
    expression.push({key:'occurred_time',op,val:value});
  }
  if (start && end && Date.parse(start) >= Date.parse(end)) fail('结束时间须晚于开始时间。');
  return {op:'AND',expression};
}

function validateRow(row) {
  resourceId(row?.id);resourceId(row?.transaction_id);resourceId(row?.review?.id);
  if (row.active !== true || row.account_ref_id !== 0 || row.review.status !== 'CONFIRMED')
    fail('所选流水不再属于有效且来源未识别的范围；请重新读取，不隐藏或部分补齐。');
}

export function sourceCompletionPage(result, page, size) {
  if (!result || result.page_index !== page || result.page_size !== size || !Array.isArray(result.items)
    || !Number.isSafeInteger(result.total) || result.total < 0 || ![20,50,100].includes(size)
    || result.items.length !== Math.min(size,Math.max(0,result.total-(page-1)*size)))
    fail('完整分页结果不一致；本次选择未改变，请重新读取。');
  const ids = new Set();
  result.items.forEach(row => {validateRow(row);if (ids.has(row.id)) fail('流水 ID 重复，未加入部分范围。');ids.add(row.id);});
  return result;
}

// Changes are all-or-nothing, including a page/all-filter selection exceeding
// the existing command budget. Never quietly select only the first 100 rows.
export function sourceCompletionSelection(selected, rows, remove=false) {
  if (!(selected instanceof Map) || !Array.isArray(rows)) fail('选择范围无效。');
  const next = new Map(selected), ids = new Set();
  for (const row of rows) {
    validateRow(row);
    if (ids.has(row.id)) fail('本次选择含重复流水。');
    ids.add(row.id);
    if (remove) {next.delete(row.id);continue;}
    const previous = next.get(row.id);
    if (previous && JSON.stringify(previous) !== JSON.stringify(row)) fail('已选流水上下文发生变化，请清空并重新选择。');
    next.set(row.id,structuredClone(row));
  }
  if (next.size > sourceCompletionLimit) fail('本次超过100条更正预算；选择未改变，请缩小筛选或分次明确选择。');
  for (const [id,row] of next) {validateRow(row);if (id !== row.id) fail('选择身份不一致。');}
  return next;
}

export function sourceCompletionWhole(result) {
  if (result?.total > sourceCompletionLimit) fail(`当前筛选共${result.total}条，超过每次100条更正预算；未选择前100条，请缩小筛选或明确选择页面。`);
  return sourceCompletionPage(result,1,100).items;
}

export function sourceCompletionIntent(selected, target, duplicateIds, keepers) {
  if (!(selected instanceof Map) || !selected.size || selected.size > sourceCompletionLimit) fail('请选择1至100条待补齐流水。');
  const ref = resourceId(target);
  const rows = [...selected].sort(([a],[b])=>a-b);
  rows.forEach(([id,row])=>{validateRow(row);if (id !== row.id) fail('选择身份不一致。');});
  const intent = accountCorrectionIntent(rows[0][0],ref,0,duplicateIds,keepers);
  intent.account_corrections = rows.map(([id])=>({ledger_id:id,account_ref_id:ref}));
  return intent;
}

export function sourceCompletionPlan(plan, selected, target) {
  const intent=sourceCompletionIntent(selected,target,[],new Map()),expected=new Map(intent.account_corrections.map(row=>[row.ledger_id,row.account_ref_id]));
  const changes=(plan.new_reviews || []).flatMap(row=>row.account_changes || []),seen=new Set();
  for(const row of changes) {
    if(!expected.has(row.ledger_id) || seen.has(row.ledger_id) || row.before_account_ref_id!==0 || row.after_account_ref_id!==expected.get(row.ledger_id))
      fail('完整预览与本次指定来源范围不一致，不能确认。');
    seen.add(row.ledger_id);
  }
  if(seen.size!==expected.size)fail('完整预览遗漏本次指定流水，不能确认。');
}

// Only GET operations use this budget. Financial preview/command still use
// the shared immutable publication flow; no timeout ever replays a command.
export async function boundedSourceRead(load, signal, budgetMs=30_000) {
  if (signal?.aborted) throw new DOMException('读取已中止','AbortError');
  const controller = new AbortController();
  let timer, rejectStop;
  const stopped = new Promise((_,reject)=>{rejectStop=reject;});
  const abort = () => {controller.abort();rejectStop(new DOMException('读取已中止','AbortError'));};
  signal?.addEventListener('abort',abort,{once:true});
  timer = setTimeout(()=>{controller.abort();rejectStop(new Error('完整范围读取超时；没有应用部分结果，请重新读取。'));},budgetMs);
  try {return await Promise.race([Promise.resolve().then(()=>load(controller.signal)),stopped]);}
  finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);controller.abort();}
}

export function sourceCompletionOriginals(plan, signal) {
  const ids = (plan.new_reviews || []).map(row=>resourceId(row.source_review_id));
  if (!ids.length || ids.length > 100 || new Set(ids).size !== ids.length) fail('完整原事项范围无效，不能确认。');
  return boundedSourceRead(async readSignal => {
    const originals = new Map();let next=0;
    await Promise.all(Array.from({length:Math.min(6,ids.length)},async()=>{
      while (next < ids.length) {
        const id=ids[next++], row=await request(`/paam/ledger/v1/review/${id}`,{signal:readSignal});
        if (readSignal.aborted) throw new DOMException('读取已中止','AbortError');
        if (row.id !== id) fail('完整原事项 ID 不一致，不能确认。');
        originals.set(id,row);
      }
    }));
    return originals;
  },signal);
}

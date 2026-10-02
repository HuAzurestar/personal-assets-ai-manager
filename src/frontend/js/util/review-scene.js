// Presentation policy only. The backend still validates and expands the whole
// immutable intent; hiding a section must never leave unnoticed write input.
const policies = new Map([
  ['NORMAL', {cash:true, defaultType:'TRANSACTION', hint:'完整选择收支事实，核对金额与来源卡即可。'}],
  ['REFUND', {cash:true, defaultType:'TRANSACTION', hint:'核对退款事实及来源；不推断原交易或债务对象。'}],
  ['INTERNAL_TRANSFER', {cash:true, defaultType:'ACCOUNT_TRANSFER', hint:'明确选择真实转账两端，保留各自现金；不作为重复证据排除。'}],
  ['DUPLICATE', {cash:true, duplicate:true, defaultType:'DUPLICATE', hint:'本次只选择排除的 B，逐项明确仍计现金的 A 和不同来源卡。'}],
  ['BORROW_REPAY', {cash:true, quantity:true, link:true, duplicate:true, defaultType:'ASSET_LIABILITY', hint:'明确独立借据、本金数量及现金归因；费用单独拆为收支，不按人名合并借据。'}],
  ['SHARED_PAYMENT', {cash:true, quantity:true, link:true, duplicate:true, phase:true, defaultType:'ASSET_LIABILITY', hint:'选择共同费用阶段，区分本人承担的收支与有据往来本金。'}],
  ['POS_OPENING', {quantity:true, defaultType:'TRANSACTION', hint:'仅登记有据期初数量，不新增现金，不选择现金事实。'}],
  ['POS_POSITION_OPEN', {cash:true, quantity:true, link:true, duplicate:true, defaultType:'TRANSACTION', hint:'对象增加须有数量证据；可以无现金，有现金时明确分类及归因。'}],
  ['POS_POSITION_SETTLE', {cash:true, quantity:true, link:true, duplicate:true, defaultType:'TRANSACTION', hint:'对象减少明确选择原始 IN 来源；可以无现金，不自动 FIFO 或假装结清。'}],
  ['POS_CREDIT_PURCHASE', {cash:true, quantity:true, link:true, duplicate:true, defaultType:'TRANSACTION', hint:'信用消费登记收支与负债增加；已关联款项须完整归因，不另造现金。'}],
  ['POS_CREDIT_REPAY', {cash:true, quantity:true, link:true, duplicate:true, defaultType:'ASSET_LIABILITY', hint:'本金减少信用负债并关联原始来源；独立费用另列，不与本金混同。'}],
]);
export function reviewScene(code) {
  const policy = policies.get(code);
  if (!policy) throw new Error('不支持的业务场景，请重新选择');
  return Object.freeze({cash:false, quantity:false, link:false, phase:false, duplicate:false,
    ...policy, economicTypes:Object.freeze(policy.quantity
      ? ['TRANSACTION', 'ACCOUNT_TRANSFER', 'ASSET_LIABILITY', 'DUPLICATE'] : [policy.defaultType])});
}

export function incompatibleSceneInputs(code, counts) {
  const scene = reviewScene(code), result = {};
  const applicable = {facts:scene.cash, cash:scene.cash, drafts:scene.quantity,
    legs:scene.quantity, links:scene.link, duplicates:scene.duplicate};
  for (const [key, allowed] of Object.entries(applicable)) {
    const count = counts[key] ?? 0;
    if (!Number.isSafeInteger(count) || count < 0) throw new Error('草稿计数无效');
    if (!allowed && count) result[key] = count;
  }
  return result;
}

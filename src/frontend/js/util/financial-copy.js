// Presentation only. Never infer approval, identity, zero quantity or write
// outcome from a label; retain the server's original state and error codes.
const financialStates = {
  coverage: {FULL:'金额已完整分配',PARTIAL:'金额部分分配',UNRESOLVED:'金额覆盖待处理'},
  default: {KNOWN:'原系统默认唯一',MISSING:'原系统默认缺失',AMBIGUOUS:'原系统默认不唯一'},
  review: {CONFIRMED:'解释已生效',REVOKED:'解释已停用',PENDING:'解释待确认'},
  quantity: {KNOWN:'有据数量',UNKNOWN:'数量未知（没有数量证据，不是零）',NEEDS_REVIEW:'数量待核对（来源失效，需核对；不是零）'},
  identity: {KNOWN:'已关联数量对象',NEEDS_IDENTITY:'尚需关联数量对象',NOT_APPLICABLE:'不涉及数量对象'},
  cost: {UNKNOWN:'成本未知',NEEDS_REVIEW:'成本待核对'},
  position: {ACTIVE:'使用中',ARCHIVED:'已归档（证据保留）',SETTLED:'已标记结清（仍需核对当前数量）'},
};

export const financialScopeNote = '金额分配完整只表示解释覆盖，包含不计现金的重复证据；解释生效只表示当前采用，不代表业务已核对正确。';

export function financialStateLabel(group, code, technical = true) {
  const states = Object.hasOwn(financialStates,group) ? financialStates[group] : null;
  const label = states && Object.hasOwn(states,code) ? states[code] : '状态未知';
  return `${label}${technical && typeof code === 'string' && code ? `（${code}）` : ''}`;
}

const financialReasons = {
  IMPORT_REVIEW_REQUIRED:'新增现金风险尚未明确：核对候选后选择跳过、具名配对，或明确确认另一笔真实交易',
  ROW_INVALID:'来源行会计字段无效，不能接受；可跳过并保留原证据',
  NON_POSTED_EVIDENCE:'交易尚未入账，不能作为真实现金接受；跳过保留原证据',
  NEUTRAL_EVIDENCE:'这是非现金变动的来源证据，不能作为真实现金接受；跳过保留原证据',
  ROW_RECHECK_REQUIRED:'旧跳过／问题行需明确重新检查',
  FACT_CONFLICT:'来源身份与已有事实冲突，不能覆盖旧事实',
  IDENTITY_AMBIGUOUS:'来源身份不唯一，须核对而不能自动选择',
  SOURCE_IDENTITY_REQUIRED:'来源身份不足，不能自动认定同一交易',
  STALE_PREVIEW:'来源、账户或账务前提已变化，须重新核验',
  PREVIEW_CHANGED:'预览已变化，须读取最新预览并重新核验',
  WRITE_BUSY:'数据库正忙，或本次处理超过写入预算；请稍后重新读取并核验',
  QUERY_BUSY:'读取暂时繁忙，或超过读取预算；请缩小范围后重新读取',
  READ_BUDGET_EXCEEDED:'完整结果超过本次读取预算；请缩小范围，不会把截断内容当完整计划',
  DETAIL_LIMIT:'完整详情超过本次容量；请缩小读取范围，不会静默截断',
  RELATION_BROKEN:'账务引用或覆盖关系不完整；不能隐藏问题或继续提交，请核对相关原事项',
  FACT_COVERAGE_REQUIRED:'每个所选事实的金额必须完整分配；金额完整不代表业务判断正确',
  LEGACY_COVERAGE_REVIEW_REQUIRED:'旧事项存在部分覆盖；须明确重组完整事项，不能自动补造剩余默认',
  DEFAULT_IDENTITY_REQUIRED:'原系统默认缺失或不唯一，不能猜选默认解释；请核对原事项',
  DEFAULT_DEACTIVATION_FORBIDDEN:'停用后会失去完整有效解释；请核对整组影响及原系统默认恢复',
  INVALID_PRINCIPAL:'本金现金和数量必须完整对应；不能留下未归因的本金，请核对金额、单位及数量关系',
  INVALID_DUPLICATE:'重复证据须明确对应仍计现金的保留交易；不能把转账两端或保留交易再次排除',
  INVALID_ACCOUNT_BINDING:'来源绑定与所选事实或场景不一致；请核对具名来源和完整范围',
  ACCOUNT_CORRECTION_KEEPER_REQUIRED:'来源更正涉及重复证据；须明确选择仍计现金的保留交易，再预览完整事项',
  FACT_NOT_FOUND:'所选来源事实不存在或已不可读取；请重新读取并选择',
  REFERENCE_NOT_FOUND:'引用对象不存在或已不可读取；请重新读取完整事项',
  REVIEW_NOT_FOUND:'解释不存在或已不可读取；请重新读取当前事项',
  POSITION_PARTY_REQUIRED:'数量对象需要明确的有效本方个人；请先选择个人，不按同名猜身份',
  POSITION_NOT_ACTIVE:'数量对象不在使用状态；请核对对象元数据，不能新增数量变化',
  POSITION_SOURCE_REQUIRED:'减少数量必须明确选择原增加证据；不能按金额或姓名猜来源',
  POSITION_SOURCE_INVALID:'数量来源已失效或将被本次整组变更停用；请核对来源依赖，不能当作有据结清',
  INVALID_POSITION_SOURCE:'数量来源不属于该对象的有效增加证据；期初或增加不应引用旧来源腿',
  INVALID_POSITION_REFERENCE:'所选或新建数量对象引用不一致；请核对对象及数量证据',
  INVALID_POSITION_ALLOCATION:'现金到数量的归因范围或关系不一致；请核对完整金额及数量腿',
  LEDGER_POSITION_ALLOCATION_OVERFLOW:'归因到数量的款项超过原现金金额；请核对拆分，不增加另一笔现金',
  UNIT_MISMATCH:'现金币种或数量单位不一致；请选择同一精确单位，不自动换汇或换算',
  INVALID_CASE_CODE:'业务场景与现金或数量结构不一致；请核对场景要求及原事项',
  INVALID_REVIEW:'解释缺少完整现金或数量证据，或重复选择事实；请核对完整范围',
  VALIDATION_ERROR:'输入未通过接口校验；请核对必填项、精确金额、单位和引用',
  ENTITY_CHANGED:'对象或解释前提已变化；保留草稿，重新读取并预览',
  INVALID_ID:'对象编号不是精确有效整数或超出范围；请重新选择具名对象，不手工猜编号',
  RESULT_UNKNOWN:'提交结果未知；须先读取当前持久状态核对，不自动重发命令',
};

export function financialIssueReason(code, fallback = '操作未通过校验，请核对当前选择和完整计划') {
  return Object.hasOwn(financialReasons,code) ? financialReasons[code] : fallback;
}

export function financialIssueMessage(error, fallback) {
  const code = typeof error?.code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(error.code) ? error.code : '';
  // Unknown English server diagnostics are not useful business guidance and
  // may contain private details. Preserve Chinese local validation guidance.
  const local = !code && /[\u4e00-\u9fff]/.test(error?.message || '') ? error.message : undefined;
  const reason = financialIssueReason(code,local || fallback);
  return `${reason}${code ? `（${code}）` : ''}`;
}

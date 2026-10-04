// Infer only an unchosen draft. Saved/user choices remain authoritative;
// this is not permission to bypass duplicate risk or confirm a financial write.
export function importRowChoice(row, selectedChoice) {
  if (selectedChoice) return selectedChoice;
  if (row.choice) return row.choice;
  const normal = ['NEW', 'EXISTING', 'PROCESSED'].includes(row.classification)
    && !row.issue_codes?.length && importRiskState(row) === 'NONE_IN_SCOPE';
  return {decision:normal ? 'ACCEPT' : 'SKIP', recheck:false, account_ref_id:null};
}

// Evidence-only/read-only rows do not create new cash. Missing or inconsistent
// hints on prospective new cash are unknown, never a fabricated safe zero.
export function importRiskState(row) {
  if (row.classification !== 'NEW') return 'NONE_IN_SCOPE';
  const hint = row.duplicate_hint;
  if (hint?.state === 'NONE_IN_SCOPE' && hint.candidate_count === 0 && hint.scope?.source_known === true) return hint.state;
  if (hint?.state === 'SUSPECTED' && Number.isSafeInteger(hint.candidate_count) && hint.candidate_count > 0) return hint.state;
  return 'UNCHECKED';
}

export function importRiskUnresolved(row, choice) {
  if (importRiskState(row) === 'NONE_IN_SCOPE' || choice.decision === 'SKIP') return false;
  if (['LINK_EXISTING','DUPLICATE'].includes(choice.resolution) && choice.target) return false;
  return !(choice.resolution === 'NEW' && choice.acknowledge_new_risk === true);
}

export function importRiskLabel(row) {
  const state = importRiskState(row);
  if (state === 'SUSPECTED') return `疑似重复 · ${row.duplicate_hint.candidate_count} 个候选（未认定重复）`;
  if (state === 'UNCHECKED') return '风险未核验 · 不能按零候选处理';
  return '本次核验范围内未发现候选，不代表全库无重复';
}

export function importIssueMessage(code) {
  return ({IMPORT_REVIEW_REQUIRED:'新增现金风险尚未明确：核对候选后选择跳过、具名配对，或明确确认另一笔真实交易',
    ROW_INVALID:'来源行会计字段无效，不能接受；可跳过并保留原证据',
    ROW_RECHECK_REQUIRED:'旧跳过／问题行需明确重新检查',
    FACT_CONFLICT:'来源身份与已有事实冲突，不能覆盖旧事实',
    IDENTITY_AMBIGUOUS:'来源身份不唯一，须核对而不能自动选择',
    SOURCE_IDENTITY_REQUIRED:'来源身份不足，不能自动认定同一交易',
    STALE_PREVIEW:'来源、账户或账务前提已变化，须重新核验',
    PREVIEW_CHANGED:'预览已变化，须读取最新预览并重新核验',
    WRITE_BUSY:'本批未提交：数据库忙或写入预算不足，稍后重新核验后由你再次确认'}[code]) || '未通过导入校验，请按完整计划中的文件和行号核对';
}

import { financialIssueReason } from './financial-copy.js';

// Infer only an unchosen draft. Saved/user choices remain authoritative;
// this is not permission to bypass duplicate risk or confirm a financial write.
export function importRowChoice(row, selectedChoice) {
  if (selectedChoice) return selectedChoice;
  if (row.choice) return row.choice;
  const normal = ['NEW', 'EXISTING', 'PROCESSED'].includes(row.classification)
    && !row.issue_codes?.length && importRiskState(row) === 'NONE_IN_SCOPE';
  // Server recommendation describes the full preview, not just this page.
  // Never use an ACCEPT recommendation to bypass invalid fields or risk.
  return {decision:normal && row.default_decision !== 'SKIP' ? 'ACCEPT' : 'SKIP', recheck:false, account_ref_id:null};
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
  return financialIssueReason(code,'未通过导入校验，请按完整计划中的文件和行号核对');
}

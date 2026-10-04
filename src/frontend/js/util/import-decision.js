// Infer only an unchosen draft. Saved/user choices remain authoritative;
// this is not permission to bypass duplicate risk or confirm a financial write.
export function importRowChoice(row, selectedChoice) {
  if (selectedChoice) return selectedChoice;
  if (row.choice) return row.choice;
  const normal = ['NEW', 'EXISTING', 'PROCESSED'].includes(row.classification)
    && !row.issue_codes?.length;
  return {decision:normal ? 'ACCEPT' : 'SKIP', recheck:false, account_ref_id:null};
}

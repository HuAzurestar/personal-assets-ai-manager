import { jsonRequest, isUnknownWrite } from '../api/client.js';
import { resourceId } from '../util/core.js';

export function manualSourceDraft(values) {
  const result = {account_id:0};
  for (const [key,limit] of [['name',120],['institution',120],['reference',200]]) {
    const value = values[key] ?? '';
    if (typeof value !== 'string' || value.length > limit) throw new Error('来源卡资料格式或长度不合法');
    result[key] = value;
  }
  if (!result.name.trim()) throw new Error('请为本次来源卡填写可辨认的名称');
  // Only human metadata, not parser namespace/identity/strength or cash intent.
  return result;
}

export function sourceCardCreation({valid=()=>true,
  create=values=>jsonRequest('/paam/ledger/v1/account-ref','POST',values)}={}) {
  let busy=false, unknown=false, completed=false;
  return {
    get busy(){return busy;}, get unknown(){return unknown;}, get completed(){return completed;},
    async submit(values) {
      if (busy || unknown || completed || !valid()) return null;
      const draft = manualSourceDraft(values);
      busy=true;
      try {
        const row = await create(draft);
        resourceId(row?.id);
        if (row.account_id !== 0 || row.status !== 'ACTIVE' || row.identity_strength !== 'UNKNOWN'
            || row.source_namespace !== '' || row.source_identity !== '')
          throw new Error('创建响应无法核对；请查询当前来源卡，不要重发创建');
        completed=true;
        return valid() ? row : null;
      } catch(error) {
        if (isUnknownWrite(error)) unknown=true;
        throw error;
      } finally {busy=false;}
    },
  };
}

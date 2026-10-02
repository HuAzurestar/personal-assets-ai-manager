// UI default is current; API omission still means explicit PO inspection of
// all stored cash outputs. "all" must survive URL/form/pagination transport.
export function flowVisibility(params) {
  const value = params.get('active');
  if (value == null || value === '') return 'true';
  if (['true', 'false', 'all'].includes(value)) return value;
  throw Object.assign(new Error('流水有效状态只能为当前有效、已停用历史或包含历史。'),
    {code: 'LIST_FILTER_VALUE_INVALID', status: 422});
}

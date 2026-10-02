import { resourceId } from './core.js';

// Draft identities belong to the UI only. Resolve current array indices at the
// final build boundary; never persist or submit these tokens as financial IDs.
export function draftIndex(rows, reference, label) {
  if (!reference) throw new Error(`请选择${label}`);
  const indices = rows.flatMap((row, index) => row.dataset.draftId === reference ? [index] : []);
  if (!indices.length) throw new Error(`引用的${label}已移除，请重新选择`);
  if (indices.length !== 1) throw new Error(`${label}草稿引用不唯一`);
  return indices[0];
}

export function positionTarget(reference, drafts) {
  if (reference.startsWith('new:')) return { new_position_index: draftIndex(drafts, reference.slice(4), '新对象') };
  if (reference.startsWith('existing:')) return { existing_position_id: resourceId(reference.slice(9)) };
  throw new Error('请选择明确的数量对象');
}

export function linkTarget(cashReference, legReference, cashRows, legRows) {
  return { allocation_index: draftIndex(cashRows, cashReference, '现金拆分项'), leg_index: draftIndex(legRows, legReference, '数量腿') };
}

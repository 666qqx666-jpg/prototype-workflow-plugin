export const PENDING_STATES = Object.freeze(['carried', 'resolved', 'superseded', 'new']);
const sameSet = (left, right) => left.length === right.length && left.every((value) => right.includes(value));

export function validatePendingReconciliation({currentItems = [], previousItems = [], reconciliation = {}, sources = []}) {
  const errors = [];
  const sourceIds = new Set(sources.map(({sourceId}) => sourceId));
  const states = PENDING_STATES.map((state) => reconciliation?.[state]);
  if (states.some((items) => !Array.isArray(items))) return ['E_PENDING_RECONCILIATION state missing'];
  const ids = (items) => items.map((item) => item?.pendingId);
  const currentIds = ids(currentItems), previousIds = ids(previousItems);
  const carriedIds = ids(reconciliation.carried), resolvedIds = ids(reconciliation.resolved);
  const supersededIds = ids(reconciliation.superseded), newIds = ids(reconciliation.new);
  const classified = [...carriedIds, ...resolvedIds, ...supersededIds, ...newIds];
  if ([currentIds, previousIds, classified].some((list) => list.some((id) => typeof id !== 'string' || !id.trim()) || new Set(list).size !== list.length)) errors.push('E_PENDING_RECONCILIATION duplicate or missing pendingId');
  if (!sameSet(previousIds, [...carriedIds, ...resolvedIds, ...supersededIds])) errors.push('E_PENDING_RECONCILIATION previous set is not closed');
  if (!sameSet(currentIds, [...carriedIds, ...newIds])) errors.push('E_PENDING_RECONCILIATION current set differs from carried plus new');
  const validRefs = (refs) => Array.isArray(refs) && refs.length > 0 && refs.every((ref) => sourceIds.has(ref?.sourceId) && ref.locator?.trim() && ref.excerpt?.trim());
  for (const item of currentItems) {
    const assessment = item?.authorityAssessment;
    if (item.status !== 'unresolved' || !validRefs(assessment?.checkedSourceRefs) || !assessment?.conclusion?.trim() || !assessment?.reason?.trim()) errors.push(`E_PENDING_RECONCILIATION current ${item?.pendingId ?? 'unknown'}`);
  }
  for (const item of [...reconciliation.resolved, ...reconciliation.superseded]) {
    if (!item?.conclusion?.trim() || !validRefs(item.sourceRefs)) errors.push(`E_PENDING_RECONCILIATION closed ${item?.pendingId ?? 'unknown'}`);
  }
  for (const item of reconciliation.superseded) if (!newIds.includes(item.newPendingId) || item.newPendingId === item.pendingId) errors.push(`E_PENDING_RECONCILIATION superseded ${item.pendingId}`);
  return [...new Set(errors)];
}

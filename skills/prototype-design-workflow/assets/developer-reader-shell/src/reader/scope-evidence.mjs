const PRODUCT_AUTHORITIES = new Set(['current-product-requirement', 'confirmed-product-decision']);
const VERIFIED_AUTHORITIES = new Set(['observed-runtime', 'current-code', 'same-version-screenshot']);
const SAMPLE_SOURCE_TYPES = new Set(['runtime', 'code', 'screenshot', 'prototype-baseline']);

export function validateScopeEvidence({scopeEvidence, evidenceSources}) {
  const errors = [];
  const byId = new Map((Array.isArray(evidenceSources) ? evidenceSources : [])
    .filter((source) => source && typeof source.sourceId === 'string')
    .map((source) => [source.sourceId, source]));
  const validRefs = (refs, authorities) => Array.isArray(refs) && refs.length > 0 && refs.every((ref) => {
    const source = byId.get(ref?.sourceId);
    return source && authorities.has(source.authority) && typeof ref.locator === 'string' && ref.locator.trim()
      && typeof ref.excerpt === 'string' && ref.excerpt.trim();
  });
  const scope = scopeEvidence?.productScope;
  if (!scope || typeof scope.summary !== 'string' || !scope.summary.trim() || !Array.isArray(scope.included)
      || !Array.isArray(scope.excluded) || !validRefs(scope.sourceRefs, PRODUCT_AUTHORITIES)) {
    errors.push('E_SCOPE_EVIDENCE_INVALID productScope');
  }
  const sample = scopeEvidence?.sampleSource;
  if (!sample || !['present', 'not-used'].includes(sample.status) || !Array.isArray(sample.samples)
      || (sample.status === 'present' && sample.samples.length === 0)
      || (sample.status === 'not-used' && (sample.samples.length !== 0 || !sample.note?.trim()))) {
    errors.push('E_SCOPE_EVIDENCE_INVALID sampleSource');
  } else {
    for (const item of sample.samples) {
      if (!item?.sampleId?.trim() || !item?.label?.trim() || !item?.context?.trim()
          || !Array.isArray(item.sourceIds) || item.sourceIds.length === 0
          || item.sourceIds.some((id) => !SAMPLE_SOURCE_TYPES.has(byId.get(id)?.sourceType))) {
        errors.push(`E_SCOPE_EVIDENCE_INVALID sampleSource/${item?.sampleId ?? 'unknown'}`);
      }
    }
    if (sample.status === 'present' && typeof sample.note !== 'string') errors.push('E_SCOPE_EVIDENCE_INVALID sampleSource/note');
  }
  const coverage = scopeEvidence?.verifiedCoverage;
  if (!coverage || !Array.isArray(coverage.verified) || !Array.isArray(coverage.notVerified)
      || !validRefs(coverage.sourceRefs, VERIFIED_AUTHORITIES)) {
    errors.push('E_SCOPE_EVIDENCE_INVALID verifiedCoverage');
  }
  return errors;
}

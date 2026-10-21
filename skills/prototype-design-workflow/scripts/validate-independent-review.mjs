import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

export const REQUIRED_NEGATIVE_CHECK_IDS = Object.freeze(['unrelated-field-preservation','empty-and-missing-values','historical-out-of-candidate-data','unauthorized-actions','concurrent-conflict','post-save-membership']);

export function validateIndependentReview({review, manifestBytes, prototypeBytes, evidenceBundleBytes, evidenceBundle = null, currentSnapshotBytes = null, previousManifestBytes = null, previousSnapshotBytes = null, requiredTriggers = []}) {
  const errors = [];
  const expected = {
    manifestHash: sha(manifestBytes),
    prototypeHash: sha(prototypeBytes),
    evidenceBundleHash: sha(evidenceBundleBytes)
  };
  for (const [key, value] of Object.entries(expected)) {
    if (review?.subject?.[key]?.toLowerCase() !== value) errors.push(`E_REVIEW_SUBJECT_HASH ${key}`);
  }
  for (const trigger of requiredTriggers) {
    if (!(review?.triggers ?? []).includes(trigger)) errors.push(`E_REVIEW_TRIGGER_MISSING ${trigger}`);
  }
  if (review?.schemaVersion === '2.0.0') {
    const sourceIds = new Set((evidenceBundle?.sources ?? []).map((source) => source.sourceId));
    const refsResolve = (refs) => Array.isArray(refs) && refs.length > 0 && refs.every((ref) => {
      if (typeof ref !== 'string' || !ref.trim()) return false;
      const value = ref.startsWith('evidence:') ? ref.slice('evidence:'.length) : ref;
      const sourceId = value.split(':', 1)[0];
      return sourceIds.has(ref) || sourceIds.has(value) || sourceIds.has(sourceId);
    });
    const subject = {
      currentSnapshotHash: currentSnapshotBytes === null ? null : sha(currentSnapshotBytes),
      previousManifestHash: previousManifestBytes === null ? null : sha(previousManifestBytes),
      previousSnapshotHash: previousSnapshotBytes === null ? null : sha(previousSnapshotBytes)
    };
    for (const [key, value] of Object.entries(subject)) if (review.subject?.[key] !== value) errors.push(`E_REVIEW_SUBJECT_HASH ${key}`);
    for (const section of ['scopeReview','pendingReview']) {
      if (review[section]?.result !== 'pass' || !refsResolve(review[section]?.evidenceRefs) || !review[section]?.note?.trim()) errors.push(`E_REVIEW_${section.toUpperCase()}`);
    }
    const checks = review.negativeChecks ?? [];
    const ids = checks.map((item) => item.checkId);
    if (new Set(ids).size !== ids.length || REQUIRED_NEGATIVE_CHECK_IDS.some((id) => !ids.includes(id))) errors.push('E_REVIEW_NEGATIVE_CHECKS missing or duplicate check');
    for (const item of checks) {
      if (item.applicability === 'applicable' && (!['pass','block'].includes(item.result) || !refsResolve(item.evidenceRefs))) errors.push(`E_REVIEW_NEGATIVE_CHECKS ${item.checkId} E_REVIEW_EVIDENCE_REF`);
      if (item.applicability === 'not-applicable' && (item.result !== 'not-applicable' || !item.note?.trim() || item.evidenceRefs?.length)) errors.push(`E_REVIEW_NEGATIVE_CHECKS ${item.checkId}`);
      if (item.applicability === 'applicable' && item.result === 'block') errors.push(`E_REVIEW_NEGATIVE_CHECKS ${item.checkId} block`);
    }
    const unresolvedRefs = (refs) => (refs ?? []).filter((ref) => {
      if (typeof ref !== 'string') return true;
      const value = ref.startsWith('evidence:') ? ref.slice('evidence:'.length) : ref;
      return !sourceIds.has(ref) && !sourceIds.has(value) && !sourceIds.has(value.split(':', 1)[0]);
    });
    for (const section of ['scopeReview', 'pendingReview']) for (const ref of unresolvedRefs(review[section]?.evidenceRefs)) errors.push(`E_REVIEW_EVIDENCE_REF ${section} ${ref}`);
    for (const item of checks) for (const ref of unresolvedRefs(item.evidenceRefs)) errors.push(`E_REVIEW_EVIDENCE_REF ${item.checkId} ${ref}`);
  }
  if (review?.reviewerRole !== 'independent' || review?.verdict !== 'pass') {
    errors.push('E_REVIEW_NOT_PASSED');
  }
  return {ok: errors.length === 0, errors};
}

function sha(value) {
  return createHash('sha256').update(value).digest('hex');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args=process.argv.slice(2), value=(flag)=>{const i=args.indexOf(flag);return i<0?null:args[i+1];};
    const required=['--review','--manifest','--prototype','--evidence','--current-snapshot'];
    if(required.some((flag)=>!value(flag))) throw new Error('E_REVIEW_INPUT_REQUIRED');
    const read=(flag)=>readFileSync(resolve(value(flag)));
    const reviewBytes=read('--review'),review=JSON.parse(reviewBytes.toString('utf8'));
    const base=dirname(fileURLToPath(import.meta.url));
    const require=createRequire(new URL('../assets/developer-reader-shell/package.json',import.meta.url));
    const Ajv=require('ajv/dist/2020').default;
    const schema=JSON.parse(readFileSync(resolve(base,'../assets/independent-review.schema.json'),'utf8'));
    const valid=new Ajv({allErrors:true,strict:false}).compile(schema);
    if(!valid(review)) throw new Error(`E_INDEPENDENT_REVIEW_SCHEMA ${valid.errors.map((e)=>e.instancePath).join(',')}`);
    const previousManifest=value('--previous-manifest')?read('--previous-manifest'):null;
    const previousSnapshot=value('--previous-snapshot')?read('--previous-snapshot'):null;
    if(Boolean(previousManifest)!==Boolean(previousSnapshot)) throw new Error('E_REVIEW_PREVIOUS_INPUT_REQUIRED');
    const evidenceBundleBytes=read('--evidence'),evidenceBundle=JSON.parse(evidenceBundleBytes.toString('utf8'));
    const evidenceSchema=JSON.parse(readFileSync(resolve(base,'../assets/evidence-bundle.schema.json'),'utf8'));
    if(!new Ajv({allErrors:true,strict:false}).compile(evidenceSchema)(evidenceBundle)) throw new Error('E_REVIEW_EVIDENCE_BUNDLE_INVALID');
    const result=validateIndependentReview({review,manifestBytes:read('--manifest'),prototypeBytes:read('--prototype'),evidenceBundleBytes,evidenceBundle,currentSnapshotBytes:read('--current-snapshot'),previousManifestBytes:previousManifest,previousSnapshotBytes:previousSnapshot,requiredTriggers:args.flatMap((item,i)=>item==='--required-trigger'&&args[i+1]?[args[i+1]]:[])});
    if(!result.ok) throw new Error(result.errors.join('\n'));
    process.stdout.write('PASS independent-review-v2\n');
  } catch(error) { process.stderr.write(`FAIL ${error.message}\n`); process.exitCode=1; }
}

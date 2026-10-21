import {createHash} from 'node:crypto';
import {existsSync, mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {validateEvidenceBundle, validatePrototypeIdentity} from './validate-evidence-bundle.mjs';
import {validateIndependentReview} from './validate-independent-review.mjs';

const shellPackage = new URL('../assets/developer-reader-shell/package.json', import.meta.url);
const shellRequire = createRequire(shellPackage);
const Ajv2020 = shellRequire('ajv/dist/2020').default;

async function main() {
  const args = process.argv.slice(2);
  const manifestArg = valueAfter(args, '--manifest');
  const prototypeArg = valueAfter(args, '--prototype');
  const evidenceArg = valueAfter(args, '--evidence');
  const reviewArg = valueAfter(args, '--review');
  const currentSnapshotArg = valueAfter(args, '--current-snapshot');
  const previousManifestArg = valueAfter(args, '--previous-manifest');
  const previousSnapshotArg = valueAfter(args, '--previous-snapshot');
  const baselineReceiptArg = valueAfter(args, '--lineage-baseline-receipt');
  const workspaceRootArg = valueAfter(args, '--workspace-root');
  const evidenceRoots = valuesAfter(args, '--evidence-root');
  const snapshotOutArg = valueAfter(args, '--snapshot-out');
  const metadataOutArg = valueAfter(args, '--metadata-out');
  const deferIndependentReview = args.includes('--defer-independent-review');
  if (!manifestArg || !prototypeArg) {
    throw new Error('Usage: node scripts/validate-prototype-handoff.mjs --manifest <manifest.json> --prototype <prototype.html> --evidence <evidence-bundle.json> --workspace-root <dir> --evidence-root <dir> [--evidence-root <dir>...] [--review <independent-review.json>] [--allow-legacy-v2]');
  }
  if (deferIndependentReview && reviewArg) throw new Error('E_REVIEW_DEFER_CONFLICT');

  const manifestPath = resolve(manifestArg);
  const prototypePath = resolve(prototypeArg);
  const manifestBytes = readFileSync(manifestPath);
  const manifest = parseStrictJson(manifestBytes.toString('utf8'));
  const isLegacyV2 = manifest.schemaVersion === '2.0.0';
  if (isLegacyV2 && !args.includes('--allow-legacy-v2')) {
    throw new Error('E_LEGACY_V2_EXPLICIT_OPT_IN_REQUIRED');
  }
  const isLegacyV3 = manifest.schemaVersion === '3.0.0' && args.includes('--allow-legacy-v3');
  if (!isLegacyV2 && !isLegacyV3 && manifest.schemaVersion !== '4.0.0') {
    throw new Error(manifest.schemaVersion === '3.0.0' ? 'E_MANIFEST_V4_REQUIRED' : `E_SCHEMA_VERSION ${manifest.schemaVersion ?? 'missing'}`);
  }

  const schemaUrl = isLegacyV2
    ? new URL('../assets/change-manifest-v2.schema.json', import.meta.url)
    : isLegacyV3
      ? new URL('../assets/change-manifest-v3.schema.json', import.meta.url)
      : new URL('../assets/change-manifest.schema.json', import.meta.url);
  validateWithSchema(manifest, schemaUrl, 'E_SCHEMA_VALIDATION');
  const {validateManifest} = await import(isLegacyV2
    ? '../assets/developer-reader-shell/src/reader/contract-v2.mjs'
    : isLegacyV3
      ? '../assets/developer-reader-shell/src/reader/contract-v3.mjs'
      : '../assets/developer-reader-shell/src/reader/contract.mjs');
  let lineageContext = {};
  if (!isLegacyV2 && !isLegacyV3) {
    const lineageArgs = [previousManifestArg, previousSnapshotArg, baselineReceiptArg];
    if (manifest.delivery?.lineage?.mode === 'initial' && lineageArgs.some(Boolean)) throw new Error('E_LINEAGE_INPUT_REQUIRED initial forbids previous inputs');
    if (manifest.delivery?.lineage?.mode === 'revision' && lineageArgs.some((item) => !item)) throw new Error('E_LINEAGE_INPUT_REQUIRED revision needs previous manifest, snapshot and receipt');
    if (manifest.delivery?.lineage?.mode === 'revision') {
      const previousManifestBytes = readFileSync(resolve(previousManifestArg));
      const previousManifest = JSON.parse(previousManifestBytes.toString('utf8'));
      const previousSnapshotBytes = readFileSync(resolve(previousSnapshotArg));
      const receiptBytes = readFileSync(resolve(baselineReceiptArg));
      const receipt = JSON.parse(receiptBytes.toString('utf8'));
      validateWithSchema(receipt, new URL('../assets/lineage-baseline-receipt.schema.json', import.meta.url), 'E_LINEAGE_RECEIPT_SCHEMA');
      if (receipt.previousManifest.sha256 !== createHash('sha256').update(previousManifestBytes).digest('hex') || receipt.previousSnapshot.sha256 !== createHash('sha256').update(previousSnapshotBytes).digest('hex')) throw new Error('E_LINEAGE_HASH_MISMATCH');
      if (manifest.delivery.lineage.baselineReceiptSha256 !== createHash('sha256').update(receiptBytes).digest('hex')) throw new Error('E_LINEAGE_HASH_MISMATCH baseline receipt');
      lineageContext = {previousManifest, previousManifestSha256: createHash('sha256').update(previousManifestBytes).digest('hex'), previousSnapshotSha256: createHash('sha256').update(previousSnapshotBytes).digest('hex'), previousManifestBytes, previousSnapshotBytes, baselineReceipt: receipt};
    }
  }
  const contract = validateManifest(manifest, lineageContext);
  if (!contract.ok) {
    throw new Error(contract.errors.map(({code, path}) => `${code} ${path}`).join('\nFAIL '));
  }
  ensureNoBlockingPendingItems(manifest);
  ensureCoverageClosure(manifest);

  let evidenceValidation = null;
  let evidenceBundle = null;
  let evidenceBundleBytes = null;
  let requiredReviewTriggers = [];
  if (!isLegacyV2) {
    if (!evidenceArg) throw new Error('E_EVIDENCE_BUNDLE_REQUIRED');
    if (!workspaceRootArg) throw new Error('E_WORKSPACE_ROOT_REQUIRED');
    if (evidenceRoots.length === 0) throw new Error('E_EVIDENCE_ROOT_REQUIRED');
    const evidencePath = resolve(evidenceArg);
    evidenceBundleBytes = readFileSync(evidencePath);
    evidenceBundle = JSON.parse(evidenceBundleBytes.toString('utf8'));
    validateWithSchema(evidenceBundle, new URL('../assets/evidence-bundle.schema.json', import.meta.url), 'E_EVIDENCE_BUNDLE_SCHEMA');
    evidenceValidation = validateEvidenceBundle({manifest, bundle: evidenceBundle, bundlePath: evidencePath, allowedRoots: evidenceRoots});
    if (!evidenceValidation.ok) throw new Error(evidenceValidation.errors.join('\nFAIL '));
    const identity = validatePrototypeIdentity({manifest, prototypePath, workspaceRoot: workspaceRootArg, loadedSources: evidenceValidation.loadedSources});
    if (!identity.ok) throw new Error(identity.errors.join('\nFAIL '));
    requiredReviewTriggers = [...new Set([
      ...(manifest.delivery.riskTriggers ?? []),
      ...evidenceValidation.trustTriggers,
      ...identity.trustTriggers,
      ...(manifest.globalRules.length > 0 ? ['global-rule-classification'] : []),
      ...(manifest.surfaces.length > 1 ? ['complex-multi-surface'] : []),
      ...(manifest.requirement.coverage === 'full' ? ['full-coverage'] : [])
    ])];
  }

  const providedHtml = readFileSync(prototypePath, 'utf8');
  const prototypeHtml = extractPrototypeHtml(providedHtml);
  const prototypeHash = createHash('sha256').update(prototypeHtml).digest('hex');
  if (prototypeHash !== manifest.prototype.sourceHash.toLowerCase()) {
    throw new Error(`E_PROTOTYPE_HASH expected=${manifest.prototype.sourceHash} actual=${prototypeHash}`);
  }
  if (!isLegacyV2 && requiredReviewTriggers.length > 0 && !deferIndependentReview) {
    if (!reviewArg) throw new Error(`E_INDEPENDENT_REVIEW_REQUIRED triggers=${requiredReviewTriggers.join(',')}`);
    const review = JSON.parse(readFileSync(resolve(reviewArg), 'utf8'));
    const currentSnapshotBytes = currentSnapshotArg ? readFileSync(resolve(currentSnapshotArg)) : null;
    if (review.schemaVersion === '2.0.0' && !currentSnapshotBytes) throw new Error('E_REVIEW_SNAPSHOT_REQUIRED');
    validateWithSchema(review, new URL(review.schemaVersion === '1.0.0' && isLegacyV3 ? '../assets/independent-review-v1.schema.json' : '../assets/independent-review.schema.json', import.meta.url), 'E_INDEPENDENT_REVIEW_SCHEMA');
    const reviewValidation = validateIndependentReview({
      review,
      manifestBytes,
      prototypeBytes: Buffer.from(providedHtml),
      evidenceBundleBytes,
      evidenceBundle,
      currentSnapshotBytes,
      previousManifestBytes: lineageContext.previousManifestBytes ?? null,
      previousSnapshotBytes: lineageContext.previousSnapshotBytes ?? null,
      requiredTriggers: requiredReviewTriggers
    });
    if (!reviewValidation.ok) throw new Error(reviewValidation.errors.join('\nFAIL '));
  }
  if (deferIndependentReview) process.stdout.write('PASS independent review deferred until after Builder Handoff\n');

  const {auditAnchors} = await import('../assets/developer-reader-shell/scripts/audit-anchors.mjs');
  const anchors = await auditAnchors({prototypeHtml, manifest, validateContract: !isLegacyV2 && !isLegacyV3, lineageContext});
  process.stdout.write(`PASS manifest ${manifest.cards.length} cards\n`);
  process.stdout.write(`PASS ${anchors.length} anchors across ${manifest.surfaces.length} surfaces\n`);
  process.stdout.write(`PASS coverage ${manifest.requirement.coverage}\n`);

  if (isLegacyV2) {
    process.stdout.write('PASS legacy-v2 structural validation only; not eligible for evidence-grounded Standard readiness\n');
    return;
  }

  const temporary = mkdtempSync(join(tmpdir(), 'prototype-reader-runtime-'));
  try {
    const snapshotPath = snapshotOutArg ? resolve(snapshotOutArg) : join(temporary, 'snapshot.html');
    const metadataPath = metadataOutArg ? resolve(metadataOutArg) : `${snapshotPath}.build.json`;
    if (snapshotOutArg && (existsSync(snapshotPath) || existsSync(metadataPath))) throw new Error('E_SNAPSHOT_OUTPUT_EXISTS');
    runNode([
      fileURLToPath(new URL('../assets/developer-reader-shell/scripts/build-snapshot.mjs', import.meta.url)),
      '--manifest', manifestPath,
      '--prototype', prototypePath,
      '--evidence', resolve(evidenceArg),
      '--workspace-root', resolve(workspaceRootArg),
      ...evidenceRoots.flatMap((root) => ['--evidence-root', resolve(root)]),
      ...(reviewArg ? ['--review', resolve(reviewArg)] : []),
      ...(deferIndependentReview ? ['--defer-independent-review'] : []),
      '--out', snapshotPath,
      '--metadata', metadataPath,
      ...(isLegacyV3 ? ['--allow-legacy-v3'] : [])
      ,...(previousManifestArg ? ['--previous-manifest', resolve(previousManifestArg)] : [])
      ,...(previousSnapshotArg ? ['--previous-snapshot', resolve(previousSnapshotArg)] : [])
      ,...(baselineReceiptArg ? ['--lineage-baseline-receipt', resolve(baselineReceiptArg)] : [])
      ,...(currentSnapshotArg ? ['--current-snapshot', resolve(currentSnapshotArg)] : [])
    ]);
    if (snapshotOutArg) process.stdout.write(`PASS snapshot ${snapshotPath} metadata ${metadataPath}\n`);
    process.stdout.write(`PASS runtime ${manifest.cards.length} cards: bidirectional, visible connector, state restore\n`);
  } finally {
    if (!snapshotOutArg) rmSync(temporary, {recursive: true, force: true});
  }
}

function validateWithSchema(value, schemaUrl, errorCode) {
  const schema = JSON.parse(readFileSync(fileURLToPath(schemaUrl), 'utf8'));
  const validate = new Ajv2020({allErrors: true, strict: false}).compile(schema);
  if (!validate(value)) {
    const details = validate.errors.map(({instancePath, keyword}) => `${instancePath || '$'}:${keyword}`).join(',');
    throw new Error(`${errorCode} ${details}`);
  }
}

function ensureCoverageClosure(manifest) {
  if (manifest.requirement.coverage !== 'full') return;
  const coveredPages = new Set(manifest.cards.map(({pageId}) => pageId));
  const uncoveredPages = manifest.pages.map(({pageId}) => pageId).filter((pageId) => !coveredPages.has(pageId));
  const scopeGapCount = [...(manifest.requirement.scope?.navigationOnly ?? []), ...(manifest.requirement.scope?.outOfSlice ?? [])].length;
  if (uncoveredPages.length > 0 || scopeGapCount > 0) {
    throw new Error(`E_FULL_COVERAGE_INCOMPLETE uncoveredPages=${uncoveredPages.join(',') || 'none'} scopeGaps=${scopeGapCount}`);
  }
}

function ensureNoBlockingPendingItems(manifest) {
  const blocking = [...manifest.cards, ...(manifest.globalRules ?? [])]
    .flatMap(({pendingItems = []}) => pendingItems)
    .filter(({blocksDelivery}) => blocksDelivery);
  if (blocking.length > 0) throw new Error(`E_BLOCKING_PENDING_ITEMS count=${blocking.length}`);
}

function extractPrototypeHtml(html) {
  const match = html.match(/window\.__PROTOTYPE_BASE64__\s*=\s*['"]([^'"]+)['"]/);
  return match ? Buffer.from(match[1], 'base64').toString('utf8') : html;
}

function runNode(args) {
  const result = spawnSync(process.execPath, args, {encoding: 'utf8'});
  if (result.status !== 0) throw new Error(result.stderr.trim() || result.stdout.trim() || 'E_RUNTIME_BUILD');
  return result.stdout;
}

function valueAfter(values, flag) {
  const index = values.indexOf(flag);
  return index >= 0 ? values[index + 1] : null;
}

function valuesAfter(values, flag) {
  return values.flatMap((value, index) => value === flag && values[index + 1] ? [values[index + 1]] : []);
}

export function parseStrictJson(source) {
  let index = 0;
  const whitespace = () => { while (index < source.length && /\s/.test(source[index])) index += 1; };
  const string = () => {
    const start = index++;
    while (index < source.length) {
      if (source[index] === '\\') { index += 2; continue; }
      if (source[index++] === '"') return JSON.parse(source.slice(start, index));
    }
    throw new Error('E_JSON_PARSE');
  };
  const value = () => {
    whitespace();
    if (source[index] === '{') {
      index += 1; whitespace(); const keys = new Set();
      if (source[index] === '}') { index += 1; return; }
      while (index < source.length) {
        whitespace(); if (source[index] !== '"') throw new Error('E_JSON_PARSE');
        const key = string(); if (keys.has(key)) throw new Error(`E_DUPLICATE_JSON_KEY ${key}`); keys.add(key);
        whitespace(); if (source[index++] !== ':') throw new Error('E_JSON_PARSE'); value(); whitespace();
        if (source[index] === '}') { index += 1; return; }
        if (source[index++] !== ',') throw new Error('E_JSON_PARSE');
      }
      throw new Error('E_JSON_PARSE');
    }
    if (source[index] === '[') {
      index += 1; whitespace(); if (source[index] === ']') { index += 1; return; }
      while (index < source.length) { value(); whitespace(); if (source[index] === ']') { index += 1; return; } if (source[index++] !== ',') throw new Error('E_JSON_PARSE'); }
      throw new Error('E_JSON_PARSE');
    }
    if (source[index] === '"') { string(); return; }
    const match = source.slice(index).match(/^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
    if (!match) throw new Error('E_JSON_PARSE'); index += match[0].length;
  };
  value(); whitespace(); if (index !== source.length) throw new Error('E_JSON_PARSE');
  return JSON.parse(source);
}

main().catch((error) => {
  process.stderr.write(`FAIL ${error.message}\n`);
  process.exit(1);
});

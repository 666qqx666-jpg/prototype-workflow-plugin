import {
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync
} from 'node:fs';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {dirname, resolve} from 'node:path';
import {build} from 'esbuild';
import {auditAnchors} from './audit-anchors.mjs';
import {auditReaderRuntime} from './audit-reader-runtime.mjs';
import {validateEvidenceBundle, validatePrototypeIdentity} from '../../../scripts/validate-evidence-bundle.mjs';
import {validateSnapshotReview} from '../../../scripts/validate-snapshot-review.mjs';
import {validateFrozenReviewMigration} from '../src/reader/frozen-review-migration.mjs';

const root = resolve(import.meta.dirname, '..');
const src = resolve(root, 'src');
const args = process.argv.slice(2);
const manifestArg = valueAfter(args, '--manifest');
const prototypeArg = valueAfter(args, '--prototype');
const outputArg = valueAfter(args, '--out');
const evidenceArg = valueAfter(args, '--evidence');
const workspaceRootArg = valueAfter(args, '--workspace-root');
const evidenceRoots = valuesAfter(args, '--evidence-root');
const reviewArg = valueAfter(args, '--review');
const deferIndependentReview = args.includes('--defer-independent-review');
const currentSnapshotArg = valueAfter(args, '--current-snapshot');
const migrationDescriptorArg = valueAfter(args, '--migration-descriptor');
const isLegacyV3=args.includes('--allow-legacy-v3');

if (!manifestArg || !prototypeArg || !outputArg || !workspaceRootArg || (!migrationDescriptorArg && (!evidenceArg || evidenceRoots.length === 0))) {
  throw new Error(
    'Usage: node scripts/build-snapshot.mjs --manifest <manifest.json> --prototype <prototype.html> --evidence <evidence-bundle.json> --workspace-root <dir> --evidence-root <dir> [--evidence-root <dir>...] [--review <independent-review.json>] --out <snapshot.html>'
  );
}

const manifestPath = resolve(manifestArg);
const prototypePath = resolve(prototypeArg);
const htmlTarget = resolve(outputArg);
const metadataTarget = resolve(valueAfter(args, '--metadata') ?? `${htmlTarget}.build.json`);
const manifestText = readFileSync(manifestPath, 'utf8');
const manifest = JSON.parse(manifestText);
const {validateManifest}=await import(isLegacyV3?'../src/reader/contract-v3.mjs':'../src/reader/contract.mjs');
if (migrationDescriptorArg) {
  const legacyShape = validateManifest(manifest);
  if (!legacyShape.ok) throw new Error(`E_SCHEMA_VALIDATION ${legacyShape.errors.map(({code,path}) => `${code}:${path}`).join(',')}`);
} else {
  validateSchema(manifest, resolve(root, isLegacyV3 ? '../change-manifest-v3.schema.json' : '../change-manifest.schema.json'), 'E_SCHEMA_VALIDATION');
}
const previousManifestArg = valueAfter(args, '--previous-manifest');
const previousSnapshotArg = valueAfter(args, '--previous-snapshot');
const baselineReceiptArg = valueAfter(args, '--lineage-baseline-receipt');
const previousManifestBytes = previousManifestArg ? readFileSync(resolve(previousManifestArg)) : null;
const previousSnapshotBytes = previousSnapshotArg ? readFileSync(resolve(previousSnapshotArg)) : null;
const baselineReceiptBytes = baselineReceiptArg ? readFileSync(resolve(baselineReceiptArg)) : null;
const previousManifest = previousManifestBytes ? JSON.parse(previousManifestBytes.toString('utf8')) : null;
const baselineReceipt = baselineReceiptBytes ? JSON.parse(baselineReceiptBytes.toString('utf8')) : null;
const lineageContext = previousManifest ? {previousManifest, previousManifestSha256:sha(previousManifestBytes), previousSnapshotSha256:sha(previousSnapshotBytes), previousManifestBytes, previousSnapshotBytes, baselineReceipt} : {};
const validation = validateManifest(manifest, lineageContext);
const migration = migrationDescriptorArg
  ? validateFrozenReviewMigration({descriptorPath: resolve(migrationDescriptorArg), manifestPath, snapshotPath: null, metadataPath: null})
  : null;
if (migration && (!isLegacyV3 || manifest.requirement.coverage !== 'pilot-slice' || !deferIndependentReview || reviewArg)) {
  throw new Error('E_MIGRATION_PROFILE');
}

if (!validation.ok) {
  throw new Error(
    `manifest is not eligible for a read-only snapshot: ${validation.errors.map(({code}) => code).join(',')}`
  );
}
let evidenceBundleBytes = null;
let evidenceBundle = null;
let evidence = {trustTriggers: [], loadedSources: new Map()};
let identity = {trustTriggers: []};
if (!migration) {
  const evidencePath = resolve(evidenceArg);
  evidenceBundleBytes = readFileSync(evidencePath);
  evidenceBundle = JSON.parse(evidenceBundleBytes.toString('utf8'));
  validateSchema(evidenceBundle, resolve(root, '../evidence-bundle.schema.json'), 'E_EVIDENCE_BUNDLE_SCHEMA');
  evidence = validateEvidenceBundle({manifest, bundle: evidenceBundle, bundlePath: evidencePath, allowedRoots: evidenceRoots});
  if (!evidence.ok) throw new Error(evidence.errors.join('\n'));
  identity = validatePrototypeIdentity({manifest, prototypePath, workspaceRoot: workspaceRootArg, loadedSources: evidence.loadedSources});
  if (!identity.ok) throw new Error(identity.errors.join('\n'));
}
const requiredReviewTriggers = [...new Set([
  ...(manifest.delivery.riskTriggers ?? []),
  ...evidence.trustTriggers,
  ...identity.trustTriggers,
  ...(manifest.globalRules.length > 0 ? ['global-rule-classification'] : []),
  ...(manifest.surfaces.length > 1 ? ['complex-multi-surface'] : []),
  ...(manifest.requirement.coverage === 'full' ? ['full-coverage'] : [])
])];
if (requiredReviewTriggers.length > 0 && !deferIndependentReview) {
  if (!reviewArg) throw new Error(`E_INDEPENDENT_REVIEW_REQUIRED triggers=${requiredReviewTriggers.join(',')}`);
  const review = JSON.parse(readFileSync(resolve(reviewArg), 'utf8'));
  const currentSnapshotBytes = currentSnapshotArg ? readFileSync(resolve(currentSnapshotArg)) : null;
  if (review.schemaVersion === '2.0.0' && !currentSnapshotBytes) throw new Error('E_REVIEW_SNAPSHOT_REQUIRED');
  validateSchema(review, resolve(root, isLegacyV3&&review.schemaVersion==='1.0.0'?'../independent-review-v1.schema.json':'../independent-review.schema.json'), 'E_INDEPENDENT_REVIEW_SCHEMA');
  const reviewValidation = validateSnapshotReview({
    review,
    manifestBytes: Buffer.from(manifestText),
    prototypeBytes: readFileSync(prototypePath),
    evidenceBundleBytes,
    evidenceBundle,
    currentSnapshotBytes,
    previousManifestBytes,
    previousSnapshotBytes,
    requiredTriggers: requiredReviewTriggers
  });
  if (!reviewValidation.ok) throw new Error(reviewValidation.errors.join('\n'));
}
if (deferIndependentReview && reviewArg) throw new Error('E_REVIEW_DEFER_CONFLICT');
const blockingPendingCount = [...manifest.cards, ...manifest.globalRules]
  .flatMap(({pendingItems = []}) => pendingItems)
  .filter(({blocksDelivery}) => blocksDelivery).length;
if (blockingPendingCount > 0) throw new Error(`E_BLOCKING_PENDING_ITEMS count=${blockingPendingCount}`);
if (manifest.requirement.coverage === 'full') {
  const coveredPages = new Set(manifest.cards.map(({pageId}) => pageId));
  const uncoveredPages = manifest.pages.map(({pageId}) => pageId).filter((pageId) => !coveredPages.has(pageId));
  const scopeGapCount = [...(manifest.requirement.scope?.navigationOnly ?? []), ...(manifest.requirement.scope?.outOfSlice ?? [])].length;
  if (uncoveredPages.length > 0 || scopeGapCount > 0) {
    throw new Error(`E_FULL_COVERAGE_INCOMPLETE uncoveredPages=${uncoveredPages.join(',') || 'none'} scopeGaps=${scopeGapCount}`);
  }
}

const prototypeBytes = readFileSync(prototypePath);
const prototypeHash = sha(prototypeBytes);
if (prototypeHash !== manifest.prototype.sourceHash.toLowerCase()) {
  throw new Error('prototype source hash differs from manifest.prototype.sourceHash');
}

await auditAnchors({prototypeHtml: prototypeBytes.toString('utf8'), manifest, validateContract: !isLegacyV3, lineageContext});

const bundle = await build({
  entryPoints: [resolve(src, 'reader/main.mjs')],
  bundle: true,
  format: 'iife',
  write: false,
  minify: false,
  legalComments: 'none'
});
const readerBundle = bundle.outputFiles[0].text;
const shell = readFileSync(resolve(src, 'shell.html'), 'utf8');
const css = readFileSync(resolve(src, 'styles/reader.css'), 'utf8');
// The browser runtime re-validates v4 before mounting annotations.  Keep only
// the verified contract inputs it needs; source bytes are deliberately not
// embedded in the reader snapshot.
const runtimeLineageContext = {
  previousManifest: lineageContext.previousManifest,
  previousManifestSha256: lineageContext.previousManifestSha256,
  previousSnapshotSha256: lineageContext.previousSnapshotSha256,
  baselineReceipt: lineageContext.baselineReceipt
};
const output = shell
  .replace('__CSS__', css)
  .replace('__MANIFEST__', JSON.stringify(manifest).replace(/</g, '\\u003c'))
  .replace('__LINEAGE_CONTEXT__', JSON.stringify(runtimeLineageContext).replace(/</g, '\\u003c'))
  .replace('__PROTOTYPE_PAYLOAD__', prototypeBytes.toString('base64'))
  .replace('__BUNDLE__', readerBundle);

if (output.includes(manifestPath) || output.includes(prototypePath)) {
  throw new Error('snapshot leaks an absolute local source path');
}
if (!output.includes(isLegacyV3?'"3.0.0"':'"4.0.0"')) {
  throw new Error(`snapshot does not contain the evidence-grounded v${isLegacyV3?'3':'4'} manifest`);
}

mkdirSync(dirname(htmlTarget), {recursive: true});
mkdirSync(dirname(metadataTarget), {recursive: true});
// Keep an .html suffix so Chromium parses the file as HTML during the
// pre-publish runtime audit instead of displaying the temporary file as text.
const htmlTemporary = `${htmlTarget}.${process.pid}.tmp.html`;
const metadataTemporary = `${metadataTarget}.${process.pid}.tmp`;
const metadata = {
  buildMode: 'readonly-snapshot',
  coverage: manifest.requirement.coverage,
  schemaVersion: manifest.schemaVersion,
  cardCount: manifest.cards.length,
  globalRuleCount: manifest.globalRules.length,
  prototypeSha256: prototypeHash,
  manifestSha256: sha(manifestText),
  readerSha256: hashReaderSources(css, shell),
  outputSha256: sha(output),
  readerComposition: manifest.delivery.readerComposition,
  ...(migrationDescriptorArg ? {migrationDescriptorSha256: sha(readFileSync(resolve(migrationDescriptorArg)))} : {}),
  previousManifestSha256: previousManifestBytes ? sha(previousManifestBytes) : null,
  previousSnapshotSha256: previousSnapshotBytes ? sha(previousSnapshotBytes) : null,
  lineageBaselineReceiptSha256: baselineReceiptBytes ? sha(baselineReceiptBytes) : null,
  builtAt: new Date().toISOString()
};

try {
  writeFileSync(htmlTemporary, output);
  writeFileSync(metadataTemporary, `${JSON.stringify(metadata, null, 2)}\n`);
  if (migrationDescriptorArg) validateFrozenReviewMigration({descriptorPath: resolve(migrationDescriptorArg), manifestPath, snapshotPath: htmlTemporary, metadataPath: metadataTemporary});
  const decodedPayload = Buffer.from(
    output.match(/window\.__PROTOTYPE_BASE64__\s*=\s*'([^']+)'/)?.[1] ?? '',
    'base64'
  );
  if (!decodedPayload.equals(prototypeBytes)) {
    throw new Error('embedded prototype payload differs from source bytes');
  }
  await auditReaderRuntime({snapshotPath: htmlTemporary, manifest});
  renameSync(metadataTemporary, metadataTarget);
  renameSync(htmlTemporary, htmlTarget);
} catch (error) {
  safeUnlink(htmlTemporary);
  safeUnlink(metadataTemporary);
  throw error;
}

console.log(`PASS snapshot build with runtime audit: ${manifest.cards.length} cards, ${validation.summary.pages} pages`);

function valueAfter(values, flag) {
  const index = values.indexOf(flag);
  return index >= 0 ? values[index + 1] : null;
}

function valuesAfter(values, flag) {
  return values.flatMap((value, index) => value === flag && values[index + 1] ? [values[index + 1]] : []);
}

function validateSchema(value, schemaPath, errorCode) {
  const require = createRequire(new URL('../package.json', import.meta.url));
  const Ajv2020 = require('ajv/dist/2020').default;
  const validate = new Ajv2020({allErrors: true, strict: false}).compile(JSON.parse(readFileSync(schemaPath, 'utf8')));
  if (!validate(value)) {
    const details = validate.errors.map(({instancePath, keyword}) => `${instancePath || '$'}:${keyword}`).join(',');
    throw new Error(`${errorCode} ${details}`);
  }
}

function sha(value) {
  return createHash('sha256').update(value).digest('hex');
}

function hashReaderSources(css, shell) {
  const files = [
    'bridge.mjs',
    'connector.mjs',
    'contract.mjs',
    'main.mjs',
    'render.mjs',
    'state-store.mjs',
    'surface-snapshot.mjs',
    'surface-state.mjs'
  ];
  const hash = createHash('sha256');
  for (const file of files) hash.update(readFileSync(resolve(src, 'reader', file)));
  hash.update(css);
  hash.update(shell);
  return hash.digest('hex');
}

function safeUnlink(path) {
  try {
    unlinkSync(path);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

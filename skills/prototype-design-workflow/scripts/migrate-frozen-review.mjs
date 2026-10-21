#!/usr/bin/env node
import {createHash} from 'node:crypto';
import {copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {basename, dirname, isAbsolute, relative, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {locateEvidenceText} from './validate-evidence-bundle.mjs';
import {createPrdSelection, extractFrozenReaderBindings} from '../assets/developer-reader-shell/src/reader/frozen-review-migration.mjs';

const args = process.argv.slice(2);
const get = (name) => { const index = args.indexOf(`--${name}`); return index < 0 ? null : args[index + 1]; };
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const required = ['freeze-dir', 'prd', 'prd-version', 'prd-sha256', 'prototype', 'manifest', 'reader', 'metadata', 'out-dir', 'receipt-id', 'created-at'];
try {
  const values = Object.fromEntries(required.map((key) => [key, get(key)]));
  if (required.some((key) => !values[key])) throw new Error('E_MIGRATION_INPUT_REQUIRED usage');
  const freezeDir = resolve(values['freeze-dir']);
  const freezeManifestPath = resolve(freezeDir, 'FREEZE-MANIFEST.json');
  const prdPath = resolve(values.prd), prototypePath = resolve(values.prototype);
  const oldManifestPath = resolve(values.manifest), oldReaderPath = resolve(values.reader), oldMetadataPath = resolve(values.metadata);
  const outDir = resolve(values['out-dir']);
  if (existsSync(outDir)) throw new Error('E_MIGRATION_OUTPUT_EXISTS');

  const freezeManifestBytes = readFileSync(freezeManifestPath);
  const freeze = JSON.parse(freezeManifestBytes.toString('utf8'));
  if (freeze.schemaVersion !== '1.0.0' || freeze.status !== 'product-convergence-frozen' ||
      !Array.isArray(freeze.artifacts) || !Array.isArray(freeze.supportingEvidence)) throw new Error('E_MIGRATION_FREEZE_MANIFEST');
  for (const entry of [...freeze.artifacts, ...freeze.supportingEvidence]) {
    if (!entry || !entry.archivePath || !/^[a-f0-9]{64}$/.test(entry.sha256) || !Number.isSafeInteger(entry.bytes)) throw new Error('E_MIGRATION_FREEZE_ENTRY');
    const artifactPath = resolve(freezeDir, entry.archivePath);
    const rel = relative(freezeDir, artifactPath);
    if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('E_MIGRATION_FREEZE_PATH');
    const bytes = readFileSync(artifactPath);
    if (bytes.length !== entry.bytes || sha(bytes) !== entry.sha256) throw new Error(`E_MIGRATION_FREEZE_ARTIFACT ${entry.role}`);
  }
  const findRole = (items, role) => items.find((item) => item.role === role);
  const frozenPrd = findRole(freeze.artifacts, 'prd-authority');
  const frozenPrototype = findRole(freeze.artifacts, 'business-prototype-current-source');
  const frozenReader = findRole(freeze.artifacts, 'single-cumulative-review-entry');
  const frozenManifest = findRole(freeze.supportingEvidence, 'reader-manifest');
  const frozenMetadata = findRole(freeze.supportingEvidence, 'reader-build-record');
  if (![frozenPrd, frozenPrototype, frozenReader, frozenManifest, frozenMetadata].every(Boolean)) throw new Error('E_MIGRATION_FREEZE_ROLES');

  const prdBytes = readFileSync(prdPath), prototypeBytes = readFileSync(prototypePath);
  const prdSelection = createPrdSelection({
    prdPath,
    prdBytes,
    frozenPrd,
    version: values['prd-version'],
    expectedSha256: values['prd-sha256']
  });
  const oldManifestBytes = readFileSync(oldManifestPath), oldReaderBytes = readFileSync(oldReaderPath), oldMetadataBytes = readFileSync(oldMetadataPath);
  const oldManifest = JSON.parse(oldManifestBytes.toString('utf8'));
  const oldMetadata = JSON.parse(oldMetadataBytes.toString('utf8'));
  if (oldManifest.schemaVersion !== '3.0.0' || oldManifest.requirement?.coverage !== 'pilot-slice' ||
      oldMetadata.buildMode !== 'product-review-cumulative-draft' ||
      oldMetadata.reviewEntryRole !== 'single-cumulative-review-entry') throw new Error('E_MIGRATION_LEGACY_PROFILE');
  if (sha(prototypeBytes) !== oldManifest.prototype.sourceHash.toLowerCase() ||
      oldMetadata.readerSha256 !== sha(oldReaderBytes) || oldMetadata.prototypeSha256 !== sha(prototypeBytes) ||
      frozenPrototype.sha256 !== sha(prototypeBytes) || frozenReader.sha256 !== sha(oldReaderBytes) ||
      frozenManifest.sha256 !== sha(oldManifestBytes) || frozenMetadata.sha256 !== sha(oldMetadataBytes)) {
    throw new Error('E_MIGRATION_LEGACY_HASH_BINDING');
  }
  let legacyBindings;
  try { legacyBindings = extractFrozenReaderBindings(oldReaderBytes.toString('utf8')); }
  catch { throw new Error('E_MIGRATION_LEGACY_READER_BINDING'); }
  if (JSON.stringify(legacyBindings.manifest) !== JSON.stringify(oldManifest) ||
      !legacyBindings.payload.equals(prototypeBytes)) throw new Error('E_MIGRATION_LEGACY_READER_BINDING');
  const primaryId = oldManifest.requirement.primarySourceId;
  const primarySource = oldManifest.evidenceSources.find(({sourceId}) => sourceId === primaryId);
  if (!primarySource || primarySource.sourceType !== 'prd' || primarySource.sourceHash !== frozenPrd.sha256) throw new Error('E_MIGRATION_PRD_BASELINE_BINDING');

  const manifest = structuredClone(oldManifest);
  manifest.evidenceSources.find(({sourceId}) => sourceId === primaryId).sourceHash = sha(prdBytes);
  const sourceRefs = [];
  for (const owner of [...(manifest.cards ?? []), ...(manifest.globalRules ?? [])]) {
    for (const binding of owner.evidenceBindings ?? []) for (const ref of binding.sourceRefs ?? []) if (ref.sourceId === primaryId) sourceRefs.push(ref);
  }
  if (!sourceRefs.length) throw new Error('E_MIGRATION_PRD_REFS_MISSING');
  for (const ref of sourceRefs) {
    const located = locateEvidenceText(prdBytes.toString('utf8'), ref.locator);
    if (!located.ok || !located.text.includes(ref.excerpt ?? '')) throw new Error(`E_MIGRATION_CURRENT_PRD_REF ${ref.locator}`);
  }
  if (!manifest.prototype?.sourcePath || isAbsolute(manifest.prototype.sourcePath) ||
      manifest.prototype.sourcePath.split(/[\\/]/).some((part) => part === '..')) throw new Error('E_MIGRATION_PROTOTYPE_PATH');

  mkdirSync(outDir, {recursive: false});
  mkdirSync(resolve(outDir, 'migration'), {recursive: true});
  cpSync(freezeDir, resolve(outDir, 'migration/freeze'), {recursive: true, errorOnExist: true});
  mkdirSync(resolve(outDir, 'inputs'), {recursive: true});
  mkdirSync(dirname(resolve(outDir, manifest.prototype.sourcePath)), {recursive: true});
  copyFileSync(prdPath, resolve(outDir, 'inputs/current-prd.md'));
  copyFileSync(prototypePath, resolve(outDir, manifest.prototype.sourcePath));
  copyFileSync(oldManifestPath, resolve(outDir, 'migration/previous-manifest.json'));
  copyFileSync(oldReaderPath, resolve(outDir, 'migration/previous-reader.html'));
  copyFileSync(oldMetadataPath, resolve(outDir, 'migration/previous-build-metadata.json'));
  writeFileSync(resolve(outDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, {flag: 'wx'});

  const pathRecord = (path) => { const bytes = readFileSync(resolve(outDir, path)); return {path, sha256: sha(bytes), bytes: bytes.length}; };
  const descriptor = {
    schemaVersion: 'frozen-cumulative-reader-migration/1.1.0',
    files: {
      currentPrd: pathRecord('inputs/current-prd.md'),
      prototype: pathRecord(manifest.prototype.sourcePath),
      previousManifest: pathRecord('migration/previous-manifest.json'),
      previousReader: pathRecord('migration/previous-reader.html'),
      previousBuildMetadata: pathRecord('migration/previous-build-metadata.json'),
      freezeManifest: pathRecord('migration/freeze/FREEZE-MANIFEST.json')
    },
    prdSelection,
    legacyMetadata: {
      buildMode: oldMetadata.buildMode,
      reviewEntryRole: oldMetadata.reviewEntryRole,
      declaredManifestSha256: oldMetadata.manifestSha256,
      actualManifestSha256: sha(oldManifestBytes),
      declaredReaderSha256: oldMetadata.readerSha256,
      actualReaderSha256: sha(oldReaderBytes),
      declaredPrototypeSha256: oldMetadata.prototypeSha256,
      actualPrototypeSha256: sha(prototypeBytes)
    },
    scope: {
      coverage: oldManifest.requirement.coverage,
      pageIds: [...oldManifest.pages].sort((a, b) => a.order - b.order).map(({pageId}) => pageId),
      cards: oldManifest.cards.map((card) => ({cardId: card.cardId, displayNumber: card.displayNumber, sha256: sha(Buffer.from(JSON.stringify(card)))}))
    },
    migratedManifestSha256: sha(readFileSync(resolve(outDir, 'manifest.json')))
  };
  const descriptorPath = resolve(outDir, 'migration/descriptor.json');
  writeFileSync(descriptorPath, `${JSON.stringify(descriptor, null, 2)}\n`, {flag: 'wx'});

  const shellRoot = resolve(import.meta.dirname, '../assets/developer-reader-shell');
  const buildScript = resolve(shellRoot, 'scripts/build-snapshot.mjs');
  const snapshotPath = resolve(outDir, 'cumulative-reader.html');
  const metadataPath = resolve(outDir, 'cumulative-reader.build.json');
  runNode(buildScript, [
    '--allow-legacy-v3', '--defer-independent-review', '--migration-descriptor', descriptorPath,
    '--manifest', resolve(outDir, 'manifest.json'), '--prototype', resolve(outDir, manifest.prototype.sourcePath),
    '--out', snapshotPath, '--metadata', metadataPath, '--workspace-root', outDir
  ]);
  const receiptPath = resolve(outDir, 'lineage-baseline-receipt.json');
  runNode(resolve(import.meta.dirname, 'create-lineage-baseline-receipt.mjs'), [
    '--manifest', resolve(outDir, 'manifest.json'), '--snapshot', snapshotPath, '--metadata', metadataPath,
    '--migration-descriptor', descriptorPath, '--out', receiptPath, '--receipt-id', values['receipt-id'],
    '--requirement-id', manifest.requirement.id, '--created-at', values['created-at']
  ]);
  process.stdout.write(`PASS frozen review migration cards=${manifest.cards.length} pages=${manifest.pages.length} coverage=${manifest.requirement.coverage} run=${outDir}\n`);
} catch (error) {
  process.stderr.write(`FAIL ${error.message}\n`);
  process.exitCode = 1;
}

function runNode(script, arguments_) {
  const result = spawnSync(process.execPath, [script, ...arguments_], {encoding: 'utf8', timeout: 180000});
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.status !== 0) throw new Error(result.stderr?.trim() || `E_MIGRATION_CHILD ${basename(script)}`);
}

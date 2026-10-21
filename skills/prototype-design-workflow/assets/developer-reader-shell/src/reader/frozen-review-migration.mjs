import {createHash} from 'node:crypto';
import {readFileSync, lstatSync} from 'node:fs';
import {dirname, isAbsolute, relative, resolve} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {locateEvidenceText} from '../../../../scripts/validate-evidence-bundle.mjs';

const LEGACY_FORMAT = 'frozen-cumulative-reader-migration/1.0.0';
const FORMAT = 'frozen-cumulative-reader-migration/1.1.0';
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const closed = (value, keys, label) => {
  if (!value || Array.isArray(value) || typeof value !== 'object' ||
      Object.keys(value).sort().join(',') !== [...keys].sort().join(',')) {
    throw new Error(`E_MIGRATION_DESCRIPTOR_SHAPE ${label}`);
  }
};
const HASH = /^[a-f0-9]{64}$/;

// Snapshot HTML is data produced by build-snapshot.mjs, not JavaScript to
// execute here.  Keep this parser deliberately narrow: it accepts exactly
// one object-literal manifest assignment and one canonical Base64 payload
// assignment, while allowing unrelated generated assignments between them.
export function extractFrozenReaderBindings(reader, errorCode = 'E_MIGRATION_READER_BINDING') {
  if (typeof reader !== 'string') throw new Error(errorCode);
  const manifestMarker = 'window.__HANDOFF_MANIFEST__';
  const payloadMarker = 'window.__PROTOTYPE_BASE64__';
  const manifestStarts = assignmentStarts(reader, manifestMarker);
  const payloadStarts = assignmentStarts(reader, payloadMarker);
  if (manifestStarts.length !== 1 || payloadStarts.length !== 1) throw new Error(errorCode);
  const manifestText = readJsonObject(reader, manifestStarts[0]);
  const payload = readBase64Payload(reader, payloadStarts[0]);
  let manifest;
  try {
    manifest = JSON.parse(manifestText);
  } catch {
    throw new Error(errorCode);
  }
  if (!manifest || Array.isArray(manifest) || typeof manifest !== 'object') throw new Error(errorCode);
  const decodedPayload = Buffer.from(payload, 'base64');
  if (decodedPayload.toString('base64') !== payload) throw new Error(errorCode);
  return {manifest, payload: decodedPayload};
}

function assignmentStarts(reader, marker) {
  const expression = new RegExp(`${marker.replaceAll('.', '\\.') }\\s*=\\s*`, 'g');
  const starts = [];
  for (let match = expression.exec(reader); match; match = expression.exec(reader)) starts.push(match.index + match[0].length);
  return starts;
}

function readJsonObject(reader, start) {
  let index = start;
  while (/\\s/.test(reader[index] ?? '')) index += 1;
  if (reader[index] !== '{') throw new Error('E_MIGRATION_READER_BINDING');
  const begin = index;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (; index < reader.length; index += 1) {
    const char = reader[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') quoted = false;
      continue;
    }
    if (char === '"') quoted = true;
    else if (char === '{') depth += 1;
    else if (char === '}') {
      depth -= 1;
      if (depth === 0) return reader.slice(begin, index + 1);
      if (depth < 0) break;
    }
  }
  throw new Error('E_MIGRATION_READER_BINDING');
}

function readBase64Payload(reader, start) {
  const remainder = reader.slice(start).match(/^'([A-Za-z0-9+/]*={0,2})'\s*;/);
  if (!remainder || !remainder[1] || remainder[1].length % 4 !== 0) throw new Error('E_MIGRATION_READER_BINDING');
  return remainder[1];
}

export function createPrdSelection({prdPath, prdBytes, frozenPrd, version, expectedSha256}) {
  if (typeof prdPath !== 'string' || !isAbsolute(prdPath)) throw new Error('E_MIGRATION_PRD_SELECTED_PATH');
  if (!frozenPrd || typeof frozenPrd.sourcePath !== 'string' || !isAbsolute(frozenPrd.sourcePath) ||
      typeof frozenPrd.archivePath !== 'string' || !frozenPrd.archivePath || !HASH.test(frozenPrd.sha256 ?? '')) {
    throw new Error('E_MIGRATION_PRD_AUTHORITY');
  }
  if (typeof version !== 'string' || !version.trim() || version.length > 128 || /[\x00-\x1f\x7f]/.test(version)) {
    throw new Error('E_MIGRATION_PRD_VERSION');
  }
  if (!HASH.test(expectedSha256 ?? '')) throw new Error('E_MIGRATION_PRD_EXPECTED_HASH');
  const actualSha256 = sha(prdBytes);
  if (actualSha256 !== expectedSha256) throw new Error('E_MIGRATION_PRD_EXPECTED_HASH_MISMATCH');
  const selectedSourcePath = resolve(prdPath);
  const historicalAuthoritySourcePath = resolve(frozenPrd.sourcePath);
  return {
    mode: selectedSourcePath === historicalAuthoritySourcePath ? 'authority-source' : 'explicit-snapshot',
    version: version.trim(),
    selectedSourcePath,
    expectedSha256,
    actualSha256,
    historicalAuthority: {
      sourcePath: historicalAuthoritySourcePath,
      archivePath: frozenPrd.archivePath,
      sha256: frozenPrd.sha256
    }
  };
}

function containedRead(root, relativePath, label) {
  if (typeof relativePath !== 'string' || !relativePath || isAbsolute(relativePath) ||
      relativePath.split(/[\\/]/).some((part) => !part || part === '.' || part === '..')) {
    throw new Error(`E_MIGRATION_PATH ${label}`);
  }
  const absolute = resolve(root, relativePath);
  const rel = relative(root, absolute);
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error(`E_MIGRATION_PATH ${label}`);
  let cursor = root;
  for (const part of rel.split(/[\\/]/)) {
    cursor = resolve(cursor, part);
    try {
      if (lstatSync(cursor).isSymbolicLink()) throw new Error(`E_MIGRATION_SYMLINK ${label}`);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      throw new Error(`E_MIGRATION_FILE_MISSING ${label}`);
    }
  }
  try { return readFileSync(absolute); }
  catch { throw new Error(`E_MIGRATION_FILE_MISSING ${label}`); }
}

export function validateFrozenReviewMigration({descriptorPath, manifestPath, snapshotPath = null, metadataPath = null}) {
  const runRoot = dirname(resolve(manifestPath));
  const descriptorBytes = readFileSync(resolve(descriptorPath));
  const descriptor = JSON.parse(descriptorBytes.toString('utf8'));
  if (descriptor.schemaVersion === LEGACY_FORMAT) {
    closed(descriptor, ['schemaVersion', 'files', 'legacyMetadata', 'scope', 'migratedManifestSha256'], 'root');
  } else if (descriptor.schemaVersion === FORMAT) {
    closed(descriptor, ['schemaVersion', 'files', 'prdSelection', 'legacyMetadata', 'scope', 'migratedManifestSha256'], 'root');
  } else {
    throw new Error('E_MIGRATION_DESCRIPTOR_KIND');
  }
  closed(descriptor.files, ['currentPrd', 'prototype', 'previousManifest', 'previousReader', 'previousBuildMetadata', 'freezeManifest'], 'files');
  const labels = {
    currentPrd: 'currentPrd', prototype: 'prototype', previousManifest: 'previousManifest',
    previousReader: 'previousReader', previousBuildMetadata: 'previousBuildMetadata', freezeManifest: 'freezeManifest'
  };
  const bytes = {};
  for (const [key, label] of Object.entries(labels)) {
    closed(descriptor.files[key], ['path', 'sha256', 'bytes'], `files.${key}`);
    const record = descriptor.files[key];
    if (!HASH.test(record.sha256) || !Number.isSafeInteger(record.bytes) || record.bytes < 1) throw new Error(`E_MIGRATION_DESCRIPTOR_INPUT ${key}`);
    bytes[key] = containedRead(runRoot, record.path, label);
    if (bytes[key].length !== record.bytes || sha(bytes[key]) !== record.sha256) throw new Error(`E_MIGRATION_HASH_MISMATCH ${key}`);
  }

  const oldManifest = JSON.parse(bytes.previousManifest.toString('utf8'));
  const oldMetadata = JSON.parse(bytes.previousBuildMetadata.toString('utf8'));
  const currentManifestBytes = readFileSync(resolve(manifestPath));
  const currentManifest = JSON.parse(currentManifestBytes.toString('utf8'));
  const hasOutput = Boolean(snapshotPath || metadataPath);
  if (hasOutput && (!snapshotPath || !metadataPath)) throw new Error('E_MIGRATION_OUTPUT_REQUIRED');
  const currentMetadataBytes = hasOutput ? readFileSync(resolve(metadataPath)) : null;
  const currentMetadata = currentMetadataBytes ? JSON.parse(currentMetadataBytes.toString('utf8')) : null;
  const currentSnapshotBytes = hasOutput ? readFileSync(resolve(snapshotPath)) : null;
  const currentPrototypeBytes = bytes.prototype;
  const previousReader = bytes.previousReader.toString('utf8');

  if (oldManifest.schemaVersion !== '3.0.0' || oldManifest.requirement?.coverage !== 'pilot-slice' ||
      oldMetadata.buildMode !== 'product-review-cumulative-draft' ||
      oldMetadata.reviewEntryRole !== 'single-cumulative-review-entry') throw new Error('E_MIGRATION_LEGACY_PROFILE');
  if (oldMetadata.readerSha256 !== sha(bytes.previousReader) || oldMetadata.prototypeSha256 !== sha(bytes.prototype) ||
      oldManifest.prototype?.sourceHash !== sha(bytes.prototype)) throw new Error('E_MIGRATION_LEGACY_METADATA_BINDING');
  let previousBindings;
  try { previousBindings = extractFrozenReaderBindings(previousReader); }
  catch { throw new Error('E_MIGRATION_PREVIOUS_READER_BINDING'); }
  if (!isDeepStrictEqual(previousBindings.manifest, oldManifest) ||
      !previousBindings.payload.equals(bytes.prototype)) throw new Error('E_MIGRATION_PREVIOUS_READER_BINDING');

  const freezePath = descriptor.files.freezeManifest.path;
  const freezeRoot = resolve(runRoot, dirname(freezePath));
  const freeze = JSON.parse(bytes.freezeManifest.toString('utf8'));
  if (freeze.schemaVersion !== '1.0.0' || freeze.status !== 'product-convergence-frozen' ||
      !Array.isArray(freeze.artifacts) || !Array.isArray(freeze.supportingEvidence)) throw new Error('E_MIGRATION_FREEZE_MANIFEST');
  const checkFreezeFile = (entry, kind) => {
    if (!entry || !HASH.test(entry.sha256) || !Number.isSafeInteger(entry.bytes) || typeof entry.archivePath !== 'string') {
      throw new Error(`E_MIGRATION_FREEZE_ENTRY ${kind}`);
    }
    const artifact = containedRead(freezeRoot, entry.archivePath, `${kind}.${entry.archivePath}`);
    if (artifact.length !== entry.bytes || sha(artifact) !== entry.sha256) throw new Error(`E_MIGRATION_FREEZE_ARTIFACT ${kind}`);
  };
  for (const entry of freeze.artifacts) checkFreezeFile(entry, 'artifact');
  for (const entry of freeze.supportingEvidence) checkFreezeFile(entry, 'evidence');
  const role = (items, name) => items.find((entry) => entry.role === name);
  const archivePrd = role(freeze.artifacts, 'prd-authority');
  if (!archivePrd || role(freeze.artifacts, 'business-prototype-current-source')?.sha256 !== sha(bytes.prototype) ||
      role(freeze.artifacts, 'single-cumulative-review-entry')?.sha256 !== sha(bytes.previousReader) ||
      role(freeze.supportingEvidence, 'reader-manifest')?.sha256 !== sha(bytes.previousManifest) ||
      role(freeze.supportingEvidence, 'reader-build-record')?.sha256 !== sha(bytes.previousBuildMetadata)) {
    throw new Error('E_MIGRATION_FREEZE_BINDING');
  }
  if (descriptor.schemaVersion === FORMAT) {
    closed(descriptor.prdSelection,
      ['mode', 'version', 'selectedSourcePath', 'expectedSha256', 'actualSha256', 'historicalAuthority'],
      'prdSelection');
    closed(descriptor.prdSelection.historicalAuthority, ['sourcePath', 'archivePath', 'sha256'],
      'prdSelection.historicalAuthority');
    const expectedSelection = createPrdSelection({
      prdPath: descriptor.prdSelection.selectedSourcePath,
      prdBytes: bytes.currentPrd,
      frozenPrd: archivePrd,
      version: descriptor.prdSelection.version,
      expectedSha256: descriptor.prdSelection.expectedSha256
    });
    if (!isDeepStrictEqual(descriptor.prdSelection, expectedSelection)) {
      throw new Error('E_MIGRATION_PRD_SELECTION_DRIFT');
    }
  }
  const expectedLegacyMetadata = {
    buildMode: oldMetadata.buildMode,
    reviewEntryRole: oldMetadata.reviewEntryRole,
    declaredManifestSha256: oldMetadata.manifestSha256,
    actualManifestSha256: sha(bytes.previousManifest),
    declaredReaderSha256: oldMetadata.readerSha256,
    actualReaderSha256: sha(bytes.previousReader),
    declaredPrototypeSha256: oldMetadata.prototypeSha256,
    actualPrototypeSha256: sha(bytes.prototype)
  };
  closed(descriptor.legacyMetadata, Object.keys(expectedLegacyMetadata), 'legacyMetadata');
  if (!isDeepStrictEqual(descriptor.legacyMetadata, expectedLegacyMetadata)) throw new Error('E_MIGRATION_LEGACY_DECLARATION');

  const sourceId = oldManifest.requirement?.primarySourceId;
  const oldPrdSource = oldManifest.evidenceSources?.find((source) => source.sourceId === sourceId);
  if (!oldPrdSource || oldPrdSource.sourceType !== 'prd' || oldPrdSource.sourceHash !== archivePrd.sha256) {
    throw new Error('E_MIGRATION_PRD_BASELINE_BINDING');
  }
  const expectedCurrentManifest = structuredClone(oldManifest);
  expectedCurrentManifest.evidenceSources.find((source) => source.sourceId === sourceId).sourceHash = sha(bytes.currentPrd);
  if (!isDeepStrictEqual(currentManifest, expectedCurrentManifest)) throw new Error('E_MIGRATION_CURRENT_MANIFEST_DRIFT');
  const refs = [];
  for (const owner of [...(oldManifest.cards ?? []), ...(oldManifest.globalRules ?? [])]) {
    for (const binding of owner.evidenceBindings ?? []) for (const ref of binding.sourceRefs ?? []) {
      if (ref.sourceId === sourceId) refs.push(ref);
    }
  }
  if (refs.length === 0) throw new Error('E_MIGRATION_PRD_REFS_MISSING');
  for (const ref of refs) {
    const found = locateEvidenceText(bytes.currentPrd.toString('utf8'), ref.locator);
    if (!found.ok || !found.text.includes(ref.excerpt ?? '')) throw new Error(`E_MIGRATION_CURRENT_PRD_REF ${ref.locator}`);
  }

  const expectedScope = {
    coverage: oldManifest.requirement.coverage,
    pageIds: [...oldManifest.pages].sort((a, b) => a.order - b.order).map(({pageId}) => pageId),
    cards: oldManifest.cards.map((card) => ({
      cardId: card.cardId,
      displayNumber: card.displayNumber,
      sha256: sha(Buffer.from(JSON.stringify(card)))
    }))
  };
  closed(descriptor.scope, ['coverage', 'pageIds', 'cards'], 'scope');
  if (!isDeepStrictEqual(descriptor.scope, expectedScope) || descriptor.scope.coverage !== 'pilot-slice' ||
      descriptor.migratedManifestSha256 !== sha(currentManifestBytes)) throw new Error('E_MIGRATION_SCOPE_DRIFT');

  if (hasOutput) {
    if (currentMetadata.buildMode !== 'readonly-snapshot' || currentMetadata.schemaVersion !== currentManifest.schemaVersion ||
        currentMetadata.coverage !== currentManifest.requirement.coverage || currentMetadata.outputSha256 !== sha(currentSnapshotBytes) ||
        currentMetadata.manifestSha256 !== sha(currentManifestBytes) || currentMetadata.prototypeSha256 !== sha(currentPrototypeBytes) ||
        currentMetadata.migrationDescriptorSha256 !== sha(descriptorBytes)) throw new Error('E_MIGRATION_OUTPUT_BINDING');
    let currentBindings;
    try { currentBindings = extractFrozenReaderBindings(currentSnapshotBytes.toString('utf8')); }
    catch { throw new Error('E_MIGRATION_OUTPUT_CONTENT'); }
    if (!isDeepStrictEqual(currentBindings.manifest, currentManifest) ||
        !currentBindings.payload.equals(currentPrototypeBytes)) throw new Error('E_MIGRATION_OUTPUT_CONTENT');
  }
  return {coverage: 'pilot-slice', cardCount: oldManifest.cards.length, pageCount: oldManifest.pages.length, descriptorSha256: sha(descriptorBytes)};
}

import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createPrdSelection, extractFrozenReaderBindings, validateFrozenReviewMigration} from '../assets/developer-reader-shell/src/reader/frozen-review-migration.mjs';

const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const put = (root, path, bytes) => {
  const target = resolve(root, path);
  mkdirSync(join(target, '..'), {recursive: true});
  writeFileSync(target, bytes);
  return {path, sha256: sha(bytes), bytes: bytes.length};
};

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'frozen-review-migration-'));
  const previousPrd = Buffer.from('Synthetic approved scope\nSynthetic entry appears in the header\n');
  const currentPrd = Buffer.from('Synthetic approved scope\nSynthetic entry appears in the header\nUpdated contact point\n');
  const prototype = Buffer.from('<!doctype html><main id="view-example" class="active"><button id="example-entry">Synthetic entry</button></main>');
  const manifest = {
    schemaVersion: '3.0.0',
    requirement: {id: 'REQ-1', title: 'Frozen synthetic review', coverage: 'pilot-slice', primarySourceId: 'prd'},
    prototype: {sourceLabel: 'prototype.html', sourcePath: 'prototype.html', sourceHash: sha(prototype), sourceRole: 'current-workspace-prototype'},
    delivery: {mode: 'standard', readerRole: 'derived-readonly', riskTriggers: []},
    baselineEvidence: {
      mode: 'zero-to-one',
      runtimePage: {availability: 'not-applicable', sourceIds: [], note: 'Synthetic migration fixture'},
      code: {availability: 'not-applicable', sourceIds: [], note: 'Synthetic migration fixture'},
      screenshots: {availability: 'not-applicable', sourceIds: [], note: 'Synthetic migration fixture'},
      conflicts: []
    },
    evidenceSources: [{sourceId: 'prd', sourceType: 'prd', authority: 'current-product-requirement', label: 'Synthetic PRD', contentKind: 'text', sourceHash: sha(previousPrd)}],
    pages: [{pageId: 'P1', title: 'Page one', order: 1}],
    surfaces: [{surfaceId: 'view:example', kind: 'view', rootSelector: '#view-example', activeSelector: '#view-example.active', parentSurfaceIds: [], exitActions: [], exitGuardSurfaceIds: [], snapshotSelectors: ['#view-example']}],
    cards: [{
      cardId: 'C1', displayNumber: '1', requirementId: 'REQ-1', pageId: 'P1', surfaceId: 'view:example', region: 'header',
      title: 'Synthetic entry', summary: 'Synthetic approved scope {with \\ slash}', changeKinds: ['add'],
      changeContent: {added: ['Synthetic entry'], placement: 'header', finalResult: 'Synthetic entry appears in the header'},
      fields: [], rules: [], interactions: [], errors: [], pendingItems: [], history: [],
      evidenceBindings: [
        {claimPath: '/cards/0/title', sourceRefs: [{sourceId: 'prd', locator: 'text:Synthetic approved scope', excerpt: 'Synthetic approved scope'}]},
        {claimPath: '/cards/0/summary', sourceRefs: [{sourceId: 'prd', locator: 'text:Synthetic approved scope', excerpt: 'Synthetic approved scope'}]},
        {claimPath: '/cards/0/changeContent', sourceRefs: [{sourceId: 'prd', locator: 'text:Synthetic entry appears in the header', excerpt: 'Synthetic entry appears in the header'}]}
      ],
      anchor: {kind: 'element', selector: '#example-entry', enterActions: [], manualPath: 'Example → Synthetic entry'}
    }],
    globalRules: []
  };
  const manifestBytes = Buffer.from(JSON.stringify(manifest));
  const readerBytes = Buffer.from(`<!doctype html><script>window.__HANDOFF_MANIFEST__ = ${JSON.stringify(manifest)};\n    window.__PROTOTYPE_BASE64__ = '${prototype.toString('base64')}';</script>`);
  const metadata = {buildMode: 'product-review-cumulative-draft', reviewEntryRole: 'single-cumulative-review-entry', manifestSha256: 'f'.repeat(64), readerSha256: sha(readerBytes), prototypeSha256: sha(prototype)};
  const metadataBytes = Buffer.from(JSON.stringify(metadata));
  const previous = {
    currentPrd: put(root, 'current-prd.md', currentPrd),
    prototype: put(root, 'prototype.html', prototype),
    previousManifest: put(root, 'migration/previous-manifest.json', manifestBytes),
    previousReader: put(root, 'migration/previous-reader.html', readerBytes),
    previousBuildMetadata: put(root, 'migration/previous-build-metadata.json', metadataBytes),
    archivePrd: put(root, 'migration/freeze/artifacts/prd.md', previousPrd),
    archivePrototype: put(root, 'migration/freeze/artifacts/prototype.html', prototype),
    archiveReader: put(root, 'migration/freeze/artifacts/reader.html', readerBytes),
    archiveManifest: put(root, 'migration/freeze/evidence/manifest.json', manifestBytes),
    archiveMetadata: put(root, 'migration/freeze/evidence/metadata.json', metadataBytes)
  };
  const freeze = {
    schemaVersion: '1.0.0', status: 'product-convergence-frozen',
    artifacts: [
      {role: 'prd-authority', sourcePath: '/authority/current-prd.md', archivePath: 'artifacts/prd.md', bytes: previous.archivePrd.bytes, sha256: previous.archivePrd.sha256},
      {role: 'business-prototype-current-source', archivePath: 'artifacts/prototype.html', bytes: previous.archivePrototype.bytes, sha256: previous.archivePrototype.sha256},
      {role: 'single-cumulative-review-entry', archivePath: 'artifacts/reader.html', bytes: previous.archiveReader.bytes, sha256: previous.archiveReader.sha256}
    ],
    supportingEvidence: [
      {role: 'reader-manifest', archivePath: 'evidence/manifest.json', bytes: previous.archiveManifest.bytes, sha256: previous.archiveManifest.sha256},
      {role: 'reader-build-record', archivePath: 'evidence/metadata.json', bytes: previous.archiveMetadata.bytes, sha256: previous.archiveMetadata.sha256}
    ]
  };
  const freezeBytes = Buffer.from(JSON.stringify(freeze));
  const freezeRecord = put(root, 'migration/freeze/FREEZE-MANIFEST.json', freezeBytes);
  const currentManifest = structuredClone(manifest);
  currentManifest.evidenceSources[0].sourceHash = sha(currentPrd);
  const currentManifestBytes = Buffer.from(JSON.stringify(currentManifest));
  put(root, 'manifest.json', currentManifestBytes);
  const descriptor = {
    schemaVersion: 'frozen-cumulative-reader-migration/1.1.0',
    files: {
      currentPrd: previous.currentPrd, prototype: previous.prototype,
      previousManifest: previous.previousManifest, previousReader: previous.previousReader,
      previousBuildMetadata: previous.previousBuildMetadata,
      freezeManifest: {path: 'migration/freeze/FREEZE-MANIFEST.json', sha256: freezeRecord.sha256, bytes: freezeBytes.length}
    },
    prdSelection: createPrdSelection({
      prdPath: '/snapshots/v1.11/current-prd.md', prdBytes: currentPrd,
      frozenPrd: freeze.artifacts[0], version: 'V1.11', expectedSha256: sha(currentPrd)
    }),
    legacyMetadata: {
      buildMode: metadata.buildMode, reviewEntryRole: metadata.reviewEntryRole,
      declaredManifestSha256: metadata.manifestSha256, actualManifestSha256: sha(manifestBytes),
      declaredReaderSha256: metadata.readerSha256, actualReaderSha256: sha(readerBytes),
      declaredPrototypeSha256: metadata.prototypeSha256, actualPrototypeSha256: sha(prototype)
    },
    scope: {coverage: 'pilot-slice', pageIds: ['P1'], cards: [{cardId: 'C1', displayNumber: '1', sha256: sha(Buffer.from(JSON.stringify(manifest.cards[0])))}]},
    migratedManifestSha256: sha(currentManifestBytes)
  };
  const descriptorPath = resolve(root, put(root, 'migration/descriptor.json', Buffer.from(JSON.stringify(descriptor))).path);
  return {
    root, descriptor, descriptorPath, manifestPath: resolve(root, 'manifest.json'), currentManifest, currentManifestBytes,
    prototype, cleanup: () => rmSync(root, {recursive: true, force: true})
  };
}

function outputFixture(f, snapshotText) {
  const snapshotBytes = Buffer.from(snapshotText);
  const metadata = {
    buildMode: 'readonly-snapshot', schemaVersion: '3.0.0', coverage: 'pilot-slice',
    outputSha256: sha(snapshotBytes), manifestSha256: sha(f.currentManifestBytes), prototypeSha256: sha(f.prototype),
    migrationDescriptorSha256: sha(readFileSync(f.descriptorPath))
  };
  return {
    snapshotPath: resolve(f.root, 'cumulative-reader.html'),
    metadataPath: resolve(f.root, 'cumulative-reader.build.json'),
    write() {
      writeFileSync(this.snapshotPath, snapshotBytes);
      writeFileSync(this.metadataPath, JSON.stringify(metadata));
    }
  };
}

test('freeze-bound migration accepts the one recognized legacy review profile and preserves its declared slice', () => {
  const f = fixture();
  try {
    const result = validateFrozenReviewMigration({descriptorPath: f.descriptorPath, manifestPath: f.manifestPath});
    assert.deepEqual({coverage: result.coverage, cardCount: result.cardCount, pageCount: result.pageCount}, {coverage: 'pilot-slice', cardCount: 1, pageCount: 1});
  } finally { f.cleanup(); }
});

test('reader bindings support both templates and reject missing, damaged, or conflicting assignments', () => {
  const manifest = {title: 'quoted "value"', nested: {brace: '{}', escaped: '\\'}};
  const payload = Buffer.from('payload bytes').toString('base64');
  const oldTemplate = `window.__HANDOFF_MANIFEST__ = ${JSON.stringify(manifest)};\nwindow.__PROTOTYPE_BASE64__ = '${payload}';`;
  const currentTemplate = `window.__HANDOFF_MANIFEST__ = ${JSON.stringify(manifest)};\nwindow.__HANDOFF_LINEAGE_CONTEXT__ = {"nested":{"text":"quoted \\\"{}\\\""}};\nwindow.__PROTOTYPE_BASE64__ = '${payload}';`;
  for (const reader of [oldTemplate, currentTemplate]) {
    const extracted = extractFrozenReaderBindings(reader);
    assert.deepEqual(extracted.manifest, manifest);
    assert.deepEqual(extracted.payload, Buffer.from('payload bytes'));
  }
  assert.throws(() => extractFrozenReaderBindings(currentTemplate.replace(payload, '%%%%')), /E_MIGRATION_READER_BINDING/);
  assert.throws(() => extractFrozenReaderBindings(currentTemplate.replace('window.__PROTOTYPE_BASE64__', 'window.__OTHER__')), /E_MIGRATION_READER_BINDING/);
  assert.throws(() => extractFrozenReaderBindings(`${currentTemplate}\nwindow.__PROTOTYPE_BASE64__ = '${payload}';`), /E_MIGRATION_READER_BINDING/);
});

test('migration output validation accepts the current lineage template and rejects content drift', () => {
  const f = fixture();
  try {
    const snapshot = `<!doctype html><script>window.__HANDOFF_MANIFEST__ = ${JSON.stringify(f.currentManifest)};\nwindow.__HANDOFF_LINEAGE_CONTEXT__ = {"previousManifest":{"nested":"{quoted}\\\""}};\nwindow.__PROTOTYPE_BASE64__ = '${f.prototype.toString('base64')}';</script>`;
    const output = outputFixture(f, snapshot);
    output.write();
    assert.equal(validateFrozenReviewMigration({descriptorPath: f.descriptorPath, manifestPath: f.manifestPath, ...output}).cardCount, 1);

    const conflict = outputFixture(f, `${snapshot}\n<script>window.__HANDOFF_MANIFEST__ = {};</script>`);
    conflict.write();
    assert.throws(() => validateFrozenReviewMigration({descriptorPath: f.descriptorPath, manifestPath: f.manifestPath, ...conflict}), /E_MIGRATION_OUTPUT_CONTENT/);
  } finally { f.cleanup(); }
});

test('actual frozen migration CLI generates and verifies a current reader, metadata, runtime audit, and lineage receipt', () => {
  const f = fixture();
  try {
    const outDir = resolve(f.root, 'migrated-run');
    const cli = fileURLToPath(new URL('../scripts/migrate-frozen-review.mjs', import.meta.url));
    const result = spawnSync(process.execPath, [cli,
      '--freeze-dir', resolve(f.root, 'migration/freeze'), '--prd', resolve(f.root, 'current-prd.md'),
      '--prd-version', 'V1.11', '--prd-sha256', f.descriptor.prdSelection.expectedSha256,
      '--prototype', resolve(f.root, 'prototype.html'), '--manifest', resolve(f.root, 'migration/previous-manifest.json'),
      '--reader', resolve(f.root, 'migration/previous-reader.html'), '--metadata', resolve(f.root, 'migration/previous-build-metadata.json'),
      '--out-dir', outDir, '--receipt-id', 'SYNTHETIC-MIGRATION-001', '--created-at', '2026-10-12T00:00:00+00:00'
    ], {encoding: 'utf8', timeout: 180000});
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /PASS frozen review migration cards=1 pages=1 coverage=pilot-slice/);
    const snapshotPath = resolve(outDir, 'cumulative-reader.html');
    const metadataPath = resolve(outDir, 'cumulative-reader.build.json');
    assert.ok(existsSync(snapshotPath) && existsSync(metadataPath) && existsSync(resolve(outDir, 'lineage-baseline-receipt.json')));
    assert.equal(validateFrozenReviewMigration({
      descriptorPath: resolve(outDir, 'migration/descriptor.json'), manifestPath: resolve(outDir, 'manifest.json'), snapshotPath, metadataPath
    }).cardCount, 1);
  } finally { f.cleanup(); }
});

test('migration descriptor rejects unknown claims, changed historic hashes, and false coverage', () => {
  const f = fixture();
  try {
    const extra = {...f.descriptor, fabricated: true};
    const extraPath = resolve(f.root, put(f.root, 'extra.json', Buffer.from(JSON.stringify(extra))).path);
    assert.throws(() => validateFrozenReviewMigration({descriptorPath: extraPath, manifestPath: f.manifestPath}), /E_MIGRATION_DESCRIPTOR_SHAPE/);
    const wrongHash = structuredClone(f.descriptor); wrongHash.files.previousReader.sha256 = '0'.repeat(64);
    const wrongHashPath = resolve(f.root, put(f.root, 'wrong-hash.json', Buffer.from(JSON.stringify(wrongHash))).path);
    assert.throws(() => validateFrozenReviewMigration({descriptorPath: wrongHashPath, manifestPath: f.manifestPath}), /E_MIGRATION_HASH_MISMATCH/);
    const falseCoverage = structuredClone(f.descriptor); falseCoverage.scope.coverage = 'full';
    const falseCoveragePath = resolve(f.root, put(f.root, 'false-coverage.json', Buffer.from(JSON.stringify(falseCoverage))).path);
    assert.throws(() => validateFrozenReviewMigration({descriptorPath: falseCoveragePath, manifestPath: f.manifestPath}), /E_MIGRATION_SCOPE_DRIFT/);
    const wrongMode = structuredClone(f.descriptor); wrongMode.prdSelection.mode = 'authority-source';
    const wrongModePath = resolve(f.root, put(f.root, 'wrong-mode.json', Buffer.from(JSON.stringify(wrongMode))).path);
    assert.throws(() => validateFrozenReviewMigration({descriptorPath: wrongModePath, manifestPath: f.manifestPath}), /E_MIGRATION_PRD_SELECTION_DRIFT/);
  } finally { f.cleanup(); }
});

test('explicit PRD snapshot selection binds version, actual digest, and historical authority', () => {
  const prdBytes = Buffer.from('Frozen V1.11\n');
  const frozenPrd = {sourcePath: '/authority/prd.md', archivePath: 'artifacts/prd.md', sha256: 'a'.repeat(64)};
  const selection = createPrdSelection({
    prdPath: '/snapshots/v1.11/prd.md', prdBytes, frozenPrd,
    version: 'V1.11', expectedSha256: sha(prdBytes)
  });
  assert.deepEqual(selection, {
    mode: 'explicit-snapshot', version: 'V1.11', selectedSourcePath: '/snapshots/v1.11/prd.md',
    expectedSha256: sha(prdBytes), actualSha256: sha(prdBytes),
    historicalAuthority: {sourcePath: '/authority/prd.md', archivePath: 'artifacts/prd.md', sha256: 'a'.repeat(64)}
  });
  assert.throws(() => createPrdSelection({
    prdPath: '/snapshots/v1.11/prd.md', prdBytes, frozenPrd,
    version: '', expectedSha256: sha(prdBytes)
  }), /E_MIGRATION_PRD_VERSION/);
  assert.throws(() => createPrdSelection({
    prdPath: '/snapshots/v1.11/prd.md', prdBytes, frozenPrd,
    version: 'V1.11', expectedSha256: '0'.repeat(64)
  }), /E_MIGRATION_PRD_EXPECTED_HASH_MISMATCH/);
});

test('legacy 1.0 migration descriptors remain readable', () => {
  const f = fixture();
  try {
    const legacy = structuredClone(f.descriptor);
    legacy.schemaVersion = 'frozen-cumulative-reader-migration/1.0.0';
    delete legacy.prdSelection;
    const legacyPath = resolve(f.root, put(f.root, 'legacy.json', Buffer.from(JSON.stringify(legacy))).path);
    assert.equal(validateFrozenReviewMigration({descriptorPath: legacyPath, manifestPath: f.manifestPath}).cardCount, 1);
  } finally { f.cleanup(); }
});

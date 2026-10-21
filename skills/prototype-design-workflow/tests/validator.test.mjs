import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

const validatorPath = fileURLToPath(new URL('../scripts/validate-prototype-handoff.mjs', import.meta.url));
const builderPath = fileURLToPath(new URL('../assets/developer-reader-shell/scripts/build-snapshot.mjs', import.meta.url));
const baselineReceiptPath = fileURLToPath(new URL('../scripts/create-lineage-baseline-receipt.mjs', import.meta.url));
const sha = (value) => createHash('sha256').update(value).digest('hex');
const sourceContents = {
  'prd-current': '新增示例入口\n页面增加一个示例入口。\n示例入口位于示例页面顶部。\n',
  'runtime-current': '已实际打开示例页，顶部操作区可见。\n',
  'code-current': '<main id="view-example"><button id="example-entry">Entry</button></main>\n',
  'screenshot-current': 'fake-image-bytes',
  'prototype-before-change': '<!doctype html><html><body><main id="view-example" class="active"></main></body></html>'
};
const scopeEvidence = () => ({
  productScope: {summary: '示例范围已确认。', included: ['示例入口'], excluded: [], sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
  sampleSource: {status: 'present', samples: [{sampleId: 'runtime-sample', label: '示例运行态', context: '已观察示例页入口。', sourceIds: ['runtime-current']}], note: '使用合成运行态样本。'},
  verifiedCoverage: {verified: ['示例入口'], notVerified: [], sourceRefs: [{sourceId: 'runtime-current', locator: 'text:已实际打开示例页', excerpt: '已实际打开示例页'}]}
});

function viewSurface(overrides = {}) {
  return {
    surfaceId: 'view:example',
    kind: 'view',
    rootSelector: '#view-example',
    activeSelector: '#view-example.active',
    parentSurfaceIds: [],
    exitActions: [],
    exitGuardSurfaceIds: [],
    snapshotSelectors: ['#view-example'],
    ...overrides
  };
}

function card(overrides = {}) {
  return {
    cardId: 'EXAMPLE-CARD-01',
    displayNumber: '01',
    requirementId: 'EXAMPLE-REQ',
    pageId: 'example-page',
    surfaceId: 'view:example',
    region: '示例区域',
    title: '新增示例入口',
    summary: '页面增加一个示例入口。',
    changeKinds: ['add'],
    changeContent: {added: ['示例入口'], placement: '示例页面顶部', finalResult: '示例入口可见'},
    fields: [],
    rules: [],
    interactions: [],
    errors: [],
    pendingItems: [],
    evidenceBindings: [
      {claimPath: '/cards/0/title', sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
      {claimPath: '/cards/0/summary', sourceRefs: [{sourceId: 'prd-current', locator: 'text:页面增加一个示例入口', excerpt: '页面增加一个示例入口'}]},
      {claimPath: '/cards/0/changeContent', sourceRefs: [{sourceId: 'prd-current', locator: 'text:示例入口位于示例页面顶部', excerpt: '示例入口位于示例页面顶部'}]}
    ],
    anchor: {kind: 'element', selector: '#example-entry', enterActions: [], manualPath: '示例页面 → 示例入口'},
    history: [],
    ...overrides
  };
}

function manifest() {
  return {
    schemaVersion: '3.0.0',
    requirement: {id: 'EXAMPLE-REQ', title: 'Example requirement', coverage: 'pilot-slice', primarySourceId: 'prd-current'},
    prototype: {
      sourceLabel: 'prototype-example.html',
      sourcePath: 'prototype.html',
      sourceHash: '0'.repeat(64),
      baselineSourceId: 'prototype-before-change',
      baselineHash: sha(sourceContents['prototype-before-change']),
      sourceRole: 'current-workspace-prototype'
    },
    delivery: {mode: 'standard', readerRole: 'derived-readonly', riskTriggers: []},
    baselineEvidence: {
      mode: 'existing-system-increment',
      runtimePage: {availability: 'available', sourceIds: ['runtime-current'], note: '已走查当前页面。'},
      code: {availability: 'available', sourceIds: ['code-current'], note: '已定向检索当前代码。'},
      screenshots: {availability: 'available', sourceIds: ['screenshot-current'], note: '已冻结同版本截图。'},
      conflicts: []
    },
    evidenceSources: [
      {sourceId: 'prd-current', sourceType: 'prd', authority: 'current-product-requirement', label: '当前 PRD', contentKind: 'text', sourceHash: sha(sourceContents['prd-current'])},
      {sourceId: 'runtime-current', sourceType: 'runtime', authority: 'observed-runtime', label: '真实页面走查', contentKind: 'text', sourceHash: sha(sourceContents['runtime-current'])},
      {sourceId: 'code-current', sourceType: 'code', authority: 'current-code', label: '当前代码', contentKind: 'source-code', sourceHash: sha(sourceContents['code-current'])},
      {sourceId: 'screenshot-current', sourceType: 'screenshot', authority: 'same-version-screenshot', label: '同版本截图', contentKind: 'image', sourceHash: sha(sourceContents['screenshot-current'])},
      {sourceId: 'prototype-before-change', sourceType: 'prototype-baseline', authority: 'current-prototype-baseline', label: '修改前原型', contentKind: 'html', sourceHash: sha(sourceContents['prototype-before-change'])}
    ],
    pages: [{pageId: 'example-page', title: 'Example page', order: 1}],
    surfaces: [viewSurface()],
    globalRules: [],
    cards: [card()]
  };
}

function run(currentManifest, html, mutateEvidence = () => {}, includeReview = true, deferIndependentReview = false) {
  const directory = mkdtempSync(join(tmpdir(), 'prototype-handoff-validator-'));
  const manifestPath = join(directory, 'manifest.json');
  const prototypePath = join(directory, 'prototype.html');
  const evidencePath = join(directory, 'evidence-bundle.json');
  const reviewPath = join(directory, 'independent-review.json');
  currentManifest.prototype.sourceHash = sha(html);
  const evidenceSources = Object.entries(sourceContents).map(([sourceId, content]) => {
    const path = join(directory, `${sourceId}.evidence`);
    writeFileSync(path, content);
    const sourceType = currentManifest.evidenceSources.find((source) => source.sourceId === sourceId)?.sourceType;
    const originKind = sourceType === 'runtime' || sourceType === 'screenshot'
      ? 'captured-runtime'
      : 'workspace-file';
    return {sourceId, sourceType, path, origin: {kind: originKind, locator: `${sourceId}.evidence`}};
  });
  const evidenceBundle = {schemaVersion: '1.0.0', sources: evidenceSources};
  mutateEvidence({directory, evidenceBundle});
  const manifestText = `${JSON.stringify(currentManifest, null, 2)}\n`;
  const evidenceText = `${JSON.stringify(evidenceBundle, null, 2)}\n`;
  writeFileSync(manifestPath, manifestText);
  writeFileSync(prototypePath, html);
  writeFileSync(evidencePath, evidenceText);
  const reviewTriggers = [...new Set([
    ...(currentManifest.delivery.riskTriggers ?? []),
    'unverified-authoritative-source',
    'unverified-prototype-lineage',
    ...(currentManifest.globalRules.length > 0 ? ['global-rule-classification'] : []),
    ...(currentManifest.surfaces.length > 1 ? ['complex-multi-surface'] : []),
    ...(currentManifest.requirement.coverage === 'full' ? ['full-coverage'] : [])
  ])];
  const review = {
    schemaVersion: '1.0.0',
    reviewId: 'TEST-REVIEW',
    reviewerRole: 'independent',
    reviewedAt: '2026-08-17T08:00:00Z',
    subject: {manifestHash: sha(manifestText), prototypeHash: sha(html), evidenceBundleHash: sha(evidenceText)},
    triggers: reviewTriggers,
    verdict: 'pass',
    findings: []
  };
  writeFileSync(reviewPath, `${JSON.stringify(review, null, 2)}\n`);
  return spawnSync(process.execPath, [
    validatorPath,
    '--manifest', manifestPath,
    '--prototype', prototypePath,
    '--evidence', evidencePath,
    '--workspace-root', directory,
    '--evidence-root', directory,
    '--allow-legacy-v3',
    ...(deferIndependentReview ? ['--defer-independent-review'] : []),
    ...(includeReview ? ['--review', reviewPath] : [])
  ], {encoding: 'utf8'});
}

function runLegacyV2(html) {
  const directory = mkdtempSync(join(tmpdir(), 'prototype-handoff-v2-'));
  const manifestPath = join(directory, 'manifest.json');
  const prototypePath = join(directory, 'prototype.html');
  const legacy = {
    schemaVersion: '2.0.0',
    requirement: {id: 'EXAMPLE-REQ', title: 'Legacy', coverage: 'pilot-slice', sourceLabel: 'prd.md', sourceHash: 'a'.repeat(64)},
    prototype: {sourceLabel: 'prototype.html', sourceHash: sha(html)},
    pages: [{pageId: 'example-page', title: 'Example', order: 1}],
    surfaces: [{...viewSurface(), snapshotSelectors: []}],
    cards: [{
      cardId: 'EXAMPLE-CARD-01', displayNumber: '01', requirementId: 'EXAMPLE-REQ', pageId: 'example-page',
      surfaceId: 'view:example', region: '区域', title: '旧卡片', summary: '旧版只做结构兼容。',
      changeKinds: ['add'], changeContent: {added: ['入口'], placement: '顶部', finalResult: '入口可见'},
      fields: [], rules: [], interactions: [], errors: [], pendingItems: [],
      sourceRefs: [{sourceType: 'prd', file: 'prd.md', section: '1'}],
      anchor: {kind: 'element', selector: '#example-entry', enterActions: [], manualPath: '示例 → 入口'}, history: []
    }]
  };
  writeFileSync(manifestPath, `${JSON.stringify(legacy, null, 2)}\n`);
  writeFileSync(prototypePath, html);
  return spawnSync(process.execPath, [validatorPath, '--manifest', manifestPath, '--prototype', prototypePath, '--allow-legacy-v2'], {encoding: 'utf8'});
}

const validHtml = '<!doctype html><html><body><main id="view-example" class="active"><button id="example-entry">Entry</button></main></body></html>';
const coreReaderItems = ['change-overview', 'collapse-expand-all', 'return-previous-prototype', 'full-development-snapshot', 'change-kind-color', 'current-location', 'bidirectional-visible-connector', 'fact-source-details'];

function v4InitialManifest(coverage) {
  const current = manifest();
  current.schemaVersion = '4.0.0';
  current.requirement.coverage = coverage;
  current.cards[0].displayNumber = '1';
  current.scopeEvidence = scopeEvidence();
  current.delivery = {
    mode: 'standard', readerRole: 'derived-readonly', riskTriggers: [],
    lineage: {
      mode: 'initial', previousManifestSha256: null, previousSnapshotSha256: null, baselineReceiptSha256: null,
      preservedCardIds: [], modifiedCardIds: [], addedCardIds: ['EXAMPLE-CARD-01'], removedCardIds: [], removalDecisions: []
    },
    readerComposition: {coreItems: coreReaderItems, customItems: [], removedItems: []},
    pendingReconciliation: {carried: [], resolved: [], superseded: [], new: []}
  };
  return current;
}

function writeEvidenceFixture(directory, current) {
  const sources = Object.entries(sourceContents).map(([sourceId, content]) => {
    const path = join(directory, `${sourceId}.evidence`);
    writeFileSync(path, content);
    const sourceType = current.evidenceSources.find((source) => source.sourceId === sourceId).sourceType;
    return {sourceId, sourceType, path, origin: {kind: ['runtime', 'screenshot'].includes(sourceType) ? 'captured-runtime' : 'workspace-file', locator: `${sourceId}.evidence`}};
  });
  const evidencePath = join(directory, 'evidence.json');
  writeFileSync(evidencePath, `${JSON.stringify({schemaVersion: '1.0.0', sources}, null, 2)}\n`);
  return evidencePath;
}

test('valid v3 manifest passes evidence, anchor and runtime gates', () => {
  const result = run(manifest(), validHtml);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /PASS runtime 1 cards/);
});

test('v4 revision preserves verified lineage context through anchor audit and snapshot build', () => {
  const directory = mkdtempSync(join(tmpdir(), 'prototype-v4-lineage-context-'));
  try {
    const html = validHtml;
    const previous = manifest();
    previous.cards[0].displayNumber = '1';
    const previousText = `${JSON.stringify(previous, null, 2)}\n`;
    const previousSnapshot = Buffer.from('<!doctype html><title>previous reader</title>\n');
    const receipt = {
      schemaVersion: '1.0.0', receiptId: 'LINEAGE-CONTEXT-001', requirementId: 'EXAMPLE-REQ',
      previousManifest: {pathLabel: 'previous-manifest.json', sha256: sha(previousText)},
      previousSnapshot: {pathLabel: 'previous-reader.html', sha256: sha(previousSnapshot)},
      buildMetadata: {pathLabel: 'previous-reader.build.json', sha256: 'a'.repeat(64)},
      runtimeAudit: {status: 'pass', coreItemIds: [], customItems: []}, createdAt: '2026-10-09T00:00:00Z'
    };
    const receiptText = `${JSON.stringify(receipt, null, 2)}\n`;
    const current = structuredClone(previous);
    current.schemaVersion = '4.0.0';
    current.prototype.sourceHash = sha(html);
    current.scopeEvidence = scopeEvidence();
    current.delivery = {
      mode: 'standard', readerRole: 'derived-readonly', riskTriggers: [],
      lineage: {
        mode: 'revision', previousManifestSha256: sha(previousText), previousSnapshotSha256: sha(previousSnapshot),
        baselineReceiptSha256: sha(receiptText), preservedCardIds: ['EXAMPLE-CARD-01'],
        modifiedCardIds: [], addedCardIds: [], removedCardIds: [], removalDecisions: []
      },
      readerComposition: {
        coreItems: ['change-overview', 'collapse-expand-all', 'return-previous-prototype', 'full-development-snapshot', 'change-kind-color', 'current-location', 'bidirectional-visible-connector', 'fact-source-details'],
        customItems: [], removedItems: []
      },
      pendingReconciliation: {carried: [], resolved: [], superseded: [], new: []}
    };
    const manifestPath = join(directory, 'manifest.json');
    const prototypePath = join(directory, 'prototype.html');
    const evidencePath = join(directory, 'evidence.json');
    const previousManifestPath = join(directory, 'previous-manifest.json');
    const previousSnapshotPath = join(directory, 'previous-reader.html');
    const receiptPath = join(directory, 'lineage-receipt.json');
    const snapshotPath = join(directory, 'reader.html');
    const metadataPath = join(directory, 'reader.build.json');
    writeFileSync(manifestPath, `${JSON.stringify(current, null, 2)}\n`);
    writeFileSync(prototypePath, html);
    writeFileSync(previousManifestPath, previousText);
    writeFileSync(previousSnapshotPath, previousSnapshot);
    writeFileSync(receiptPath, receiptText);
    const sources = Object.entries(sourceContents).map(([sourceId, content]) => {
      const path = join(directory, `${sourceId}.evidence`);
      writeFileSync(path, content);
      const sourceType = current.evidenceSources.find((source) => source.sourceId === sourceId).sourceType;
      return {sourceId, sourceType, path, origin: {kind: ['runtime', 'screenshot'].includes(sourceType) ? 'captured-runtime' : 'workspace-file', locator: `${sourceId}.evidence`}};
    });
    writeFileSync(evidencePath, `${JSON.stringify({schemaVersion: '1.0.0', sources}, null, 2)}\n`);
    const common = ['--manifest', manifestPath, '--prototype', prototypePath, '--evidence', evidencePath,
      '--workspace-root', directory, '--evidence-root', directory, '--defer-independent-review'];
    const lineageInputs = [
      '--previous-manifest', previousManifestPath, '--previous-snapshot', previousSnapshotPath,
      '--lineage-baseline-receipt', receiptPath];

    const red = spawnSync(process.execPath, [builderPath, ...common, '--out', join(directory, 'missing-context.html')], {encoding: 'utf8'});
    assert.equal(red.status, 1);
    assert.match(red.stderr, /E_LINEAGE_INPUT_REQUIRED/);

    const handoff = spawnSync(process.execPath, [validatorPath, ...common, ...lineageInputs, '--snapshot-out', snapshotPath, '--metadata-out', metadataPath], {encoding: 'utf8'});
    assert.equal(handoff.status, 0, handoff.stderr || handoff.stdout);
    assert.match(handoff.stdout, /PASS snapshot/);

    const directSnapshot = join(directory, 'reader-direct.html');
    const direct = spawnSync(process.execPath, [builderPath, ...common, ...lineageInputs, '--out', directSnapshot, '--metadata', `${directSnapshot}.build.json`], {encoding: 'utf8'});
    assert.equal(direct.status, 0, direct.stderr || direct.stdout);
    assert.match(direct.stdout, /PASS snapshot build with runtime audit/);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});

test('a complete declared v4 full or pilot slice can mint a baseline receipt and close the next v4 revision', () => {
  const directory = mkdtempSync(join(tmpdir(), 'prototype-v4-baseline-loop-'));
  try {
    for (const coverage of ['full', 'pilot-slice']) {
      const prefix = join(directory, coverage);
      const baseline = v4InitialManifest(coverage);
      baseline.prototype.sourceHash = sha(validHtml);
      const manifestPath = `${prefix}-initial-manifest.json`;
      const prototypePath = join(directory, 'prototype.html');
      const snapshotPath = `${prefix}-initial-reader.html`;
      const metadataPath = `${snapshotPath}.build.json`;
      const receiptPath = `${prefix}-baseline-receipt.json`;
      writeFileSync(manifestPath, `${JSON.stringify(baseline, null, 2)}\n`);
      writeFileSync(prototypePath, validHtml);
      const evidencePath = writeEvidenceFixture(directory, baseline);
      const common = ['--manifest', manifestPath, '--prototype', prototypePath, '--evidence', evidencePath,
        '--workspace-root', directory, '--evidence-root', directory, '--defer-independent-review'];
      const initial = spawnSync(process.execPath, [validatorPath, ...common, '--snapshot-out', snapshotPath, '--metadata-out', metadataPath], {encoding: 'utf8', timeout: 180000});
      assert.equal(initial.status, 0, initial.stderr || initial.stdout);

      const receipt = spawnSync(process.execPath, [baselineReceiptPath,
        '--manifest', manifestPath, '--snapshot', snapshotPath, '--metadata', metadataPath,
        '--out', receiptPath, '--receipt-id', `BASE-${coverage}`, '--requirement-id', 'EXAMPLE-REQ',
        '--created-at', '2026-10-13T00:00:00Z'], {encoding: 'utf8', timeout: 180000});
      assert.equal(receipt.status, 0, receipt.stderr || receipt.stdout);

      if (coverage === 'pilot-slice') {
        const manifestBytes = readFileSync(manifestPath);
        const metadataBytes = readFileSync(metadataPath);
        const snapshotBytes = readFileSync(snapshotPath);
        const rejected = (label, pattern) => {
          const result = spawnSync(process.execPath, [baselineReceiptPath,
            '--manifest', manifestPath, '--snapshot', snapshotPath, '--metadata', metadataPath,
            '--out', `${prefix}-${label}.json`, '--receipt-id', `REJECT-${label}`, '--requirement-id', 'EXAMPLE-REQ',
            '--created-at', '2026-10-13T00:00:00Z'], {encoding: 'utf8', timeout: 180000});
          assert.notEqual(result.status, 0);
          assert.match(result.stderr, pattern);
        };
        const forged = JSON.parse(manifestBytes);
        forged.schemaVersion = 'team.reader.7';
        writeFileSync(manifestPath, `${JSON.stringify(forged)}\n`);
        rejected('unknown-version', /E_LINEAGE_BASELINE_UNRECOGNIZED/);
        writeFileSync(manifestPath, manifestBytes);

        const missingCard = JSON.parse(manifestBytes);
        missingCard.cards = [];
        writeFileSync(manifestPath, `${JSON.stringify(missingCard)}\n`);
        rejected('missing-card', /E_LINEAGE_BASELINE_AUDIT/);
        writeFileSync(manifestPath, manifestBytes);

        const missingCore = JSON.parse(manifestBytes);
        missingCore.delivery.readerComposition.coreItems = [];
        writeFileSync(manifestPath, `${JSON.stringify(missingCore)}\n`);
        rejected('missing-core', /E_LINEAGE_BASELINE_UNRECOGNIZED/);
        writeFileSync(manifestPath, manifestBytes);

        const driftedMetadata = JSON.parse(metadataBytes);
        driftedMetadata.outputSha256 = '0'.repeat(64);
        writeFileSync(metadataPath, `${JSON.stringify(driftedMetadata)}\n`);
        rejected('metadata-drift', /E_LINEAGE_BASELINE_AUDIT/);
        writeFileSync(metadataPath, metadataBytes);

        writeFileSync(snapshotPath, Buffer.concat([snapshotBytes, Buffer.from('<!-- drift -->\n')]));
        rejected('reader-drift', /E_LINEAGE_BASELINE_AUDIT/);
        writeFileSync(snapshotPath, snapshotBytes);
      }

      const revision = structuredClone(baseline);
      const previousManifest = readFileSync(manifestPath);
      const previousSnapshot = readFileSync(snapshotPath);
      const receiptBytes = readFileSync(receiptPath);
      revision.delivery.lineage = {
        mode: 'revision', previousManifestSha256: sha(previousManifest), previousSnapshotSha256: sha(previousSnapshot),
        baselineReceiptSha256: sha(receiptBytes), preservedCardIds: ['EXAMPLE-CARD-01'],
        modifiedCardIds: [], addedCardIds: [], removedCardIds: [], removalDecisions: []
      };
      const revisionManifestPath = `${prefix}-revision-manifest.json`;
      const revisionSnapshotPath = `${prefix}-revision-reader.html`;
      writeFileSync(revisionManifestPath, `${JSON.stringify(revision, null, 2)}\n`);
      const revisionResult = spawnSync(process.execPath, [validatorPath,
        '--manifest', revisionManifestPath, '--prototype', prototypePath, '--evidence', evidencePath,
        '--workspace-root', directory, '--evidence-root', directory, '--defer-independent-review',
        '--previous-manifest', manifestPath, '--previous-snapshot', snapshotPath,
        '--lineage-baseline-receipt', receiptPath, '--snapshot-out', revisionSnapshotPath,
        '--metadata-out', `${revisionSnapshotPath}.build.json`], {encoding: 'utf8', timeout: 180000});
      assert.equal(revisionResult.status, 0, revisionResult.stderr || revisionResult.stdout);
      assert.match(revisionResult.stdout, /PASS snapshot/);
    }
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});

test('snapshot builder cannot bypass evidence and workspace identity gates', () => {
  const result = spawnSync(process.execPath, [builderPath, '--manifest', 'x', '--prototype', 'y', '--out', 'z'], {encoding: 'utf8'});
  assert.equal(result.status, 1);
  assert.match(result.stderr, /--evidence.*--workspace-root.*--evidence-root/);
});

test('unverified authoritative sources and prototype lineage require independent review', () => {
  const result = run(manifest(), validHtml, () => {}, false);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_INDEPENDENT_REVIEW_REQUIRED/);
});

test('Builder validation can defer Independent Review until after Handoff', () => {
  const result = run(manifest(), validHtml, () => {}, false, true);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /independent review deferred until after Builder Handoff/);
});

test('card referencing an unknown surface reports E_CARD_SURFACE', () => {
  const current = manifest();
  current.cards[0].surfaceId = 'view:missing';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_CARD_SURFACE/);
});

test('anchor outside its declared surface reports E_ANCHOR_OUTSIDE_SURFACE', () => {
  const html = '<!doctype html><html><body><main id="view-example" class="active"></main><button id="example-entry">Entry</button></body></html>';
  const result = run(manifest(), html);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_ANCHOR_OUTSIDE_SURFACE/);
});

test('body cannot masquerade as a real surface or anchor', () => {
  const current = manifest();
  current.surfaces[0].rootSelector = 'body';
  current.cards[0].anchor.selector = 'body';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_VIRTUAL_SURFACE_FORBIDDEN|E_FAKE_ANCHOR_FORBIDDEN/);
});

test('unbound semantic claim fails closed', () => {
  const current = manifest();
  current.cards[0].evidenceBindings = current.cards[0].evidenceBindings.filter(({claimPath}) => !claimPath.endsWith('/summary'));
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_SEMANTIC_CLAIM_UNBOUND/);
});

test('generic placeholder copy is rejected', () => {
  const current = manifest();
  current.cards[0].summary = '查看或完成当前操作';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_PLACEHOLDER_SEMANTICS/);
});

test('unresolvable evidence excerpt is rejected', () => {
  const current = manifest();
  current.cards[0].evidenceBindings[0].sourceRefs[0].excerpt = '文档里不存在的句子';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_EVIDENCE_EXCERPT_UNRESOLVED/);
});

test('a locator must resolve as its declared locator kind', () => {
  const current = manifest();
  current.cards[0].evidenceBindings[0].sourceRefs[0].locator = 'heading:新增示例入口';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_EVIDENCE_LOCATOR_UNRESOLVED/);
});

test('blocking pending items block pilot slices as well as full delivery', () => {
  const current = manifest();
  current.cards[0].pendingItems.push({question: '确认入口权限', owner: '产品', impact: '决定按钮是否可见', blocksDelivery: true});
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_BLOCKING_PENDING_ITEMS/);
});

test('baseline channels cannot be satisfied by a differently typed source', () => {
  const current = manifest();
  current.baselineEvidence.runtimePage.sourceIds = ['prd-current'];
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_BASELINE_SOURCE_TYPE/);
});

test('runtime or code cannot independently authorize target semantics', () => {
  const current = manifest();
  for (const binding of current.cards[0].evidenceBindings) {
    binding.sourceRefs = [{sourceId: 'code-current', locator: 'text:id="view-example"', excerpt: 'id="view-example"'}];
  }
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_AUTHORITATIVE_SEMANTIC_SOURCE_REQUIRED/);
});

test('evidence files outside explicitly allowed roots are rejected', () => {
  const current = manifest();
  const result = run(current, validHtml, ({evidenceBundle}) => {
    evidenceBundle.sources.find(({sourceId}) => sourceId === 'runtime-current').path = '/etc/hosts';
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_EVIDENCE_OUTSIDE_TRUSTED_ROOT/);
});

test('prototype path must resolve to the declared current workspace source', () => {
  const current = manifest();
  current.prototype.sourcePath = 'replacement.html';
  const result = run(current, validHtml, ({directory}) => {
    writeFileSync(join(directory, 'replacement.html'), validHtml);
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_PROTOTYPE_WORKSPACE_IDENTITY/);
});

test('each surface root must be covered by state snapshots', () => {
  const current = manifest();
  current.surfaces[0].snapshotSelectors = [];
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_SCHEMA_VALIDATION|E_SURFACE_ROOT_SNAPSHOT_REQUIRED/);
});

test('locatable interaction copy cannot escape into globalRules', () => {
  const current = manifest();
  current.pages.push({pageId: 'other-page', title: 'Other', order: 2});
  current.globalRules.push({
    ruleId: 'G-1',
    title: '批量导入操作',
    summary: '触发批量导入后呈现侧边层。',
    scope: 'all-pages',
    appliesToPageIds: ['example-page', 'other-page'],
    nonLocatableReason: 'cross-page-policy',
    rules: ['该操作位于两个业务页的标题区。'],
    evidenceBindings: [
      {claimPath: '/globalRules/0/title', sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
      {claimPath: '/globalRules/0/summary', sourceRefs: [{sourceId: 'prd-current', locator: 'text:页面增加一个示例入口', excerpt: '页面增加一个示例入口'}]},
      {claimPath: '/globalRules/0/rules/0', sourceRefs: [{sourceId: 'prd-current', locator: 'text:示例入口位于示例页面顶部', excerpt: '示例入口位于示例页面顶部'}]}
    ],
    pendingItems: []
  });
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_GLOBAL_RULE_LOCATABLE_SEMANTICS/);
});

test('global rule scope classification must itself be evidence-bound', () => {
  const current = manifest();
  current.pages.push({pageId: 'other-page', title: 'Other', order: 2});
  current.globalRules.push({
    ruleId: 'G-1',
    title: '统一权限约束',
    summary: '只读用户不可提交。',
    scope: 'all-pages',
    appliesToPageIds: ['example-page', 'other-page'],
    nonLocatableReason: 'cross-page-policy',
    rules: ['编辑用户可以提交。'],
    evidenceBindings: [
      {claimPath: '/globalRules/0/title', sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
      {claimPath: '/globalRules/0/summary', sourceRefs: [{sourceId: 'prd-current', locator: 'text:页面增加一个示例入口', excerpt: '页面增加一个示例入口'}]},
      {claimPath: '/globalRules/0/rules/0', sourceRefs: [{sourceId: 'prd-current', locator: 'text:示例入口位于示例页面顶部', excerpt: '示例入口位于示例页面顶部'}]}
    ],
    pendingItems: []
  });
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_SEMANTIC_CLAIM_UNBOUND/);
});

test('offscreen anchors are scrolled into view before connector validation', () => {
  const html = '<!doctype html><html><body><main id="view-example" class="active"><div style="height:2400px"></div><button id="example-entry">Entry</button></main></body></html>';
  const result = run(manifest(), html);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /PASS runtime 1 cards/);
});

test('an over-viewport reader card keeps its title connector endpoint visible across two live cycles', () => {
  const current = manifest();
  current.cards[0] = card({
    summary: '页面增加一个示例入口。',
    changeContent: {
      added: Array.from({length: 36}, (_, index) => `超长说明条目 ${index + 1}：页面增加一个示例入口。`.repeat(3)),
      placement: '示例页面顶部',
      finalResult: '示例入口可见'
    }
  });
  const preludeCards = Array.from({length: 10}, (_, index) => card({
    cardId: `EXAMPLE-CARD-${String(index + 2).padStart(2, '0')}`,
    displayNumber: String(index + 2).padStart(2, '0'),
    title: `前置示例入口 ${index + 1}`,
    summary: `前置说明 ${index + 1}，用于让目标卡标题在初始阅读器视口外。`,
    anchor: {kind: 'element', selector: `#example-entry-${index + 2}`, enterActions: [], manualPath: `示例页面 → 前置示例入口 ${index + 1}`},
    evidenceBindings: [
      {claimPath: `/cards/${index + 1}/title`, sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
      {claimPath: `/cards/${index + 1}/summary`, sourceRefs: [{sourceId: 'prd-current', locator: 'text:页面增加一个示例入口', excerpt: '页面增加一个示例入口'}]},
      {claimPath: `/cards/${index + 1}/changeContent`, sourceRefs: [{sourceId: 'prd-current', locator: 'text:示例入口位于示例页面顶部', excerpt: '示例入口位于示例页面顶部'}]}
    ]
  }));
  current.cards.push(...preludeCards, card({
    cardId: 'EXAMPLE-CARD-12',
    displayNumber: '12',
    title: '第二个示例入口',
    summary: '第二个示例入口位于长卡之后，初始标题必须位于阅读器视口外。',
    anchor: {kind: 'element', selector: '#example-entry-12', enterActions: [], manualPath: '示例页面 → 第二个示例入口'},
    evidenceBindings: [
      {claimPath: '/cards/11/title', sourceRefs: [{sourceId: 'prd-current', locator: 'text:新增示例入口', excerpt: '新增示例入口'}]},
      {claimPath: '/cards/11/summary', sourceRefs: [{sourceId: 'prd-current', locator: 'text:页面增加一个示例入口', excerpt: '页面增加一个示例入口'}]},
      {claimPath: '/cards/11/changeContent', sourceRefs: [{sourceId: 'prd-current', locator: 'text:示例入口位于示例页面顶部', excerpt: '示例入口位于示例页面顶部'}]}
    ]
  }));
  const html = `<!doctype html><html><body><main id="view-example" class="active"><button id="example-entry">Entry</button>${Array.from({length: 11}, (_, index) => `<button id="example-entry-${index + 2}">Entry ${index + 2}</button>`).join('')}</main></body></html>`;
  const result = run(current, html);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /PASS runtime 12 cards/);
});

test('visible state drift outside reversible controls fails return-state audit', () => {
  const current = manifest();
  current.surfaces.push({
    surfaceId: 'drawer:example',
    kind: 'drawer',
    rootSelector: '#drawer-example',
    activeSelector: '#drawer-example.active',
    parentSurfaceIds: ['view:example'],
    exitActions: [{type: 'click', selector: '#close-drawer'}],
    exitGuardSurfaceIds: [],
    snapshotSelectors: ['#drawer-example']
  });
  current.cards[0].surfaceId = 'drawer:example';
  current.cards[0].anchor = {
    kind: 'element',
    selector: '#example-entry',
    enterActions: [{type: 'click', selector: '#open-drawer'}],
    manualPath: '示例页面 → 打开抽屉 → 示例入口'
  };
  const html = `<!doctype html><html><head><style>.drawer:not(.active){display:none}</style></head><body>
    <main id="view-example" class="active"><button id="open-drawer">Open</button></main>
    <aside id="drawer-example" class="drawer"><p id="drawer-status">closed</p><button id="example-entry">Entry</button><button id="close-drawer">Close</button></aside>
    <script>
      const openDrawer = () => { document.querySelector('#drawer-example').classList.add('active'); document.querySelector('#drawer-status').textContent = 'opened'; };
      document.querySelector('#open-drawer').addEventListener('click', openDrawer);
      document.querySelector('#close-drawer').addEventListener('click', () => document.querySelector('#drawer-example').classList.remove('active'));
    </script>
  </body></html>`;
  const result = run(current, html);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_RUNTIME_STATE_RESTORE/);
});

test('repeat transition on the same live page exposes hidden JavaScript state leaks', () => {
  const current = manifest();
  current.surfaces.push({
    surfaceId: 'drawer:example',
    kind: 'drawer',
    rootSelector: '#drawer-example',
    activeSelector: '#drawer-example.active',
    parentSurfaceIds: ['view:example'],
    exitActions: [{type: 'click', selector: '#close-drawer'}],
    exitGuardSurfaceIds: [],
    snapshotSelectors: ['#drawer-example']
  });
  current.cards[0].surfaceId = 'drawer:example';
  current.cards[0].anchor = {
    kind: 'element',
    selector: '#example-entry',
    enterActions: [{type: 'click', selector: '#open-drawer'}],
    manualPath: '示例页面 → 打开抽屉 → 示例入口'
  };
  const html = `<!doctype html><html><head><style>.drawer:not(.active){display:none}</style></head><body>
    <main id="view-example" class="active"><button id="open-drawer">Open</button></main>
    <aside id="drawer-example" class="drawer"><button id="example-entry">Entry</button><button id="close-drawer">Close</button></aside>
    <script>
      let openCount = 0;
      document.querySelector('#open-drawer').addEventListener('click', () => {
        openCount += 1;
        const drawer = document.querySelector('#drawer-example');
        drawer.classList.add('active');
        if (openCount > 1) document.querySelector('#example-entry').dataset.leaked = 'true';
      });
      document.querySelector('#close-drawer').addEventListener('click', () => document.querySelector('#drawer-example').classList.remove('active'));
    </script>
  </body></html>`;
  const result = run(current, html);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_RUNTIME_STATE_RESTORE_REPEAT/);
});

test('screenshot alone cannot ground product semantics', () => {
  const current = manifest();
  for (const binding of current.cards[0].evidenceBindings) {
    binding.sourceRefs = [{sourceId: 'screenshot-current', locator: 'text:fake-image-bytes', excerpt: 'fake-image-bytes'}];
  }
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_SEMANTIC_SOURCE_REQUIRED/);
});

test('a unique but hidden anchor is rejected', () => {
  const html = '<!doctype html><html><body><main id="view-example" class="active"><button id="example-entry" hidden>Entry</button></main></body></html>';
  const result = run(manifest(), html);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_ANCHOR_NOT_VISIBLE/);
});

test('duplicated semantic blocks across locatable cards are rejected', () => {
  const current = manifest();
  const second = structuredClone(current.cards[0]);
  second.cardId = 'EXAMPLE-CARD-02';
  second.displayNumber = '02';
  second.anchor.selector = '#example-entry-2';
  second.evidenceBindings = second.evidenceBindings.map((binding) => ({
    ...binding,
    claimPath: binding.claimPath.replace('/cards/0/', '/cards/1/')
  }));
  current.cards.push(second);
  const html = '<!doctype html><html><body><main id="view-example" class="active"><button id="example-entry">Entry</button><button id="example-entry-2">Entry 2</button></main></body></html>';
  const result = run(current, html);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_DUPLICATE_CARD_SEMANTICS/);
});

test('full coverage with an uncovered page reports E_FULL_COVERAGE_INCOMPLETE', () => {
  const current = manifest();
  current.requirement.coverage = 'full';
  current.pages.push({pageId: 'uncovered-page', title: 'Uncovered', order: 2});
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_FULL_COVERAGE_INCOMPLETE/);
});

test('legacy v2 requires explicit opt-in', () => {
  const current = manifest();
  current.schemaVersion = '2.0.0';
  const result = run(current, validHtml);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /E_LEGACY_V2_EXPLICIT_OPT_IN_REQUIRED/);
});

test('legacy v2 explicit opt-in remains readable but is not Standard-ready', () => {
  const result = runLegacyV2(validHtml);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /legacy-v2 structural validation only/);
});

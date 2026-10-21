import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, readFileSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {validatePrototypeIdentity} from '../scripts/validate-evidence-bundle.mjs';

const sha = (text) => createHash('sha256').update(text).digest('hex');
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'prototype-first-generation-'));
  const output = join(root, 'prototype.html');
  writeFileSync(output, '<!doctype html><html><body>new prototype</body></html>');
  const proof = join(root, 'approval.md');
  writeFileSync(proof, 'Synthetic user confirms no existing prototype and authorizes first generation.');
  const confirmation = {schema_version: 1, kind: 'first-prototype-confirmation',
    workspace_root: root, prototype_relative_path: 'prototype.html',
    no_existing_prototype_confirmed: true, searched_roots: ['.'],
    confirmed_by: 'synthetic-user', confirmed_at: '2026-09-18T00:00:00Z',
    confirmation_evidence: {path: proof, sha256: sha('Synthetic user confirms no existing prototype and authorizes first generation.')}};
  const confirmationPath = join(root, 'confirmation.json');
  const confirmationText = JSON.stringify(confirmation);
  writeFileSync(confirmationPath, confirmationText);
  const declaration = {schema_version: 1, kind: 'first-prototype-baseline',
    confirmation_path: confirmationPath, confirmation_sha256: sha(confirmationText),
    workspace_root: root, prototype_relative_path: 'prototype.html'};
  const text = JSON.stringify(declaration);
  const manifest = {prototype: {sourcePath: 'prototype.html', sourceHash: sha('new'),
    baselineKind: 'first-generation', baselineSourceId: 'first', baselineHash: sha(text)},
    baselineEvidence: {mode: 'existing-system-increment'}, cards: [{changeKinds: ['add']}]};
  const sources = new Map([['first', {sourceType: 'prototype-baseline', contentKind: 'text', sourceHash: sha(text), text}],
    ['runtime', {sourceType: 'runtime'}], ['code', {sourceType: 'code'}], ['screenshot', {sourceType: 'screenshot'}]]);
  return {manifest, prototypePath: output, workspaceRoot: root, loadedSources: sources, proof};
}

test('first-generation declaration passes identity without pretending to be an old HTML', () => {
  const result = validatePrototypeIdentity(fixture());
  assert.equal(result.ok, true, result.errors.join('\n'));
  assert.ok(result.trustTriggers.includes('first-real-use'));
});
test('first generation cannot use a fake HTML as its declaration', () => {
  const data = fixture();
  data.loadedSources.get('first').text = '<html>fake old baseline</html>';
  assert.equal(validatePrototypeIdentity(data).ok, false);
});
test('first generation cannot ignore confirmation proof drift', () => {
  const data = fixture();
  writeFileSync(data.proof, 'different approval');
  assert.equal(validatePrototypeIdentity(data).ok, false);
});
test('first generation does not bypass real-system code or screenshot evidence', () => {
  const data = fixture();
  data.loadedSources.delete('code');
  assert.equal(validatePrototypeIdentity(data).ok, false);
});

test('first declaration cannot be relabeled as an existing prototype', () => {
  const data = fixture();
  delete data.manifest.prototype.baselineKind;
  assert.equal(validatePrototypeIdentity(data).ok, false);
});

test('first declaration rejects duplicate confirmation JSON fields', () => {
  const data = fixture();
  const declaration = JSON.parse(data.loadedSources.get('first').text);
  const text = readFileSync(declaration.confirmation_path, 'utf8').replace('"schema_version":1', '"schema_version":1,"schema_version":1');
  writeFileSync(declaration.confirmation_path, text);
  declaration.confirmation_sha256 = sha(text);
  data.loadedSources.get('first').text = JSON.stringify(declaration);
  data.loadedSources.get('first').sourceHash = sha(JSON.stringify(declaration));
  data.manifest.prototype.baselineHash = sha(JSON.stringify(declaration));
  assert.equal(validatePrototypeIdentity(data).ok, false);
});

test('first-generation Standard runs real schema, runtime and independent-review gates', () => {
  const data = fixture();
  const directory = data.workspaceRoot;
  const html = '<!doctype html><html><body><main id="view-example" class="active"><button id="example-entry">Entry</button></main></body></html>';
  writeFileSync(data.prototypePath, html);
  const contents = {prd: '新增入口\n页面增加入口。\n入口位于顶部。\n', runtime: 'Current synthetic page observed.', code: 'export const Entry = true;', screenshot: 'synthetic-image', first: data.loadedSources.get('first').text};
  const kinds = {prd: 'prd', runtime: 'runtime', code: 'code', screenshot: 'screenshot', first: 'prototype-baseline'};
  const authorities = {prd: 'current-product-requirement', runtime: 'observed-runtime', code: 'current-code', screenshot: 'same-version-screenshot', first: 'current-prototype-baseline'};
  const evidence = {schemaVersion: '1.0.0', sources: Object.entries(contents).map(([id, text]) => {
    const path = join(directory, `${id}.txt`);
    writeFileSync(path, text);
    return {sourceId: id, sourceType: kinds[id], path, origin: {kind: ['runtime', 'screenshot'].includes(id) ? 'captured-runtime' : 'workspace-file', locator: `${id}.txt`}};
  })};
  const manifest = {schemaVersion: '3.0.0',
    requirement: {id: 'SYNTHETIC', title: 'Synthetic first prototype', coverage: 'pilot-slice', primarySourceId: 'prd'},
    prototype: {...data.manifest.prototype, sourceLabel: 'prototype.html', sourceRole: 'current-workspace-prototype', sourceHash: sha(html)},
    delivery: {mode: 'standard', readerRole: 'derived-readonly', riskTriggers: []},
    baselineEvidence: {mode: 'existing-system-increment', runtimePage: {availability: 'available', sourceIds: ['runtime'], note: 'Observed synthetic runtime'}, code: {availability: 'available', sourceIds: ['code'], note: 'Read synthetic code'}, screenshots: {availability: 'available', sourceIds: ['screenshot'], note: 'Synthetic same-version screenshot'}, conflicts: []},
    evidenceSources: Object.entries(contents).map(([id, text]) => ({sourceId: id, sourceType: kinds[id], authority: authorities[id], label: id, contentKind: id === 'screenshot' ? 'image' : id === 'code' ? 'source-code' : 'text', sourceHash: sha(text)})),
    pages: [{pageId: 'example-page', title: 'Example', order: 1}],
    surfaces: [{surfaceId: 'view:example', kind: 'view', rootSelector: '#view-example', activeSelector: '#view-example.active', parentSurfaceIds: [], exitActions: [], exitGuardSurfaceIds: [], snapshotSelectors: ['#view-example']}],
    globalRules: [], cards: [{cardId: 'FIRST-01', displayNumber: '01', requirementId: 'SYNTHETIC', pageId: 'example-page', surfaceId: 'view:example', region: '顶部', title: '新增入口', summary: '页面增加入口。', changeKinds: ['add'], changeContent: {added: ['入口'], placement: '顶部', finalResult: '入口可见'}, fields: [], rules: [], interactions: [], errors: [], pendingItems: [], history: [],
      evidenceBindings: [{claimPath: '/cards/0/title', sourceRefs: [{sourceId: 'prd', locator: 'text:新增入口', excerpt: '新增入口'}]}, {claimPath: '/cards/0/summary', sourceRefs: [{sourceId: 'prd', locator: 'text:页面增加入口', excerpt: '页面增加入口'}]}, {claimPath: '/cards/0/changeContent', sourceRefs: [{sourceId: 'prd', locator: 'text:入口位于顶部', excerpt: '入口位于顶部'}]}], anchor: {kind: 'element', selector: '#example-entry', enterActions: [], manualPath: 'Example → Entry'}}]};
  const manifestText = JSON.stringify(manifest);
  const evidenceText = JSON.stringify(evidence);
  const manifestPath = join(directory, 'manifest.json'), evidencePath = join(directory, 'evidence.json');
  writeFileSync(manifestPath, manifestText); writeFileSync(evidencePath, evidenceText);
  const validator = fileURLToPath(new URL('../scripts/validate-prototype-handoff.mjs', import.meta.url));
  const args = [validator, '--manifest', manifestPath, '--prototype', data.prototypePath, '--evidence', evidencePath, '--workspace-root', directory, '--evidence-root', directory, '--allow-legacy-v3'];
  const blocked = spawnSync(process.execPath, args, {encoding: 'utf8'});
  assert.equal(blocked.status, 1);
  assert.match(blocked.stderr, /E_INDEPENDENT_REVIEW_REQUIRED/);
  const reviewPath = join(directory, 'synthetic-test-review.json');
  writeFileSync(reviewPath, JSON.stringify({schemaVersion: '1.0.0', reviewId: 'SYNTHETIC-ONLY', reviewerRole: 'independent', reviewedAt: '2026-09-18T00:00:00Z', subject: {manifestHash: sha(manifestText), prototypeHash: sha(html), evidenceBundleHash: sha(evidenceText)}, triggers: ['first-real-use', 'unverified-authoritative-source'], verdict: 'pass', findings: []}));
  const passed = spawnSync(process.execPath, [...args, '--review', reviewPath], {encoding: 'utf8'});
  assert.equal(passed.status, 0, passed.stderr);
  assert.match(passed.stdout, /PASS runtime 1 cards/);
});

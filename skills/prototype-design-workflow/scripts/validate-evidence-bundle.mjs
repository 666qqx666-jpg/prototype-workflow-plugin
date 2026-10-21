import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {existsSync, lstatSync, readFileSync, realpathSync} from 'node:fs';
import {basename, dirname, isAbsolute, relative, resolve} from 'node:path';

const TEXT_KINDS = new Set(['text', 'html', 'source-code']);
const ORIGIN_KINDS = Object.freeze({
  prd: new Set(['workspace-file', 'user-provided', 'task-record']),
  decision: new Set(['workspace-file', 'user-provided', 'task-record']),
  runtime: new Set(['captured-runtime', 'task-record']),
  code: new Set(['workspace-file']),
  screenshot: new Set(['captured-runtime', 'user-provided']),
  'prototype-baseline': new Set(['workspace-file'])
});

export function validateEvidenceBundle({manifest, bundle, bundlePath, allowedRoots = []}) {
  const errors = [];
  const fail = (code, detail) => errors.push(`${code} ${detail}`);
  const bundleSources = new Map();
  const trustedRoots = allowedRoots.filter(Boolean).map((root) => realpathSync(resolve(root)));
  if (trustedRoots.length === 0) fail('E_EVIDENCE_ROOT_REQUIRED', 'no --evidence-root');
  for (const source of bundle?.sources ?? []) {
    if (bundleSources.has(source.sourceId)) fail('E_EVIDENCE_BUNDLE_DUPLICATE', source.sourceId);
    bundleSources.set(source.sourceId, source);
  }

  const loaded = new Map();
  for (const source of manifest.evidenceSources ?? []) {
    const entry = bundleSources.get(source.sourceId);
    if (!entry) {
      fail('E_EVIDENCE_FILE_MISSING', source.sourceId);
      continue;
    }
    const filePath = isAbsolute(entry.path)
      ? entry.path
      : resolve(dirname(bundlePath), entry.path);
    if (!existsSync(filePath)) {
      fail('E_EVIDENCE_FILE_MISSING', `${source.sourceId} ${entry.path}`);
      continue;
    }
    const resolvedPath = realpathSync(filePath);
    if (!trustedRoots.some((root) => isWithinRoot(resolvedPath, root))) {
      fail('E_EVIDENCE_OUTSIDE_TRUSTED_ROOT', `${source.sourceId} ${entry.path}`);
      continue;
    }
    if (entry.sourceType !== source.sourceType || !ORIGIN_KINDS[source.sourceType]?.has(entry.origin?.kind) || !entry.origin?.locator) {
      fail('E_EVIDENCE_PROVENANCE', source.sourceId);
      continue;
    }
    if (entry.origin.kind === 'workspace-file' && !entry.origin.locator.endsWith(basename(resolvedPath))) {
      fail('E_EVIDENCE_ORIGIN_LOCATOR', source.sourceId);
      continue;
    }
    const bytes = readFileSync(filePath);
    const actualHash = createHash('sha256').update(bytes).digest('hex');
    if (actualHash !== source.sourceHash.toLowerCase()) {
      fail('E_EVIDENCE_HASH', `${source.sourceId} expected=${source.sourceHash} actual=${actualHash}`);
      continue;
    }
    loaded.set(source.sourceId, {
      ...source,
      resolvedPath,
      origin: entry.origin,
      trustedRoots,
      gitVerified: entry.origin.kind === 'workspace-file' && gitHeadHash(resolvedPath) === source.sourceHash.toLowerCase(),
      text: TEXT_KINDS.has(source.contentKind) ? bytes.toString('utf8') : null
    });
  }

  for (const owner of [...(manifest.globalRules ?? []), ...(manifest.cards ?? [])]) {
    for (const binding of owner.evidenceBindings ?? []) {
      for (const ref of binding.sourceRefs ?? []) {
        const source = loaded.get(ref.sourceId);
        if (!source) continue;
        if (!TEXT_KINDS.has(source.contentKind)) {
          fail('E_EVIDENCE_NOT_TEXTUAL', `${binding.claimPath} ${ref.sourceId}`);
          continue;
        }
        if (!ref.excerpt) {
          fail('E_EVIDENCE_EXCERPT_REQUIRED', `${binding.claimPath} ${ref.sourceId}`);
          continue;
        }
        const located = locateEvidenceText(source.text, ref.locator);
        if (!located.ok) {
          fail(located.code, `${binding.claimPath} ${ref.sourceId} ${ref.locator}`);
          continue;
        }
        if (!located.text.includes(ref.excerpt)) {
          fail('E_EVIDENCE_EXCERPT_UNRESOLVED', `${binding.claimPath} ${ref.sourceId}`);
        }
      }
    }
  }
  const trustTriggers = [...loaded.values()].some((source) =>
    ['prd', 'decision'].includes(source.sourceType) && !source.gitVerified
  ) ? ['unverified-authoritative-source'] : [];
  return {ok: errors.length === 0, errors, loadedSourceCount: loaded.size, loadedSources: loaded, trustTriggers};
}

export function validatePrototypeIdentity({manifest, prototypePath, workspaceRoot, loadedSources}) {
  const errors = [];
  const root = realpathSync(resolve(workspaceRoot));
  const actual = realpathSync(resolve(prototypePath));
  const expected = realpathSync(resolve(root, manifest.prototype.sourcePath));
  if (!isWithinRoot(actual, root) || actual !== expected) {
    errors.push(`E_PROTOTYPE_WORKSPACE_IDENTITY expected=${expected} actual=${actual}`);
  }
  const trustTriggers = [];
  if (manifest.baselineEvidence?.mode === 'existing-system-increment') {
    const baseline = loadedSources.get(manifest.prototype.baselineSourceId);
    if (!baseline || baseline.sourceType !== 'prototype-baseline' ||
        baseline.sourceHash.toLowerCase() !== manifest.prototype.baselineHash.toLowerCase()) {
      errors.push('E_PROTOTYPE_BASELINE_IDENTITY');
    }
    if (manifest.prototype.baselineKind === 'first-generation') {
      try {
        const declaration = strictJson(baseline?.text ?? '');
        closedRecord(declaration, ['schema_version', 'kind', 'confirmation_path', 'confirmation_sha256', 'workspace_root', 'prototype_relative_path'], 'first-prototype-baseline');
        const linkedRoots = baseline.trustedRoots ?? [root];
        assertLinkedMaterial(declaration.confirmation_path, linkedRoots);
        const confirmationBytes = readFileSync(declaration.confirmation_path);
        if (hashBytes(confirmationBytes) !== declaration.confirmation_sha256) throw new Error('confirmation drift');
        const confirmation = strictJson(confirmationBytes.toString('utf8'));
        closedRecord(confirmation, ['schema_version', 'kind', 'workspace_root', 'prototype_relative_path', 'no_existing_prototype_confirmed', 'searched_roots', 'confirmed_by', 'confirmed_at', 'confirmation_evidence'], 'first-prototype-confirmation');
        const proof = confirmation.confirmation_evidence;
        assertLinkedMaterial(proof?.path, linkedRoots);
        if (baseline.contentKind !== 'text' || confirmation.no_existing_prototype_confirmed !== true ||
            !confirmation.confirmed_by?.trim() || !confirmation.confirmed_at?.trim() ||
            !Array.isArray(confirmation.searched_roots) || confirmation.searched_roots.length === 0 ||
            !proof || Object.keys(proof).sort().join(',') !== 'path,sha256' ||
            hashBytes(readFileSync(proof.path)) !== proof.sha256 ||
            realpathSync(declaration.workspace_root) !== root || realpathSync(confirmation.workspace_root) !== root ||
            declaration.prototype_relative_path !== manifest.prototype.sourcePath ||
            confirmation.prototype_relative_path !== manifest.prototype.sourcePath ||
            !['runtime', 'code', 'screenshot'].every((kind) => [...loadedSources.values()].some((source) => source.sourceType === kind))) {
          throw new Error('first generation identity or evidence missing');
        }
        for (const searchRoot of confirmation.searched_roots) {
          if (typeof searchRoot !== 'string' || isAbsolute(searchRoot) || searchRoot.split('/').includes('..') ||
              !isWithinRoot(realpathSync(resolve(root, searchRoot)), root)) throw new Error('invalid source search root');
        }
        let target = resolve(root, manifest.prototype.sourcePath);
        while (target !== root) {
          if (existsSync(target) && lstatSync(target).isSymbolicLink()) throw new Error('symlinked first source');
          target = dirname(target);
        }
        trustTriggers.push('first-real-use');
      } catch {
        errors.push('E_FIRST_PROTOTYPE_DECLARATION');
      }
      return {ok: errors.length === 0, errors, trustTriggers};
    }
    if (manifest.prototype.baselineKind && manifest.prototype.baselineKind !== 'existing-prototype') {
      errors.push('E_PROTOTYPE_BASELINE_KIND');
    }
    if (baseline?.contentKind !== 'html') errors.push('E_PROTOTYPE_EXISTING_BASELINE_HTML_REQUIRED');
    const hasMutation = (manifest.cards ?? []).some(({changeKinds = []}) =>
      changeKinds.some((kind) => ['add', 'modify', 'delete'].includes(kind))
    );
    if (hasMutation && manifest.prototype.sourceHash.toLowerCase() === manifest.prototype.baselineHash.toLowerCase()) {
      errors.push('E_PROTOTYPE_INCREMENT_NOT_APPLIED');
    }
    if (gitHeadHash(actual) !== manifest.prototype.baselineHash.toLowerCase()) {
      trustTriggers.push('unverified-prototype-lineage');
    }
  }
  return {ok: errors.length === 0, errors, trustTriggers};
}

function hashBytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function closedRecord(value, keys, kind) {
  if (!value || Array.isArray(value) || Object.keys(value).sort().join(',') !== keys.sort().join(',') ||
      value.schema_version !== 1 || value.kind !== kind) throw new Error('invalid first prototype record');
}

function assertLinkedMaterial(path, roots) {
  if (typeof path !== 'string' || !isAbsolute(path) || !/\.(md|txt|json)$/.test(path) ||
      path.split('/').some((part) => ['.ssh', '.config'].includes(part)) ||
      !roots.some((root) => isWithinRoot(realpathSync(path), root))) throw new Error('unsafe confirmation material');
}

function strictJson(text) {
  const result = JSON.parse(text);
  // JSON.parse alone silently keeps the last duplicate key. Scan the already
  // syntax-validated JSON recursively and reject duplicates at every depth.
  let position = 0;
  const space = () => { while (/\s/.test(text[position] ?? '') && position < text.length) position += 1; };
  const string = () => {
    const start = position++;
    while (position < text.length) {
      if (text[position] === '\\') position += 2;
      else if (text[position++] === '"') break;
    }
    return JSON.parse(text.slice(start, position));
  };
  function value() {
    space();
    if (text[position] === '{') {
      position += 1; space(); const keys = new Set();
      while (text[position] !== '}') {
        const key = string();
        if (keys.has(key)) throw new Error('duplicate confirmation field');
        keys.add(key); space(); position += 1; value(); space();
        if (text[position] === ',') { position += 1; space(); } else break;
      }
      position += 1;
    } else if (text[position] === '[') {
      position += 1; space();
      while (text[position] !== ']') {
        value(); space(); if (text[position] === ',') { position += 1; space(); } else break;
      }
      position += 1;
    } else if (text[position] === '"') string();
    else while (position < text.length && !/[\s,}\]]/.test(text[position])) position += 1;
  }
  value();
  return result;
}

function isWithinRoot(path, root) {
  return path === root || path.startsWith(`${root}/`);
}

function gitHeadHash(path) {
  const rootResult = spawnSync('git', ['-C', dirname(path), 'rev-parse', '--show-toplevel'], {encoding: 'utf8'});
  if (rootResult.status !== 0) return null;
  const root = rootResult.stdout.trim();
  const relativePath = relative(root, path);
  if (!relativePath || relativePath.startsWith('..')) return null;
  const content = spawnSync('git', ['-C', root, 'show', `HEAD:${relativePath}`]);
  if (content.status !== 0) return null;
  return createHash('sha256').update(content.stdout).digest('hex');
}

export function locateEvidenceText(text, locator) {
  if (locator.startsWith('lines:')) {
    const match = locator.match(/^lines:(\d+)(?:-(\d+))?$/);
    if (!match) return {ok: false, code: 'E_EVIDENCE_LOCATOR_INVALID'};
    const start = Number(match[1]);
    const end = Number(match[2] ?? match[1]);
    const lines = text.split(/\r?\n/);
    if (start < 1 || end < start || end > lines.length) return {ok: false, code: 'E_EVIDENCE_LOCATOR_UNRESOLVED'};
    return {ok: true, text: lines.slice(start - 1, end).join('\n')};
  }
  const match = locator.match(/^(heading|selector|route|symbol|text):(.+)$/);
  if (!match) return {ok: false, code: 'E_EVIDENCE_LOCATOR_INVALID'};
  const kind = match[1];
  const needle = match[2].trim();
  if (!needle) return {ok: false, code: 'E_EVIDENCE_LOCATOR_INVALID'};
  if (kind === 'heading') return locateMarkdownHeading(text, needle);
  const index = text.indexOf(needle);
  if (index < 0) return {ok: false, code: 'E_EVIDENCE_LOCATOR_UNRESOLVED'};
  const windowStart = Math.max(0, index - 800);
  const windowEnd = Math.min(text.length, index + needle.length + 1600);
  return {ok: true, text: text.slice(windowStart, windowEnd)};
}

function locateMarkdownHeading(text, needle) {
  const lines = text.split(/\r?\n/);
  let start = -1;
  let level = null;
  for (const [index, line] of lines.entries()) {
    const match = line.match(/^(#{1,6})\s+(.+)$/);
    if (match && match[2].includes(needle)) {
      start = index;
      level = match[1].length;
      break;
    }
  }
  if (start < 0) return {ok: false, code: 'E_EVIDENCE_LOCATOR_UNRESOLVED'};
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    const match = lines[index].match(/^(#{1,6})\s+/);
    if (match && match[1].length <= level) {
      end = index;
      break;
    }
  }
  return {ok: true, text: lines.slice(start, end).join('\n')};
}

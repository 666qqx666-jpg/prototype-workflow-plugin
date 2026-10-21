#!/usr/bin/env node
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync, existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {basename, resolve} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {CORE_READER_ITEMS} from '../assets/developer-reader-shell/src/reader/lineage.mjs';
import {extractFrozenReaderBindings} from '../assets/developer-reader-shell/src/reader/frozen-review-migration.mjs';

const args = process.argv.slice(2);
const get = (name) => { const i = args.indexOf(`--${name}`); return i < 0 ? null : args[i + 1]; };
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
try {
  const manifestPath = get('manifest'), snapshotPath = get('snapshot'), metadataPath = get('metadata'), out = get('out');
  const receiptId = get('receipt-id'), requirementId = get('requirement-id'), createdAt = get('created-at');
  const migrationDescriptorPath = get('migration-descriptor');
  const migrationDescriptorBytes = migrationDescriptorPath ? readFileSync(resolve(migrationDescriptorPath)) : null;
  if (![manifestPath, snapshotPath, metadataPath, out, receiptId, requirementId, createdAt].every(Boolean)) throw new Error('E_LINEAGE_INPUT_REQUIRED usage');
  if (existsSync(resolve(out))) throw new Error('E_LINEAGE_RECEIPT_EXISTS');
  const manifestBytes = readFileSync(resolve(manifestPath)), snapshotBytes = readFileSync(resolve(snapshotPath)), metadataBytes = readFileSync(resolve(metadataPath));
  const manifest = JSON.parse(manifestBytes.toString('utf8')), metadata = JSON.parse(metadataBytes.toString('utf8'));
  const projectCustom = manifest.prototypeReaderFormat === 'prototype-design-workflow-cumulative-v1'
    && metadata.customFormat === manifest.prototypeReaderFormat;
  // A v4 snapshot is already a Standard, read-only reader.  It can therefore
  // become the verified previous entity of the next revision, including a
  // complete declared pilot slice.  Do not make this a schema-version-only
  // escape hatch: the delivery role, lineage shape and generated entity
  // bindings below must all agree before Chromium is allowed to audit it.
  const standardV4 = manifest.schemaVersion === '4.0.0'
    && manifest.delivery?.mode === 'standard'
    && manifest.delivery?.readerRole === 'derived-readonly'
    && ['full', 'pilot-slice'].includes(manifest.requirement?.coverage)
    && ['initial', 'revision'].includes(manifest.delivery?.lineage?.mode)
    && Array.isArray(manifest.delivery?.readerComposition?.coreItems)
    && CORE_READER_ITEMS.every((itemId) => manifest.delivery.readerComposition.coreItems.includes(itemId));
  const recognizedManifest = manifest.schemaVersion === '3.0.0' || projectCustom || standardV4;
  if (!recognizedManifest || manifest.requirement?.id !== requirementId) throw new Error('E_LINEAGE_BASELINE_UNRECOGNIZED');
  if (metadata.migrationDescriptorSha256 && !migrationDescriptorPath) throw new Error('E_MIGRATION_DESCRIPTOR_REQUIRED');
  const auditDeclaration = metadata.runtimeAudit ?? {customItems: []};
  let snapshotBindings;
  try { snapshotBindings = extractFrozenReaderBindings(snapshotBytes.toString('utf8'), 'E_LINEAGE_BASELINE_ENTITY_BINDING'); }
  catch { throw new Error('E_LINEAGE_BASELINE_ENTITY_BINDING'); }
  if (metadata.buildMode !== 'readonly-snapshot' || metadata.outputSha256 !== digest(snapshotBytes)
      || metadata.manifestSha256 !== digest(manifestBytes) || metadata.prototypeSha256 !== digest(snapshotBindings.payload)
      || metadata.schemaVersion !== manifest.schemaVersion || metadata.coverage !== manifest.requirement?.coverage
      || !isDeepStrictEqual(metadata.readerComposition, manifest.delivery?.readerComposition)
      || !isDeepStrictEqual(snapshotBindings.manifest, manifest)
      || snapshotBindings.payload.length === 0 || digest(snapshotBindings.payload) !== manifest.prototype?.sourceHash
      || !Array.isArray(auditDeclaration.customItems)
      || (projectCustom && !metadata.runtimeAudit)) throw new Error('E_LINEAGE_BASELINE_AUDIT');
  if (projectCustom) {
    if (!manifest.requirement || !Array.isArray(manifest.pages) || !Array.isArray(manifest.cards)) throw new Error('E_LINEAGE_BASELINE_AUDIT');
    for (const item of auditDeclaration.customItems) {
      if (typeof item.selector !== 'string' || !item.selector.trim()
          || !Array.isArray(item.sourceRefs) || item.sourceRefs.length === 0
          || !manifest.cards.some((card) => (card.evidenceBindings ?? []).some((binding) =>
            binding.sourceRefs?.some((ref) => item.sourceRefs.some((candidate) => candidate.sourceId === ref.sourceId && candidate.locator === ref.locator && candidate.excerpt === ref.excerpt))))) {
        throw new Error('E_LINEAGE_BASELINE_AUDIT');
      }
    }
  }
  const runtimeAudit = resolve(import.meta.dirname, '../assets/developer-reader-shell/scripts/audit-lineage-baseline-runtime.mjs');
  const runtimeArgs = [runtimeAudit, '--snapshot', resolve(snapshotPath), '--manifest', resolve(manifestPath), '--custom-items-json', JSON.stringify(projectCustom ? auditDeclaration.customItems : [])];
  if (migrationDescriptorPath) runtimeArgs.push('--metadata', resolve(metadataPath), '--migration-descriptor', resolve(migrationDescriptorPath));
  const runtime = spawnSync(process.execPath, runtimeArgs, {encoding:'utf8', timeout:120000});
  if (runtime.error || runtime.status !== 0) throw new Error(`E_LINEAGE_BASELINE_RUNTIME_AUDIT ${runtime.stderr?.trim().slice(0, 500) ?? runtime.error?.message ?? 'failed'}`);
  let runtimeResult;
  try { runtimeResult = JSON.parse(runtime.stdout.match(/LINEAGE_AUDIT_JSON (.+)/)?.[1] ?? 'null'); } catch {}
  if (!runtimeResult || runtimeResult.status !== 'pass' || !Array.isArray(runtimeResult.coreItemIds)
      || CORE_READER_ITEMS.some((id) => !runtimeResult.coreItemIds.includes(id))
      || runtimeResult.coreItemIds.some((id) => !CORE_READER_ITEMS.includes(id))) throw new Error('E_LINEAGE_BASELINE_RUNTIME_CORE_INCOMPLETE');
  const receipt = {schemaVersion:'1.0.0',receiptId,requirementId,previousManifest:{pathLabel:basename(manifestPath),sha256:digest(manifestBytes)},previousSnapshot:{pathLabel:basename(snapshotPath),sha256:digest(snapshotBytes)},buildMetadata:{pathLabel:basename(metadataPath),sha256:digest(metadataBytes)},...(migrationDescriptorBytes ? {migrationDescriptor:{pathLabel:basename(migrationDescriptorPath),sha256:digest(migrationDescriptorBytes)}} : {}),runtimeAudit:{status:'pass',coreItemIds:runtimeResult.coreItemIds,customItems:auditDeclaration.customItems},createdAt};
  writeFileSync(resolve(out), `${JSON.stringify(receipt,null,2)}\n`, {flag:'wx',mode:0o600});
  process.stdout.write(`PASS lineage baseline receipt ${receiptId}\n`);
} catch (error) { process.stderr.write(`FAIL ${error.message}\n`); process.exitCode = 1; }

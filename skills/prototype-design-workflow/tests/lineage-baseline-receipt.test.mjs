import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, writeFileSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const script = new URL('../scripts/create-lineage-baseline-receipt.mjs', import.meta.url).pathname;
const core = ['change-overview','collapse-expand-all','return-previous-prototype','full-development-snapshot','change-kind-color','current-location','bidirectional-visible-connector','fact-source-details'];
function create(schemaVersion) {
  const root = mkdtempSync(join(tmpdir(), 'lineage-baseline-'));
  const paths = {manifest:join(root,'manifest.json'),snapshot:join(root,'reader.html'),metadata:join(root,'metadata.json'),out:join(root,'receipt.json')};
  const sourceRefs=[{sourceId:'prd',locator:'text:approved scope',excerpt:'approved scope'}];
  const customItems=schemaVersion==='project-custom-cumulative-v1'?[{itemId:'custom-filter',selector:'#custom-filter',sourceRefs}]:[];
  const manifest={schemaVersion,requirement:{id:'REQ-1'},pages:[{pageId:'P1'}],cards:[{cardId:'C1',evidenceBindings:[{sourceRefs}]}],evidenceSources:[{sourceId:'prd'}]};
  if(schemaVersion==='project-custom-cumulative-v1')manifest.prototypeReaderFormat='prototype-design-workflow-cumulative-v1';
  writeFileSync(paths.manifest, JSON.stringify(manifest));
  const customNode=customItems.length?'<div id="custom-filter">Custom capability</div>':'';
  const snapshot=Buffer.from(`<!doctype html><body data-build-mode="readonly-snapshot"><main class="handoff-shell" data-panel-collapsed="false"><iframe class="prototype-frame"></iframe><aside class="developer-reader"><section class="page-group"><article class="element-card" data-card-id="C1" data-expanded="false"><button data-card-trigger="C1" data-card-toggle onclick="const c=this.closest(\'.element-card\');c.dataset.expanded=String(c.dataset.expanded!==\'true\')">toggle</button></article></section><button data-return-prototype>return</button><button data-panel-toggle onclick="const s=this.closest(\'.handoff-shell\');s.dataset.panelCollapsed=String(s.dataset.panelCollapsed!==\'true\')">panel</button>${customNode}</aside><svg><path data-connector-path></path></svg></main></body>`);writeFileSync(paths.snapshot,snapshot);
  writeFileSync(paths.metadata, JSON.stringify({buildMode:'readonly-snapshot',schemaVersion,customFormat:manifest.prototypeReaderFormat,outputSha256:createHash('sha256').update(snapshot).digest('hex'),runtimeAudit:{status:'pass',coreItemIds:core,customItems}}));
  const run = () => spawnSync(process.execPath,[script,'--manifest',paths.manifest,'--snapshot',paths.snapshot,'--metadata',paths.metadata,'--out',paths.out,'--receipt-id','BASE-1','--requirement-id','REQ-1','--created-at','2026-09-29T00:00:00Z'],{encoding:'utf8',timeout:120000});
  return {root,paths,run};
}

test('v3 placeholder reader cannot mint a receipt by self-reporting all eight capabilities',()=>{const f=create('3.0.0');try{const result=f.run();assert.notEqual(result.status,0);assert.match(result.stderr,/E_LINEAGE_BASELINE_(?:ENTITY_BINDING|RUNTIME_AUDIT)/);assert.throws(()=>readFileSync(f.paths.out));}finally{rmSync(f.root,{recursive:true,force:true});}});
test('custom cumulative placeholder reader cannot mint a receipt',()=>{const f=create('project-custom-cumulative-v1');try{const result=f.run();assert.notEqual(result.status,0);assert.match(result.stderr,/E_LINEAGE_BASELINE_(?:ENTITY_BINDING|RUNTIME_AUDIT)/);assert.throws(()=>readFileSync(f.paths.out));}finally{rmSync(f.root,{recursive:true,force:true});}});
test('unknown manifest with self-reported custom audit is rejected',()=>{const f=create('team.reader.7');try{assert.match(f.run().stderr,/E_LINEAGE_BASELINE_UNRECOGNIZED/);}finally{rmSync(f.root,{recursive:true,force:true});}});
test('recognized custom item must exist in the actual Chromium-rendered Snapshot',()=>{const f=create('project-custom-cumulative-v1');try{const metadata=JSON.parse(readFileSync(f.paths.metadata));metadata.runtimeAudit.customItems[0].selector='#nonexistent-capability';writeFileSync(f.paths.metadata,JSON.stringify(metadata));assert.match(f.run().stderr,/E_LINEAGE_BASELINE_(?:ENTITY_BINDING|RUNTIME_AUDIT)/);}finally{rmSync(f.root,{recursive:true,force:true});}});
test('custom reader with failed runtime audit is rejected',()=>{const f=create('project-custom-cumulative-v1');try{const metadata=JSON.parse(readFileSync(f.paths.metadata));metadata.runtimeAudit.status='fail';writeFileSync(f.paths.metadata,JSON.stringify(metadata));assert.match(f.run().stderr,/E_LINEAGE_BASELINE_(?:ENTITY_BINDING|RUNTIME_AUDIT)/);}finally{rmSync(f.root,{recursive:true,force:true});}});

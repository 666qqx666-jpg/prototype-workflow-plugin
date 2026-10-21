import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {validateDisplayNumbers, validateManifest} from '../assets/developer-reader-shell/src/reader/contract.mjs';
import {validateScopeEvidence} from '../assets/developer-reader-shell/src/reader/scope-evidence.mjs';

const require=createRequire(new URL('../assets/developer-reader-shell/package.json',import.meta.url));
const Ajv=require('ajv/dist/2020').default;
const schema=JSON.parse(readFileSync(new URL('../assets/change-manifest.schema.json',import.meta.url),'utf8'));
const sources=[
 {sourceId:'prd',sourceType:'prd',authority:'current-product-requirement'},
 {sourceId:'runtime',sourceType:'runtime',authority:'observed-runtime'},
 {sourceId:'screenshot',sourceType:'screenshot',authority:'same-version-screenshot'}
];
const scope=()=>({productScope:{summary:'confirmed scope',included:['A'],excluded:[],sourceRefs:[{sourceId:'prd',locator:'p1',excerpt:'scope decision'}]},sampleSource:{status:'present',samples:[{sampleId:'s1',label:'sample',context:'demo',sourceIds:['runtime']}],note:'sample only'},verifiedCoverage:{verified:['A'],notVerified:['B'],sourceRefs:[{sourceId:'runtime',locator:'walk',excerpt:'observed branch'}]}});

test('v4 initial closes scope and sequence',()=>{assert.deepEqual(validateScopeEvidence({scopeEvidence:scope(),evidenceSources:sources}),[]);assert.deepEqual(validateDisplayNumbers([{displayNumber:'1'},{displayNumber:'2'}]),[]);});
test('current standard rejects v3',()=>{assert.ok(validateManifest({schemaVersion:'3.0.0'}).errors.some(({code})=>code==='E_MANIFEST_V4_REQUIRED'));});
test('unknown top-level field fails closed',()=>{const v=new Ajv({strict:false}).compile(schema);const data={schemaVersion:'4.0.0',surprise:true};assert.equal(v(data),false);});
test('duplicate JSON key fails closed',()=>{const dir=mkdtempSync(join(tmpdir(),'manifest-v4-'));try{const manifest=join(dir,'m.json'),prototype=join(dir,'p.html');writeFileSync(manifest,'{"schemaVersion":"4.0.0","schemaVersion":"3.0.0"}');writeFileSync(prototype,'');const result=spawnSync(process.execPath,[new URL('../scripts/validate-prototype-handoff.mjs',import.meta.url).pathname,'--manifest',manifest,'--prototype',prototype],{encoding:'utf8'});assert.match(result.stderr,/E_DUPLICATE_JSON_KEY/);}finally{rmSync(dir,{recursive:true,force:true});}});
for(const [label,value] of [['01','01'],['zero','0'],['decimal','1.0'],['alpha','a'],['duplicate','1'],['gap','3'],['reverse','2']]) test(`display ${label} fails`,()=>{const cards=label==='duplicate'?[{displayNumber:'1'},{displayNumber:'1'}]:label==='reverse'?[{displayNumber:'2'},{displayNumber:'1'}]:[{displayNumber:value}];assert.ok(validateDisplayNumbers(cards).length>0);});
test('sample present requires sample',()=>{const x=scope();x.sampleSource.samples=[];assert.ok(validateScopeEvidence({scopeEvidence:x,evidenceSources:sources}).some((e)=>e.includes('sampleSource')));});
test('sample not-used requires empty samples and note',()=>{const x=scope();x.sampleSource={status:'not-used',samples:[{}],note:''};assert.ok(validateScopeEvidence({scopeEvidence:x,evidenceSources:sources}).length>0);});
test('product scope requires authoritative source',()=>{const x=scope();x.productScope.sourceRefs[0].sourceId='runtime';assert.ok(validateScopeEvidence({scopeEvidence:x,evidenceSources:sources}).length>0);});
test('verified coverage rejects product-only source',()=>{const x=scope();x.verifiedCoverage.sourceRefs[0].sourceId='prd';assert.ok(validateScopeEvidence({scopeEvidence:x,evidenceSources:sources}).length>0);});
test('verified coverage records in-scope unverified branches',()=>{const x=scope();assert.deepEqual(validateScopeEvidence({scopeEvidence:x,evidenceSources:sources}),[]);assert.equal(x.verifiedCoverage.notVerified[0],'B');});

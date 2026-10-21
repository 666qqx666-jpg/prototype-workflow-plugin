#!/usr/bin/env node
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync, openSync, closeSync, unlinkSync, copyFileSync, readdirSync} from 'node:fs';
import {basename, dirname, isAbsolute, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {locateEvidenceText} from './validate-evidence-bundle.mjs';

const args=process.argv.slice(2), get=(name)=>{const i=args.indexOf(`--${name}`);return i<0?null:args[i+1]};
const sha=(b)=>createHash('sha256').update(b).digest('hex');
const inputPaths={prd:get('prd'),prototype:get('prototype'),reader:get('reader'),record:get('record'),manifest:get('manifest'),evidence:get('evidence'),'reader-metadata':get('reader-metadata'),'validation-receipt':get('validation-receipt')};
const output=resolve(get('output')??'.'); const lock=`${output}.publish-lock`; let stage=null; let lockFd=null;
function fail(code){const e=new Error(code);e.code=code;throw e;}
function validate() {
 const bytes=Object.fromEntries(Object.entries(inputPaths).map(([key,value])=>{if(!value)fail('E_CONVERGENCE_RECORD_INVALID missing input');return [key,readFileSync(resolve(value))]}));
 const record=JSON.parse(bytes.record), manifest=JSON.parse(bytes.manifest), evidence=JSON.parse(bytes.evidence), metadata=JSON.parse(bytes['reader-metadata']);
 const digest={prdSha256:sha(bytes.prd),prototypeSha256:sha(bytes.prototype),readerSha256:sha(bytes.reader)};
 const allowed=['schemaVersion','freezeId','requirementId','productVersion','inputs','prototypeSync','localValidation','blockingPendingIds','userDecision'];
 if(Object.keys(record).some((key)=>!allowed.includes(key)))fail('E_CONVERGENCE_RECORD_INVALID unknown field');
 const require=createRequire(new URL('../assets/developer-reader-shell/package.json',import.meta.url)),Ajv=require('ajv/dist/2020').default;
 const schema=JSON.parse(readFileSync(new URL('../assets/product-convergence-record.schema.json',import.meta.url),'utf8'));
 if(!new Ajv({allErrors:true,strict:false}).compile(schema)(record))fail('E_CONVERGENCE_RECORD_INVALID schema');
 const validation=JSON.parse(bytes['validation-receipt'].toString('utf8'));
 if(record.schemaVersion!=='1.0.0'||record.freezeId!==get('freeze-id')||record.productVersion!==get('product-version')||record.requirementId!==manifest.requirement?.id||record.prototypeSync!=='pass'||record.localValidation?.status!=='pass'||record.localValidation?.receiptSha256!==sha(bytes['validation-receipt'])||validation.status!=='pass'||validation.validator_mode!=='standard'||validation.manifest_contract_version!=='4.0.0'||validation.manifest_sha256!==sha(bytes.manifest)||validation.evidence_sha256!==sha(bytes.evidence)||validation.prototype_sha256!==digest.prototypeSha256||validation.reader_snapshot_sha256!==digest.readerSha256||validation.reader_metadata_sha256!==sha(bytes['reader-metadata'])||record.blockingPendingIds?.length||record.userDecision?.status!=='approved-to-freeze'||!record.userDecision.sourceRef?.excerpt?.trim())fail('E_CONVERGENCE_RECORD_INVALID E_FREEZE_VALIDATION_RECEIPT');
 if(Object.entries(digest).some(([key,value])=>record.inputs?.[key]!==value))fail('E_FREEZE_INPUT_DRIFT');
 if(metadata.manifestSha256!==sha(bytes.manifest)||metadata.prototypeSha256!==digest.prototypeSha256||metadata.outputSha256!==digest.readerSha256||metadata.schemaVersion!=='4.0.0')fail('E_FREEZE_BINDING_MISMATCH');
 if(!metadata.readerComposition||metadata.buildMode!=='readonly-snapshot')fail('E_FREEZE_BINDING_MISMATCH non-cumulative reader');
 const ref=record.userDecision.sourceRef;
 const authority=(evidence.sources??[]).find((item)=>item.sourceId===ref.sourceId);
 const declared=(manifest.evidenceSources??[]).find((item)=>item.sourceId===ref.sourceId);
 if(!authority||!declared||declared.sourceType!=='decision'||authority.sourceType!=='decision'||!authority.path||isAbsolute(authority.path)||authority.path.split(/[\\/]/).includes('..'))fail('E_CONVERGENCE_RECORD_INVALID decision source');
 const decisionPath=resolve(dirname(resolve(inputPaths.evidence)),authority.path);
 const decisionBytes=readFileSync(decisionPath);
 if(sha(decisionBytes)!==declared.sourceHash.toLowerCase()||!['text','html','source-code'].includes(declared.contentKind))fail('E_CONVERGENCE_RECORD_INVALID decision evidence hash');
 const located=locateEvidenceText(decisionBytes.toString('utf8'),ref.locator);
 if(!located.ok||!located.text.includes(ref.excerpt))fail('E_CONVERGENCE_RECORD_INVALID decision evidence reference');
 const blockers=[...(manifest.cards??[]),...(manifest.globalRules??[])].flatMap((item)=>item.pendingItems??[]).filter((item)=>item.blocksDelivery);
 if(blockers.length)fail('E_CONVERGENCE_RECORD_INVALID blocking pending in manifest');
 return {bytes,record,manifest,evidence,metadata,digest};
}
function verifyNoSensitive(text){if(/(?:\/Users\/|node_modules)/i.test(text)||text.includes('BEGIN PRIVATE'+' KEY')||text.toLowerCase().includes('obsidian'+' vault'))fail('E_FREEZE_PRIVACY');}
function writeTree(dir,bundle,record){
 const product=join(dir,'product');mkdirSync(product,{recursive:true});
 for(const [key,name] of [['prd','prd.md'],['prototype','prototype.html'],['reader','reader.html']])copyFileSync(resolve(inputPaths[key]),join(product,name));
 const files=['product/prd.md','product/prototype.html','product/reader.html'];
 const meta={schemaVersion:'1.0.0',freezeId:record.freezeId,productVersion:record.productVersion,requirementId:record.requirementId,frozenAt:get('frozen-at'),inputs:bundle.digest,manifestSha256:sha(bundle.bytes.manifest),evidenceSha256:sha(bundle.bytes.evidence),recordSha256:sha(bundle.bytes.record),validationReceiptSha256:sha(bundle.bytes['validation-receipt']),artifacts:files.map((path)=>({path,originalFileName:basename(inputPaths[path.includes('prd')?'prd':path.includes('prototype')?'prototype':'reader']),bytes:bundle.bytes[path.includes('prd')?'prd':path.includes('prototype')?'prototype':'reader'].length,sha256:sha(bundle.bytes[path.includes('prd')?'prd':path.includes('prototype')?'prototype':'reader'])})),boundary:'Read-only historical freeze; not a Standard, Independent Review, Final Gate or Stable release.'};
 const manifestText=JSON.stringify(meta,null,2)+'\n'; verifyNoSensitive(manifestText);writeFileSync(join(dir,'FREEZE-MANIFEST.json'),manifestText,{flag:'wx'});
 const checks=files.map((path)=>`${sha(readFileSync(join(dir,path)))}  ${path}`).join('\n')+`\n${sha(readFileSync(join(dir,'FREEZE-MANIFEST.json')))}  FREEZE-MANIFEST.json\n`;
 writeFileSync(join(dir,'SHA256SUMS'),checks,{flag:'wx'});writeFileSync(join(dir,'README.md'),`# Product convergence freeze\n\nFreeze ID: ${record.freezeId}\nProduct version: ${record.productVersion}\nFrozen at: ${get('frozen-at')}\n\nThis directory is a read-only historical snapshot. Active sources remain in their original locations.\n`,{flag:'wx'});
}
try {
 if(!get('frozen-at')||!get('freeze-id')||!get('product-version')||!get('validation-receipt'))fail('E_CONVERGENCE_RECORD_INVALID required explicit metadata and --validation-receipt');
 if(existsSync(output))fail('E_FREEZE_OUTPUT_EXISTS');
 const before=validate();lockFd=openSync(lock,'wx',0o600);stage=mkdtempSync(join(dirname(output),`.${basename(output)}.staging-`));writeTree(stage,before,before.record);
 const after=validate();if(Object.keys(before.bytes).some((key)=>sha(before.bytes[key])!==sha(after.bytes[key])))fail('E_FREEZE_INPUT_DRIFT');
 for(const name of readdirSync(stage,{recursive:true})){const path=join(stage,name);if(path.endsWith('.json')||path.endsWith('.md'))verifyNoSensitive(readFileSync(path,'utf8'));}
 if(existsSync(output))fail('E_FREEZE_OUTPUT_EXISTS');renameSync(stage,output);stage=null;process.stdout.write(`PASS product convergence freeze ${before.record.freezeId}\n`);
}catch(error){process.stderr.write(`FAIL ${error.code??error.message}\n`);process.exitCode=1;}
finally{if(stage)rmSync(stage,{recursive:true,force:true});if(lockFd!==null){closeSync(lockFd);try{unlinkSync(lock)}catch{}}}

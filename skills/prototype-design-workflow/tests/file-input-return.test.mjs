import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../assets/developer-reader-shell/package.json',import.meta.url));
const {chromium}=require('playwright');
const {build}=require('esbuild');

test('reader return preserves the selected file and can restore it after a temporary clear',async()=>{
  const script=await build({entryPoints:[fileURLToPath(new URL('../assets/developer-reader-shell/src/reader/surface-snapshot.mjs',import.meta.url))],bundle:true,write:false,format:'iife',globalName:'Snapshot'});
  const browser=await chromium.launch();
  try {
    const page=await browser.newPage({offline:true});
    await page.setContent('<section id="view"><input type="file" id="upload"></section>');
    await page.addScriptTag({content:script.outputFiles[0].text});
    await page.locator('#upload').setInputFiles({name:'rows.xlsx',mimeType:'application/octet-stream',buffer:Buffer.from('selected-content')});
    const result=await page.evaluate(async()=>{
      const surfaces=[{surfaceId:'view:test',kind:'view',activeSelector:'#view',rootSelector:'#view',snapshotSelectors:['#view']}];
      const saved=Snapshot.capturePrototypeSnapshot(document,surfaces);
      if(!saved.ok)throw new Error(saved.code);
      const results=[];
      for(const clear of [false,true]) {
        if(clear)document.querySelector('#upload').value='';
        const restored=Snapshot.restorePrototypeSnapshot(document,saved.snapshot);
        const file=document.querySelector('#upload').files[0];
        results.push({ok:restored.ok,name:file?.name,text:file?await file.text():null});
      }
      return results;
    });
    assert.deepEqual(result,[{ok:true,name:'rows.xlsx',text:'selected-content'},{ok:true,name:'rows.xlsx',text:'selected-content'}]);
  } finally {await browser.close();}
});

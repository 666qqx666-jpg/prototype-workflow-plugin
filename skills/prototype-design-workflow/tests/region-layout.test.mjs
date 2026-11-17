import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {assertRegionOrder} from '../scripts/assert-region-order.mjs';

const require = createRequire(new URL('../assets/developer-reader-shell/package.json', import.meta.url));
const {chromium} = require('playwright');

test('the same two working regions can have a different layout or disclosure model', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport: {width: 640, height: 480}});
    await page.setContent(`
      <main>
        <section id="draft"><label>草稿 <input></label></section>
        <section id="notes"><button>查看备注</button></section>
      </main>
    `);
    const container = page.locator('main');
    const expected = {first: '#draft', second: '#notes', order: 'above'};
    await page.addStyleTag({content: '#draft { min-height: 600px }'});
    await assertRegionOrder(container, expected);
    await page.addStyleTag({content: 'main { display: flex }'});
    await assert.rejects(assertRegionOrder(container, expected), /Expected #draft above #notes/);
    await assertRegionOrder(container, {...expected, order: 'left-of'});

    await page.setContent(`
      <main>
        <nav><button onclick="draft.hidden=false;notes.hidden=true">草稿</button>
          <button onclick="draft.hidden=true;notes.hidden=false">备注</button></nav>
        <section id="draft"><label>草稿 <input></label></section>
        <section id="notes" hidden><button>查看备注</button></section>
      </main>
    `);
    await page.getByLabel('草稿').fill('修改');
    await assert.rejects(assertRegionOrder(container, expected), /Region is hidden: #notes/);
    await page.getByRole('button', {name: '备注', exact: true}).click();
    await page.getByRole('button', {name: '查看备注'}).click();
    await assert.rejects(assertRegionOrder(container, expected), /Region is hidden: #draft/);
  } finally {
    await browser.close();
  }
});

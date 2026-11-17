import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {embedWorkbookTools} from '../scripts/embed-workbook-tools.mjs';
import {assertRegionSize} from '../scripts/assert-region-order.mjs';

const require = createRequire(new URL('../assets/developer-reader-shell/package.json', import.meta.url));
const {chromium} = require('playwright');

test('standalone page measures its control and downloads a reimportable workbook', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({offline: true});
    await page.setContent(embedWorkbookTools(`
      <!-- prototype-workbook-tools -->
      <div id="box" style="height:72px"></div>
      <button id="export">导出</button>
      <script>
        document.querySelector('#export').onclick = () =>
          PrototypeWorkbook.downloadTable('example.xlsx', [['标识','数量'],['001','1'],['002','错']]);
      </script>
    `));
    await assertRegionSize(page.locator('#box'), {height: 72});
    await assert.rejects(assertRegionSize(page.locator('#box'), {height: 96}), /measured 72px/);
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#export').click()]);
    const data = await readFile(await download.path());
    assert.equal(data.subarray(0, 2).toString(), 'PK');
    const result = await page.evaluate(async bytes => {
      const file = new File([Uint8Array.from(bytes)], 'example.xlsx');
      const rows = await PrototypeWorkbook.readTable(file);
      const validate = values => values[1] === '错' ? ['请修正数量'] : [];
      const run = table => PrototypeWorkbook.processTable(table, '原因', validate);
      const initial = run(rows);
      const repeated = run(initial.failureTable);
      const fixed = structuredClone(repeated.failureTable);
      fixed[1][1] = '2';
      // An unresolved relationship must leave that row untouched without
      // preventing the following independent row from being accepted.
      const saved = [];
      const related = PrototypeWorkbook.processTable(
        [['标识','关联'], ['003','missing'], ['004','known']], '原因', values => {
          const target = {known: {id: 'k1'}}[values[1]];
          if (!target) return ['关联不存在'];
          saved.push({id: values[0], target: target.id});
          return [];
        });
      let unexpected;
      try { PrototypeWorkbook.processTable(rows, '原因', () => { throw new Error('program-error'); }); }
      catch (error) { unexpected = error.message; }
      return {initial, repeated, corrected: run(fixed), related, saved, unexpected};
    }, [...data]);
    const failureTable = [['标识', '数量', '原因'], ['002', '错', '请修正数量']];
    assert.deepEqual(result.initial, {success: 1, failed: 1, failureTable});
    assert.deepEqual(result.repeated, {success: 0, failed: 1, failureTable});
    assert.deepEqual(result.corrected, {success: 1, failed: 0, failureTable: null});
    assert.deepEqual(result.related, {success: 1, failed: 1,
      failureTable: [['标识','关联','原因'], ['003','missing','关联不存在']]});
    assert.deepEqual(result.saved, [{id: '004', target: 'k1'}]);
    assert.equal(result.unexpected, 'program-error');
  } finally {
    await browser.close();
  }
});

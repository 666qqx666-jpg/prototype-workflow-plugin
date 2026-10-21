import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeOverviewLabels, overviewCountsMatch} from '../assets/developer-reader-shell/scripts/audit-lineage-baseline-runtime.mjs';

test('block-level metric markup normalizes rendered line breaks without changing expected counts', () => {
  const rendered = ['15\n 张元素卡', ' 4\n 个新增 ', '6\n 个现有能力变更', '0\n 个关键复用', '2\n 条全局规则'];
  const expected = ['15 张元素卡', '4 个新增', '6 个现有能力变更', '0 个关键复用', '2 条全局规则'];
  assert.deepEqual(normalizeOverviewLabels(rendered), expected);
  assert.equal(overviewCountsMatch(rendered, expected), true);
});

test('normalized metrics still reject wrong values, missing rows, duplicates, and extras', () => {
  const expected = ['15 张元素卡', '4 个新增'];
  assert.equal(overviewCountsMatch(['14\n 张元素卡', '4 个新增'], expected), false);
  assert.equal(overviewCountsMatch(['15\n 张元素卡'], expected), false);
  assert.equal(overviewCountsMatch(['15\n 张元素卡', '15\n 张元素卡'], expected), false);
  assert.equal(overviewCountsMatch(['15\n 张元素卡', '4 个新增', '0 条全局规则'], expected), false);
});

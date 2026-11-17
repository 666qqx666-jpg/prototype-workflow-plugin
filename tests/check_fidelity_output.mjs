// Evaluate actual generated artifacts; no browser or formal Standard claims.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const output = path.resolve(process.argv[2]);
const generated = await import(pathToFileURL(path.join(output, 'interaction.mjs')));
const read = async file => JSON.parse(await fs.readFile(path.join(output, file), 'utf8'));
const initial = await read('initial-cards.json');
const revised = await read('revised-cards.json');
const checks = [];
const check = (name, fn) => { try { fn(); checks.push({name, status: 'pass'}); } catch (error) { checks.push({name, status: 'fail', error: error.message}); } };
const ids = rows => rows.map(row => row.id);

check('原列表输入后需显式查询，重置恢复全部', () => {
  const m = generated.createModel(); m.setSearch('甲');
  assert.deepEqual(ids(m.list()), ['m1', 'm2']); m.query();
  assert.deepEqual(ids(m.list()), ['m1']); m.resetSearch();
  assert.deepEqual(ids(m.list()), ['m1', 'm2']);
});
check('成员组双侧搜索与已选顺序', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.openGroups();
  assert.deepEqual(ids(m.availableGroups('协')), ['g3']);
  assert.deepEqual(ids(m.availableGroups('运营')), []);
  assert.deepEqual(ids(m.selectedGroups()), ['g2', 'g1']);
  assert.deepEqual(ids(m.selectedGroups('运营')), ['g1']);
});
check('组弹窗取消不回写详情草稿', () => {
  const m = generated.createModel(); m.openDetail('m1'); const before = m.detail();
  m.openGroups(); m.removeGroup('g2'); m.addGroup('g3'); m.cancelGroups();
  assert.deepEqual(m.detail(), before);
});
check('组确认只回写详情，详情保存才持久化顺序', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.openGroups();
  m.addGroup('g3'); m.moveGroup(2, 0); m.confirmGroups();
  assert.deepEqual(m.detail().groups, ['g3', 'g2', 'g1']);
  assert.deepEqual(m.list()[0].groups, ['g2', 'g1']); m.saveDetail();
  m.openDetail('m1'); assert.deepEqual(m.detail().groups, ['g3', 'g2', 'g1']);
});
check('组重复添加不复制，非法排序保留状态', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.openGroups();
  m.addGroup('g1'); const before = m.selectedGroups();
  assert.throws(() => m.moveGroup(-1, 0)); assert.deepEqual(m.selectedGroups(), before);
  assert.throws(() => m.addGroup('missing')); assert.deepEqual(m.selectedGroups(), before);
});
check('详情重置还原已保存资料并清除未确认组草稿', () => {
  const m = generated.createModel(); m.openDetail('m1'); const before = m.detail();
  m.setName('临时名'); m.setRegion('北区'); m.openGroups(); m.removeGroup('g2');
  m.resetDetail(); assert.deepEqual(m.detail(), before);
  assert.throws(() => m.selectedGroups());
});
check('详情取消不改变列表', () => {
  const m = generated.createModel(); const before = m.list(); m.openDetail('m1');
  m.setName('未保存'); m.setRegion('北区'); m.cancelDetail();
  assert.deepEqual(m.list(), before); assert.equal(m.detail(), null);
});
check('原姓名必填失败保留草稿与已保存值', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.setName(' ');
  const before = m.detail(); assert.throws(() => m.saveDetail());
  assert.deepEqual(m.detail(), before); assert.equal(m.list()[0].name, '甲');
});
check('新增类型单选写回、回填、使用账号数', () => {
  assert.deepEqual(new Set(generated.MEMBER_TYPES), new Set(['个人', '团队', '合作方']));
  const m = generated.createModel(); m.openDetail('m1');
  m.setDetailType('团队'); m.setDetailType('个人'); m.saveDetail();
  assert.deepEqual(m.typeUsage(), {'个人': 1, '团队': 0, '合作方': 0});
  m.openDetail('m1'); m.saveDetail();
  m.setTypeFilter('个人'); m.query(); assert.deepEqual(ids(m.list()), ['m1']);
  m.setTypeFilter('团队'); m.query(); assert.deepEqual(ids(m.list()), []);
});
check('新增筛选沿用显式查询及重置', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.setDetailType('个人'); m.saveDetail();
  m.setTypeFilter('个人'); assert.deepEqual(ids(m.list()), ['m1', 'm2']);
  m.query(); assert.deepEqual(ids(m.list()), ['m1']);
  m.resetSearch(); assert.deepEqual(ids(m.list()), ['m1', 'm2']);
});
check('新类型字段继承详情重置和取消边界', () => {
  const m = generated.createModel(); m.openDetail('m1'); m.setDetailType('个人'); m.saveDetail();
  m.openDetail('m1'); m.setDetailType('团队'); m.resetDetail(); m.saveDetail();
  assert.deepEqual(m.typeUsage(), {'个人': 1, '团队': 0, '合作方': 0});
  m.openDetail('m1'); m.setDetailType('合作方'); m.cancelDetail();
  assert.deepEqual(m.typeUsage(), {'个人': 1, '团队': 0, '合作方': 0});
});
check('初稿沿革新增集合完整且卡身份唯一', () => {
  const ids = initial.cards.map(c => c.cardId);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(initial.lineage.mode, 'initial');
  assert.deepEqual([...initial.lineage.addedCardIds].sort(), ids.sort());
});
check('修订沿革互斥闭合，删除规则指向真实承接卡', () => {
  const oldIds = ['C-filter', 'C-detail', 'C-save', 'C-region', 'C-types'];
  const l = revised.lineage; assert.equal(l.mode, 'revision');
  const old = [...l.retainedCardIds, ...l.updatedCardIds, ...l.removedCardIds];
  assert.deepEqual([...old].sort(), oldIds.sort());
  const all = [...old, ...l.addedCardIds]; assert.equal(new Set(all).size, all.length);
  const newIds = revised.cards.map(c => c.cardId);
  assert.equal(new Set(newIds).size, newIds.length);
  assert.deepEqual([...newIds].sort(), [...l.retainedCardIds, ...l.updatedCardIds, ...l.addedCardIds].sort());
  for (const id of l.removedCardIds) {
    const targets = revised.removedRuleDestinations[id]; assert.ok(targets?.length);
    for (const target of targets) assert.ok(newIds.includes(target));
  }
});
console.log(JSON.stringify({kind: 'generated-model-and-card-structure', checks,
  status: checks.every(c => c.status === 'pass') ? 'pass' : 'fail',
  notCovered: ['卡片语义须人工对照原始材料', '实际页面视觉和 DOM 交互', 'Reader 运行与正式 Standard', '真实插件首次生成全链']}, null, 2));
process.exitCode = checks.every(c => c.status === 'pass') ? 0 : 1;

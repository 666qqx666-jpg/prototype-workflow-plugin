import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {renderReader} from '../assets/developer-reader-shell/src/reader/render.mjs';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

test('skill routes developer handoff to one reference', () => {
  const skill = read('SKILL.md');
  assert.match(skill, /references\/developer-readable-prototype\.md/);
  assert.match(skill, /开发可读性交付/);
});

test('reference defines the confirmed information model', () => {
  const reference = read('references/developer-readable-prototype.md');
  assert.match(reference, /关键字段/);
  assert.match(reference, /业务规则/);
  assert.match(reference, /交互说明/);
  assert.match(reference, /异常反馈/);
  assert.match(reference, /最上层活动容器/);
  assert.match(reference, /接口映射默认不展示/);
  assert.match(reference, /5\/20 分钟.*不作为门控/s);
  assert.match(reference, /schemaVersion=4\.0\.0/);
  assert.match(reference, /v2\/v3 只保留历史读取/);
  assert.match(reference, /自动语义门.*不能证明/s);
  assert.match(reference, /globalRules/);
  assert.match(reference, /右卡→原型、原型→右卡、可见连线、同一活页面两轮返回状态恢复/);
});

test('candidate contains reusable contract and shell', () => {
  const schema = read('assets/change-manifest.schema.json');
  const template = read('assets/change-manifest-template.json');
  assert.match(schema, /navigationOnly/);
  assert.match(schema, /evidenceBindings/);
  assert.match(schema, /baselineEvidence/);
  assert.match(template, /outOfSlice/);
  assert.match(template, /globalRules/);
  assert.doesNotThrow(() => read('assets/evidence-bundle.schema.json'));
  assert.doesNotThrow(() => read('assets/evidence-bundle-template.json'));
  assert.doesNotThrow(() => read('assets/independent-review.schema.json'));
  assert.doesNotThrow(() => read('assets/independent-review-template.json'));
  assert.doesNotThrow(() => read('assets/independent-review-v1.schema.json'));
  assert.doesNotThrow(() => read('assets/lineage-baseline-receipt.schema.json'));
  assert.doesNotThrow(() => read('assets/product-convergence-record.schema.json'));
  assert.doesNotThrow(() => read('scripts/validate-independent-review.mjs'));
  assert.doesNotThrow(() => read('scripts/create-lineage-baseline-receipt.mjs'));
  assert.doesNotThrow(() => read('assets/developer-reader-shell/src/reader/surface-state.mjs'));
  assert.doesNotThrow(() => read('assets/developer-reader-shell/src/reader/surface-snapshot.mjs'));
  assert.doesNotThrow(() => read('assets/developer-reader-shell/scripts/audit-reader-runtime.mjs'));
  assert.doesNotThrow(() => read('scripts/validate-prototype-handoff.mjs'));
});

test('global rules render without pretending to be locatable cards', () => {
  const manifest = {
    requirement: {title: '示例', coverage: 'pilot-slice'},
    pages: [],
    cards: [],
    evidenceSources: [{sourceId: 'prd', label: '当前 PRD'}],
    globalRules: [{
      ruleId: 'G-1',
      title: '统一权限',
      summary: '只读用户不可提交。',
      rules: ['仍可查看已有结果。'],
      evidenceBindings: [{claimPath: '/globalRules/0/summary', sourceRefs: [{sourceId: 'prd', locator: 'heading:权限'}]}]
    }]
  };
  const html = renderReader(manifest, {cards: 0, add: 0, changed: 0, criticalReuse: 0, globalRules: 1}, {
    focusCardId: null,
    expandedCardIds: new Set(),
    panelCollapsed: false,
    returnPoint: null,
    navigationError: null
  });
  assert.match(html, /全局规则/);
  assert.match(html, /不绑定页面编号、锚点或连线/);
  assert.doesNotMatch(html, /data-card-id="G-1"/);
});

import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from 'playwright';
import {auditReaderRuntime} from './audit-reader-runtime.mjs';
import {validateFrozenReviewMigration} from '../src/reader/frozen-review-migration.mjs';
import {CORE_READER_ITEMS} from '../src/reader/lineage.mjs';

export function normalizeOverviewLabels(labels) {
  return labels.map((label) => String(label).replace(/\s+/g, ' ').trim());
}

export function overviewCountsMatch(labels, expected) {
  const normalized = normalizeOverviewLabels(labels);
  return normalized.length === expected.length && expected.every((label) => normalized.includes(label));
}

export function isCompleteDeclaredV4Snapshot(manifest) {
  return manifest?.schemaVersion === '4.0.0'
    && manifest.delivery?.mode === 'standard'
    && manifest.delivery?.readerRole === 'derived-readonly'
    && ['full', 'pilot-slice'].includes(manifest.requirement?.coverage);
}

export async function auditLineageBaselineRuntime({snapshotPath, manifestPath, metadataPath = null, migrationDescriptorPath = null, customItems = [], browserType = chromium}) {
  const manifest = JSON.parse(readFileSync(resolve(manifestPath), 'utf8'));
  const migration = migrationDescriptorPath
    ? validateFrozenReviewMigration({descriptorPath: migrationDescriptorPath, manifestPath, snapshotPath, metadataPath})
    : null;
  const browser = await browserType.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(2500);
  const checked = new Set();
  try {
    await page.goto(pathToFileURL(resolve(snapshotPath)).href, {waitUntil: 'load'});
    const shell = page.locator('.handoff-shell');
    const reader = page.locator('.developer-reader');
    const readerContent = page.locator('[data-reader-content]');
    const frame = page.locator('.prototype-frame');
    await reader.waitFor({state: 'visible'});
    await frame.waitFor({state: 'visible'});
    await page.frameLocator('.prototype-frame').locator('body').waitFor({state: 'visible'});

    // Change overview: verify the rendered counts describe the actual card and page set.
    const cards = page.locator('.element-card');
    const pageGroups = page.locator('.page-group');
    if (await cards.count() !== manifest.cards.length || await pageGroups.count() !== manifest.pages.length) {
      throw new Error('E_LINEAGE_BASELINE_RUNTIME_OVERVIEW');
    }
    const overviewLabels = await page.locator('.overview-counts > span').evaluateAll((nodes) =>
      nodes.map((node) => node.textContent)
    );
    const addCount = manifest.cards.filter(({changeKinds = []}) => changeKinds.includes('add')).length;
    const changedCount = manifest.cards.filter(({changeKinds = []}) => changeKinds.some((kind) => ['modify', 'delete'].includes(kind))).length;
    const reuseCount = manifest.cards.filter(({changeKinds = []}) => changeKinds.includes('critical-reuse')).length;
    const expectedOverview = [`${manifest.cards.length} 张元素卡`, `${addCount} 个新增`, `${changedCount} 个现有能力变更`, `${reuseCount} 个关键复用`, `${(manifest.globalRules ?? []).length} 条全局规则`];
    if (!overviewCountsMatch(overviewLabels, expectedOverview)
        || !await page.locator('.reader-header h1').isVisible()
        || (await page.locator('.reader-header h1').innerText()).trim() !== manifest.requirement.title) throw new Error('E_LINEAGE_BASELINE_RUNTIME_OVERVIEW');
    checked.add('change-overview');

    // A Standard v4 pilot slice is complete for its declared page/card scope.
    // Keep the ordered entities and all runtime checks below mandatory; this
    // does not relabel a slice as product-wide full coverage.
    const completeDeclaredSnapshot = isCompleteDeclaredV4Snapshot(manifest);
    if ((manifest.requirement?.coverage !== 'full' && !migration && !completeDeclaredSnapshot)
        || await page.locator('[data-reader-content]').count() !== 1) {
      throw new Error('E_LINEAGE_BASELINE_RUNTIME_FULL_SNAPSHOT');
    }
    const expectedPages = [...manifest.pages].sort((a, b) => a.order - b.order).map(({pageId}) => pageId);
    const renderedPages = await page.locator('.page-group').evaluateAll((nodes) => nodes.map((node) => node.dataset.pageId));
    if (JSON.stringify(renderedPages) !== JSON.stringify(expectedPages)) throw new Error('E_LINEAGE_BASELINE_RUNTIME_FULL_SNAPSHOT_PAGES');
    for (const card of manifest.cards) {
      const node = page.locator(`[data-card-id="${cssEscape(card.cardId)}"]`);
      if (await node.count() !== 1 || !(await node.locator('[data-card-trigger]').isVisible())
          || (await node.locator('.card-number').innerText()).trim() !== card.displayNumber
          || (await node.locator('.card-summary-main strong').innerText()).trim() !== card.title
          || (await node.locator('.card-result').innerText()).trim() !== card.summary
          || await node.locator('xpath=ancestor::section[contains(@class, "page-group")]').getAttribute('data-page-id') !== card.pageId) {
        throw new Error(`E_LINEAGE_BASELINE_RUNTIME_FULL_SNAPSHOT ${card.cardId}`);
      }
    }
    checked.add('full-development-snapshot');

    // Change-kind colors: require the documented semantic classes and distinct computed colors.
    const colors = await page.evaluate(() => {
      const host = document.querySelector('[data-reader-content]');
      const probeNodes = ['add', 'modify', 'delete'].map((kind) => {
        const probe = document.createElement('span');
        probe.className = `kind-tag kind-tag--${kind}`;
        probe.textContent = kind;
        host.append(probe);
        return {kind, color: getComputedStyle(probe).color, background: getComputedStyle(probe).backgroundColor, node: probe};
      });
      const colors = probeNodes.map(({kind, color, background}) => ({kind, color, background}));
      probeNodes.forEach(({node}) => node.remove());
      return colors;
    });
    if (colors.some(({color, background}) => !color || !background || color === 'rgb(0, 0, 0)' || background === 'rgba(0, 0, 0, 0)')
        || new Set(colors.map(({color, background}) => `${color}|${background}`)).size !== 3) {
      throw new Error('E_LINEAGE_BASELINE_RUNTIME_KIND_COLOR');
    }
    checked.add('change-kind-color');

    // Whole reader collapse/expand must change the actual panel state and visibility, then restore it.
    const panelToggle = page.locator('[data-panel-toggle]');
    if (await panelToggle.count() !== 1) throw new Error('E_LINEAGE_BASELINE_RUNTIME_PANEL');
    if (!await panelToggle.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_PANEL_CONTROL_HIDDEN');
    await panelToggle.click();
    if (await shell.getAttribute('data-panel-collapsed') !== 'true' || await readerContent.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_PANEL');
    if (!await panelToggle.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_PANEL_CONTROL_HIDDEN');
    await panelToggle.click();
    if (await shell.getAttribute('data-panel-collapsed') !== 'false' || !await readerContent.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_PANEL');
    checked.add('collapse-expand-all');

    // Per-card expand/collapse is behavioral and must leave each card in its original state.
    for (const card of manifest.cards) {
      const node = page.locator(`[data-card-id="${cssEscape(card.cardId)}"]`);
      const toggle = node.locator('[data-card-toggle]');
      const initial = await node.getAttribute('data-expanded');
      await toggle.click();
      const expanded = await node.getAttribute('data-expanded');
      const bodyVisible = await node.locator('[data-card-body]').isVisible();
      if (expanded === initial || !bodyVisible) throw new Error(`E_LINEAGE_BASELINE_RUNTIME_CARD ${card.cardId}`);
      await toggle.click();
      if (await node.getAttribute('data-expanded') !== initial) throw new Error(`E_LINEAGE_BASELINE_RUNTIME_CARD ${card.cardId}`);
    }

    // Fact details toggle independently for each source block; toggling one must not open another.
    const sourceToggles = page.locator('[data-source-toggle]');
    const sourceCount = await sourceToggles.count();
    if (sourceCount < 1) throw new Error('E_LINEAGE_BASELINE_RUNTIME_FACT_SOURCE');
    for (let i = 0; i < sourceCount; i++) {
      const toggle = sourceToggles.nth(i);
      const section = toggle.locator('xpath=..');
      const card = toggle.locator('xpath=ancestor::article[contains(@class, "element-card")]');
      const hasCard = await card.count() === 1;
      const cardWasExpanded = !hasCard || await card.getAttribute('data-expanded') === 'true';
      if (hasCard && !cardWasExpanded) await card.locator('[data-card-toggle]').click();
      const list = section.locator('ul');
      if (await list.count() !== 1 || await list.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_FACT_SOURCE_INITIAL');
      await toggle.click();
      if (await toggle.getAttribute('aria-expanded') !== 'true' || !await list.isVisible() || !(await list.innerText()).trim()) {
        throw new Error('E_LINEAGE_BASELINE_RUNTIME_FACT_SOURCE_OPEN');
      }
      const otherOpen = await page.locator('[data-source-toggle][aria-expanded="true"]').count();
      if (otherOpen !== 1) throw new Error('E_LINEAGE_BASELINE_RUNTIME_FACT_SOURCE_INDEPENDENCE');
      await toggle.click();
      if (await toggle.getAttribute('aria-expanded') !== 'false' || await list.isVisible()) throw new Error('E_LINEAGE_BASELINE_RUNTIME_FACT_SOURCE_CLOSE');
      if (hasCard && !cardWasExpanded) await card.locator('[data-card-toggle]').click();
    }
    checked.add('fact-source-details');

    for (const item of customItems) {
      const selector = page.locator(item.selector);
      if (await selector.count() === 0 || !(await selector.first().isVisible())) throw new Error(`E_LINEAGE_BASELINE_RUNTIME_CUSTOM ${item.itemId}`);
    }

    // This shared runtime audit verifies both navigation directions, visible geometry,
    // current focus, and restoration of the prior prototype state for every card.
    await browser.close();
    const navigation = await auditReaderRuntime({snapshotPath, manifest});
    if (navigation.length !== manifest.cards.length || navigation.some((item) => !item.bidirectional || !item.connector || !item.stateRestore)) {
      throw new Error('E_LINEAGE_BASELINE_RUNTIME_NAVIGATION');
    }
    checked.add('return-previous-prototype');
    checked.add('current-location');
    checked.add('bidirectional-visible-connector');
    const coreItemIds = CORE_READER_ITEMS.filter((itemId) => checked.has(itemId));
    return {status: coreItemIds.length === CORE_READER_ITEMS.length ? 'pass' : 'fail', coreItemIds, checkedCardCount: manifest.cards.length, checkedCustomItemCount: customItems.length};
  } finally {
    if (browser.isConnected()) await browser.close();
  }
}

function cssEscape(value) { return String(value).replace(/["\\]/g, '\\$&'); }

async function main() {
  const args = process.argv.slice(2), get = (key) => { const index = args.indexOf(key); return index < 0 ? null : args[index + 1]; };
  const customItemsJson = get('--custom-items-json');
  const migrationDescriptorPath = get('--migration-descriptor');
  const metadataPath = get('--metadata');
  if (migrationDescriptorPath && !metadataPath) throw new Error('E_MIGRATION_METADATA_REQUIRED');
  const result = await auditLineageBaselineRuntime({snapshotPath: get('--snapshot'), manifestPath: get('--manifest'), metadataPath, migrationDescriptorPath, customItems: customItemsJson ? JSON.parse(customItemsJson) : []});
  if (result.status !== 'pass') throw new Error(`E_LINEAGE_BASELINE_RUNTIME_CORE_MISSING ${CORE_READER_ITEMS.filter((id) => !result.coreItemIds.includes(id)).join(',')}`);
  process.stdout.write(`PASS lineage baseline runtime cards=${result.checkedCardCount} custom=${result.checkedCustomItemCount} core=${result.coreItemIds.length}\nLINEAGE_AUDIT_JSON ${JSON.stringify(result)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => { process.stderr.write(`FAIL ${error.message}\n`); process.exitCode = 1; });
}

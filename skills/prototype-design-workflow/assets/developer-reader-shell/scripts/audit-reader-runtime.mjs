import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from 'playwright';

export async function auditReaderRuntime({snapshotPath, manifest, browserType = chromium}) {
  const browser = await browserType.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(5000);
  page.setDefaultNavigationTimeout(5000);
  const results = [];
  try {
    for (const card of manifest.cards) {
      await openSnapshot(page, snapshotPath);
      const frame = page.frameLocator('.prototype-frame');
      const before = await prototypeSignature(frame, manifest.surfaces);
      const readerBefore = await readerAnchorGeometry(page, card.cardId);

      await page.locator(`[data-card-trigger="${cssEscape(card.cardId)}"]`).click();
      await assertReaderAnchorSettlesInstantly(page, card.cardId, readerBefore);
      await expectFocused(page, frame, card);
      await assertConnector(page, card.cardId, 'reader-to-prototype');
      await page.locator('[data-return-prototype]').click();
      await page.waitForFunction(() => !document.querySelector('.element-card[data-focused="true"]'));
      const after = await prototypeSignature(frame, manifest.surfaces);
      if (after !== before) throw new Error(`E_RUNTIME_STATE_RESTORE ${card.cardId} drift=${signatureDrift(before, after)}`);

      // Repeat on the same live document. A reload here would hide leaked JS
      // state from the first transition and create a false PASS.
      await page.locator(`[data-card-trigger="${cssEscape(card.cardId)}"]`).click();
      await expectFocused(page, frame, card);
      await assertConnector(page, card.cardId, 'reader-to-prototype-repeat');
      await page.locator('[data-return-prototype]').click();
      await page.waitForFunction(() => !document.querySelector('.element-card[data-focused="true"]'));
      const afterRepeat = await prototypeSignature(frame, manifest.surfaces);
      if (afterRepeat !== before) throw new Error(`E_RUNTIME_STATE_RESTORE_REPEAT ${card.cardId} drift=${signatureDrift(before, afterRepeat)}`);

      await openSnapshot(page, snapshotPath);
      for (const action of card.anchor.enterActions ?? []) {
        const locator = frame.locator(action.selector).first();
        if (action.type === 'click') await locator.click();
        if (action.type === 'assert-visible' && !(await locator.isVisible())) {
          throw new Error(`E_RUNTIME_ENTER_ACTION ${card.cardId} ${action.selector}`);
        }
        if (action.type === 'assert-hidden' && await locator.count() > 0 && await locator.isVisible()) {
          throw new Error(`E_RUNTIME_ENTER_ACTION ${card.cardId} ${action.selector}`);
        }
      }
      const target = frame.locator(card.anchor.selector);
      const badge = frame.locator(`.dev-reader-badge[data-card-id="${cssEscape(card.cardId)}"]`);
      await target.waitFor({state: 'visible'});
      await target.scrollIntoViewIfNeeded();
      await page.waitForFunction(
        ({selector, cardId}) => document.querySelector('.prototype-frame')?.contentDocument
          ?.querySelector(selector)?.getAttribute('data-dev-reader-card-id') === cardId,
        {selector: card.anchor.selector, cardId: card.cardId}
      );
      await badge.waitFor({state: 'visible'});
      const readerBeforeBadge = await readerAnchorGeometry(page, card.cardId);
      await badge.click();
      // Badge handling intentionally defers focus to the next animation frame;
      // sample immediately after that dispatch boundary, before any smooth
      // scrolling could advance through an intermediate position.
      await page.evaluate(() => new Promise(requestAnimationFrame));
      await assertReaderAnchorSettlesInstantly(page, card.cardId, readerBeforeBadge, 'prototype-to-reader');
      await expectFocused(page, frame, card);
      await assertConnector(page, card.cardId, 'prototype-to-reader');
      results.push({cardId: card.cardId, bidirectional: true, connector: true, stateRestore: true});
    }
    return results;
  } finally {
    await page.close();
    await browser.close();
  }
}

async function readerAnchorGeometry(page, cardId) {
  return page.evaluate((expectedCardId) => {
    const reader = document.querySelector('.developer-reader');
    const card = document.querySelector(`[data-card-id="${CSS.escape(expectedCardId)}"]`);
    const anchor = card?.querySelector('.card-summary-main') ?? card;
    if (!reader || !anchor) return null;
    const rect = anchor.getBoundingClientRect();
    return {scrollTop: reader.scrollTop, visible: rect.bottom > 0 && rect.top < innerHeight};
  }, cardId);
}

async function assertReaderAnchorSettlesInstantly(page, cardId, before, direction = 'reader-to-prototype') {
  if (!before || before.visible) return;
  const immediate = await readerAnchorGeometry(page, cardId);
  if (!immediate?.visible) throw new Error(`E_RUNTIME_READER_ANCHOR_NOT_IMMEDIATE ${cardId} ${direction}`);
  await page.waitForTimeout(75);
  const settled = await readerAnchorGeometry(page, cardId);
  if (!settled?.visible || settled.scrollTop !== immediate.scrollTop) {
    throw new Error(`E_RUNTIME_READER_SCROLL_INTERMEDIATE ${cardId} ${direction}`);
  }
}

async function openSnapshot(page, snapshotPath) {
  await page.goto(pathToFileURL(resolve(snapshotPath)).href, {waitUntil: 'load'});
  await page.locator('.prototype-frame').waitFor({state: 'visible'});
  await page.frameLocator('.prototype-frame').locator('body').waitFor({state: 'visible'});
  await page.waitForFunction(() => document.querySelector('.prototype-frame')?.contentDocument
    ?.querySelector('#dev-reader-annotations'));
}

async function expectFocused(page, frame, card) {
  await page.locator(`[data-card-id="${cssEscape(card.cardId)}"][data-focused="true"]`).waitFor({state: 'visible'});
  await page.locator(`[data-card-id="${cssEscape(card.cardId)}"] [data-focus-label]`).waitFor({state: 'visible'});
  await frame.locator(`${card.anchor.selector}.dev-reader-target--focused`).waitFor({state: 'visible'});
}

async function assertConnector(page, cardId, direction) {
  await page.waitForFunction(() => {
    const path = document.querySelector('[data-connector-path]');
    return path?.dataset.visible === 'true' && Boolean(path.getAttribute('d')) && path.style.visibility !== 'hidden';
  });
  const pathData = await page.locator('[data-connector-path]').getAttribute('d');
  if (!pathData?.startsWith('M ')) throw new Error(`E_RUNTIME_CONNECTOR ${cardId} ${direction}`);
  const visibleGeometry = await page.evaluate((expectedCardId) => {
    const frame = document.querySelector('.prototype-frame');
    const card = document.querySelector(`[data-card-id="${CSS.escape(expectedCardId)}"][data-focused="true"]`);
    const readerAnchor = card?.querySelector('.card-summary-main') ?? card;
    const target = frame?.contentDocument?.querySelector('.dev-reader-target--focused');
    const path = document.querySelector('[data-connector-path]');
    if (!frame || !readerAnchor || !target || !path) return false;
    const frameRect = frame.getBoundingClientRect();
    const innerRect = target.getBoundingClientRect();
    const anchorRect = readerAnchor.getBoundingClientRect();
    const targetRect = {
      left: frameRect.left + innerRect.left,
      right: frameRect.left + innerRect.right,
      top: frameRect.top + innerRect.top,
      bottom: frameRect.top + innerRect.bottom
    };
    const intersects = (rect) => rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
    const numbers = (path.getAttribute('d')?.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
    const endpointsFinite = numbers.length >= 4 && numbers.every(Number.isFinite);
    const start = {x: numbers[0], y: numbers[1]};
    const end = {x: numbers.at(-2), y: numbers.at(-1)};
    return intersects(targetRect) && intersects(anchorRect) && endpointsFinite &&
      start.x >= frameRect.left && start.x <= frameRect.right && start.y >= frameRect.top && start.y <= frameRect.bottom &&
      end.x >= 0 && end.x <= innerWidth && end.y >= 0 && end.y <= innerHeight;
  }, cardId);
  if (!visibleGeometry) throw new Error(`E_RUNTIME_CONNECTOR_NOT_VISIBLE ${cardId} ${direction}`);
}

async function prototypeSignature(frame, surfaces) {
  return frame.locator('body').evaluate((body, registered) => {
    const normalizeClasses = (value) => String(value ?? '').split(/\s+/)
      .filter((name) => name && !name.startsWith('dev-reader-')).sort().join(' ');
    const controls = (root) => [...root.querySelectorAll('input, select, textarea')]
      .map((control) => ({value: control.value, checked: Boolean(control.checked), selectedIndex: control.selectedIndex}));
    const nodes = [...new Set(registered.flatMap(({snapshotSelectors = []}) => snapshotSelectors))]
      .map((selector) => {
        const node = body.ownerDocument.querySelector(selector);
        if (!node) return {selector, missing: true};
        const clone = node.cloneNode(true);
        clone.querySelector('#dev-reader-annotations')?.remove();
        clone.querySelectorAll('*').forEach((element) => {
          element.removeAttribute('data-dev-reader-card-id');
          const className = normalizeClasses(element.className);
          if (className) element.className = className;
          else element.removeAttribute('class');
        });
        return {
          selector,
          className: normalizeClasses(node.className),
          text: node.textContent,
          markup: clone.innerHTML,
          controls: controls(node),
          scrollLeft: node.scrollLeft,
          scrollTop: node.scrollTop
        };
      });
    return JSON.stringify({
      active: registered.filter(({activeSelector}) => body.ownerDocument.querySelector(activeSelector)).map(({surfaceId}) => surfaceId),
      nodes,
      hash: body.ownerDocument.defaultView.location.hash,
      scrollX: body.ownerDocument.defaultView.scrollX,
      scrollY: body.ownerDocument.defaultView.scrollY
    });
  }, surfaces);
}

function cssEscape(value) {
  return String(value).replace(/["\\]/g, '\\$&');
}

function signatureDrift(before, after) {
  const left = JSON.parse(before);
  const right = JSON.parse(after);
  const paths = [];
  const visit = (a, b, path) => {
    if (Object.is(a, b)) return;
    if (typeof a !== 'object' || a === null || typeof b !== 'object' || b === null) {
      paths.push(path || '$');
      return;
    }
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      visit(a[key], b[key], path ? `${path}.${key}` : key);
      if (paths.length >= 8) return;
    }
  };
  visit(left, right, '');
  return paths.join(',') || 'unknown';
}

async function main() {
  const args = process.argv.slice(2);
  const snapshot = valueAfter(args, '--snapshot');
  const manifestPath = valueAfter(args, '--manifest');
  if (!snapshot || !manifestPath) throw new Error('Usage: node scripts/audit-reader-runtime.mjs --snapshot <snapshot.html> --manifest <manifest.json>');
  const manifest = JSON.parse(readFileSync(resolve(manifestPath), 'utf8'));
  const results = await auditReaderRuntime({snapshotPath: snapshot, manifest});
  process.stdout.write(`PASS runtime ${results.length} cards: bidirectional, connector, state restore\n`);
}

function valueAfter(args, flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : null;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`FAIL ${error.message}\n`);
    process.exit(1);
  });
}

import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {validateManifest} from '../src/reader/contract.mjs';
import {resolveActiveSurfacePath} from '../src/reader/surface-state.mjs';

export async function auditAnchors({
  prototypeHtml,
  manifest,
  browserType = chromium,
  validateContract = true,
  lineageContext = {}
}) {
  if (validateContract) {
    // A revision has already had its predecessor bytes and baseline receipt
    // verified by the caller.  Preserve that exact context when this
    // downstream audit re-runs the v4 contract; dropping it would turn a
    // valid revision into an artificial E_LINEAGE_INPUT_REQUIRED failure.
    const validation = validateManifest(manifest, lineageContext);
    if (!validation.ok) {
      throw new Error(`E_MANIFEST_INVALID ${validation.errors.map(({code, path}) => `${code}:${path}`).join(',')}`);
    }
  }

  const browser = await browserType.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const results = [];
  try {
    await page.setContent(prototypeHtml, {waitUntil: 'domcontentloaded'});
    await auditSurfaceSelectors(page, manifest.surfaces);

    for (const card of manifest.cards) {
      // A fresh document also resets top-level lexical bindings from the
      // single-file prototype before the next card's inline script executes.
      await page.goto('about:blank');
      await page.setContent(prototypeHtml, {waitUntil: 'domcontentloaded'});
      for (const action of card.anchor.enterActions) {
        const locator = page.locator(action.selector);
        const actionCount = await locator.count();
        if (action.type === 'assert-hidden') {
          if (actionCount > 0 && await locator.first().isVisible()) {
            throw new Error(`E_STATE_ACTION_NOT_HIDDEN ${card.cardId} ${action.selector}`);
          }
          continue;
        }
        if (actionCount === 0) {
          throw new Error(`E_STATE_ACTION_MISSING ${card.cardId} ${action.selector}`);
        }
        if (action.type === 'click') await locator.first().click();
        if (action.type === 'assert-visible' && !(await locator.first().isVisible())) {
          throw new Error(`E_STATE_ACTION_NOT_VISIBLE ${card.cardId} ${action.selector}`);
        }
      }

      const activeSurfaceIds = [];
      for (const surface of manifest.surfaces) {
        const count = await page.locator(surface.activeSelector).count();
        if (count > 1) {
          throw new Error(`E_SURFACE_AMBIGUOUS ${surface.surfaceId} count=${count}`);
        }
        if (count === 1) activeSurfaceIds.push(surface.surfaceId);
      }
      const activeSet = new Set(activeSurfaceIds);
      const activePath = resolveActiveSurfacePath({
        querySelectorAll(selector) {
          const surface = manifest.surfaces.find(({activeSelector}) => activeSelector === selector);
          return surface && activeSet.has(surface.surfaceId) ? [{}] : [];
        }
      }, manifest.surfaces);
      if (!activePath.ok) throw new Error(`${activePath.code} ${card.cardId}`);
      if (activePath.top.surfaceId !== card.surfaceId) {
        throw new Error(
          `E_SURFACE_TARGET_INACTIVE ${card.cardId} expected=${card.surfaceId} actual=${activePath.top.surfaceId}`
        );
      }

      const surface = manifest.surfaces.find(({surfaceId}) => surfaceId === card.surfaceId);
      const rootCount = await page.locator(surface.rootSelector).count();
      if (rootCount !== 1) {
        throw new Error(
          `${rootCount === 0 ? 'E_SURFACE_ROOT_MISSING' : 'E_SURFACE_ROOT_AMBIGUOUS'} ${surface.surfaceId} count=${rootCount}`
        );
      }
      const anchorCount = await page.locator(card.anchor.selector).count();
      if (anchorCount !== 1) {
        throw new Error(
          `${anchorCount === 0 ? 'E_ANCHOR_MISSING' : 'E_ANCHOR_AMBIGUOUS'} ${card.cardId} ${card.anchor.selector} count=${anchorCount}`
        );
      }
      const insideSurface = await page.evaluate(
        ({rootSelector, anchorSelector}) =>
          document.querySelector(rootSelector)?.contains(document.querySelector(anchorSelector)) ?? false,
        {rootSelector: surface.rootSelector, anchorSelector: card.anchor.selector}
      );
      if (!insideSurface) {
        throw new Error(`E_ANCHOR_OUTSIDE_SURFACE ${card.cardId} ${card.anchor.selector}`);
      }
      if (!(await page.locator(card.anchor.selector).isVisible())) {
        throw new Error(`E_ANCHOR_NOT_VISIBLE ${card.cardId} ${card.anchor.selector}`);
      }
      if (await hasUnknownOverlay(page, manifest.surfaces, activeSurfaceIds)) {
        throw new Error(`E_SURFACE_AMBIGUOUS ${card.cardId} unknown-overlay`);
      }
      results.push({
        cardId: card.cardId,
        selector: card.anchor.selector,
        surfaceId: card.surfaceId,
        activeSurfacePath: activePath.path.map(({surfaceId}) => surfaceId),
        count: anchorCount,
        anchorCount,
        insideSurface,
        visible: true
      });
    }
    return results;
  } finally {
    await page.close();
    await browser.close();
  }
}

async function auditSurfaceSelectors(page, surfaces) {
  for (const surface of surfaces) {
    for (const [label, selector] of [
      ['rootSelector', surface.rootSelector],
      ['activeSelector', surface.activeSelector],
      ...surface.snapshotSelectors.map((snapshotSelector) => ['snapshotSelector', snapshotSelector])
    ]) {
      let count;
      try {
        count = await page.locator(selector).count();
      } catch {
        throw new Error(`E_SURFACE_SELECTOR_INVALID ${surface.surfaceId} ${label} ${selector}`);
      }
      if (label === 'rootSelector' && count === 0) {
        throw new Error(`E_SURFACE_ROOT_MISSING ${surface.surfaceId} ${selector}`);
      }
      if ((label === 'rootSelector' || label === 'snapshotSelector') && count > 1) {
        throw new Error(`E_SURFACE_ROOT_AMBIGUOUS ${surface.surfaceId} ${selector} count=${count}`);
      }
      if (label === 'snapshotSelector' && count === 0) {
        throw new Error(`E_SURFACE_ROOT_MISSING ${surface.surfaceId} ${selector}`);
      }
      if (label === 'activeSelector' && count > 1) {
        throw new Error(`E_SURFACE_AMBIGUOUS ${surface.surfaceId} count=${count}`);
      }
    }
  }
}

async function hasUnknownOverlay(page, surfaces, activeSurfaceIds) {
  return page.evaluate(({surfaces: registered, activeIds}) => {
    const activeRoots = registered
      .filter(({surfaceId, kind}) => kind !== 'view' && activeIds.includes(surfaceId))
      .map(({rootSelector}) => document.querySelector(rootSelector))
      .filter(Boolean);
    const overlays = [...document.querySelectorAll([
      '.drawer-mask.show',
      '.modal-mask.show',
      '[class*="drawer"][class*="show"]',
      '[class*="modal"][class*="show"]',
      '[role="dialog"][aria-modal="true"]'
    ].join(','))].filter((element) => {
      const style = getComputedStyle(element);
      return !element.hidden &&
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        element.getClientRects().length > 0;
    });
    return overlays.some((overlay) => !activeRoots.some((root) =>
      overlay === root || overlay.contains(root) || root.contains(overlay)
    ));
  }, {surfaces, activeIds: activeSurfaceIds});
}

async function main() {
  const args = process.argv.slice(2);
  const prototypeArg = valueAfter(args, '--prototype');
  const manifestArg = valueAfter(args, '--manifest');
  if (!prototypeArg || !manifestArg) {
    throw new Error('Usage: node scripts/audit-anchors.mjs --manifest <manifest.json> --prototype <prototype.html>');
  }
  const prototypePath = resolve(prototypeArg);
  const manifestPath = resolve(manifestArg);
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const prototypeHtml = readFileSync(prototypePath, 'utf8');
  const results = await auditAnchors({prototypeHtml, manifest});
  for (const result of results) {
    process.stdout.write(
      `PASS ${result.cardId} surface=${result.surfaceId} anchorCount=${result.anchorCount} insideSurface=${result.insideSurface}\n`
    );
  }
  process.stdout.write(
    `PASS audited ${results.length} element-card anchors across ${manifest.surfaces.length} registered surfaces\n`
  );
}

function valueAfter(args, flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : null;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`FAIL ${error.message}\n`);
    process.exit(1);
  });
}

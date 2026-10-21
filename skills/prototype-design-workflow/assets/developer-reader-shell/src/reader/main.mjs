import {validateManifest} from './contract.mjs';
import {validateManifest as validateManifestV3} from './contract-v3.mjs';
import {initialState, reduce} from './state-store.mjs';
import {renderReader} from './render.mjs';
import {
  captureState,
  clearElementFocus,
  focusElement,
  locateUnique,
  mountAnnotations,
  resolveCardFromClick,
  restoreReturnPoint,
  runActions
} from './bridge.mjs';
import {createConnector} from './connector.mjs';
import {
  cardsForSurface,
  createSurfaceIndex,
  findUnknownOverlay,
  planSurfaceTransition,
  resolveActiveSurfacePath,
  resolveTargetSurfacePath,
  validateAnchorInSurface
} from './surface-state.mjs';

let state = initialState;
let annotationController = null;
let iframeClickHandler = null;
let iframeKeyHandler = null;
let iframeScrollHandler = null;
let readerNavigationInProgress = false;
let activeSurfacePath = [];
let pendingTransition = null;
let surfaceSyncFrame = null;
let surfaceObserver = null;

const manifest = window.__HANDOFF_MANIFEST__;
const lineageContext = window.__HANDOFF_LINEAGE_CONTEXT__ ?? {};
const validation = manifest.schemaVersion === '3.0.0' ? validateManifestV3(manifest) : validateManifest(manifest, lineageContext);
document.body.dataset.coverage = manifest?.requirement?.coverage ?? 'unknown';
const surfaceIndex = createSurfaceIndex(manifest.surfaces ?? []);
const frame = document.querySelector('.prototype-frame');
const reader = document.querySelector('.developer-reader');
const shell = document.querySelector('.handoff-shell');
const connectorSvg = document.querySelector('#readerConnector');
const connectorPath = connectorSvg.querySelector('[data-connector-path]');
const connector = createConnector({
  svg: connectorSvg,
  path: connectorPath,
  frame,
  reader
});

function getCard(cardId) {
  return manifest.cards.find((card) => card.cardId === cardId) ?? null;
}

function resolveSurfaceState() {
  const doc = frame.contentDocument;
  if (!doc) return {ok: false, code: 'E_SURFACE_TARGET_INACTIVE', path: []};
  const resolved = resolveActiveSurfacePath(doc, manifest.surfaces);
  if (!resolved.ok) return resolved;
  return {
    ...resolved,
    unknownOverlay: findUnknownOverlay(doc, manifest.surfaces)
  };
}

function isActiveExitGuard(path) {
  if (path.length < 2) return false;
  const top = path.at(-1);
  const parent = path.at(-2);
  return (parent.exitGuardSurfaceIds ?? []).includes(top.surfaceId);
}

function mountCurrentAnnotations(surfaceState = resolveSurfaceState()) {
  const doc = frame.contentDocument;
  if (!doc) return;
  annotationController?.destroy();
  const cards = surfaceState.ok && !surfaceState.unknownOverlay
    ? cardsForSurface(manifest.cards, surfaceState.top.surfaceId)
    : [];
  annotationController = mountAnnotations(doc, cards);
  activeSurfacePath = surfaceState.ok ? surfaceState.path : [];
}

function reconnectFocusedConnector() {
  if (!state.focusCardId || state.panelCollapsed || pendingTransition) {
    connector.hide();
    return;
  }
  const doc = frame.contentDocument;
  const card = getCard(state.focusCardId);
  const surfaceState = resolveSurfaceState();
  if (
    !doc ||
    !card ||
    !surfaceState.ok ||
    surfaceState.unknownOverlay ||
    surfaceState.top.surfaceId !== card.surfaceId
  ) {
    connector.hide();
    return;
  }
  const target = locateUnique(doc, card.anchor);
  const titleTarget = readerCardAnchor(card.cardId);
  if (!target.ok || !titleTarget) {
    connector.hide();
    return;
  }
  annotationController?.update();
  connector.connect(target.element, titleTarget);
}

// The reader endpoint is deliberately the summary/title, rather than the
// whole card. An expanded card can be taller than the viewport; treating the
// card as its own scroll target would leave this connector endpoint offscreen.
function readerCardAnchor(cardId) {
  const cardElement = reader.querySelector(`[data-card-id="${CSS.escape(cardId)}"]`);
  return cardElement?.querySelector('.card-summary-main') ?? cardElement;
}

function drawReader() {
  reader.innerHTML = renderReader(manifest, validation.summary, state);
  shell.dataset.panelCollapsed = String(state.panelCollapsed);
  connectorSvg.toggleAttribute('hidden', state.panelCollapsed);

  reader.querySelector('[data-panel-toggle]')?.addEventListener('click', () => {
    state = reduce(state, {type: 'TOGGLE_PANEL'});
    drawReader();
    if (state.panelCollapsed) connector.hide();
    else requestAnimationFrame(reconnectFocusedConnector);
  });

  reader.querySelector('[data-return-prototype]')?.addEventListener('click', returnPrototypeState);

  reader.querySelectorAll('[data-card-trigger]').forEach((button) => {
    button.addEventListener('click', () => {
      const card = getCard(button.dataset.cardTrigger);
      if (card) navigateToCard(card);
    });
  });

  reader.querySelectorAll('[data-card-toggle]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      state = reduce(state, {type: 'TOGGLE_CARD', cardId: button.dataset.cardToggle});
      drawReader();
      if (state.focusCardId) requestAnimationFrame(reconnectFocusedConnector);
    });
  });

  reader.querySelectorAll('[data-source-toggle]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      state = reduce(state, {type: 'TOGGLE_SOURCE', sourceId: button.dataset.sourceToggle});
      drawReader();
      if (state.focusCardId) requestAnimationFrame(reconnectFocusedConnector);
    });
  });
}

function showNavigationError(card, code) {
  state = reduce(state, {
    type: 'NAVIGATION_ERROR',
    message: `${code}：无法自动定位“${card.title}”。人工路径：${card.anchor.manualPath}`
  });
  drawReader();
  connector.hide();
}

function synchronizeSurfaceState() {
  const doc = frame.contentDocument;
  if (!doc) return;
  const surfaceState = resolveSurfaceState();

  if (pendingTransition) {
    const guardStillActive = surfaceState.ok &&
      surfaceState.top.surfaceId === pendingTransition.guardSurfaceId;
    if (!guardStillActive) {
      continuePendingTransition();
      return;
    }
  }

  if (!surfaceState.ok || surfaceState.unknownOverlay) {
    clearElementFocus(doc);
    state = reduce(state, {type: 'CLEAR_FOCUS'});
    connector.hide();
    mountCurrentAnnotations(surfaceState);
    drawReader();
    return;
  }

  const focusedCard = getCard(state.focusCardId);
  if (!focusedCard || focusedCard.surfaceId !== surfaceState.top.surfaceId) {
    clearElementFocus(doc);
    state = reduce(state, {type: 'CLEAR_FOCUS'});
    connector.hide();
  }

  mountCurrentAnnotations(surfaceState);
  const currentCard = getCard(state.focusCardId);
  if (currentCard) {
    const located = locateUnique(doc, currentCard.anchor);
    if (located.ok) focusElement(doc, located.element, currentCard.cardId);
    else {
      clearElementFocus(doc);
      state = reduce(state, {type: 'CLEAR_FOCUS'});
      connector.hide();
    }
  }
  drawReader();
  if (state.focusCardId) requestAnimationFrame(reconnectFocusedConnector);
}

function scheduleSurfaceSync() {
  if (surfaceSyncFrame !== null) return;
  surfaceSyncFrame = requestAnimationFrame(() => {
    surfaceSyncFrame = null;
    synchronizeSurfaceState();
  });
}

function normalizedPrototypeClasses(value) {
  return String(value ?? '')
    .split(/\s+/)
    .filter((name) => name && !name.startsWith('dev-reader-'))
    .sort()
    .join(' ');
}

function isReaderOwnedNode(node) {
  if (node?.nodeType !== 1) return true;
  return node.id === 'dev-reader-annotations' ||
    node.id === 'dev-reader-bridge-styles' ||
    Boolean(node.closest?.('#dev-reader-annotations'));
}

function shouldScheduleSurfaceSync(records) {
  return records.some((record) => {
    if (record.type === 'attributes') {
      return normalizedPrototypeClasses(record.oldValue) !==
        normalizedPrototypeClasses(record.target.className);
    }
    if (record.type === 'childList') {
      return [...record.addedNodes, ...record.removedNodes]
        .some((node) => !isReaderOwnedNode(node));
    }
    return false;
  });
}

function focusPrototypeCard(cardId) {
  const card = getCard(cardId);
  const doc = frame.contentDocument;
  const surfaceState = resolveSurfaceState();
  if (
    !card ||
    !doc ||
    !surfaceState.ok ||
    surfaceState.unknownOverlay ||
    surfaceState.top.surfaceId !== card.surfaceId
  ) {
    synchronizeSurfaceState();
    return;
  }
  const ownership = validateAnchorInSurface(doc, card, surfaceIndex.get(card.surfaceId));
  if (!ownership.ok) {
    showNavigationError(card, ownership.code);
    return;
  }
  finalizeCardFocus(card, ownership.element);
}

function navigateToCard(card) {
  if (pendingTransition) return;
  const doc = frame.contentDocument;
  if (!doc) return;
  const current = resolveSurfaceState();
  if (!current.ok) {
    showNavigationError(card, current.code);
    return;
  }
  if (current.unknownOverlay) {
    synchronizeSurfaceState();
    return;
  }
  if (isActiveExitGuard(current.path)) return;

  const target = resolveTargetSurfacePath(card.surfaceId, surfaceIndex, current.path);
  if (!target.ok) {
    showNavigationError(card, target.code);
    return;
  }
  const captured = captureState(doc, manifest.surfaces);
  if (!captured.ok) {
    showNavigationError(card, captured.code);
    return;
  }
  if (state.returnPoint === null) {
    state = reduce(state, {
      type: 'SET_RETURN_POINT',
      returnPoint: captured.snapshot
    });
  }

  const plan = planSurfaceTransition(current.path, target.path);
  const context = {
    card,
    beforeSnapshot: captured.snapshot,
    sourceFocusCardId: state.focusCardId,
    plan,
    remainingExitSurfaces: [...plan.exits],
    targetPath: target.path,
    guardSurfaceId: null
  };
  runReaderNavigation(() => executeTransition(context));
}

function runReaderNavigation(callback) {
  readerNavigationInProgress = true;
  try {
    callback();
  } finally {
    readerNavigationInProgress = false;
  }
}

function executeTransition(context) {
  const doc = frame.contentDocument;
  const card = context.card;
  if (!doc) return;

  if (!context.plan.sameTop && context.remainingExitSurfaces.length > 0) {
    const exitResult = exitSurfacesOrPause(context);
    if (exitResult.pending || !exitResult.ok) return;
  }

  const refreshed = resolveSurfaceState();
  const targetAlreadyActive = refreshed.ok &&
    !refreshed.unknownOverlay &&
    refreshed.top.surfaceId === card.surfaceId;
  if (!targetAlreadyActive) {
    const entered = runActions(doc, card.anchor.enterActions);
    if (!entered.ok) {
      rollbackNavigation(context, entered.code);
      return;
    }
  }

  const finalPath = resolveSurfaceState();
  if (
    !finalPath.ok ||
    finalPath.unknownOverlay ||
    finalPath.top.surfaceId !== card.surfaceId
  ) {
    rollbackNavigation(context, 'E_SURFACE_TARGET_INACTIVE');
    return;
  }

  const ownership = validateAnchorInSurface(
    doc,
    card,
    surfaceIndex.get(card.surfaceId)
  );
  if (!ownership.ok) {
    rollbackNavigation(context, ownership.code);
    return;
  }
  finalizeCardFocus(card, ownership.element);
}

function exitSurfacesOrPause(context) {
  const doc = frame.contentDocument;
  while (context.remainingExitSurfaces.length > 0) {
    const surface = context.remainingExitSurfaces[0];
    if ((surface.exitActions ?? []).length === 0) {
      context.remainingExitSurfaces.shift();
      continue;
    }

    const exited = runActions(doc, surface.exitActions);
    if (exited.ok) {
      context.remainingExitSurfaces.shift();
      continue;
    }

    const current = resolveSurfaceState();
    const guardSurfaceId = current.ok ? current.top.surfaceId : null;
    if (
      exited.code === 'E_STATE_ACTION_NOT_HIDDEN' &&
      (surface.exitGuardSurfaceIds ?? []).includes(guardSurfaceId)
    ) {
      context.guardSurfaceId = guardSurfaceId;
      pendingTransition = context;
      state = reduce(state, {
        type: 'START_PENDING_NAVIGATION',
        cardId: context.card.cardId
      });
      clearElementFocus(doc);
      state = reduce(state, {type: 'CLEAR_FOCUS'});
      connector.hide();
      mountCurrentAnnotations(current);
      drawReader();
      return {ok: false, pending: true};
    }

    rollbackNavigation(context, 'E_SURFACE_EXIT_FAILED');
    return {ok: false, pending: false};
  }
  return {ok: true, pending: false};
}

function continuePendingTransition() {
  const context = pendingTransition;
  const doc = frame.contentDocument;
  if (!context || !doc) return;
  const current = resolveSurfaceState();
  const sourceSurfaceId = context.remainingExitSurfaces[0]?.surfaceId;
  const sourceStillActive = current.ok &&
    current.path.some(({surfaceId}) => surfaceId === sourceSurfaceId);

  if (sourceStillActive) {
    const restored = restoreReturnPoint(doc, context.beforeSnapshot);
    pendingTransition = null;
    state = reduce(state, {type: 'CLEAR_PENDING_NAVIGATION'});
    if (!restored.ok) {
      rollbackNavigation(context, restored.code);
      return;
    }
    synchronizeSurfaceState();
    const sourceCard = getCard(context.sourceFocusCardId);
    if (sourceCard) {
      const ownership = validateAnchorInSurface(
        doc,
        sourceCard,
        surfaceIndex.get(sourceCard.surfaceId)
      );
      if (ownership.ok) finalizeCardFocus(sourceCard, ownership.element);
    }
    return;
  }

  if (!current.ok || current.unknownOverlay) {
    rollbackNavigation(context, current.code ?? 'E_SURFACE_PARENT_INVALID');
    return;
  }
  context.remainingExitSurfaces.shift();
  context.guardSurfaceId = null;
  pendingTransition = null;
  state = reduce(state, {type: 'CLEAR_PENDING_NAVIGATION'});
  runReaderNavigation(() => executeTransition(context));
}

function rollbackNavigation(context, code) {
  const doc = frame.contentDocument;
  if (!doc) return;
  const restored = restoreReturnPoint(doc, context.beforeSnapshot);
  pendingTransition = null;
  state = reduce(state, {type: 'CLEAR_PENDING_NAVIGATION'});
  state = reduce(state, {type: 'CLEAR_FOCUS'});
  clearElementFocus(doc);
  connector.hide();
  synchronizeSurfaceState();
  showNavigationError(
    context.card,
    restored.ok ? code : `${code}+${restored.code}`
  );
}

function finalizeCardFocus(card, element) {
  const doc = frame.contentDocument;
  if (!doc) return;
  state = reduce(state, {type: 'FOCUS_CARD', cardId: card.cardId});
  mountCurrentAnnotations();
  const currentTarget = locateUnique(doc, card.anchor);
  if (!currentTarget.ok) {
    showNavigationError(card, currentTarget.code);
    return;
  }
  currentTarget.element.scrollIntoView({block: 'center', inline: 'nearest'});
  focusElement(doc, currentTarget.element, card.cardId);
  drawReader();

  // Keep the programmatic position synchronous and aligned with the same
  // endpoint that reconnectFocusedConnector() uses. Smooth scrolling exposes
  // an intermediate geometry to the runtime audit.
  readerCardAnchor(card.cardId)?.scrollIntoView({
    block: 'center',
    inline: 'nearest',
    behavior: 'instant'
  });
  requestAnimationFrame(() => {
    annotationController?.update();
    reconnectFocusedConnector();
  });
}

function clearFocus({keepReturnPoint = true} = {}) {
  const doc = frame.contentDocument;
  if (doc) clearElementFocus(doc);
  state = reduce(state, {type: 'CLEAR_FOCUS'});
  state = reduce(state, {type: 'CLEAR_NAVIGATION_ERROR'});
  if (!keepReturnPoint) state = reduce(state, {type: 'CLEAR_RETURN_POINT'});
  connector.hide();
  drawReader();
}

function returnPrototypeState() {
  const doc = frame.contentDocument;
  if (!doc || !state.returnPoint || pendingTransition) return;
  const restored = restoreReturnPoint(doc, state.returnPoint);
  if (!restored.ok) {
    state = reduce(state, {
      type: 'NAVIGATION_ERROR',
      message: `${restored.code}：无法恢复进入定位前的原型状态。`
    });
    drawReader();
    return;
  }
  clearFocus({keepReturnPoint: false});
  synchronizeSurfaceState();
}

function bindIframeEvents() {
  const doc = frame.contentDocument;
  if (!doc) return;
  if (iframeClickHandler) doc.removeEventListener('click', iframeClickHandler);
  if (iframeKeyHandler) doc.removeEventListener('keydown', iframeKeyHandler);
  if (iframeScrollHandler) {
    doc.defaultView.removeEventListener('scroll', iframeScrollHandler);
  }
  surfaceObserver?.disconnect();

  iframeClickHandler = (event) => {
    if (readerNavigationInProgress) return;
    const cardId = resolveCardFromClick(event.target);
    requestAnimationFrame(() => {
      if (readerNavigationInProgress) return;
      if (cardId) {
        focusPrototypeCard(cardId);
      } else if (!event.target.closest('button, a, input, select, textarea, [role="button"]')) {
        clearFocus();
      }
      scheduleSurfaceSync();
    });
  };
  iframeKeyHandler = (event) => {
    if (event.key === 'Escape') {
      clearFocus();
      scheduleSurfaceSync();
    }
  };
  iframeScrollHandler = () => annotationController?.update();

  doc.addEventListener('click', iframeClickHandler);
  doc.addEventListener('keydown', iframeKeyHandler);
  doc.defaultView.addEventListener('scroll', iframeScrollHandler, {passive: true});

  surfaceObserver = new doc.defaultView.MutationObserver((records) => {
    if (shouldScheduleSurfaceSync(records)) scheduleSurfaceSync();
  });
  surfaceObserver.observe(doc.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class'],
    attributeOldValue: true
  });
}

reader.addEventListener('click', (event) => {
  if (!event.target.closest('button, a, .element-card') && event.target.closest('.reader-content')) {
    clearFocus();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') clearFocus();
});

frame.addEventListener('load', () => {
  bindIframeEvents();
  synchronizeSurfaceState();
});

if (!validation.ok) {
  reader.innerHTML = `<div class="navigation-error" role="alert">manifest 校验失败：${
    validation.errors.map(({code, path}) => `${code} ${path}`).join('；')
  }</div>`;
  connector.destroy();
} else {
  drawReader();
  frame.srcdoc = new TextDecoder().decode(
    Uint8Array.from(atob(window.__PROTOTYPE_BASE64__), (character) => character.charCodeAt(0))
  );
}

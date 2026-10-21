import {
  capturePrototypeSnapshot,
  restorePrototypeSnapshot
} from './surface-snapshot.mjs';

const STYLE_ID = 'dev-reader-bridge-styles';
const LAYER_ID = 'dev-reader-annotations';
const TARGET_ATTRIBUTE = 'data-dev-reader-card-id';
const FOCUS_CLASS = 'dev-reader-target--focused';

const BRIDGE_STYLES = `
  #${LAYER_ID} {
    position: fixed;
    inset: 0;
    z-index: 2147483000;
    pointer-events: none;
    overflow: visible;
  }
  .dev-reader-badge {
    position: absolute;
    display: grid;
    place-items: center;
    width: 24px;
    height: 24px;
    border: 2px solid #1677ff;
    border-radius: 999px;
    background: #eaf3ff;
    color: #0958d9;
    box-shadow: 0 2px 8px rgba(22, 119, 255, .22);
    font: 700 12px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    transition: transform .16s ease, opacity .16s ease, background .16s ease;
    pointer-events: auto;
    cursor: pointer;
  }
  .dev-reader-badge[hidden] { display: none !important; }
  .dev-reader-badge--dimmed { opacity: .42; }
  .dev-reader-badge--focused {
    width: 28px;
    height: 28px;
    color: #fff;
    background: #1677ff;
    box-shadow: 0 0 0 5px rgba(22, 119, 255, .18), 0 6px 16px rgba(22, 119, 255, .3);
  }
  .${FOCUS_CLASS} {
    outline: 3px solid #1677ff !important;
    outline-offset: 4px !important;
    box-shadow: 0 0 0 8px rgba(22, 119, 255, .16), 0 8px 24px rgba(22, 119, 255, .22) !important;
    background-color: #eaf3ff !important;
    position: relative;
    z-index: 2147482000 !important;
  }
`;

export function locateUnique(doc, anchor) {
  let matches;
  try {
    matches = [...doc.querySelectorAll(anchor.selector)];
  } catch {
    return {ok: false, code: 'E_ANCHOR_SELECTOR_INVALID', count: 0};
  }
  if (matches.length === 1) return {ok: true, element: matches[0], count: 1};
  return {
    ok: false,
    code: matches.length === 0 ? 'E_ANCHOR_MISSING' : 'E_ANCHOR_AMBIGUOUS',
    count: matches.length
  };
}

export const locateAnchor = locateUnique;

export function captureState(doc, surfaces = []) {
  return capturePrototypeSnapshot(doc, surfaces);
}

export function restoreReturnPoint(doc, point) {
  if (!point) return {ok: false, code: 'E_RETURN_POINT_MISSING'};
  return restorePrototypeSnapshot(doc, point);
}

function isVisible(element) {
  if (!element || element.hidden) return false;
  const view = element.ownerDocument?.defaultView;
  const style = view?.getComputedStyle?.(element);
  return style ? style.display !== 'none' && style.visibility !== 'hidden' : true;
}

export function enterAndLocate(doc, card, {capture = true} = {}) {
  const captured = capture ? captureState(doc) : {ok: true, snapshot: null};
  if (!captured.ok) return captured;
  const before = captured.snapshot;
  const actions = runActions(doc, card.anchor.enterActions);
  if (!actions.ok) {
    if (before) restoreReturnPoint(doc, before);
    return {...actions, before};
  }
  const located = locateUnique(doc, card.anchor);
  if (!located.ok) {
    if (before) restoreReturnPoint(doc, before);
    return {...located, before};
  }
  return {...located, before};
}

export const enterAndFocus = enterAndLocate;

export function runActions(doc, actions = []) {
  for (const action of actions) {
    let target;
    try {
      target = doc.querySelector(action.selector);
    } catch {
      return {ok: false, code: 'E_STATE_ACTION_SELECTOR_INVALID', action};
    }

    if (action.type === 'assert-hidden') {
      if (target && isVisible(target)) {
        return {ok: false, code: 'E_STATE_ACTION_NOT_HIDDEN', action};
      }
      continue;
    }

    if (!target) return {ok: false, code: 'E_STATE_ACTION_MISSING', action};
    if (action.type === 'click') {
      target.click();
      continue;
    }
    if (action.type === 'assert-visible') {
      if (!isVisible(target)) {
        return {ok: false, code: 'E_STATE_ACTION_NOT_VISIBLE', action};
      }
      continue;
    }
    return {ok: false, code: 'E_STATE_ACTION_UNSUPPORTED', action};
  }
  return {ok: true};
}

export function injectBridgeStyles(doc) {
  if (doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement('style');
  style.id = STYLE_ID;
  style.textContent = BRIDGE_STYLES;
  doc.head.append(style);
}

export function mountAnnotations(doc, cards) {
  injectBridgeStyles(doc);
  const previousLayer = doc.getElementById(LAYER_ID);
  previousLayer?.remove();
  doc.querySelectorAll(`[${TARGET_ATTRIBUTE}]`).forEach((element) => {
    element.removeAttribute(TARGET_ATTRIBUTE);
  });

  const layer = doc.createElement('div');
  layer.id = LAYER_ID;
  layer.setAttribute('aria-hidden', 'true');
  doc.body.append(layer);

  const bindings = [];
  for (const card of cards) {
    const located = locateUnique(doc, card.anchor);
    if (!located.ok) continue;
    const target = located.element;
    target.setAttribute(TARGET_ATTRIBUTE, card.cardId);
    const badge = doc.createElement('span');
    badge.className = 'dev-reader-badge';
    badge.dataset.cardId = card.cardId;
    badge.setAttribute(TARGET_ATTRIBUTE, card.cardId);
    badge.textContent = card.displayNumber;
    layer.append(badge);
    bindings.push({card, target, badge});
  }

  const update = () => {
    for (const {target, badge} of bindings) {
      const rect = target.getBoundingClientRect();
      badge.hidden = !isVisible(target) || (rect.width === 0 && rect.height === 0);
      badge.style.transform = `translate(${Math.round(rect.right - 12)}px, ${Math.round(rect.top - 12)}px)`;
    }
  };
  update();
  return {
    count: bindings.length,
    update,
    destroy() {
      for (const {target} of bindings) target.removeAttribute(TARGET_ATTRIBUTE);
      layer.remove();
    }
  };
}

export function resolveCardFromClick(target) {
  return target?.closest?.(`[${TARGET_ATTRIBUTE}]`)?.getAttribute(TARGET_ATTRIBUTE) ?? null;
}

export function focusElement(doc, element, cardId) {
  clearElementFocus(doc);
  element.classList.add(FOCUS_CLASS);
  doc.body.dataset.devReaderHasFocus = 'true';
  for (const badge of doc.querySelectorAll(`#${LAYER_ID} .dev-reader-badge`)) {
    const focused = badge.dataset.cardId === cardId;
    badge.classList.toggle('dev-reader-badge--focused', focused);
    badge.classList.toggle('dev-reader-badge--dimmed', !focused);
  }
}

export function clearElementFocus(doc) {
  doc.querySelectorAll(`.${FOCUS_CLASS}`).forEach((element) => element.classList.remove(FOCUS_CLASS));
  delete doc.body.dataset.devReaderHasFocus;
  for (const badge of doc.querySelectorAll(`#${LAYER_ID} .dev-reader-badge`)) {
    badge.classList.remove('dev-reader-badge--focused', 'dev-reader-badge--dimmed');
  }
}

export const clearFocus = clearElementFocus;

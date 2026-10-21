import {resolveActiveSurfacePath} from './surface-state.mjs';

const CLASS_STATE_SELECTOR = [
  '.view[id]',
  '.drawer-mask[id]',
  '.modal-mask[id]',
  '[class*="drawer"][id]',
  '[class*="modal"][id]',
  '[role="tab"][id]'
].join(', ');
const CONTROL_SELECTOR = 'input, select, textarea';

function queryAll(doc, selector) {
  try {
    return {ok: true, matches: [...doc.querySelectorAll(selector)]};
  } catch {
    return {ok: false, matches: []};
  }
}

function captureControls(node) {
  return [...node.querySelectorAll(CONTROL_SELECTOR)].map((control, index) => ({
    index,
    value: control.value,
    checked: Boolean(control.checked),
    indeterminate: Boolean(control.indeterminate),
    selectedIndex: Number.isInteger(control.selectedIndex) ? control.selectedIndex : -1
  }));
}

function restoreControls(node, controls) {
  const current = [...node.querySelectorAll(CONTROL_SELECTOR)];
  for (const saved of controls ?? []) {
    const control = current[saved.index];
    if (!control) continue;
    if ('value' in control) control.value = saved.value;
    if ('checked' in control) control.checked = saved.checked;
    if ('indeterminate' in control) control.indeterminate = saved.indeterminate;
    if ('selectedIndex' in control && saved.selectedIndex >= 0) {
      control.selectedIndex = saved.selectedIndex;
    }
  }
}

export function capturePrototypeSnapshot(doc, surfaces = []) {
  let activeSurfaceIds = [];
  if (surfaces.length > 0) {
    const active = resolveActiveSurfacePath(doc, surfaces);
    if (!active.ok) return {ok: false, code: active.code};
    activeSurfaceIds = active.path.map(({surfaceId}) => surfaceId);
  }

  const selectors = [...new Set(
    surfaces.flatMap(({snapshotSelectors = []}) => snapshotSelectors)
  )];
  const dynamicNodes = [];
  for (const selector of selectors) {
    const queried = queryAll(doc, selector);
    if (!queried.ok) {
      return {ok: false, code: 'E_SNAPSHOT_SELECTOR_INVALID', selector};
    }
    if (queried.matches.length > 1) {
      return {ok: false, code: 'E_SNAPSHOT_SELECTOR_AMBIGUOUS', selector};
    }
    if (queried.matches.length === 0) {
      dynamicNodes.push({selector, exists: false});
      continue;
    }
    const node = queried.matches[0];
    dynamicNodes.push({
      selector,
      exists: true,
      innerHTML: node.innerHTML,
      className: node.className,
      scrollLeft: node.scrollLeft ?? 0,
      scrollTop: node.scrollTop ?? 0,
      controls: captureControls(node)
    });
  }

  const classQuery = queryAll(doc, CLASS_STATE_SELECTOR);
  if (!classQuery.ok) return {ok: false, code: 'E_SNAPSHOT_CLASS_SELECTOR_INVALID'};

  return {
    ok: true,
    snapshot: {
      activeSurfaceIds,
      dynamicNodes,
      classStates: classQuery.matches
        .filter(({id}) => id)
        .map((node) => ({
          id: node.id,
          className: node.className,
          ariaSelected: node.hasAttribute('aria-selected') ? node.getAttribute('aria-selected') : null
        })),
      activeNavView: doc.querySelector('.nav-link.active')?.dataset?.view ?? null,
      activeQueueTab: doc.querySelector('[data-queue-tab].active')?.dataset?.queueTab ?? null,
      iframeScroll: {
        x: doc.defaultView?.scrollX ?? 0,
        y: doc.defaultView?.scrollY ?? 0
      }
    }
  };
}

export function restorePrototypeSnapshot(doc, snapshot) {
  if (!snapshot) return {ok: false, code: 'E_RETURN_POINT_MISSING'};

  for (const saved of snapshot.dynamicNodes ?? []) {
    if (!saved.exists) continue;
    const queried = queryAll(doc, saved.selector);
    if (!queried.ok) {
      return {ok: false, code: 'E_SNAPSHOT_SELECTOR_INVALID', selector: saved.selector};
    }
    if (queried.matches.length !== 1) {
      return {
        ok: false,
        code: queried.matches.length === 0
          ? 'E_SNAPSHOT_SELECTOR_MISSING'
          : 'E_SNAPSHOT_SELECTOR_AMBIGUOUS',
        selector: saved.selector
      };
    }
    const node = queried.matches[0];
    // Never replace live markup: doing so would silently destroy listeners
    // registered with addEventListener. Business DOM must restore itself via
    // its normal exit/enter behavior; this layer only restores reversible UI
    // state and the runtime audit rejects remaining visible drift.
    node.className = saved.className;
    restoreControls(node, saved.controls);
  }

  for (const saved of snapshot.classStates ?? []) {
    const node = doc.getElementById(saved.id);
    if (node) {
      node.className = saved.className;
      if (saved.ariaSelected === null) node.removeAttribute('aria-selected');
      else if (saved.ariaSelected !== undefined) node.setAttribute('aria-selected', saved.ariaSelected);
    }
  }

  doc.querySelectorAll('.nav-link').forEach((link) => {
    link.classList.toggle('active', link.dataset.view === snapshot.activeNavView);
  });
  doc.querySelectorAll('[data-queue-tab]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.queueTab === snapshot.activeQueueTab);
  });

  for (const saved of snapshot.dynamicNodes ?? []) {
    if (!saved.exists) continue;
    const queried = queryAll(doc, saved.selector);
    if (queried.ok && queried.matches.length === 1) {
      queried.matches[0].scrollLeft = saved.scrollLeft ?? 0;
      queried.matches[0].scrollTop = saved.scrollTop ?? 0;
    }
  }

  doc.defaultView?.scrollTo?.(
    snapshot.iframeScroll?.x ?? 0,
    snapshot.iframeScroll?.y ?? 0
  );
  return {ok: true};
}

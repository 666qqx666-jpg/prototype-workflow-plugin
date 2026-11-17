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
const APPLICATION_STATE_KEY = '__PROTOTYPE_READER_STATE__';

// Keep a detached JSON value, never a function, DOM node, Promise or live model.
function copyApplicationState(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'object' || seen.has(value)) throw new Error('invalid state');
  const proto = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && proto !== null &&
      (Object.prototype.toString.call(value) !== '[object Object]' || Object.getPrototypeOf(proto) !== null)) {
    throw new Error('non-data state');
  }
  if (Reflect.ownKeys(value).some(key => typeof key === 'symbol')) throw new Error('symbol state');
  seen.add(value);
  const result = Array.isArray(value) ? Array.from(value, item => copyApplicationState(item, seen))
    : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copyApplicationState(item, seen)]));
  seen.delete(value);
  return result;
}

function applicationAdapter(doc) {
  const adapter = doc.defaultView?.[APPLICATION_STATE_KEY];
  if (adapter === undefined) return null;
  if (!adapter || typeof adapter.capture !== 'function' || typeof adapter.restore !== 'function') {
    throw new Error('invalid adapter');
  }
  return adapter;
}

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
    ...(control.type === 'file' ? {files: [...control.files]} : {}),
    checked: Boolean(control.checked),
    indeterminate: Boolean(control.indeterminate),
    selectedIndex: Number.isInteger(control.selectedIndex) ? control.selectedIndex : -1,
    ...(control.multiple && control.options ? {selectedOptions: [...control.options].map(option => option.selected)} : {})
  }));
}

function restoreControls(node, controls, strict = false) {
  const current = [...node.querySelectorAll(CONTROL_SELECTOR)];
  if (strict && current.length !== (controls ?? []).length) return false;
  for (const saved of controls ?? []) {
    const control = current[saved.index];
    if (!control) continue;
    if (saved.files) {
      if (control.type !== 'file') return false;
      // A file input cannot accept a non-empty value. Preserve the actual
      // user-selected File objects, including when application restore rebuilt
      // the control. This reads no path and dispatches no import/change event.
      try {
        const currentFiles = [...control.files];
        if (currentFiles.length !== saved.files.length || currentFiles.some((file, i) => file !== saved.files[i])) {
          const transfer = new control.ownerDocument.defaultView.DataTransfer();
          for (const file of saved.files) transfer.items.add(file);
          control.files = transfer.files;
        }
      } catch { return false; }
    } else if ('value' in control) {
      if (control.type === 'file' && saved.value) return false;
      control.value = saved.value;
    }
    if ('checked' in control) control.checked = saved.checked;
    if ('indeterminate' in control) control.indeterminate = saved.indeterminate;
    if ('selectedIndex' in control && saved.selectedIndex >= 0) {
      control.selectedIndex = saved.selectedIndex;
    }
    if (saved.selectedOptions && control.options) {
      if (strict && control.options.length !== saved.selectedOptions.length) return false;
      [...control.options].forEach((option, index) => { option.selected = Boolean(saved.selectedOptions[index]); });
    }
  }
  return true;
}

export function capturePrototypeSnapshot(doc, surfaces = []) {
  let applicationState;
  try {
    const adapter = applicationAdapter(doc);
    if (adapter) applicationState = {version: 1, value: copyApplicationState(adapter.capture())};
  } catch {
    return {ok: false, code: 'E_SNAPSHOT_APPLICATION_STATE_INVALID'};
  }
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
      ...(applicationState ? {applicationState} : {}),
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

  // Reconstruct dynamic content through the prototype's normal rendering code
  // before restoring individual controls. Do not replace live innerHTML here.
  if (snapshot.applicationState) {
    try {
      const adapter = applicationAdapter(doc);
      if (!adapter || snapshot.applicationState.version !== 1) throw new Error('missing adapter');
      const result = adapter.restore(copyApplicationState(snapshot.applicationState.value));
      if (result !== undefined) throw new Error('restore must be synchronous and return void');
    } catch {
      return {ok: false, code: 'E_RETURN_APPLICATION_STATE_FAILED'};
    }
  }

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
    if (!restoreControls(node, saved.controls, Boolean(snapshot.applicationState))) {
      return {ok: false, code: 'E_RETURN_CONTROL_STATE_MISMATCH', selector: saved.selector};
    }
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

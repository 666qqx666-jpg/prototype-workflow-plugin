import test from 'node:test';
import assert from 'node:assert/strict';
import {capturePrototypeSnapshot, restorePrototypeSnapshot} from '../assets/developer-reader-shell/src/reader/surface-snapshot.mjs';

function element({id = '', className = '', selected = null} = {}) {
  const attributes = new Map(selected === null ? [] : [['aria-selected', selected]]);
  return {
    id, className, scrollLeft: 0, scrollTop: 0,
    querySelectorAll() { return []; },
    hasAttribute(name) { return attributes.has(name); },
    getAttribute(name) { return attributes.get(name) ?? null; },
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); },
    _attributes: attributes
  };
}

test('return snapshot restores custom tab classes and aria-selected state', () => {
  const view = element({id: 'app', className: 'view active'});
  const allTab = element({id: 'tab-all', className: 'workspace-tab selected', selected: 'true'});
  const pendingTab = element({id: 'tab-pending', className: 'workspace-tab', selected: 'false'});
  const panel = element({id: 'panel', className: 'panel'});
  const byId = new Map([view, allTab, pendingTab, panel].map((node) => [node.id, node]));
  const doc = {
    querySelectorAll(selector) {
      if (selector === '#active') return [view];
      if (selector === '#panel') return [panel];
      if (selector.includes('[role="tab"][id]')) return [view, allTab, pendingTab];
      if (selector === '.nav-link' || selector === '[data-queue-tab]') return [];
      return [];
    },
    querySelector() { return null; },
    getElementById(id) { return byId.get(id) ?? null; },
    defaultView: {scrollX: 0, scrollY: 0, scrollTo() {}}
  };
  const surfaces = [{surfaceId: 'view:test', kind: 'view', activeSelector: '#active', rootSelector: '#app', snapshotSelectors: ['#panel']}];
  const captured = capturePrototypeSnapshot(doc, surfaces);
  assert.equal(captured.ok, true);
  allTab.className = 'workspace-tab';
  allTab.setAttribute('aria-selected', 'false');
  pendingTab.className = 'workspace-tab selected';
  pendingTab.setAttribute('aria-selected', 'true');
  assert.equal(restorePrototypeSnapshot(doc, captured.snapshot).ok, true);
  assert.equal(allTab.className, 'workspace-tab selected');
  assert.equal(allTab.getAttribute('aria-selected'), 'true');
  assert.equal(pendingTab.className, 'workspace-tab');
  assert.equal(pendingTab.getAttribute('aria-selected'), 'false');
});

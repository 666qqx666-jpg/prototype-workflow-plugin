import test from 'node:test';
import assert from 'node:assert/strict';
import {capturePrototypeSnapshot, restorePrototypeSnapshot} from '../assets/developer-reader-shell/src/reader/surface-snapshot.mjs';

function dynamicFixture() {
  let model = {draft: ['one', 'two']}, controls = [], confirmed = [];
  const render = () => { controls = model.draft.map(value => ({value, checked: true, confirm: () => confirmed.push(value)})); };
  render();
  const panel = {className: '', querySelectorAll: () => controls};
  const doc = {
    defaultView: {__PROTOTYPE_READER_STATE__: {
      capture: () => model,
      restore: value => { model = value; render(); }
    }},
    querySelectorAll: selector => selector === '#panel' ? [panel] : [],
    querySelector: () => null, getElementById: () => null
  };
  // No surfaces needed to exercise application model restoration.
  return {doc, panel, render, clear: () => { model.draft = []; render(); },
    model: () => model, controls: () => controls, confirmed: () => confirmed};
}

test('application draft is detached and restored before controls; callbacks remain usable across two returns', () => {
  const f = dynamicFixture();
  const saved = capturePrototypeSnapshot(f.doc).snapshot;
  saved.dynamicNodes = [{selector: '#panel', exists: true, className: '', controls: f.controls().map((x,index) => ({index,value:x.value,checked:true,indeterminate:false,selectedIndex:-1}))}];
  for (let i=0;i<2;i++) {
    f.clear();
    assert.deepEqual(saved.applicationState.value.draft, ['one','two']);
    assert.equal(restorePrototypeSnapshot(f.doc, saved).ok, true);
    assert.deepEqual(f.model().draft, ['one','two']);
    f.controls()[1].confirm();
    f.model().draft.push('changed');
  }
  assert.deepEqual(f.confirmed(), ['two','two']);
});

test('invalid or asynchronous application state is not captured as a success', () => {
  for (const bad of [undefined, Promise.resolve({}), {fn() {}}, {x: NaN}, new Date(), new Map(), {x: undefined}]) {
    const {doc} = dynamicFixture();
    doc.defaultView.__PROTOTYPE_READER_STATE__.capture = () => bad;
    assert.equal(capturePrototypeSnapshot(doc).code, 'E_SNAPSHOT_APPLICATION_STATE_INVALID');
  }
  const {doc} = dynamicFixture(); const cyclic = {};cyclic.self=cyclic;
  doc.defaultView.__PROTOTYPE_READER_STATE__.capture = () => cyclic;
  assert.equal(capturePrototypeSnapshot(doc).ok, false);
});

test('missing, throwing or asynchronous restoration fails closed', () => {
  for (const kind of ['missing','throw','async']) {
    const {doc} = dynamicFixture();const saved=capturePrototypeSnapshot(doc).snapshot;
    if(kind==='missing')delete doc.defaultView.__PROTOTYPE_READER_STATE__;
    else doc.defaultView.__PROTOTYPE_READER_STATE__.restore = kind==='throw' ? () => {throw new Error('private detail');} : async () => {};
    assert.deepEqual(restorePrototypeSnapshot(doc,saved),{ok:false,code:'E_RETURN_APPLICATION_STATE_FAILED'});
  }
});

test('adapter that does not reconstruct dynamic controls cannot return a success', () => {
  const f=dynamicFixture(),saved=capturePrototypeSnapshot(f.doc).snapshot;
  saved.dynamicNodes=[{selector:'#panel',exists:true,className:'',controls:[{index:0,value:'one'}]}];
  f.clear();f.doc.defaultView.__PROTOTYPE_READER_STATE__.restore=()=>{};
  assert.equal(restorePrototypeSnapshot(f.doc,saved).code,'E_RETURN_CONTROL_STATE_MISMATCH');
});

test('broken adapter fails capture without exposing its exception', () => {
  const {doc}=dynamicFixture();doc.defaultView.__PROTOTYPE_READER_STATE__={capture(){throw new Error('private detail');},restore(){}};
  assert.deepEqual(capturePrototypeSnapshot(doc),{ok:false,code:'E_SNAPSHOT_APPLICATION_STATE_INVALID'});
});

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

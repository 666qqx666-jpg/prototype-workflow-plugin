export const ENUMS = Object.freeze({
  coverage: ['full', 'pilot-slice'],
  changeKind: ['add', 'modify', 'delete', 'critical-reuse'],
  anchorKind: ['element', 'region'],
  surfaceKind: ['view', 'drawer', 'modal'],
  actionType: ['click', 'assert-visible', 'assert-hidden'],
  sourceType: ['prd']
});

const hasText = (value) => typeof value === 'string' && value.trim().length > 0;
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isHash = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
const asArray = (value) => Array.isArray(value) ? value : [];

function findForbiddenApiMappings(value, path = '') {
  if (!isObject(value) && !Array.isArray(value)) return [];
  const findings = [];
  for (const [key, child] of Object.entries(value)) {
    const childPath = path ? `${path}.${key}` : key;
    if (key === 'apis' || key === 'implementation') findings.push(childPath);
    findings.push(...findForbiddenApiMappings(child, childPath));
  }
  return findings;
}

function findSurfaceCycles(surfaces) {
  const parentsById = new Map(
    surfaces
      .filter((surface) => isObject(surface) && hasText(surface.surfaceId))
      .map((surface) => [surface.surfaceId, asArray(surface.parentSurfaceIds)])
  );
  const visiting = [];
  const visited = new Set();
  const cyclic = new Set();

  function visit(surfaceId) {
    const cycleStart = visiting.indexOf(surfaceId);
    if (cycleStart >= 0) {
      for (const id of visiting.slice(cycleStart)) cyclic.add(id);
      return;
    }
    if (visited.has(surfaceId)) return;
    visiting.push(surfaceId);
    for (const parentId of parentsById.get(surfaceId) ?? []) {
      if (parentsById.has(parentId)) visit(parentId);
    }
    visiting.pop();
    visited.add(surfaceId);
  }

  for (const surfaceId of parentsById.keys()) visit(surfaceId);
  return cyclic;
}

export function validateManifest(manifest) {
  const errors = [];
  const warnings = [];
  const error = (code, path, detail = '') => errors.push({code, path, detail});

  if (!isObject(manifest)) {
    error('E_MANIFEST_OBJECT', '$');
    return {ok: false, errors, warnings, summary: emptySummary()};
  }

  if (manifest.schemaVersion !== '2.0.0') error('E_SCHEMA_VERSION', 'schemaVersion');
  if (Object.hasOwn(manifest, 'changes')) error('E_LEGACY_CHANGES', 'changes');

  const requirement = isObject(manifest.requirement) ? manifest.requirement : {};
  if (!hasText(requirement.id)) error('E_REQUIREMENT_ID', 'requirement.id');
  if (!hasText(requirement.title)) error('E_REQUIREMENT_TITLE', 'requirement.title');
  if (!ENUMS.coverage.includes(requirement.coverage)) error('E_COVERAGE', 'requirement.coverage');
  if (!hasText(requirement.sourceLabel) || !isHash(requirement.sourceHash)) {
    error('E_REQUIREMENT_SOURCE', 'requirement');
  }
  if (requirement.scope !== undefined) {
    if (!isObject(requirement.scope)) {
      error('E_REQUIREMENT_SCOPE', 'requirement.scope');
    } else {
      for (const key of ['included', 'navigationOnly', 'outOfSlice']) {
        if (!Array.isArray(requirement.scope[key]) ||
            requirement.scope[key].some((item) => !hasText(item))) {
          error('E_REQUIREMENT_SCOPE', `requirement.scope.${key}`);
        }
      }
    }
  }

  const prototype = isObject(manifest.prototype) ? manifest.prototype : {};
  if (!hasText(prototype.sourceLabel) || !isHash(prototype.sourceHash)) {
    error('E_PROTOTYPE_SOURCE', 'prototype');
  }

  const pages = asArray(manifest.pages);
  if (pages.length === 0) error('E_PAGES_REQUIRED', 'pages');
  const pageIds = new Set();
  for (const [index, page] of pages.entries()) {
    const path = `pages[${index}]`;
    if (!isObject(page) || !hasText(page.pageId) || !hasText(page.title) || !Number.isInteger(page.order)) {
      error('E_PAGE', path);
      continue;
    }
    if (pageIds.has(page.pageId)) error('E_DUPLICATE_PAGE_ID', `${path}.pageId`);
    pageIds.add(page.pageId);
  }

  const surfaces = asArray(manifest.surfaces);
  if (!Array.isArray(manifest.surfaces) || surfaces.length === 0) {
    error('E_SURFACES_REQUIRED', 'surfaces');
  }
  const surfaceIds = new Set();
  const surfaceById = new Map();
  for (const [index, surface] of surfaces.entries()) {
    const path = `surfaces[${index}]`;
    if (!isObject(surface)) {
      error('E_SURFACE_OBJECT', path);
      continue;
    }
    if (!hasText(surface.surfaceId)) {
      error('E_SURFACE_ID', `${path}.surfaceId`);
    } else if (surfaceIds.has(surface.surfaceId)) {
      error('E_DUPLICATE_SURFACE_ID', `${path}.surfaceId`);
    } else {
      surfaceIds.add(surface.surfaceId);
      surfaceById.set(surface.surfaceId, surface);
    }
    if (!ENUMS.surfaceKind.includes(surface.kind)) {
      error('E_SURFACE_KIND', `${path}.kind`);
    }
    if (!hasText(surface.rootSelector)) {
      error('E_SURFACE_SELECTOR', `${path}.rootSelector`);
    }
    if (!hasText(surface.activeSelector)) {
      error('E_SURFACE_SELECTOR', `${path}.activeSelector`);
    }
    for (const key of ['parentSurfaceIds', 'exitActions', 'exitGuardSurfaceIds', 'snapshotSelectors']) {
      if (!Array.isArray(surface[key])) error('E_SURFACE_OBJECT', `${path}.${key}`);
    }
    for (const [selectorIndex, selector] of asArray(surface.snapshotSelectors).entries()) {
      if (!hasText(selector)) error('E_SURFACE_SELECTOR', `${path}.snapshotSelectors[${selectorIndex}]`);
    }
    for (const [actionIndex, action] of asArray(surface.exitActions).entries()) {
      if (!isObject(action) || !ENUMS.actionType.includes(action.type) || !hasText(action.selector)) {
        error('E_STATE_ACTION', `${path}.exitActions[${actionIndex}]`);
      }
    }
    if ((surface.kind === 'drawer' || surface.kind === 'modal') &&
        asArray(surface.exitActions).length === 0) {
      error('E_SURFACE_EXIT_ACTIONS_REQUIRED', `${path}.exitActions`);
    }
    if (surface.kind === 'view' && asArray(surface.parentSurfaceIds).length > 0) {
      error('E_SURFACE_PARENT_INVALID', `${path}.parentSurfaceIds`);
    }
  }

  for (const [index, surface] of surfaces.entries()) {
    if (!isObject(surface)) continue;
    const path = `surfaces[${index}]`;
    for (const [parentIndex, parentId] of asArray(surface.parentSurfaceIds).entries()) {
      if (!hasText(parentId) || !surfaceById.has(parentId)) {
        error('E_SURFACE_PARENT_UNKNOWN', `${path}.parentSurfaceIds[${parentIndex}]`);
      }
    }
    for (const [guardIndex, guardId] of asArray(surface.exitGuardSurfaceIds).entries()) {
      const guardPath = `${path}.exitGuardSurfaceIds[${guardIndex}]`;
      const guard = surfaceById.get(guardId);
      if (!guard) {
        error('E_SURFACE_GUARD_UNKNOWN', guardPath);
      } else if (guard.kind !== 'modal' || !asArray(guard.parentSurfaceIds).includes(surface.surfaceId)) {
        error('E_SURFACE_GUARD_INVALID', guardPath);
      }
    }
  }
  const cyclicSurfaceIds = findSurfaceCycles(surfaces);
  for (const [index, surface] of surfaces.entries()) {
    if (isObject(surface) && cyclicSurfaceIds.has(surface.surfaceId)) {
      error('E_SURFACE_PARENT_CYCLE', `surfaces[${index}].parentSurfaceIds`);
    }
  }

  const cards = asArray(manifest.cards);
  if (cards.length === 0) error('E_CARDS_REQUIRED', 'cards');
  const cardIds = new Set();
  const displayNumbers = new Set();
  const elementAnchors = new Map();

  for (const [index, card] of cards.entries()) {
    const path = `cards[${index}]`;
    if (!isObject(card)) {
      error('E_CARD_OBJECT', path);
      continue;
    }

    for (const forbiddenPath of findForbiddenApiMappings(card, path)) {
      error('E_API_MAPPING_FORBIDDEN', forbiddenPath);
    }

    if (!hasText(card.cardId)) error('E_CARD_ID', `${path}.cardId`);
    else if (cardIds.has(card.cardId)) error('E_DUPLICATE_CARD_ID', `${path}.cardId`);
    cardIds.add(card.cardId);

    if (!hasText(card.displayNumber)) error('E_DISPLAY_NUMBER', `${path}.displayNumber`);
    else if (displayNumbers.has(card.displayNumber)) error('E_DUPLICATE_DISPLAY_NUMBER', `${path}.displayNumber`);
    displayNumbers.add(card.displayNumber);

    if (card.requirementId !== requirement.id) error('E_CARD_REQUIREMENT', `${path}.requirementId`);
    if (!pageIds.has(card.pageId)) error('E_CARD_PAGE', `${path}.pageId`);
    if (!hasText(card.surfaceId) || !surfaceById.has(card.surfaceId)) {
      error('E_CARD_SURFACE', `${path}.surfaceId`);
    }
    for (const key of ['region', 'title', 'summary']) {
      if (!hasText(card[key])) error('E_CARD_TEXT', `${path}.${key}`);
    }

    const changeKinds = asArray(card.changeKinds);
    const changeKindSet = new Set(changeKinds);
    if (changeKinds.length === 0 || changeKindSet.size !== changeKinds.length ||
        changeKinds.some((kind) => !ENUMS.changeKind.includes(kind))) {
      error('E_CHANGE_KINDS', `${path}.changeKinds`);
    }

    const changeContent = isObject(card.changeContent) ? card.changeContent : {};
    if (!isObject(card.changeContent)) error('E_CHANGE_CONTENT', `${path}.changeContent`);
    if (changeKindSet.has('add')) {
      if (Object.hasOwn(card, 'before') || Object.hasOwn(card, 'after')) {
        error('E_ADD_LEGACY_COMPARISON', path);
      }
      if (!asArray(changeContent.added).some(hasText)) error('E_ADDED_CONTENT_REQUIRED', `${path}.changeContent.added`);
      if (!hasText(changeContent.placement)) error('E_PLACEMENT_REQUIRED', `${path}.changeContent.placement`);
      if (!hasText(changeContent.finalResult)) error('E_FINAL_RESULT_REQUIRED', `${path}.changeContent.finalResult`);
      if (['preserved', 'modified', 'removed'].some((key) => Object.hasOwn(changeContent, key))) {
        error('E_ADD_CHANGE_CONTENT', `${path}.changeContent`);
      }
    }
    if ((changeKindSet.has('modify') || changeKindSet.has('delete')) && !hasText(changeContent.finalResult)) {
      error('E_FINAL_RESULT_REQUIRED', `${path}.changeContent.finalResult`);
    }

    const anchor = isObject(card.anchor) ? card.anchor : {};
    if (!ENUMS.anchorKind.includes(anchor.kind) || !hasText(anchor.selector) || !hasText(anchor.manualPath)) {
      error('E_ANCHOR', `${path}.anchor`);
    }
    const actions = asArray(anchor.enterActions);
    if (!Array.isArray(anchor.enterActions)) error('E_ENTER_ACTIONS', `${path}.anchor.enterActions`);
    for (const [actionIndex, action] of actions.entries()) {
      if (!isObject(action) || !ENUMS.actionType.includes(action.type) || !hasText(action.selector)) {
        error('E_STATE_ACTION', `${path}.anchor.enterActions[${actionIndex}]`);
      }
    }

    const isPureDelete = changeKinds.length === 1 && changeKinds[0] === 'delete';
    if (isPureDelete && anchor.kind !== 'region') {
      error('E_PURE_DELETE_REGION_ANCHOR', `${path}.anchor.kind`);
    }
    if (anchor.kind === 'element' && hasText(anchor.selector)) {
      const signature = `${card.pageId}|${JSON.stringify(actions)}|${anchor.selector}`;
      const firstPath = elementAnchors.get(signature);
      if (firstPath) error('E_COMPETING_ELEMENT_CARD', `${path}.anchor`, `conflicts with ${firstPath}`);
      else elementAnchors.set(signature, `${path}.anchor`);
    }

    const listKeys = ['fields', 'rules', 'interactions', 'errors', 'pendingItems', 'sourceRefs', 'history'];
    for (const key of listKeys) {
      if (!Array.isArray(card[key])) error('E_CARD_LIST', `${path}.${key}`);
    }

    const prdSources = asArray(card.sourceRefs).filter((source) =>
      isObject(source) && source.sourceType === 'prd' && hasText(source.file) && hasText(source.section)
    );
    if (prdSources.length === 0) error('E_PRD_SOURCE_REQUIRED', `${path}.sourceRefs`);
    for (const [sourceIndex, source] of asArray(card.sourceRefs).entries()) {
      const sourcePath = `${path}.sourceRefs[${sourceIndex}]`;
      if (!isObject(source) || !ENUMS.sourceType.includes(source.sourceType) ||
          !hasText(source.file) || !hasText(source.section)) {
        error('E_SOURCE_REF', sourcePath);
        continue;
      }
      if (/[/\\]/.test(source.file)) error('E_SOURCE_FILE_LOGICAL_NAME', `${sourcePath}.file`);
    }

    for (const [fieldIndex, field] of asArray(card.fields).entries()) {
      if (!isObject(field) || !hasText(field.name) || !hasText(field.control)) {
        error('E_FIELD', `${path}.fields[${fieldIndex}]`);
      }
    }
    for (const [interactionIndex, interaction] of asArray(card.interactions).entries()) {
      if (!isObject(interaction) ||
          ['precondition', 'action', 'feedback', 'result', 'failure'].some((key) => !hasText(interaction[key]))) {
        error('E_INTERACTION', `${path}.interactions[${interactionIndex}]`);
      }
    }
    for (const [pendingIndex, pending] of asArray(card.pendingItems).entries()) {
      if (!isObject(pending) ||
          ['question', 'owner', 'impact'].some((key) => !hasText(pending[key]))) {
        error('E_PENDING_ITEM_INCOMPLETE', `${path}.pendingItems[${pendingIndex}]`);
      }
    }
  }

  const summary = {
    pages: new Set(cards.map(({pageId} = {}) => pageId).filter(Boolean)).size,
    cards: cards.length,
    add: cards.filter(({changeKinds} = {}) => asArray(changeKinds).includes('add')).length,
    changed: cards.filter(({changeKinds} = {}) => {
      const kinds = asArray(changeKinds);
      return kinds.includes('modify') || kinds.includes('delete');
    }).length,
    criticalReuse: cards.filter(({changeKinds} = {}) => asArray(changeKinds).includes('critical-reuse')).length,
    pendingItems: cards.reduce((count, {pendingItems} = {}) => count + asArray(pendingItems).length, 0)
  };

  return {ok: errors.length === 0, errors, warnings, summary};
}

function emptySummary() {
  return {pages: 0, cards: 0, add: 0, changed: 0, criticalReuse: 0, pendingItems: 0};
}

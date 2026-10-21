export const ENUMS = Object.freeze({
  coverage: ['full', 'pilot-slice'],
  changeKind: ['add', 'modify', 'delete', 'critical-reuse'],
  anchorKind: ['element', 'region'],
  surfaceKind: ['view', 'drawer', 'modal'],
  actionType: ['click', 'assert-visible', 'assert-hidden'],
  sourceType: ['prd', 'decision', 'runtime', 'code', 'screenshot', 'prototype-baseline']
});

const hasText = (value) => typeof value === 'string' && value.trim().length > 0;
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isHash = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
const asArray = (value) => Array.isArray(value) ? value : [];
const BROAD_SELECTORS = new Set(['body', 'html', ':root', '#root', '#app']);
const PLACEHOLDER_PATTERNS = [
  /(?:^|\s)(?:todo|tbd|待补充|待完善|示例内容|默认内容|暂无说明)(?:\s|$)/i,
  /现有页面(?:控件|内容|元素)/,
  /查看或完成当前操作/,
  /根据(?:实际|具体)情况/,
  /按(?:产品|文档)要求处理/
];
const NON_SEMANTIC_SOURCE_TYPES = new Set(['screenshot', 'prototype-baseline']);
const AUTHORITATIVE_SEMANTIC_SOURCE_TYPES = new Set(['prd', 'decision']);
const SOURCE_AUTHORITIES = Object.freeze({
  prd: 'current-product-requirement',
  decision: 'confirmed-product-decision',
  runtime: 'observed-runtime',
  code: 'current-code',
  screenshot: 'same-version-screenshot',
  'prototype-baseline': 'current-prototype-baseline'
});
const LOCATABLE_GLOBAL_RULE = /(?:点击|按钮|入口|字段|控件|页面顶部|页面底部|标题区|页内|顶部|底部|抽屉|弹窗|侧边层|浮层|组件|位置|位于|悬停|hover)/i;
const GLOBAL_SCOPE_EVIDENCE = /(?:全局|所有页面|全部页面|跨页面|多页面|统一适用|系统级|系统范围)/i;

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
    surfaces.filter((surface) => isObject(surface) && hasText(surface.surfaceId))
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

function semanticPathsForCard(card, index) {
  return [
    `/cards/${index}/title`,
    `/cards/${index}/summary`,
    `/cards/${index}/changeContent`,
    ...asArray(card.fields).map((_, itemIndex) => `/cards/${index}/fields/${itemIndex}`),
    ...asArray(card.rules).map((_, itemIndex) => `/cards/${index}/rules/${itemIndex}`),
    ...asArray(card.interactions).map((_, itemIndex) => `/cards/${index}/interactions/${itemIndex}`),
    ...asArray(card.errors).map((_, itemIndex) => `/cards/${index}/errors/${itemIndex}`)
  ];
}

function semanticPathsForGlobalRule(rule, index) {
  return [
    `/globalRules/${index}/title`,
    `/globalRules/${index}/summary`,
    `/globalRules/${index}/scope`,
    `/globalRules/${index}/appliesToPageIds`,
    `/globalRules/${index}/nonLocatableReason`,
    ...asArray(rule.rules).map((_, itemIndex) => `/globalRules/${index}/rules/${itemIndex}`)
  ];
}

function validateGlobalScopeEvidence(rule, index, error) {
  const classificationPaths = new Set([
    `/globalRules/${index}/scope`,
    `/globalRules/${index}/appliesToPageIds`,
    `/globalRules/${index}/nonLocatableReason`
  ]);
  for (const binding of asArray(rule.evidenceBindings)) {
    if (!classificationPaths.has(binding?.claimPath)) continue;
    if (!asArray(binding.sourceRefs).some((ref) => GLOBAL_SCOPE_EVIDENCE.test(ref?.excerpt ?? ''))) {
      error('E_GLOBAL_RULE_SCOPE_EVIDENCE', binding.claimPath);
    }
  }
}

function normalizedSemanticValue(value) {
  return JSON.stringify(value).toLowerCase().replace(/[\s，。；：、,.!！?？"'“”‘’()[\]{}]/g, '');
}

function containsPlaceholder(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(text));
}

function validateBindings({owner, ownerPath, requiredPaths, sourceById, error}) {
  const bindings = asArray(owner.evidenceBindings);
  const boundPaths = new Set();
  for (const [bindingIndex, binding] of bindings.entries()) {
    const bindingPath = `${ownerPath}.evidenceBindings[${bindingIndex}]`;
    if (!isObject(binding) || !hasText(binding.claimPath) || !Array.isArray(binding.sourceRefs)) {
      error('E_EVIDENCE_BINDING', bindingPath);
      continue;
    }
    if (boundPaths.has(binding.claimPath)) error('E_DUPLICATE_CLAIM_BINDING', `${bindingPath}.claimPath`);
    boundPaths.add(binding.claimPath);
    if (!requiredPaths.includes(binding.claimPath)) {
      error('E_EVIDENCE_CLAIM_UNKNOWN', `${bindingPath}.claimPath`, binding.claimPath);
    }
    let hasSemanticSource = false;
    let hasAuthoritativeSource = false;
    for (const [refIndex, ref] of asArray(binding.sourceRefs).entries()) {
      const refPath = `${bindingPath}.sourceRefs[${refIndex}]`;
      const source = sourceById.get(ref?.sourceId);
      if (!source || !hasText(ref?.locator)) {
        error('E_EVIDENCE_REF', refPath);
        continue;
      }
      if (!NON_SEMANTIC_SOURCE_TYPES.has(source.sourceType)) hasSemanticSource = true;
      if (AUTHORITATIVE_SEMANTIC_SOURCE_TYPES.has(source.sourceType)) hasAuthoritativeSource = true;
      if (!hasText(ref?.excerpt) || ref.excerpt.trim().length < 4) error('E_EVIDENCE_EXCERPT_TOO_SHORT', refPath);
    }
    if (!hasSemanticSource) error('E_SEMANTIC_SOURCE_REQUIRED', bindingPath);
    if (!hasAuthoritativeSource) error('E_AUTHORITATIVE_SEMANTIC_SOURCE_REQUIRED', bindingPath);
  }
  for (const requiredPath of requiredPaths) {
    if (!boundPaths.has(requiredPath)) error('E_SEMANTIC_CLAIM_UNBOUND', requiredPath);
  }
}

function validatePendingItems(items, path, error) {
  for (const [index, pending] of asArray(items).entries()) {
    if (!isObject(pending) || ['question', 'owner', 'impact'].some((key) => !hasText(pending[key])) ||
        typeof pending?.blocksDelivery !== 'boolean') {
      error('E_PENDING_ITEM_INCOMPLETE', `${path}[${index}]`);
    }
  }
}

export function validateManifest(manifest) {
  const errors = [];
  const warnings = [];
  const error = (code, path, detail = '') => errors.push({code, path, detail});

  if (!isObject(manifest)) {
    error('E_MANIFEST_OBJECT', '$');
    return {ok: false, errors, warnings, summary: emptySummary()};
  }
  if (manifest.schemaVersion !== '3.0.0') error('E_SCHEMA_VERSION', 'schemaVersion');
  if (manifest.delivery?.mode !== 'standard' || manifest.delivery?.readerRole !== 'derived-readonly') {
    error('E_DELIVERY_ROLE', 'delivery');
  }

  const requirement = isObject(manifest.requirement) ? manifest.requirement : {};
  if (!hasText(requirement.id)) error('E_REQUIREMENT_ID', 'requirement.id');
  if (!hasText(requirement.title)) error('E_REQUIREMENT_TITLE', 'requirement.title');
  if (!ENUMS.coverage.includes(requirement.coverage)) error('E_COVERAGE', 'requirement.coverage');
  if (!hasText(requirement.primarySourceId)) error('E_REQUIREMENT_SOURCE', 'requirement.primarySourceId');

  const prototype = isObject(manifest.prototype) ? manifest.prototype : {};
  if (!hasText(prototype.sourceLabel) || !hasText(prototype.sourcePath) || !isHash(prototype.sourceHash) ||
      prototype.sourceRole !== 'current-workspace-prototype') {
    error('E_PROTOTYPE_SOURCE', 'prototype');
  }

  const evidenceSources = asArray(manifest.evidenceSources);
  const sourceById = new Map();
  for (const [index, source] of evidenceSources.entries()) {
    const path = `evidenceSources[${index}]`;
    if (!isObject(source) || !hasText(source.sourceId) || !ENUMS.sourceType.includes(source.sourceType) ||
        source.authority !== SOURCE_AUTHORITIES[source.sourceType] ||
        !hasText(source.label) || !isHash(source.sourceHash)) {
      error('E_EVIDENCE_SOURCE', path);
      continue;
    }
    if (sourceById.has(source.sourceId)) error('E_DUPLICATE_SOURCE_ID', `${path}.sourceId`);
    sourceById.set(source.sourceId, source);
  }
  const primarySource = sourceById.get(requirement.primarySourceId);
  if (!primarySource || !['prd', 'decision'].includes(primarySource.sourceType)) {
    error('E_REQUIREMENT_SOURCE', 'requirement.primarySourceId');
  }

  const baseline = isObject(manifest.baselineEvidence) ? manifest.baselineEvidence : {};
  if (baseline.mode === 'existing-system-increment') {
    const channels = ['runtimePage', 'code', 'screenshots'];
    const expectedSourceType = {runtimePage: 'runtime', code: 'code', screenshots: 'screenshot'};
    for (const channel of channels) {
      const value = baseline[channel];
      if (!isObject(value) || !['available', 'unavailable', 'not-applicable'].includes(value.availability) ||
          !Array.isArray(value.sourceIds) || !hasText(value.note)) {
        error('E_BASELINE_CHANNEL', `baselineEvidence.${channel}`);
        continue;
      }
      if (value.availability === 'available' && value.sourceIds.length === 0) {
        error('E_BASELINE_SOURCE_REQUIRED', `baselineEvidence.${channel}.sourceIds`);
      }
      for (const sourceId of value.sourceIds) {
        const source = sourceById.get(sourceId);
        if (!source) error('E_BASELINE_SOURCE_UNKNOWN', `baselineEvidence.${channel}.sourceIds`);
        else if (source.sourceType !== expectedSourceType[channel]) {
          error('E_BASELINE_SOURCE_TYPE', `baselineEvidence.${channel}.sourceIds`, `${sourceId}:${source.sourceType}`);
        }
      }
    }
    if (baseline.screenshots?.availability !== 'available') {
      error('E_BASELINE_SCREENSHOT_REQUIRED', 'baselineEvidence.screenshots');
    }
    if (baseline.runtimePage?.availability !== 'available' && baseline.code?.availability !== 'available') {
      error('E_BASELINE_PRIMARY_EVIDENCE_REQUIRED', 'baselineEvidence');
    }
    if (asArray(manifest.cards).some((card) => asArray(card.interactions).length > 0) &&
        baseline.runtimePage?.availability !== 'available') {
      error('E_RUNTIME_WALKTHROUGH_REQUIRED', 'baselineEvidence.runtimePage');
    }
    if (asArray(baseline.conflicts).length > 0) error('E_BASELINE_CONFLICT', 'baselineEvidence.conflicts');
    const baselineSource = sourceById.get(prototype.baselineSourceId);
    if (!hasText(prototype.baselineSourceId) || !isHash(prototype.baselineHash) ||
        baselineSource?.sourceType !== 'prototype-baseline' ||
        baselineSource?.sourceHash?.toLowerCase() !== prototype.baselineHash?.toLowerCase()) {
      error('E_PROTOTYPE_BASELINE_REQUIRED', 'prototype');
    }
  }

  const pages = asArray(manifest.pages);
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
  if (pages.length === 0) error('E_PAGES_REQUIRED', 'pages');

  const surfaces = asArray(manifest.surfaces);
  const surfaceById = new Map();
  for (const [index, surface] of surfaces.entries()) {
    const path = `surfaces[${index}]`;
    if (!isObject(surface) || !hasText(surface.surfaceId)) {
      error('E_SURFACE_OBJECT', path);
      continue;
    }
    if (surfaceById.has(surface.surfaceId)) error('E_DUPLICATE_SURFACE_ID', `${path}.surfaceId`);
    surfaceById.set(surface.surfaceId, surface);
    if (!ENUMS.surfaceKind.includes(surface.kind)) error('E_SURFACE_KIND', `${path}.kind`);
    for (const key of ['rootSelector', 'activeSelector']) {
      if (!hasText(surface[key])) error('E_SURFACE_SELECTOR', `${path}.${key}`);
      if (BROAD_SELECTORS.has(String(surface[key]).trim())) error('E_VIRTUAL_SURFACE_FORBIDDEN', `${path}.${key}`);
    }
    for (const key of ['parentSurfaceIds', 'exitActions', 'exitGuardSurfaceIds', 'snapshotSelectors']) {
      if (!Array.isArray(surface[key])) error('E_SURFACE_OBJECT', `${path}.${key}`);
    }
    if (!asArray(surface.snapshotSelectors).includes(surface.rootSelector)) {
      error('E_SURFACE_ROOT_SNAPSHOT_REQUIRED', `${path}.snapshotSelectors`);
    }
    if ((surface.kind === 'drawer' || surface.kind === 'modal') && asArray(surface.exitActions).length === 0) {
      error('E_SURFACE_EXIT_ACTIONS_REQUIRED', `${path}.exitActions`);
    }
    if (surface.kind === 'view' && asArray(surface.parentSurfaceIds).length > 0) {
      error('E_SURFACE_PARENT_INVALID', `${path}.parentSurfaceIds`);
    }
  }
  if (surfaces.length === 0) error('E_SURFACES_REQUIRED', 'surfaces');
  for (const [index, surface] of surfaces.entries()) {
    for (const [parentIndex, parentId] of asArray(surface?.parentSurfaceIds).entries()) {
      if (!surfaceById.has(parentId)) error('E_SURFACE_PARENT_UNKNOWN', `surfaces[${index}].parentSurfaceIds[${parentIndex}]`);
    }
  }
  const cyclicSurfaceIds = findSurfaceCycles(surfaces);
  for (const [index, surface] of surfaces.entries()) {
    if (cyclicSurfaceIds.has(surface?.surfaceId)) error('E_SURFACE_PARENT_CYCLE', `surfaces[${index}].parentSurfaceIds`);
  }

  const globalRules = asArray(manifest.globalRules);
  for (const [index, rule] of globalRules.entries()) {
    const path = `globalRules[${index}]`;
    if (!isObject(rule) || !hasText(rule.ruleId) || !hasText(rule.title) || !hasText(rule.summary) || asArray(rule.rules).length === 0) {
      error('E_GLOBAL_RULE', path);
      continue;
    }
    if (['anchor', 'surfaceId', 'pageId', 'displayNumber'].some((key) => Object.hasOwn(rule, key))) {
      error('E_GLOBAL_RULE_LOCATOR_FORBIDDEN', path);
    }
    const appliesToPageIds = asArray(rule.appliesToPageIds);
    if (!['multi-page', 'all-pages'].includes(rule.scope) || appliesToPageIds.length < 2 ||
        appliesToPageIds.some((pageId) => !pageIds.has(pageId)) ||
        (rule.scope === 'all-pages' && pages.some(({pageId}) => !appliesToPageIds.includes(pageId)))) {
      error('E_GLOBAL_RULE_SCOPE', path);
    }
    if (!['cross-page-policy', 'system-wide-constraint'].includes(rule.nonLocatableReason)) {
      error('E_GLOBAL_RULE_REASON', `${path}.nonLocatableReason`);
    }
    if (LOCATABLE_GLOBAL_RULE.test(`${rule.title} ${rule.summary} ${asArray(rule.rules).join(' ')}`)) {
      error('E_GLOBAL_RULE_LOCATABLE_SEMANTICS', path);
    }
    if (containsPlaceholder({title: rule.title, summary: rule.summary, rules: rule.rules})) {
      error('E_PLACEHOLDER_SEMANTICS', path);
    }
    validateBindings({owner: rule, ownerPath: path, requiredPaths: semanticPathsForGlobalRule(rule, index), sourceById, error});
    validateGlobalScopeEvidence(rule, index, error);
    validatePendingItems(rule.pendingItems, `${path}.pendingItems`, error);
  }

  const cards = asArray(manifest.cards);
  const cardIds = new Set();
  const displayNumbers = new Set();
  const elementAnchors = new Map();
  const semanticFingerprints = new Map();
  for (const [index, card] of cards.entries()) {
    const path = `cards[${index}]`;
    if (!isObject(card)) {
      error('E_CARD_OBJECT', path);
      continue;
    }
    for (const forbiddenPath of findForbiddenApiMappings(card, path)) error('E_API_MAPPING_FORBIDDEN', forbiddenPath);
    if (!hasText(card.cardId)) error('E_CARD_ID', `${path}.cardId`);
    else if (cardIds.has(card.cardId)) error('E_DUPLICATE_CARD_ID', `${path}.cardId`);
    cardIds.add(card.cardId);
    if (!hasText(card.displayNumber)) error('E_DISPLAY_NUMBER', `${path}.displayNumber`);
    else if (displayNumbers.has(card.displayNumber)) error('E_DUPLICATE_DISPLAY_NUMBER', `${path}.displayNumber`);
    displayNumbers.add(card.displayNumber);
    if (card.requirementId !== requirement.id) error('E_CARD_REQUIREMENT', `${path}.requirementId`);
    if (!pageIds.has(card.pageId)) error('E_CARD_PAGE', `${path}.pageId`);
    const surface = surfaceById.get(card.surfaceId);
    if (!surface) error('E_CARD_SURFACE', `${path}.surfaceId`);
    for (const key of ['region', 'title', 'summary']) if (!hasText(card[key])) error('E_CARD_TEXT', `${path}.${key}`);
    const changeKinds = asArray(card.changeKinds);
    if (changeKinds.length === 0 || new Set(changeKinds).size !== changeKinds.length ||
        changeKinds.some((kind) => !ENUMS.changeKind.includes(kind))) error('E_CHANGE_KINDS', `${path}.changeKinds`);
    if (!isObject(card.changeContent)) error('E_CHANGE_CONTENT', `${path}.changeContent`);
    if (changeKinds.includes('add')) {
      if (!asArray(card.changeContent?.added).some(hasText)) error('E_ADDED_CONTENT_REQUIRED', `${path}.changeContent.added`);
      if (!hasText(card.changeContent?.placement)) error('E_PLACEMENT_REQUIRED', `${path}.changeContent.placement`);
      if (!hasText(card.changeContent?.finalResult)) error('E_FINAL_RESULT_REQUIRED', `${path}.changeContent.finalResult`);
    }
    for (const key of ['fields', 'rules', 'interactions', 'errors', 'pendingItems', 'evidenceBindings', 'history']) {
      if (!Array.isArray(card[key])) error('E_CARD_LIST', `${path}.${key}`);
    }
    for (const [fieldIndex, field] of asArray(card.fields).entries()) {
      if (!isObject(field) || !hasText(field.name) || !hasText(field.control)) error('E_FIELD', `${path}.fields[${fieldIndex}]`);
    }
    for (const [interactionIndex, interaction] of asArray(card.interactions).entries()) {
      if (!isObject(interaction) || ['precondition', 'action', 'feedback', 'result', 'failure'].some((key) => !hasText(interaction[key]))) {
        error('E_INTERACTION', `${path}.interactions[${interactionIndex}]`);
      }
    }
    validatePendingItems(card.pendingItems, `${path}.pendingItems`, error);
    const semanticValue = {title: card.title, summary: card.summary, changeContent: card.changeContent, fields: card.fields, rules: card.rules, interactions: card.interactions, errors: card.errors};
    if (containsPlaceholder(semanticValue)) error('E_PLACEHOLDER_SEMANTICS', path);
    const fingerprint = normalizedSemanticValue(semanticValue);
    const firstSemanticPath = semanticFingerprints.get(fingerprint);
    if (firstSemanticPath) error('E_DUPLICATE_CARD_SEMANTICS', path, `duplicates ${firstSemanticPath}`);
    else semanticFingerprints.set(fingerprint, path);
    validateBindings({owner: card, ownerPath: path, requiredPaths: semanticPathsForCard(card, index), sourceById, error});

    const anchor = isObject(card.anchor) ? card.anchor : {};
    if (!ENUMS.anchorKind.includes(anchor.kind) || !hasText(anchor.selector) || !hasText(anchor.manualPath)) {
      error('E_ANCHOR', `${path}.anchor`);
    }
    if (BROAD_SELECTORS.has(String(anchor.selector).trim()) || anchor.selector === surface?.rootSelector || anchor.selector === surface?.activeSelector) {
      error('E_FAKE_ANCHOR_FORBIDDEN', `${path}.anchor.selector`);
    }
    if (!Array.isArray(anchor.enterActions)) error('E_ENTER_ACTIONS', `${path}.anchor.enterActions`);
    const isPureDelete = changeKinds.length === 1 && changeKinds[0] === 'delete';
    if (isPureDelete && anchor.kind !== 'region') error('E_PURE_DELETE_REGION_ANCHOR', `${path}.anchor.kind`);
    if (anchor.kind === 'element' && hasText(anchor.selector)) {
      const signature = `${card.pageId}|${JSON.stringify(asArray(anchor.enterActions))}|${anchor.selector}`;
      const firstPath = elementAnchors.get(signature);
      if (firstPath) error('E_COMPETING_ELEMENT_CARD', `${path}.anchor`, `conflicts with ${firstPath}`);
      else elementAnchors.set(signature, `${path}.anchor`);
    }
  }
  if (cards.length === 0) error('E_CARDS_REQUIRED', 'cards');

  const summary = {
    pages: new Set(cards.map(({pageId} = {}) => pageId).filter(Boolean)).size,
    cards: cards.length,
    globalRules: globalRules.length,
    add: cards.filter(({changeKinds} = {}) => asArray(changeKinds).includes('add')).length,
    changed: cards.filter(({changeKinds} = {}) => asArray(changeKinds).some((kind) => ['modify', 'delete'].includes(kind))).length,
    criticalReuse: cards.filter(({changeKinds} = {}) => asArray(changeKinds).includes('critical-reuse')).length,
    pendingItems: [...cards, ...globalRules].reduce((count, item = {}) => count + asArray(item.pendingItems).length, 0)
  };
  return {ok: errors.length === 0, errors, warnings, summary};
}

function emptySummary() {
  return {pages: 0, cards: 0, globalRules: 0, add: 0, changed: 0, criticalReuse: 0, pendingItems: 0};
}

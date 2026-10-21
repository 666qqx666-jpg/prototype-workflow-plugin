const KIND_LABELS = Object.freeze({
  add: '新增',
  modify: '调整',
  delete: '移除',
  'critical-reuse': '关键复用'
});

const CONTENT_LABELS = Object.freeze({
  added: '新增内容',
  placement: '页面位置',
  preserved: '保留',
  modified: '调整',
  removed: '移除',
  finalResult: '最终页面结果'
});

export function escapeHtml(value = '') {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[character]);
}

const renderValue = (value) => Array.isArray(value)
  ? `<ul>${value.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
  : `<p>${escapeHtml(value)}</p>`;

function renderChangeContent(card) {
  const rows = Object.entries(card.changeContent ?? {})
    .filter(([key, value]) => CONTENT_LABELS[key] && (
      Array.isArray(value) ? value.length > 0 : String(value ?? '').length > 0
    ))
    .map(([key, value]) => `
      <div class="requirement-row requirement-row--${escapeHtml(key)}">
        <h4>${CONTENT_LABELS[key]}</h4>
        ${renderValue(value)}
      </div>
    `)
    .join('');
  return rows ? `<section class="card-section card-section--change">${rows}</section>` : '';
}

function renderFields(fields) {
  if (!fields?.length) return '';
  const orderedKeys = [
    ['control', '控件/展示'],
    ['defaultValue', '默认值'],
    ['required', '必填'],
    ['options', '可选范围'],
    ['visibility', '显示条件'],
    ['enabledWhen', '启用条件'],
    ['validation', '校验规则'],
    ['linkage', '字段联动']
  ];
  return `<section class="card-section card-section--fields" aria-label="开发关键字段">
    <div class="section-heading">
      <span>开发关键字段</span>
      <h4>页面字段与产品规则</h4>
    </div>
    <p class="field-name-summary" data-key-field-summary><b>本卡字段：</b>${fields.map(({name}) => escapeHtml(name)).join('、')}</p>
    <div class="field-list">${
    fields.map((field, index) => `<article class="field-item"><h5><span>字段 ${index + 1}</span>${escapeHtml(field.name)}</h5><dl>${
      orderedKeys
        .filter(([key]) => field[key] !== undefined && field[key] !== '')
        .map(([key, label]) => {
          const raw = field[key];
          const value = key === 'required'
            ? (raw ? '是' : '否')
            : (Array.isArray(raw) ? raw.join('、') : raw);
          return `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`;
        })
        .join('')
    }</dl></article>`).join('')
  }</div></section>`;
}

function renderRules(rules) {
  if (!rules?.length) return '';
  return `<section class="card-section"><h4>产品规则</h4><ul>${
    rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join('')
  }</ul></section>`;
}

function renderInteractions(interactions) {
  if (!interactions?.length) return '';
  const labels = [
    ['precondition', '前置状态'],
    ['action', '用户动作'],
    ['feedback', '页面反馈'],
    ['result', '结果状态'],
    ['failure', '失败反馈']
  ];
  return `<section class="card-section"><h4>交互说明</h4><ol class="interaction-list">${
    interactions.map((interaction) => `<li><dl>${
      labels.map(([key, label]) => `<div><dt>${label}</dt><dd>${escapeHtml(interaction[key])}</dd></div>`).join('')
    }</dl></li>`).join('')
  }</ol></section>`;
}

function renderErrors(errors) {
  if (!errors?.length) return '';
  return `<section class="card-section"><h4>异常与反馈</h4><div class="error-list">${
    errors.map((error) => `<article><p><b>触发：</b>${escapeHtml(error.trigger)}</p><p><b>反馈：</b>${escapeHtml(error.feedback)}</p><p><b>结果：</b>${escapeHtml(error.result)}</p></article>`).join('')
  }</div></section>`;
}

function renderPending(items) {
  if (!items?.length) return '';
  return `<section class="card-section"><h4>待确认项</h4><ul>${
    items.map((item) => `<li>${escapeHtml(item.question)}（责任：${escapeHtml(item.owner)}；影响：${escapeHtml(item.impact)}）</li>`).join('')
  }</ul></section>`;
}

function renderSources(bindings, evidenceSources, sourceId, state) {
  const sourceById = new Map(evidenceSources.map((source) => [source.sourceId, source]));
  const sources = bindings.flatMap(({claimPath, sourceRefs}) => sourceRefs.map((reference) => ({
    ...reference,
    claimPath,
    source: sourceById.get(reference.sourceId)
  })));
  const expanded = state.expandedSourceIds?.has(sourceId) ?? false;
  return `<section class="card-sources" aria-label="事实来源"><button type="button" class="source-toggle" data-source-toggle="${escapeHtml(sourceId)}" aria-expanded="${expanded}">事实来源 · ${expanded ? '收起详情' : '查看详情'}</button><ul ${expanded ? '' : 'hidden'}>${
    sources.map(({claimPath, locator, source}) => `<li>${escapeHtml(source?.label ?? '未知来源')} · ${escapeHtml(locator)} <small>${escapeHtml(claimPath)}</small></li>`).join('')
  }</ul></section>`;
}

function renderHistory(history) {
  if (!history?.length) return '';
  return `<details class="card-history"><summary>变更沿革</summary>${
    history.map((item) => `<p><b>${escapeHtml(item.requirementId)}</b>：${escapeHtml(item.summary)}</p>`).join('')
  }</details>`;
}

function renderCard(card, pageTitle, state, hasFocus, evidenceSources) {
  const focused = state.focusCardId === card.cardId;
  const expanded = focused || state.expandedCardIds.has(card.cardId);
  const labels = card.changeKinds.map((kind) => `<span class="kind-tag kind-tag--${kind}">${KIND_LABELS[kind]}</span>`).join('');
  return `
    <article
      class="element-card${focused ? ' element-card--focused' : ''}${hasFocus && !focused ? ' element-card--dimmed' : ''}"
      id="card-${escapeHtml(card.cardId)}"
      data-card-id="${escapeHtml(card.cardId)}"
      data-focused="${focused}"
      data-expanded="${expanded}"
    >
      <button class="card-summary" type="button" data-card-trigger="${escapeHtml(card.cardId)}">
        <span class="card-number">${escapeHtml(card.displayNumber)}</span>
        <span class="card-summary-main">
          <span class="card-kicker">${escapeHtml(pageTitle)} · ${escapeHtml(card.region)}</span>
          <strong>${escapeHtml(card.title)}</strong>
          <span class="card-tags">${labels}</span>
          <span class="card-result">${escapeHtml(card.summary)}</span>
        </span>
        ${focused ? '<span class="focus-label" data-focus-label>当前定位</span>' : ''}
      </button>
      <button
        class="card-toggle"
        type="button"
        data-card-toggle="${escapeHtml(card.cardId)}"
        aria-expanded="${expanded}"
        aria-label="${expanded ? '收起卡片' : '展开卡片'}"
      >${expanded ? '收起' : '展开'}</button>
      <div class="card-body" data-card-body ${expanded ? '' : 'hidden'}>
        ${renderChangeContent(card)}
        ${renderFields(card.fields)}
        ${renderRules(card.rules)}
        ${renderInteractions(card.interactions)}
        ${renderErrors(card.errors)}
        ${renderPending(card.pendingItems)}
        ${renderSources(card.evidenceBindings, evidenceSources, `card:${card.cardId}`, state)}
        ${renderHistory(card.history)}
      </div>
    </article>
  `;
}

function renderGlobalRules(globalRules, evidenceSources, state) {
  if (!globalRules?.length) return '';
  return `<section class="global-rules" aria-label="全局规则（不定位）">
    <header><h2>全局规则</h2><span>不绑定页面编号、锚点或连线</span></header>
    ${globalRules.map((rule) => `<article class="global-rule">
      <h3>${escapeHtml(rule.title)}</h3>
      <p>${escapeHtml(rule.summary)}</p>
      <ul>${rule.rules.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
      ${renderSources(rule.evidenceBindings, evidenceSources, `global:${rule.ruleId}`, state)}
    </article>`).join('')}
  </section>`;
}

export function renderReader(manifest, summary, state) {
  const hasFocus = state.focusCardId !== null;
  const isFullCoverage = manifest.requirement.coverage === 'full';
  const scope = manifest.requirement.scope;
  const coverageNote = isFullCoverage
    ? `完整需求快照：${manifest.pages.length} 个页面分组与 ${summary.cards} 张元素卡已纳入交付。`
    : `当前为代表性切片，覆盖 ${manifest.pages.length} 个交付页面分组，不代表完整 PRD 页面范围。`;
  const scopeDetails = scope ? `
    <div class="reader-quick-guide" aria-label="交付范围">
      <p><b>已纳入</b><span>${escapeHtml(scope.included.join('、') || '无')}</span></p>
      <p><b>仅导航</b><span>${escapeHtml(scope.navigationOnly.join('、') || '无')}</span></p>
      <p><b>切片外</b><span>${escapeHtml(scope.outOfSlice.join('、') || '无')}</span></p>
    </div>
  ` : '';
  const pageSections = [...manifest.pages]
    .sort((left, right) => left.order - right.order)
    .map((page) => {
      const cards = manifest.cards.filter((card) => card.pageId === page.pageId);
      return `<section class="page-group" id="page-${escapeHtml(page.pageId)}" data-page-id="${escapeHtml(page.pageId)}">
        <header class="page-group-header"><h2>${escapeHtml(page.title)}</h2><span>${cards.length} 张元素卡</span></header>
        ${cards.map((card) => renderCard(card, page.title, state, hasFocus, manifest.evidenceSources)).join('')}
      </section>`;
    })
    .join('');

  return `
    <div class="reader-toolbar">
      <button type="button" data-panel-toggle aria-label="${state.panelCollapsed ? '展开开发说明' : '收起开发说明'}">☰</button>
      <button type="button" data-return-prototype ${state.returnPoint ? '' : 'disabled'}>返回上一个原型状态</button>
    </div>
    <div class="reader-content" data-reader-content>
      <header class="reader-header">
        <p class="eyebrow">${isFullCoverage ? '开发阅读完整快照' : '开发阅读代表性切片'}</p>
        <h1>${escapeHtml(manifest.requirement.title)}</h1>
        <p>把 PRD 中与当前页面元素直接相关的字段、规则和交互放到原型旁边。</p>
        <p class="coverage-note">${escapeHtml(coverageNote)}</p>
        ${scopeDetails}
        <div class="reader-quick-guide" aria-label="阅读范围与定位方法">
          <p><b>阅读范围</b><span>本页说明页面字段、产品规则、交互和异常；技术实现请查对应技术方案。</span></p>
          <p><b>定位方法</b><span>点击右侧卡片定位左侧原型；点击左侧编号返回卡片；顶部按钮可返回进入定位前的状态。</span></p>
        </div>
        <div class="overview-counts" aria-label="需求总览">
          <span><b>${summary.cards}</b> 张元素卡</span>
          <span><b>${summary.add}</b> 个新增</span>
          <span><b>${summary.changed}</b> 个现有能力变更</span>
          <span><b>${summary.criticalReuse}</b> 个关键复用</span>
          <span><b>${summary.globalRules}</b> 条全局规则</span>
        </div>
        <nav class="page-index">${manifest.pages.map((page) => `<a href="#page-${escapeHtml(page.pageId)}">${escapeHtml(page.title)}</a>`).join('')}</nav>
      </header>
      ${state.navigationError ? `<div class="navigation-error" role="alert">${escapeHtml(state.navigationError)}</div>` : ''}
      ${renderGlobalRules(manifest.globalRules, manifest.evidenceSources, state)}
      ${pageSections}
    </div>
  `;
}

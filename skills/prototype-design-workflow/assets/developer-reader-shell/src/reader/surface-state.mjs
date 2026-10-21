const ACTIVE_OVERLAY_SELECTOR = [
  '.drawer-mask.show',
  '.modal-mask.show',
  '[class*="drawer"][class*="show"]',
  '[class*="modal"][class*="show"]',
  '[role="dialog"][aria-modal="true"]'
].join(',');

function queryAll(doc, selector) {
  try {
    return {ok: true, matches: [...doc.querySelectorAll(selector)]};
  } catch {
    return {ok: false, matches: []};
  }
}

function isVisible(element) {
  if (!element || element.hidden) return false;
  const style = element.ownerDocument?.defaultView?.getComputedStyle?.(element) ??
    element.document?.defaultView?.getComputedStyle?.(element);
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
  if (typeof element.getClientRects === 'function') {
    return element.getClientRects().length > 0;
  }
  return element.display !== 'none' && element.visibility !== 'hidden';
}

export function createSurfaceIndex(surfaces) {
  return new Map((surfaces ?? []).map((surface) => [surface.surfaceId, surface]));
}

export function resolveActiveSurfacePath(doc, surfaces) {
  const active = [];
  for (const surface of surfaces ?? []) {
    const queried = queryAll(doc, surface.activeSelector);
    if (!queried.ok || queried.matches.length > 1) {
      return {
        ok: false,
        code: 'E_SURFACE_AMBIGUOUS',
        path: [],
        surfaceId: surface.surfaceId
      };
    }
    if (queried.matches.length === 1) active.push(surface);
  }

  const views = active.filter(({kind}) => kind === 'view');
  if (views.length !== 1) {
    return {ok: false, code: 'E_SURFACE_AMBIGUOUS', path: []};
  }

  const path = [views[0]];
  const remaining = new Map(
    active
      .filter(({kind}) => kind !== 'view')
      .map((surface) => [surface.surfaceId, surface])
  );

  while (true) {
    const parentId = path.at(-1).surfaceId;
    const children = [...remaining.values()]
      .filter((surface) => surface.parentSurfaceIds.includes(parentId));
    if (children.length > 1) {
      return {ok: false, code: 'E_SURFACE_AMBIGUOUS', path};
    }
    if (children.length === 0) break;
    path.push(children[0]);
    remaining.delete(children[0].surfaceId);
  }

  if (remaining.size > 0) {
    return {ok: false, code: 'E_SURFACE_PARENT_INVALID', path};
  }
  return {ok: true, path, top: path.at(-1)};
}

export function resolveTargetSurfacePath(surfaceId, surfaceIndex, currentPath = []) {
  function build(id, visiting) {
    if (visiting.has(id)) return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};
    const surface = surfaceIndex.get(id);
    if (!surface) return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};
    const nextVisiting = new Set(visiting);
    nextVisiting.add(id);

    if (surface.kind === 'view') {
      if ((surface.parentSurfaceIds ?? []).length > 0) {
        return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};
      }
      return {ok: true, path: [surface]};
    }

    const parentIds = surface.parentSurfaceIds ?? [];
    let parentId = null;
    if (parentIds.length === 1) {
      [parentId] = parentIds;
    } else if (parentIds.length > 1) {
      const currentIds = currentPath.map(({surfaceId: currentId}) => currentId);
      parentId = [...currentIds].reverse().find((currentId) => parentIds.includes(currentId)) ?? null;
    }
    if (!parentId) return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};

    const parent = build(parentId, nextVisiting);
    if (!parent.ok || parent.path[0]?.kind !== 'view') {
      return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};
    }
    return {ok: true, path: [...parent.path, surface]};
  }

  return build(surfaceId, new Set());
}

export function planSurfaceTransition(currentPath, targetPath) {
  let commonLength = 0;
  while (
    commonLength < currentPath.length &&
    commonLength < targetPath.length &&
    currentPath[commonLength].surfaceId === targetPath[commonLength].surfaceId
  ) {
    commonLength += 1;
  }

  return {
    sameTop: currentPath.at(-1)?.surfaceId === targetPath.at(-1)?.surfaceId,
    commonPath: targetPath.slice(0, commonLength),
    exits: currentPath.slice(commonLength).reverse(),
    enters: targetPath.slice(commonLength)
  };
}

export function cardsForSurface(cards, surfaceId) {
  return (cards ?? []).filter((card) => card.surfaceId === surfaceId);
}

export function validateAnchorInSurface(doc, card, surface) {
  const rootQuery = queryAll(doc, surface.rootSelector);
  if (!rootQuery.ok || rootQuery.matches.length !== 1) {
    return {ok: false, code: 'E_SURFACE_PARENT_INVALID'};
  }
  const anchorQuery = queryAll(doc, card.anchor.selector);
  if (!anchorQuery.ok) return {ok: false, code: 'E_ANCHOR_SELECTOR_INVALID'};
  if (anchorQuery.matches.length !== 1) {
    return {
      ok: false,
      code: anchorQuery.matches.length === 0 ? 'E_ANCHOR_MISSING' : 'E_ANCHOR_AMBIGUOUS'
    };
  }
  if (!rootQuery.matches[0].contains(anchorQuery.matches[0])) {
    return {ok: false, code: 'E_ANCHOR_OUTSIDE_SURFACE'};
  }
  return {
    ok: true,
    root: rootQuery.matches[0],
    element: anchorQuery.matches[0]
  };
}

export function findUnknownOverlay(doc, surfaces) {
  const overlays = queryAll(doc, ACTIVE_OVERLAY_SELECTOR);
  if (!overlays.ok) return null;

  const registeredRoots = [];
  for (const surface of surfaces ?? []) {
    if (surface.kind === 'view') continue;
    const active = queryAll(doc, surface.activeSelector);
    if (!active.ok || active.matches.length !== 1) continue;
    const root = queryAll(doc, surface.rootSelector);
    if (root.ok && root.matches.length === 1) registeredRoots.push(root.matches[0]);
  }

  for (const overlay of overlays.matches) {
    if (!isVisible(overlay)) continue;
    const represented = registeredRoots.some((root) =>
      overlay === root || overlay.contains?.(root) || root.contains?.(overlay)
    );
    if (!represented) return overlay;
  }
  return null;
}

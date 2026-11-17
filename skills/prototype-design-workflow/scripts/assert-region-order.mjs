// Both regions are sampled in the same document and browser task, even when
// one is below the fold. The caller chooses the business state and container.
export async function assertRegionSize(locator, expected) {
  const dimensions = Object.entries(expected);
  if (!dimensions.length || dimensions.some(([axis, value]) =>
    !['width', 'height'].includes(axis) || !Number.isFinite(value) || value <= 0)) {
    throw new Error('Expected a positive CSS pixel width or height');
  }
  if (await locator.count() !== 1 || !await locator.isVisible()) throw new Error('Expected one visible region');
  const box = await locator.boundingBox();
  for (const [axis, value] of dimensions) {
    if (!box || Math.abs(box[axis] - value) > 0.5) {
      throw new Error(`Expected ${axis} ${value}px, measured ${box?.[axis]}px`);
    }
  }
  return box;
}

export async function assertRegionOrder(container, {first, second, order}) {
  if (!['above', 'left-of'].includes(order)) {
    throw new Error(`Unsupported region order: ${order}`);
  }
  const boxes = await container.evaluate((root, selectors) => selectors.map(selector => {
    const matches = root.querySelectorAll(selector);
    if (matches.length !== 1) {
      throw new Error(`Expected one region for ${selector}, found ${matches.length}`);
    }
    const element = matches[0];
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || !element.checkVisibility({opacityProperty: true, visibilityProperty: true})) {
      throw new Error(`Region is hidden: ${selector}`);
    }
    return rect.toJSON();
  }), [first, second]);
  const [a, b] = boxes;
  const inOrder = order === 'above' ? a.bottom <= b.top : a.right <= b.left;
  if (!inOrder) throw new Error(`Expected ${first} ${order} ${second}`);
  return boxes;
}

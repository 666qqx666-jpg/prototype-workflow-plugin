export function toParentRect(frameRect, innerRect) {
  return {
    left: frameRect.left + innerRect.left,
    right: frameRect.left + innerRect.right,
    top: frameRect.top + innerRect.top,
    bottom: frameRect.top + innerRect.bottom,
    width: innerRect.width,
    height: innerRect.height
  };
}

export function connectorPath(fromRect, toRect) {
  const startX = fromRect.right;
  const startY = fromRect.top + fromRect.height / 2;
  const endX = toRect.left;
  const endY = toRect.top + Math.min(34, toRect.height / 2);
  const gap = endX - startX;
  const curve = Math.max(28, Math.abs(gap) * 0.42);
  const firstControlX = gap >= 0 ? startX + curve : startX + 28;
  const secondControlX = gap >= 0 ? endX - curve : endX - 28;
  return `M ${round(startX)} ${round(startY)} C ${round(firstControlX)} ${round(startY)}, ${round(secondControlX)} ${round(endY)}, ${round(endX)} ${round(endY)}`;
}

const round = (number) => Math.round(number * 100) / 100;

export function createConnector({
  svg,
  path,
  frame,
  reader,
  windowObject = window,
  ResizeObserverClass = window.ResizeObserver
}) {
  let target = null;
  let card = null;
  let frameRequest = null;
  let destroyed = false;
  let frameWindow = null;

  const draw = () => {
    frameRequest = null;
    if (destroyed || !target || !card || svg.hasAttribute?.('hidden')) return hide();
    const innerRect = target.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    if ((innerRect.width === 0 && innerRect.height === 0) || (cardRect.width === 0 && cardRect.height === 0)) {
      return hide();
    }
    const targetRect = toParentRect(frame.getBoundingClientRect(), innerRect);
    path.setAttribute('d', connectorPath(targetRect, cardRect));
    path.dataset.visible = 'true';
    path.style.visibility = 'visible';
  };

  const schedule = () => {
    if (destroyed || frameRequest !== null) return;
    frameRequest = windowObject.requestAnimationFrame(draw);
  };

  const hide = () => {
    path.removeAttribute('d');
    path.dataset.visible = 'false';
    path.style.visibility = 'hidden';
  };

  const observer = ResizeObserverClass ? new ResizeObserverClass(schedule) : null;
  const bindFrameScroll = () => {
    if (frameWindow === frame.contentWindow) return;
    frameWindow?.removeEventListener?.('scroll', schedule);
    frameWindow = frame.contentWindow;
    frameWindow?.addEventListener?.('scroll', schedule, {passive: true});
  };

  windowObject.addEventListener('resize', schedule, {passive: true});
  reader.addEventListener('scroll', schedule, {passive: true});
  frame.addEventListener('load', bindFrameScroll);
  bindFrameScroll();

  return {
    connect(nextTarget, nextCard) {
      target = nextTarget;
      card = nextCard;
      observer?.disconnect();
      observer?.observe(target);
      observer?.observe(card);
      bindFrameScroll();
      schedule();
    },
    schedule,
    hide,
    destroy() {
      destroyed = true;
      if (frameRequest !== null) windowObject.cancelAnimationFrame(frameRequest);
      observer?.disconnect();
      frameWindow?.removeEventListener?.('scroll', schedule);
      windowObject.removeEventListener('resize', schedule);
      reader.removeEventListener('scroll', schedule);
      frame.removeEventListener('load', bindFrameScroll);
      hide();
    }
  };
}

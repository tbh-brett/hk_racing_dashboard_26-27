/* overlay.js — the one anchored-panel and flyout implementation.
 *
 * Design brief 09 §1 diagnosed a feedback loop in the previous build: a hover
 * panel positioned inside the trigger row's flow changed layout, which moved
 * content under the cursor, which took the cursor off the trigger, which closed
 * the panel, which reverted the layout -- at speed, a visible vibration. It got
 * worse further down the page because the position maths mixed scroll offset
 * inconsistently.
 *
 * Two properties break that loop at the source, and both are structural rather
 * than defensive:
 *
 *   1. The panel is appended to document.body and positioned with position:
 *      fixed against viewport coordinates. It is not in the row's flow, so it
 *      cannot move the row -- there is nothing to shift under the cursor. Using
 *      viewport coordinates also removes scroll offset from the maths entirely,
 *      which is what made the old bug worsen as you scrolled.
 *
 *   2. Position is computed once on open, from mouseenter/mouseleave. No
 *      mousemove polling, so no per-frame recalculation to drift.
 *
 * The brief asks for this to exist once and be reused everywhere -- the
 * condition-fit panel, sparkline detail, the notes popover. Reimplementing it
 * per page is how the bug comes back.
 */

const MARGIN = 8;      // keep this clear of every viewport edge
const MIN_BELOW = 140; // below this, prefer flipping above the trigger
const GAP = 6;         // trigger-to-panel gap

/** Place a panel of size `size` against `rect`, staying inside the viewport. */
export function place(rect, size) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Right-align to the trigger, then clamp both edges.
  let left = rect.right - size.width;
  if (left < MARGIN) left = MARGIN;
  if (left + size.width > vw - MARGIN) {
    left = Math.max(MARGIN, vw - MARGIN - size.width);
  }

  const spaceBelow = vh - rect.bottom - MARGIN;
  const spaceAbove = rect.top - MARGIN;

  // Prefer below; flip above only when below is genuinely cramped and above
  // is roomier. Either way cap the height so the panel scrolls internally
  // rather than being clipped or pushed off-screen.
  let top;
  let maxHeight;
  if (spaceBelow >= MIN_BELOW || spaceBelow >= spaceAbove) {
    top = rect.bottom + GAP;
    maxHeight = Math.max(80, vh - top - MARGIN);
  } else {
    maxHeight = Math.max(80, spaceAbove - GAP);
    top = Math.max(MARGIN, rect.top - GAP - maxHeight);
  }
  return { left, top, maxHeight };
}

/**
 * Attach a hover panel to `trigger`.
 * `render` returns the panel's element; it is built once and reused.
 * Returns a teardown function.
 */
export function anchoredPanel(trigger, render, { className = 'panel' } = {}) {
  let el = null;
  let open = false;

  const show = () => {
    if (open) return;
    open = true;
    el = el || render();
    el.className = className;
    el.style.position = 'fixed';
    el.style.visibility = 'hidden';
    el.style.zIndex = 'var(--z-panel)';
    document.body.appendChild(el);          // document root, never the row

    const pos = place(trigger.getBoundingClientRect(), {
      width: el.offsetWidth,
      height: el.offsetHeight,
    });
    el.style.left = `${pos.left}px`;
    el.style.top = `${pos.top}px`;
    el.style.maxHeight = `${pos.maxHeight}px`;
    el.style.overflowY = 'auto';
    el.style.visibility = 'visible';
  };

  const hide = () => {
    if (!open) return;
    open = false;
    if (el && el.parentNode) el.parentNode.removeChild(el);
  };

  trigger.addEventListener('mouseenter', show);
  trigger.addEventListener('mouseleave', hide);
  // A scroll invalidates a position computed against the old viewport.
  // Close rather than chase it -- reopening is one mouseenter away.
  window.addEventListener('scroll', hide, { passive: true });

  return () => {
    hide();
    trigger.removeEventListener('mouseenter', show);
    trigger.removeEventListener('mouseleave', hide);
    window.removeEventListener('scroll', hide);
  };
}

/**
 * A flyout that floats over the page without moving it (brief 10).
 *
 * The table underneath must not resize, reflow, or shift to make room, and the
 * scrim is deliberately semi-transparent: the point of a non-modal overlay is
 * watching the row count change underneath while a filter is adjusted.
 *
 * Dismissal is by scrim click, Escape, or an explicit close control -- all
 * three, because the brief requires no single method be mandatory.
 */
export function flyout(panelEl, { onClose = () => {} } = {}) {
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  Object.assign(scrim.style, {
    position: 'fixed',
    inset: '0',
    zIndex: 'var(--z-scrim)',
  });

  const close = () => {
    document.removeEventListener('keydown', onKey);
    scrim.remove();
    panelEl.remove();
    onClose();
  };

  const onKey = (e) => { if (e.key === 'Escape') close(); };

  scrim.addEventListener('click', close);
  document.addEventListener('keydown', onKey);

  panelEl.style.position = 'fixed';
  panelEl.style.zIndex = 'var(--z-panel)';
  panelEl.style.boxShadow = 'var(--shadow-panel)';

  document.body.appendChild(scrim);
  document.body.appendChild(panelEl);

  return close;
}

/* replay.js — HKJC's player, inside the page, wherever a run can be watched.
 *
 * The Results page embedded the player and every other ▶ in the dashboard
 * opened a new tab: the Form Guide's runs and trials, the Trials page, Lookup.
 * Owner, Oct 2026: the in-page player is the one worth having, everywhere.
 *
 * TWO FORMS OF ONE PLAYER. Results keeps its frame in the page, above the
 * finishing order, because the race is what that page is about. Everywhere
 * else a ▶ is one control on one row among dozens, and opening the player
 * inside the row would shove every row under it down the page — so the player
 * floats over the page instead. It stays put while the reader scrolls on, and
 * the next ▶ re-points it rather than opening a second.
 *
 * IT OPENS IN THE MIDDLE AND GOES WHERE IT IS DRAGGED. Pinned to a corner it
 * covered whatever was in that corner, with no way to move it off. The header
 * is the handle; where the reader leaves it is remembered, and a double-click
 * on the header puts it back in the middle.
 *
 * A ▶ IS STILL A LINK TO HKJC. Clicked plainly it plays here; middle-clicked
 * or with a modifier held, the browser does what it always did and opens a
 * tab. Nothing is stored or scraped: the URL comes from `replayUrl` or
 * `trialReplayUrl`, the only places it is built.
 *
 * ON A PHONE IT IS STILL A TAB. These pages are wider than a phone, and a
 * phone answers by laying the page out at its full width and showing a
 * window onto part of it — the Trials page measured 1082 × 2343 behind a
 * 375 × 812 screen — so a player fixed to the window lands a thousand pixels
 * from anything the reader can see. Where the screen shows less than the
 * page's width, the link is left to open HKJC's own page.
 */
import { el, externalLink } from './vocab.js';

/** HKJC's player in a frame sized to it (replay.css). Results builds its
 *  in-page player from this too, so there is one frame, not two. */
export function playerFrame(url, title) {
  const box = el('div', 'replay-frame');
  const iframe = document.createElement('iframe');
  iframe.src = url;
  iframe.dataset.src = url;
  iframe.title = title;
  iframe.allow = 'autoplay; fullscreen';
  iframe.allowFullscreen = true;
  iframe.loading = 'lazy';
  box.append(iframe);
  return box;
}

/* ── the dock ────────────────────────────────────────────────────────────── */

// Small or large, and where it was dragged to, remembered per browser:
// whoever wants it large and out of the way wants that on the next replay too.
const SIZE_KEY = 'hkrd:replay-dock';
const PLACE_KEY = 'hkrd:replay-dock-at';

function loadLarge() {
  try {
    return localStorage.getItem(SIZE_KEY) === 'large';
  } catch {
    return false;       // site data blocked: the dock simply starts small
  }
}

function saveLarge(large) {
  try {
    localStorage.setItem(SIZE_KEY, large ? 'large' : 'small');
  } catch { /* nothing to remember on is not a failure */ }
}

/** Where the reader left it, or null for the middle of the window. */
function loadPlace() {
  try {
    const at = JSON.parse(localStorage.getItem(PLACE_KEY) ?? 'null');
    return at && Number.isFinite(at.left) && Number.isFinite(at.top) ? at : null;
  } catch {
    return null;        // blocked or unreadable: the middle
  }
}

function savePlace(at) {
  try {
    if (at) localStorage.setItem(PLACE_KEY, JSON.stringify(at));
    else localStorage.removeItem(PLACE_KEY);
  } catch { /* nothing to remember on is not a failure */ }
}

let dock = null;        // built on first use, then reused

/** Put the dock at `at`, or in the middle of the window when there is none.
 *  Held inside the window either way: a size change or a smaller window must
 *  never strand the header — the only handle it has — off the screen. */
function placeDock(at) {
  dock.classList.toggle('placed', !!at);
  if (!at) {
    dock.style.left = '';
    dock.style.top = '';
    return;
  }
  const r = dock.getBoundingClientRect();
  const left = Math.min(Math.max(0, at.left), Math.max(0, window.innerWidth - r.width));
  const top = Math.min(Math.max(0, at.top), Math.max(0, window.innerHeight - r.height));
  dock.style.left = `${left}px`;
  dock.style.top = `${top}px`;
}

/** The header is the handle. Its links and buttons stay links and buttons. */
function dragBy(head) {
  head.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('a, button')) return;
    e.preventDefault();
    const r = dock.getBoundingClientRect();
    const dx = e.clientX - r.left;
    const dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    // The frame is another site's page: a pointer that crosses it mid-drag is
    // HKJC's, and the drag would stop dead under it.
    dock.classList.add('dragging');
    const stop = new AbortController();
    const drop = () => {
      stop.abort();
      dock.classList.remove('dragging');
      if (dock.classList.contains('placed')) {
        savePlace({ left: parseFloat(dock.style.left), top: parseFloat(dock.style.top) });
      }
    };
    head.addEventListener('pointermove',
      (m) => placeDock({ left: m.clientX - dx, top: m.clientY - dy }),
      { signal: stop.signal });
    head.addEventListener('pointerup', drop, { signal: stop.signal });
    head.addEventListener('pointercancel', drop, { signal: stop.signal });
  });
  head.addEventListener('dblclick', (e) => {
    if (e.target.closest('a, button')) return;
    savePlace(null);
    placeDock(null);
  });
}

/** Close the dock. Emptied, not just hidden: a hidden frame keeps playing the
 *  sound. */
export function closeReplay() {
  if (!dock) return;
  dock.hidden = true;
  dock.replaceChildren();
}

/** Play `url` in the dock, headed `label`. The same video again is left
 *  playing rather than restarted under the reader's eyes. */
export function playReplay(url, label) {
  if (!dock) {
    dock = el('aside', 'replay-dock');
    dock.setAttribute('aria-label', 'replay');
    document.body.append(dock);
    // A window made smaller pulls the dock back into it; made larger again,
    // the dock returns to where the reader put it.
    window.addEventListener('resize', () => {
      if (!dock.hidden) placeDock(loadPlace());
    });
  }
  const frame = dock.querySelector('iframe');
  if (!dock.hidden && frame && frame.dataset.src === url) return;
  dock.classList.toggle('large', loadLarge());

  const head = el('div', 'replay-hd');
  head.title = 'drag to move · double-click to put it back in the middle';
  head.append(el('span', 't', label.toUpperCase()));
  const out = externalLink(url, 'OPEN ON HKJC ↗', 'replay-out');
  out.title = 'the same replay in its own tab, full size';
  head.append(out);
  const size = el('button', 'replay-size');
  size.type = 'button';
  const drawSize = () => {
    const large = dock.classList.contains('large');
    size.textContent = large ? '⤡' : '⤢';
    size.title = large ? 'smaller' : 'larger';
    size.setAttribute('aria-label', size.title);
  };
  size.addEventListener('click', () => {
    saveLarge(dock.classList.toggle('large'));
    drawSize();
    placeDock(loadPlace());
  });
  drawSize();
  head.append(size);
  const close = el('button', 'replay-close', '✕');
  close.type = 'button';
  close.title = 'close the replay';
  close.setAttribute('aria-label', 'close the replay');
  close.addEventListener('click', closeReplay);
  head.append(close);
  dragBy(head);

  dock.hidden = false;
  dock.replaceChildren(head, playerFrame(url, label));
  placeDock(loadPlace());
}

/** Whether the screen shows the width a fixed element is placed against —
 *  false on a phone holding a page wider than itself, and when the reader has
 *  zoomed in. `innerWidth` is that width; it also counts a scrollbar the
 *  visible width does not, hence the tenth of slack (a phone shows a third). */
function dockFits() {
  const v = window.visualViewport;
  return !v || v.width >= window.innerWidth * 0.9;
}

/** A ▶ that plays in the dock. `label` names the video — "replay — 2026-10-14
 *  race 3" — and heads the player as well as the link's tooltip. */
export function replayLink(url, text, cls, label) {
  const a = externalLink(url, text, cls);
  a.title = `${label} — plays here; ctrl-click for a new tab`;
  a.addEventListener('click', (e) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (!dockFits()) return;
    e.preventDefault();
    playReplay(url, label);
  });
  return a;
}

/* palette.js — ⌘K. Six nav items, everything else behind a search box.
 *
 * retain_discard.md §3.3 (the Linear reference) calls this "the single
 * highest-leverage fix for your navigation": a handful of pages in the nav, and
 * every meeting, race and horse reachable by typing. "Sha Tin 15 July race 3",
 * "GOLDEN SIXTY", "calibration".
 *
 * It is the only meeting picker in the app. Brief 01 requires the meeting to be
 * "chosen once" and PROMPTS.md Phase 4 forbids a per-page date picker, so the
 * picker is not a control on a page — it is this.
 */
import { api } from './api.js';
import { el } from './vocab.js';
import { context, formatMeetingDate } from './context.js';

const PAGES = [
  ['Race Day', 'raceday.html'], ['Form Guide', 'form-guide.html'],
  ['Bets', 'bets.html'], ['Blackbook', 'blackbook.html'],
  ['Results', 'results.html'], ['Lookup', 'lookup.html'],
  ['Trials', 'trials.html'], ['Model Analysis', 'model-analysis.html'],
];


/** Subsequence match with a score, the shape every palette uses: "st15" finds
 *  "Sha Tin 15 Jul". Contiguous runs and word starts score higher, so the
 *  obvious answer sorts above an incidental one. */
function fuzzy(query, text) {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let score = 0;
  let i = 0;
  let run = 0;
  for (let j = 0; j < t.length && i < q.length; j += 1) {
    if (t[j] !== q[i]) { run = 0; continue; }
    run += 1;
    score += run;
    if (j === 0 || t[j - 1] === ' ' || t[j - 1] === '·') score += 4;
    i += 1;
  }
  return i === q.length ? score : -1;
}

const state = {
  open: false, query: '', items: [], shown: [], cursor: 0, horses: null,
  // Narrow to one kind. Null is everything, which is what ⌘K opens.
  only: null,
};

let root = null;
let input = null;
let list = null;

function build() {
  root = el('div', 'palette-scrim');
  root.hidden = true;
  const box = el('div', 'palette');

  const head = el('div', 'palette-head');
  head.append(el('span', 'glyph', '⌕'));
  input = el('input');
  input.placeholder = 'meeting, race, horse or page';
  input.setAttribute('aria-label', 'Command palette');
  head.append(input);
  head.append(el('span', 'esc', 'ESC'));
  box.append(head);

  list = el('div', 'palette-list');
  box.append(list);
  box.append(el('div', 'palette-foot',
    '↑↓ move · ↵ open · a race number jumps within this meeting'));
  root.append(box);
  document.body.append(root);

  input.addEventListener('input', () => { state.query = input.value; refresh(); });
  input.addEventListener('keydown', onKey);
  root.addEventListener('click', (e) => { if (e.target === root) close(); });
}

/** Everything addressable, built fresh each open so a newly loaded meeting or
 *  a horse fetched since last time is there. */
function collect() {
  const items = [];

  context.races.forEach((r) => {
    items.push({
      kind: 'RACE',
      label: `R${r.race_no} · ${r.distance ?? '—'}m · ${r.race_class ? `Class ${r.race_class}` : ''}`,
      hint: `${r.field_size} runners${r.band ? ` · ${r.band}` : ''}`,
      run: () => context.setRace(r.race_no),
    });
  });

  context.meetings.forEach((m) => {
    if (m.race_date === context.date) return;
    items.push({
      kind: 'MEETING',
      label: `${m.venue ?? ''} ${formatMeetingDate(m.race_date)}`.trim(),
      hint: `${m.races} races`,
      run: () => context.setDate(m.race_date),
    });
  });

  PAGES.forEach(([name, href]) => {
    items.push({
      kind: 'PAGE',
      label: name,
      hint: href,
      // The context travels with the link, so landing on another page keeps
      // the meeting and race you were looking at.
      run: () => {
        const url = new URL(href, window.location.href);
        if (context.date) url.searchParams.set('date', context.date);
        if (context.race) url.searchParams.set('race', String(context.race));
        window.location.href = url.toString();
      },
    });
  });

  (state.horses ?? []).forEach((h) => {
    items.push({
      kind: 'HORSE',
      label: h.horse_name,
      hint: `${h.runs} runs · last ${h.last_run ?? '—'}`,
      run: () => {
        const url = new URL('form-guide.html', window.location.href);
        url.searchParams.set('horse', h.horse_name);
        if (context.date) url.searchParams.set('date', context.date);
        window.location.href = url.toString();
      },
    });
  });

  return items;
}

/* Order for an EMPTY query — what the palette shows the moment it opens.
 *
 * The tiebreak used to be `a.kind.localeCompare(b.kind)`, which is alphabetical
 * and therefore put HORSE before MEETING, PAGE and RACE. With four hundred
 * horses loaded, the forty-row list was four hundred horses deep in horses and
 * a meeting never appeared in it — so clicking the date in the header, which
 * opens this palette, offered no way to change the date. The whole of "there is
 * no date picker" was this line.
 *
 * Cold, you want where you are (the races in this meeting), then where else you
 * could be (other meetings), then the pages, then the long tail.
 */
const KIND_RANK = { RACE: 0, MEETING: 1, PAGE: 2, HORSE: 3 };

function refresh() {
  const q = state.query.trim();

  // A bare number is almost always a race in the meeting you are looking at.
  const asRace = /^\d{1,2}$/.test(q) ? Number(q) : null;

  state.shown = state.items
    .map((it) => {
      let score = q ? fuzzy(q, `${it.label} ${it.hint ?? ''}`) : 0;
      if (asRace && it.kind === 'RACE' && it.label.startsWith(`R${asRace} `)) {
        score = 1e6;
      }
      return { ...it, score };
    })
    .filter((it) => it.score >= 0 && (!state.only || it.kind === state.only))
    .sort((a, b) => b.score - a.score
      || (KIND_RANK[a.kind] ?? 9) - (KIND_RANK[b.kind] ?? 9))
    .slice(0, 40);

  state.cursor = 0;
  draw();
}

function draw() {
  list.replaceChildren();
  if (!state.shown.length) {
    list.append(el('div', 'palette-empty', 'nothing matches'));
    return;
  }
  state.shown.forEach((it, i) => {
    const row = el('button', `palette-row${i === state.cursor ? ' on' : ''}`);
    row.append(el('span', `kind k-${it.kind.toLowerCase()}`, it.kind));
    row.append(el('span', 'label', it.label));
    if (it.hint) row.append(el('span', 'hint', it.hint));
    row.addEventListener('click', () => { it.run(); close(); });
    // MOVE the highlight; do not rebuild the list to draw it. `draw()` calls
    // replaceChildren, so redrawing on mouseenter destroyed the row the
    // pointer was on: the mousedown landed on a node that no longer existed
    // by mouseup, no click event was ever produced, and the ONLY meeting
    // picker in the app could not be clicked with a mouse. Arrow keys and
    // Enter still worked, which is how it went unnoticed.
    row.addEventListener('mouseenter', () => highlight(i));
    list.append(row);
  });
  highlight(state.cursor);
}

/** The cursor, as a class on the rows that are already there. */
function highlight(i) {
  state.cursor = i;
  [...list.children].forEach((row, n) => row.classList.toggle('on', n === i));
  list.children[i]?.scrollIntoView({ block: 'nearest' });
}

function onKey(e) {
  if (e.key === 'Escape') { close(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const n = state.shown.length;
    if (!n) return;
    // Same reason as the mouse: move the highlight rather than rebuilding.
    highlight((state.cursor + (e.key === 'ArrowDown' ? 1 : -1) + n) % n);
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    const pick = state.shown[state.cursor];
    if (pick) { pick.run(); close(); }
  }
}

/** `only` narrows to one kind — the header's meeting button opens on MEETING,
 *  so the control that shows the date offers the dates. */
export function open(seed = '', { only = null } = {}) {
  if (!root) build();
  state.open = true;
  state.only = only;
  state.items = collect();
  state.query = seed;
  input.value = seed;
  root.hidden = false;
  refresh();
  input.focus();
  input.select();

  // Horses are the long tail — fetched once, lazily, so opening the palette is
  // instant and the list fills in behind the first keystroke.
  if (state.horses === null) {
    state.horses = [];
    api.horses(400).then((body) => {
      state.horses = body.horses;
      if (state.open) { state.items = collect(); refresh(); }
    }).catch(() => { /* the palette works without them */ });
  }
}

export function close() {
  state.open = false;
  state.only = null;
  if (root) root.hidden = true;
}

/** Wire ⌘K / Ctrl-K once per page. */
export function install() {
  window.addEventListener('palette:open',
    (e) => open(e.detail?.seed ?? '', { only: e.detail?.only ?? null }));
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      state.open ? close() : open();
      return;
    }
    // A bare "/" opens it too, as long as the user is not already typing.
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
    if (e.key === '/' && !typing && !state.open) {
      e.preventDefault();
      open();
    }
  });
}

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
    .filter((it) => it.score >= 0)
    .sort((a, b) => b.score - a.score || a.kind.localeCompare(b.kind))
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
    row.addEventListener('mouseenter', () => { state.cursor = i; draw(); });
    list.append(row);
  });
  list.children[state.cursor]?.scrollIntoView({ block: 'nearest' });
}

function onKey(e) {
  if (e.key === 'Escape') { close(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const n = state.shown.length;
    if (!n) return;
    state.cursor = (state.cursor + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
    draw();
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    const pick = state.shown[state.cursor];
    if (pick) { pick.run(); close(); }
  }
}

export function open(seed = '') {
  if (!root) build();
  state.open = true;
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
  if (root) root.hidden = true;
}

/** Wire ⌘K / Ctrl-K once per page. */
export function install() {
  window.addEventListener('palette:open', (e) => open(e.detail?.seed ?? ''));
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

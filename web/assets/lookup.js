/* Lookup — ported from web/design-source/Lookup.dc.html.
 *
 * The whole archive, filtered, with five readings of the same slice: the runs
 * themselves, a breakdown by one dimension, a pivot across two, the outliers,
 * and the slice's own summary.
 *
 * Brief 07 §8 names the risk this page carries and requires the interface to
 * show it rather than hide it:
 *
 *     "This page's genuine risk is manufacturing false signals through
 *      repeated slicing. A pivot is the easiest way in this whole tool to
 *      manufacture a false finding."
 *
 * So every figure sits beside its n, every rate beside an interval, and every
 * breakdown and pivot beside the count that would clear p<.05 by luck alone at
 * the size shown. A cell under the minimum sample is dimmed to 40% rather than
 * hidden — removing it would conceal how much of the grid is noise.
 *
 * Every filter is a query-string parameter, so a slice is a URL and a finding
 * can be sent to someone else exactly as it was seen.
 */
import { api, num } from './api.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';

const NAV = [
  ['Race Day', 'raceday.html'], ['Form Guide', 'form-guide.html'],
  ['Lookup', 'lookup.html'], ['Bets', 'bets.html'],
  ['Blackbook', 'blackbook.html'], ['Results', 'results.html'],
  ['Trials', 'trials.html'], ['Model Analysis', 'model-analysis.html'],
];

const $ = (id) => document.getElementById(id);
const DASH = '—';
const MINUS = '−';
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const TABS = [['runs', 'RUNS'], ['breakdown', 'BREAKDOWN'], ['pivot', 'PIVOT'],
              ['outliers', 'OUTLIERS'], ['insight', 'THIS SLICE']];

const COLS = [
  { k: 'date', label: 'DATE' }, { k: 'r', label: 'R', cls: 'r' },
  { k: 'venue', label: 'MTG' }, { k: 'course', label: 'CRS' },
  { k: 'surface', label: 'SURF' }, { k: 'dist', label: 'DIST', cls: 'r' },
  { k: 'going', label: 'GOING' }, { k: 'class', label: 'CLASS' },
  { k: 'field', label: 'FLD', cls: 'r' }, { k: 'fin', label: 'FIN', cls: 'r' },
  { k: 'horse', label: 'HORSE' }, { k: 'draw', label: 'DR', cls: 'r' },
  { k: 'jockey', label: 'JOCKEY' }, { k: 'trainer', label: 'TRAINER' },
  { k: 'style', label: 'STYLE' }, { k: 'sp', label: 'SP', cls: 'r' },
  { k: 'fig', label: 'FIGURE', cls: 'r' },
];

// Rendered from the API's own vocabulary, so the panel cannot drift from what
// the query layer accepts. The type of each control is decided by its suffix.
const SELECTS = {
  venue: ['', 'HV', 'ST'],
  surface: ['', 'Turf', 'AWT'],
  course: ['', 'A', 'A+3', 'B', 'B+2', 'C', 'C+3', 'AWT'],
  pace_style: ['', 'Leader', 'On-Pace', 'Midfield', 'Closer'],
};

const state = {
  tab: 'runs', filters: {}, source: 'race',
  runs: null, insight: null, corpus: null, vocab: null,
  breakdown: null, pivot: null, outliers: null,
  dimension: 'draw', pivotRows: 'style', pivotCols: 'venue',
  metric: 'strike_rate', minSample: 30, delta: 6,
  loading: false,
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderNav() {
  $('nav').replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === 'lookup.html') a.setAttribute('aria-current', 'page');
    return a;
  }));
}

function renderTabs() {
  $('tab-toggle').replaceChildren(...TABS.map(([key, label]) => {
    const b = el('button', null, label);
    b.setAttribute('aria-pressed', String(state.tab === key));
    b.addEventListener('click', () => { state.tab = key; refresh(); });
    return b;
  }));
}

function pct(v, digits = 1) {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v, digits = 1) {
  if (v === null || v === undefined) return DASH;
  return `${v >= 0 ? '+' : MINUS}${(Math.abs(v) * 100).toFixed(digits)}%`;
}

function ciText(ci, digits = 0) {
  return ci && ci.length === 2
    ? `[${pct(ci[0], digits)}, ${pct(ci[1], digits)}]` : DASH;
}

function renderCorpus() {
  const c = state.corpus;
  const host = $('corpus');
  if (!c) { host.replaceChildren(); return; }
  host.replaceChildren();
  const bit = (label, value, cls) => {
    const s = el('span');
    s.append(document.createTextNode(`${label} `));
    s.append(el('b', cls ?? null, value));
    return s;
  };
  host.append(bit('DB', `${c.earliest} → ${c.latest}`));
  host.append(el('span', 'sep', '·'));
  host.append(bit('', `${c.runs.toLocaleString()} runs`));
  host.append(bit('', `${c.races.toLocaleString()} races`));
  host.append(bit('', `${c.trials.toLocaleString()} trials`));
  host.append(el('span', 'sep', '·'));
  // The half of this line that earns its place: what is MISSING.
  host.append(bit('pace-labelled',
    `${c.pace_labelled.toLocaleString()}/${c.runs.toLocaleString()} `
    + `(${pct(c.pace_share, 0)})`, c.pace_share < 0.9 ? 'gap' : null));
  host.append(bit('figures',
    `${c.figures.toLocaleString()} (${pct(c.figure_share, 0)})`,
    c.figure_share < 0.9 ? 'gap' : null));
  host.append(el('span', 'why',
    'this line states what is current and quantifies what is missing'));
}

/* ── filters ─────────────────────────────────────────────────────────────── */

function query() {
  const p = new URLSearchParams();
  Object.entries(state.filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) p.set(k, v);
  });
  if (state.source !== 'race') p.set('source', state.source);
  return p.toString();
}

/** The URL is the state. A slice is a link, and a finding can be sent on
 *  exactly as it was seen. */
function writeUrl() {
  const url = new URL(window.location.href);
  [...url.searchParams.keys()].forEach((k) => {
    if (k !== 'date' && k !== 'race') url.searchParams.delete(k);
  });
  new URLSearchParams(query()).forEach((v, k) => url.searchParams.set(k, v));
  if (state.tab !== 'runs') url.searchParams.set('tab', state.tab);
  window.history.replaceState(null, '', url);
}

function readUrl() {
  const p = new URLSearchParams(window.location.search);
  const known = new Set(Object.values(state.vocab.groups).flat());
  p.forEach((v, k) => {
    if (known.has(k)) state.filters[k] = v;
  });
  if (p.get('source')) state.source = p.get('source');
  const tab = p.get('tab');
  if (tab && TABS.some(([key]) => key === tab)) state.tab = tab;
}

function field(name) {
  const row = el('div', 'fp-field');
  row.append(el('label', null, name.replace(/_/g, ' ')));
  const options = SELECTS[name];
  const input = el(options ? 'select' : 'input');
  input.id = `f-${name}`;
  if (options) {
    options.forEach((o) => {
      const opt = el('option', null, o || 'any');
      opt.value = o;
      input.append(opt);
    });
  } else {
    input.type = /_(min|max)$/.test(name) ? 'number' : 'text';
    input.placeholder = /^date_/.test(name) ? 'YYYY-MM-DD' : '';
  }
  input.value = state.filters[name] ?? '';
  const apply = () => {
    const v = input.value.trim();
    if (v === '') delete state.filters[name];
    else state.filters[name] = v;
    load();
  };
  input.addEventListener('change', apply);
  row.append(input);
  return row;
}

function renderFilterPanel() {
  const host = $('filter-groups');
  host.replaceChildren();
  Object.entries(state.vocab.groups).forEach(([group, names]) => {
    const box = el('div', 'fp-group');
    box.append(el('span', 'k', group.toUpperCase()));
    names.forEach((n) => box.append(field(n)));
    host.append(box);
  });
  const box = el('div', 'fp-group');
  box.append(el('span', 'k', 'SOURCE'));
  const row = el('div', 'fp-field');
  row.append(el('label', null, 'runs from'));
  const sel = el('select');
  state.vocab.sources.forEach((s) => {
    const o = el('option', null, s);
    o.value = s;
    sel.append(o);
  });
  sel.value = state.source;
  sel.addEventListener('change', () => { state.source = sel.value; load(); });
  row.append(sel);
  box.append(row);
  host.append(box);

  const active = Object.keys(state.filters).length;
  $('filter-count').textContent = active ? `· ${active}` : '';
}

function renderActiveFilters() {
  const host = $('active-filters');
  host.replaceChildren();
  host.append(el('span', 'lab', 'ACTIVE'));
  const entries = Object.entries(state.filters);
  if (!entries.length) {
    host.append(el('span', 'none', 'no filters — the whole archive'));
    return;
  }
  entries.forEach(([k, v]) => {
    const b = el('button', 'chip on');
    b.append(el('span', 'kind', `${k.replace(/_/g, ' ')} `));
    b.append(document.createTextNode(String(v)));
    b.append(el('span', 'n', ' ×'));
    b.title = `remove ${k}`;
    b.addEventListener('click', () => {
      delete state.filters[k];
      renderFilterPanel();
      load();
    });
    host.append(b);
  });
}

/* ── runs ────────────────────────────────────────────────────────────────── */

function renderRunsHead() {
  $('lk-head').replaceChildren(...COLS.map((c) =>
    el('div', c.cls ?? null, c.label)));
}

function runRow(r) {
  const trial = r.source === 'trial';
  const row = el('div', `lk-row${trial ? ' trial' : ''}`);
  const cell = (cls, text) => row.append(el('div', cls, text ?? DASH));
  cell('date', r.race_date);
  cell('r', trial ? 'T' : `R${r.race_no}`);
  cell(null, r.venue);
  cell(null, r.course);
  cell(null, r.surface);
  cell('r', r.distance ? `${r.distance}` : DASH);
  cell(null, r.going);
  cell(null, r.race_class);
  cell('r', r.field_size ? String(r.field_size) : DASH);
  const fin = el('div', 'r fin', r.place_display ?? r.place ?? DASH);
  if (r.place === 1) fin.classList.add('win');
  else if (r.placed) fin.classList.add('plc');
  row.append(fin);
  const horse = el('div', 'horse', r.horse_name);
  horse.title = r.horse_name;
  row.append(horse);
  cell('r', r.draw ? String(r.draw) : DASH);
  cell(null, r.jockey);
  cell(null, r.trainer);
  cell(null, r.pace_style);
  cell('r', r.win_odds ? num(r.win_odds, 1) : DASH);
  const fig = el('div', 'r', r.et_figure === null || r.et_figure === undefined
    ? DASH : num(r.et_figure, 1));
  if (r.figure_display) fig.title = r.figure_display;
  row.append(fig);
  return row;
}

function renderRuns() {
  renderRunsHead();
  const body = state.runs;
  const host = $('lk-rows');
  if (!body) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  if (!body.runs.length) {
    const box = el('div', 'no-match');
    box.append(document.createTextNode('NO RUN MATCHES THIS FILTER SET.'));
    box.append(el('div', 'hint',
      'Filters combine as AND across groups. Remove one in the panel on the left.'));
    host.replaceChildren(box);
  } else {
    host.replaceChildren(...body.runs.map(runRow));
  }
  const foot = $('lk-foot');
  foot.replaceChildren();
  const total = state.insight?.runs ?? body.count;
  foot.append(el('span', null,
    body.truncated
      ? `Showing first ${body.count} of ${total.toLocaleString()} matching runs`
      : `${body.count.toLocaleString()} matching runs`));
  // The artboard's own line, and it has to be true: every panel is computed
  // over the whole slice, not the page of it on screen.
  foot.append(el('span', 'right',
    'every tab is computed on all matching runs, not the rows shown'));
}

/* ── breakdown ───────────────────────────────────────────────────────────── */

function dimSelect(value, onChange) {
  const sel = el('select');
  state.vocab.dimensions.forEach((d) => {
    const o = el('option', null, d);
    o.value = d;
    sel.append(o);
  });
  sel.value = value;
  sel.addEventListener('change', () => onChange(sel.value));
  return sel;
}

function renderBreakdownDims() {
  const bar = $('breakdown-dims');
  bar.replaceChildren();
  bar.append(el('span', 'k', 'BY'));
  bar.append(dimSelect(state.dimension, (v) => { state.dimension = v; load(); }));
  const b = state.breakdown;
  if (!b) return;
  bar.append(el('span', 'k', 'BASELINE'));
  bar.append(el('span', null,
    `${pct(b.baseline.strike_rate)} win over ${b.baseline.runs.toLocaleString()} `
    + 'runs in this slice'));
  bar.append(el('span', 'k', 'EXPECTED BY CHANCE'));
  // The honest denominator. Eight cells clearing out of 153 is not eight
  // findings when 7.0 were expected at random.
  bar.append(el('span', null,
    `${b.cleared} cleared of ${b.cells} · ${b.expected_by_chance} expected at random`));
}

function renderBreakdown() {
  renderBreakdownDims();
  const b = state.breakdown;
  const host = $('breakdown');
  if (!b) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  if (!b.rows.length) {
    host.replaceChildren(el('div', 'no-match', 'NO RUN MATCHES THIS FILTER SET.'));
    return;
  }
  const table = el('table', 'bd-tab');
  const head = el('tr');
  [[b.dimension.toUpperCase(), ''], ['RUNS', 'r'], ['W / P', 'r'],
   ['WIN %', 'r'], ['BASELINE', 'r'], ['Δ vs BASE', 'r'],
   ['95% CI ON WIN %', ''], ['PLACE %', 'r'], ['Δ PLACE', 'r'], ['READ', '']]
    .forEach(([label, cls]) => head.append(el('th', cls || null, label)));
  const thead = el('thead');
  thead.append(head);
  table.append(thead);

  const body = el('tbody');
  b.rows.forEach((r) => {
    const tr = el('tr', r.thin ? 'thin' : null);
    tr.append(el('td', 'label', String(r.value)));
    tr.append(el('td', 'r', r.runs.toLocaleString()));
    tr.append(el('td', 'r', `${r.wins} / ${r.places}`));
    tr.append(el('td', 'r', pct(r.strike_rate)));
    tr.append(el('td', 'r base', pct(b.baseline.strike_rate)));
    // Colour only where the interval clears the baseline.
    const cls = r.clears ? (r.win_delta >= 0 ? ' up' : ' down') : '';
    tr.append(el('td', `r delta${cls}`, signedPct(r.win_delta)));
    tr.append(el('td', 'ci', ciText(r.win_ci)));
    tr.append(el('td', 'r', pct(r.place_rate)));
    const pcls = r.place_clears ? (r.place_delta >= 0 ? ' up' : ' down') : '';
    tr.append(el('td', `r delta${pcls}`, signedPct(r.place_delta)));
    tr.append(el('td', `verdict ${r.clears && !r.thin ? 'clear' : 'noise'}`,
      r.thin ? `n<${b.min_sample}`
        : r.clears ? 'CLEARS BASELINE' : 'inside the interval'));
    if (r.thin) {
      tr.title = `${r.runs} runs — under ${b.min_sample}, so the interval is `
        + 'too wide for the difference to mean anything';
    }
    body.append(tr);
  });
  table.append(body);
  const box = el('div', 'table-box');
  box.append(table);
  host.replaceChildren(box);

  const foot = el('div', 'section-foot');
  foot.append(el('span', null,
    `${b.rows.length} values · ${b.thin_hidden} under n=${b.min_sample}`));
  foot.append(el('span', 'warn',
    b.cleared > b.expected_by_chance * 2
      ? `${b.cleared} cleared where ${b.expected_by_chance} were expected at `
        + 'random — more than luck accounts for'
      : `${b.cleared} cleared where ${b.expected_by_chance} were expected at `
        + 'random — which is to say, essentially nothing was found'));
  host.append(foot);
}

/* ── pivot ───────────────────────────────────────────────────────────────── */

function renderPivotDims() {
  const bar = $('pivot-dims');
  bar.replaceChildren();
  bar.append(el('span', 'k', 'ROWS'));
  bar.append(dimSelect(state.pivotRows, (v) => { state.pivotRows = v; load(); }));
  bar.append(el('span', 'k', 'COLUMNS'));
  bar.append(dimSelect(state.pivotCols, (v) => { state.pivotCols = v; load(); }));
  bar.append(el('span', 'k', 'METRIC'));
  const sel = el('select');
  state.vocab.metrics.forEach((m) => {
    const o = el('option', null, m.replace(/_/g, ' '));
    o.value = m;
    sel.append(o);
  });
  sel.value = state.metric;
  sel.addEventListener('change', () => { state.metric = sel.value; load(); });
  bar.append(sel);
  const p = state.pivot;
  if (p) {
    bar.append(el('span', 'k', 'CELLS'));
    bar.append(el('span', null,
      `${p.cells} · ${p.thin_cells} under n=${p.min_sample}`));
  }
}

function cellText(cell, metric) {
  if (cell.value === null || cell.value === undefined) return DASH;
  return metric === 'avg_figure' || metric === 'ae'
    ? num(cell.value, 2) : pct(cell.value);
}

function renderPivot() {
  renderPivotDims();
  const p = state.pivot;
  const host = $('pivot');
  if (!p) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  if (!p.cells) {
    host.replaceChildren(el('div', 'no-match', 'NO RUN MATCHES THIS FILTER SET.'));
    return;
  }
  const table = el('table', 'pv-tab');
  const head = el('tr');
  head.append(el('th', 'rowh', `${p.rows} ↓ / ${p.cols} →`));
  p.col_values.forEach((c) => head.append(el('th', null, String(c))));
  head.append(el('th', 'total', 'ROW TOTAL'));
  const thead = el('thead');
  thead.append(head);
  table.append(thead);

  const body = el('tbody');
  p.row_values.forEach((rv) => {
    const tr = el('tr');
    tr.append(el('td', 'rowh', String(rv)));
    p.col_values.forEach((cv) => {
      const cell = p.grid[String(rv)]?.[String(cv)];
      if (!cell) { tr.append(el('td', 'empty', DASH)); return; }
      // 40%, per the artboard. Dimmed, not hidden: removing a thin cell hides
      // how much of the pivot is noise.
      const td = el('td', cell.thin ? 'thin' : null);
      td.append(el('span', 'v', cellText(cell, p.metric)));
      td.append(el('span', 'n', `n=${cell.runs}`));
      td.title = `${rv} × ${cv} — ${cell.runs} runs, ${cell.wins} wins`
        + (cell.thin ? ` (under n=${p.min_sample})` : '');
      tr.append(td);
    });
    const t = p.row_totals[String(rv)];
    const td = el('td', 'total');
    td.append(el('span', 'v', pct(
      p.metric === 'place_rate' ? t.place_rate : t.strike_rate)));
    td.append(el('span', 'n', `n=${t.runs}`));
    tr.append(td);
    body.append(tr);
  });
  table.append(body);
  const box = el('div', 'table-box');
  box.append(table);
  host.replaceChildren(box);

  host.append(el('div', 'caveat',
    'Every cell carries its n underneath, and cells under the minimum sample '
    + `are dimmed to 40%. A pivot is the easiest way in this whole tool to `
    + `manufacture a false finding: ${p.cells} cells means roughly `
    + `${p.expected_notable} will look notable at p<.05 with nothing behind `
    + 'them.'));
}

/* ── outliers ────────────────────────────────────────────────────────────── */

function renderOutliers() {
  const o = state.outliers;
  const host = $('outliers');
  if (!o) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const note = el('div', 'ol-note');
  note.append(el('b', null, 'ONE RUN IS A STORY, NOT A SIGNAL. '));
  note.append(document.createTextNode(
    'These are runs where the finishing position most disagrees with the '
    + "market's ranking. That makes them worth watching and worth a blackbook "
    + 'note — it does not make them a pattern. A horse appearing here twice is '
    + 'the only thing on this tab that starts to mean something.'));
  host.append(note);

  const bar = el('div', 'dim-bar');
  bar.append(el('span', 'k', `|FIN Δ| ≥ ${o.delta}`));
  bar.append(el('span', null,
    `${o.matched.toLocaleString()} of ${o.of_runs.toLocaleString()} priced runs `
    + `(${pct(o.share)})`));
  bar.append(el('span', 'k', 'REPEAT OFFENDERS'));
  bar.append(el('span', null,
    `${o.repeat_horses} of ${o.horses} horses shown`));
  if (o.truncated) {
    bar.append(el('span', 'k', 'SHOWING'));
    bar.append(el('span', null,
      `first ${o.shown} — repeats are counted over these, so the true number `
      + 'is higher'));
  }
  host.append(bar);

  const head = el('div', 'ol-head');
  ['DATE', 'R', 'CONDITIONS', 'HORSE', 'FIN', 'MKT RANK', 'SP', 'STYLE', 'FIN Δ']
    .forEach((h, i) => head.append(el('div', i >= 4 ? 'r' : null, h)));
  host.append(head);

  if (!o.runs.length) {
    host.append(el('div', 'no-match',
      `No run in this slice deviates by ${o.delta} places or more from its `
      + 'market ranking.'));
    return;
  }
  o.runs.forEach((r) => {
    const row = el('div', 'ol-row');
    row.append(el('div', 'date', r.race_date));
    row.append(el('div', null, `R${r.race_no}`));
    row.append(el('div', 'cond',
      `${r.venue} ${r.distance}m ${r.surface} ${r.going ?? ''} `
      + `${r.race_class ? `Cl${r.race_class}` : ''}`.trim()));
    const horse = el('div', 'horse');
    horse.append(document.createTextNode(r.horse_name));
    if (r.repeat) horse.append(el('span', 'repeat', `×${r.appearances}`));
    horse.title = r.horse_name;
    row.append(horse);
    row.append(el('div', 'r', String(r.place)));
    row.append(el('div', 'r', String(r.market_rank)));
    row.append(el('div', 'r', num(r.win_odds, 1)));
    row.append(el('div', 'r', r.pace_style ?? DASH));
    row.append(el('div', `r delta ${r.fin_delta >= 0 ? 'up' : 'down'}`,
      `${r.fin_delta >= 0 ? '+' : MINUS}${Math.abs(r.fin_delta)}`));
    row.title = `finished ${r.place} of ${r.field_size}, ranked `
      + `${r.market_rank} by the market at ${r.win_odds}`;
    host.append(row);
  });
}

/* ── this slice ──────────────────────────────────────────────────────────── */

function panel(title, note) {
  const box = el('section', 'panel-box');
  const hd = el('div', 'panel-hd');
  hd.append(el('span', 't', title));
  if (note) hd.append(el('span', 'n', note));
  box.append(hd);
  const body = el('div', 'panel-body');
  box.append(body);
  return { box, body };
}

function renderInsight() {
  const s = state.insight;
  const host = $('insight');
  if (!s) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'THIS SLICE'));
  head.append(el('span', 'sub',
    `${s.runs.toLocaleString()} RUNS · ${s.races.toLocaleString()} RACES`
    + (s.thin ? ' · TOO THIN TO BE EVIDENCE' : '')));
  host.append(head);

  const panels = el('div', 'panels');

  const rec = panel('RECORD', `n=${s.runs.toLocaleString()}`);
  const line = el('div', 'ins-line');
  line.append(el('span', 'big', pct(s.strike_rate)));
  line.append(el('span', null, `win · ${s.wins} of ${s.runs.toLocaleString()}`));
  line.append(el('span', 'big', pct(s.place_rate)));
  line.append(el('span', null, `place · ${s.places}`));
  rec.body.append(line);
  if (s.avg_figure !== null) {
    rec.body.append(el('div', 'ins-line',
      `mean ET figure ${num(s.avg_figure, 1)} over ${s.figures.toLocaleString()} `
      + 'runs with one'));
  }
  panels.append(rec.box);

  const ae = panel('A/E · ACTUAL ÷ MARKET-IMPLIED',
    s.ae === null ? 'NO PRICED RUN' : `n=${s.ae_runs ?? s.runs}`);
  if (s.ae === null) {
    ae.body.append(el('div', 'empty-line',
      'NO RUN IN THIS SLICE CARRIES A PRICE'));
  } else {
    const l = el('div', 'ins-line');
    l.append(el('span', 'big', num(s.ae, 2)));
    l.append(el('span', null, `95% CI [${num(s.ae_lo, 2)}, ${num(s.ae_hi, 2)}]`));
    l.append(el('span', s.clears ? null : 'n',
      s.clears ? 'the interval excludes 1.00'
        : 'the interval straddles 1.00 — not shown to beat the price'));
    ae.body.append(l);
    // 1.00 drawn as a fixed point rather than a number to read against.
    const scale = el('div', 'ae-scale');
    const at = (v) => `${Math.max(0, Math.min(100, (v / 3) * 100))}%`;
    const band = el('i');
    band.style.left = at(s.ae_lo);
    band.style.right = `${100 - parseFloat(at(s.ae_hi))}%`;
    scale.append(band);
    const one = el('span', 'one');
    one.style.left = at(1);
    scale.append(one);
    const mark = el('span', 'mark');
    mark.style.left = at(s.ae);
    scale.append(mark);
    ae.body.append(scale);
    const axis = el('div', 'ae-axis');
    axis.append(el('span', null, '0.00'));
    axis.append(el('span', null, '1.00 = THE MARKET'));
    axis.append(el('span', null, '3.00'));
    ae.body.append(axis);
  }
  panels.append(ae.box);

  const st = panel('BY RUNNING STYLE', 'WITH n');
  if (!s.by_style.length) {
    st.body.append(el('div', 'empty-line', 'NO RUN IN THIS SLICE IS LABELLED'));
  } else {
    const top = Math.max(...s.by_style.map((b) => b.strike_rate ?? 0)) || 1;
    s.by_style.forEach((b) => {
      const row = el('div', 'style-row');
      row.append(el('span', 'label', b.style));
      row.append(el('span', 'n', `n=${b.runs}`));
      const track = el('span', 'track');
      const fill = el('i');
      fill.style.width = `${((b.strike_rate ?? 0) / top) * 100}%`;
      track.append(fill);
      row.append(track);
      row.append(el('span', 'v', pct(b.strike_rate)));
      st.body.append(row);
    });
  }
  panels.append(st.box);
  host.append(panels);

  host.append(el('div', 'caveat',
    s.thin
      ? `${s.runs} runs is under the ${state.minSample}-run minimum. Whatever `
        + 'the rates say, this slice is not evidence.'
      : 'One slice at p<.05 is one chance in twenty of looking notable by luck '
        + 'alone. The breakdown and pivot tabs state how many of their cells '
        + 'that accounts for.'));
}

/* ── loading ─────────────────────────────────────────────────────────────── */

function renderSummary() {
  const host = $('lk-sum');
  const s = state.insight;
  host.replaceChildren();
  if (!s) return;
  const stat = (value, label) => {
    const box = el('span');
    box.append(el('b', null, value));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  host.append(stat(s.runs.toLocaleString(), 'RUNS'));
  host.append(stat(s.races.toLocaleString(), 'RACES'));
  host.append(stat(pct(s.strike_rate), 'WIN'));
  host.append(stat(s.ae === null ? DASH : num(s.ae, 2), 'A/E'));
}

function refresh() {
  renderTabs();
  renderSummary();
  renderActiveFilters();
  TABS.forEach(([key]) => { $(`tab-${key}`).hidden = state.tab !== key; });
  if (state.tab === 'runs') renderRuns();
  if (state.tab === 'breakdown') renderBreakdown();
  if (state.tab === 'pivot') renderPivot();
  if (state.tab === 'outliers') renderOutliers();
  if (state.tab === 'insight') renderInsight();
  writeUrl();
}

/** Every tab is computed over the whole slice, so a filter change reloads all
 *  of them rather than only the one on screen. */
async function load() {
  if (state.loading) return;
  state.loading = true;
  const q = query();
  refresh();
  try {
    const [runs, insight, breakdown, pivot, outliers] = await Promise.all([
      api.lookup(`${q}${q ? '&' : ''}limit=500`),
      api.lookupInsight(q),
      api.lookupBreakdown(q, state.dimension),
      api.lookupPivot(q, state.pivotRows, state.pivotCols, state.metric),
      api.lookupOutliers(q, state.delta),
    ]);
    state.runs = runs;
    state.insight = insight;
    state.breakdown = breakdown;
    state.pivot = pivot;
    state.outliers = outliers;
  } finally {
    state.loading = false;
  }
  refresh();
}

async function boot() {
  renderNav();
  installPalette();
  await context.init();
  const [vocab, corpus] = await Promise.all([
    api.lookupFilters(), api.lookupCorpus(),
  ]);
  state.vocab = vocab;
  state.corpus = corpus;
  state.minSample = vocab.min_sample;
  state.delta = vocab.outlier_delta;
  readUrl();
  renderCorpus();
  renderFilterPanel();
  $('clear-filters').addEventListener('click', () => {
    state.filters = {};
    renderFilterPanel();
    load();
  });
  await load();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});

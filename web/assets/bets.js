/* Bets — ported from web/design-source/Bets.dc.html.
 *
 * The ledger of what was actually staked, and what it says. Three views: the
 * ledger itself, the analysis, and the reconciliation of imported statement
 * rows against logged bets.
 *
 * One rule runs through the whole analysis section, and the design prints it
 * on the artboard rather than leaving it implied:
 *
 *     "EVERY FIGURE CARRIES n AND AN INTERVAL · A 12-BET SLICE IS NOT A
 *      FINDING"
 *
 * So nothing here renders a bare ROI. A slice under 30 bets is dimmed, and a
 * point estimate whose 95% interval straddles zero is printed in the ordinary
 * text colour however green it looks — the ledger's own headline, +17.5% over
 * 1,078 bets, has an interval of [-43%, +125%] and is not a profit that has
 * been shown.
 */
import { api, num } from './api.js';
import { el, $, DASH, MINUS, renderNav, periodPicker,
         accountPicker } from './vocab.js';
import { context } from './context.js';
import { initEntry, loadEntry, renderEntry } from './bets-entry.js';
import { install as installPalette } from './palette.js';


const COLS = [
  { key: 'date', label: 'DATE' },
  { key: 'venue', label: 'MTG' },
  { key: 'race', label: 'R', cls: 'r' },
  { key: 'type', label: 'TYPE' },
  { key: 'sel', label: 'SELECTIONS' },
  { key: 'stake', label: 'STAKE', cls: 'r' },
  { key: 'ret', label: 'RETURN', cls: 'r' },
  { key: 'pnl', label: 'P/L', cls: 'r' },
  { key: 'clv', label: 'CLV', cls: 'r' },
  { key: 'src', label: 'SOURCE' },
];

const RESULTS = [['all', 'ALL'], ['won', 'WON'], ['lost', 'LOST'],
                 ['booked', 'BLACKBOOK']];
const SOURCES = [['all', 'ALL'], ['confirmed', 'STATEMENT'],
                 ['quoted', 'LOG ONLY']];

const state = {
  view: 'entry', bets: [], analysis: null, recon: null,
  search: '', type: null, result: 'all', source: 'all',
  // Which book, and how far back. Both drive EVERY view on this page — the
  // ledger, the analysis and the reconciliation — because a page that filters
  // one panel and not the others invites reading two numbers as comparable
  // when they are measured over different things.
  account: null, period: 'lifetime', window: null,
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

/** Which book and how far back — one bar, governing every view on the page. */
function renderScope() {
  const host = $('scope-bar');
  host.replaceChildren();
  host.append(accountPicker(state.account, (key) => {
    state.account = key;
    loadLedger();
  }));
  host.append(periodPicker(state.period, (key) => {
    state.period = key;
    loadLedger();
  }, { window: state.window }));
  // The entry view is about a bet not yet placed, so a window over past
  // results has nothing to say about it.
  host.hidden = state.view === 'entry';
}

function renderViewToggle() {
  // Entry first: the page's own subject is the decision, and the ledger is
  // what that decision left behind.
  const views = [['entry', 'ENTRY'], ['ledger', 'LEDGER'],
                 ['analysis', 'ANALYSIS'], ['recon', 'RECONCILE']];
  $('view-toggle').replaceChildren(...views.map(([key, label]) => {
    const b = el('button', null, label);
    b.setAttribute('aria-pressed', String(state.view === key));
    b.addEventListener('click', () => { state.view = key; render(); });
    return b;
  }));
}

function money(v, { sign = false } = {}) {
  if (v === null || v === undefined) return DASH;
  const n = Math.round(Math.abs(v)).toLocaleString();
  if (!sign) return `$${n}`;
  return `${v >= 0 ? '+' : MINUS}$${n}`;
}

function pct(v, digits = 1) {
  if (v === null || v === undefined) return DASH;
  return `${v >= 0 ? '+' : MINUS}${(Math.abs(v) * 100).toFixed(digits)}%`;
}

function ciText(ci) {
  return ci ? `[${pct(ci[0], 0)}, ${pct(ci[1], 0)}]` : DASH;
}

/* ── the headline ────────────────────────────────────────────────────────── */

function renderSummary() {
  const a = state.analysis;
  const host = $('bet-sum');
  if (!a) { host.replaceChildren(); return; }
  const o = a.overall;
  host.replaceChildren();
  const stat = (value, label, cls) => {
    const box = el('span');
    box.append(el('b', cls ?? null, value));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  host.append(stat(String(o.bets), 'BETS'));
  host.append(stat(money(o.staked), 'STAKED'));
  // Colour is earned by an interval that excludes zero, not by the sign.
  const proven = o.clears_zero ? (o.pnl >= 0 ? 'pos' : 'neg') : 'unproven';
  host.append(stat(money(o.pnl, { sign: true }), 'P/L', proven));
  host.append(stat(pct(o.roi), `ROI ${ciText(o.roi_ci)}`, proven));
}

/* ── ledger ──────────────────────────────────────────────────────────────── */

function typeCounts() {
  const counts = new Map();
  state.bets.forEach((b) => counts.set(b.bet_type, (counts.get(b.bet_type) ?? 0) + 1));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function chip(label, on, onClick, count) {
  const b = el('button', `chip${on ? ' on' : ''}`);
  b.append(document.createTextNode(label));
  if (count !== undefined) b.append(el('span', 'n', ` ${count}`));
  b.addEventListener('click', onClick);
  return b;
}

function renderChips() {
  $('type-chips').replaceChildren(...typeCounts().map(([type, n]) =>
    chip(type, state.type === type, () => {
      state.type = state.type === type ? null : type;
      render();
    }, n)));
  $('result-chips').replaceChildren(...RESULTS.map(([key, label]) =>
    chip(label, state.result === key, () => { state.result = key; render(); })));
  $('source-chips').replaceChildren(...SOURCES.map(([key, label]) =>
    chip(label, state.source === key, () => { state.source = key; render(); })));
}

function matches(b) {
  if (state.type && b.bet_type !== state.type) return false;
  if (state.result === 'won' && !(b.returned > 0)) return false;
  if (state.result === 'lost' && b.returned > 0) return false;
  if (state.result === 'booked' && !b.blackbook.length) return false;
  if (state.source === 'confirmed' && !b.statement_confirmed) return false;
  if (state.source === 'quoted' && b.statement_confirmed) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const hay = [b.bet_type, b.bookie_ref ?? '', b.race_date,
    ...b.selections.map((s) => s.horse_name ?? '')].join(' ').toLowerCase();
  return hay.includes(q);
}

function renderHead() {
  $('led-head').replaceChildren(...COLS.map((c) =>
    el('div', c.cls ?? null, c.label)));
}

/** One selection, styled by what it is.
 *
 *  A BANKER carries the ticket: if it loses, nothing else on the line matters,
 *  so it reads brightest and is placed first. A BOOKED horse is one the
 *  blackbook already had a view on, and that keeps the book's colour — the
 *  point of the book is recognising its horses in someone else's list.
 */
function selectionChip(s, booked) {
  const name = s.horse_name ?? `#${s.horse_no}`;
  const cls = [
    s.is_banker ? 'banker' : null,
    booked.has(s.horse_name) ? 'booked' : null,
  ].filter(Boolean).join(' ');
  const chip = el('span', cls || null, s.is_banker ? `${name}◆` : name);
  if (s.place) chip.title = `finished ${s.place}`;
  return chip;
}

/** Bankers first, then the rest in their own order.
 *
 *  A banker is not one selection among several — it is the leg the whole
 *  ticket depends on, and reading it third in a list of five is reading the
 *  ticket backwards. Stable within each group, so two bankers stay in the
 *  order they were struck. */
function bankersFirst(selections) {
  return [...selections].sort(
    (a, b) => (b.is_banker ? 1 : 0) - (a.is_banker ? 1 : 0));
}

function selectionText(b) {
  const booked = new Set(b.blackbook.map((x) => x.horse_name));
  const host = el('div', 'sel');

  // AN ALL-UP SPANS RACES, and a flat list of eight horses hides which leg is
  // which. One line per race, the race number first — so a four-leg ticket
  // reads as four decisions rather than a run-on of names that spills out of
  // the column.
  const legs = new Map();
  b.selections.forEach((s) => {
    const key = s.race_no ?? b.race_no;
    if (!legs.has(key)) legs.set(key, []);
    legs.get(key).push(s);
  });
  const multiRace = legs.size > 1;

  if (multiRace) {
    host.classList.add('multi');
    [...legs.entries()].sort((x, y) => (x[0] ?? 0) - (y[0] ?? 0))
      .forEach(([raceNo, picks]) => {
        const line = el('div', 'leg');
        line.append(el('span', 'rno', raceNo === null ? '⋯' : `R${raceNo}`));
        bankersFirst(picks).forEach((s, i) => {
          if (i) line.append(document.createTextNode(' · '));
          line.append(selectionChip(s, booked));
        });
        host.append(line);
      });
  } else {
    bankersFirst(b.selections).forEach((s, i) => {
      if (i) host.append(document.createTextNode(' · '));
      host.append(selectionChip(s, booked));
    });
  }

  if (!b.selections.length) host.textContent = DASH;
  host.title = b.selections.map((s) =>
    `${s.race_no ? `R${s.race_no} ` : ''}${s.horse_no} ${s.horse_name ?? ''}`
    + `${s.is_banker ? ' (banker)' : ''}`
    + `${s.place ? ` — finished ${s.place}` : ''}`).join('\n');
  return host;
}

function ledgerRow(b) {
  const row = el('div', 'led-row');
  row.append(el('div', 'date', b.race_date));
  row.append(el('div', 'venue', b.venue ?? DASH));
  // An all-up has no race number of its own: it spans them.
  row.append(el('div', 'num', b.race_no === null ? '⋯' : `R${b.race_no}`));
  row.append(el('div', 'type', b.bet_type.replace('_BANKER', '·B')
    .replace('ALLUP_', 'AU·')));
  row.append(selectionText(b));
  row.append(el('div', 'num', money(b.stake)));
  row.append(el('div', 'num', money(b.returned)));

  const pnl = el('div', `pnl ${b.pnl >= 0 ? 'pos' : 'neg'}`,
    money(b.pnl, { sign: true }));
  row.append(pnl);

  // No snapshot at or before the wager. Not zero CLV — unmeasured, and the
  // difference matters: 426 of the 1,078 bets carry a timestamp from when the
  // row was written rather than when the bet was struck.
  const clv = el('div', b.clv === null ? 'clv none'
    : `clv ${b.clv >= 0 ? 'pos' : 'neg'}`, b.clv === null ? DASH : pct(b.clv, 0));
  if (b.clv !== null) {
    clv.title = `mean over ${b.clv_legs} priced selection`
      + `${b.clv_legs === 1 ? '' : 's'} on this ticket`;
  } else {
    clv.title = 'no price captured at or before this bet was struck';
  }
  row.append(clv);

  const src = el('div', 'src');
  src.append(el('span', b.statement_confirmed ? 'confirmed' : 'quoted',
    b.statement_confirmed ? 'STMT' : 'LOG'));
  src.title = b.statement_confirmed
    ? `confirmed against statement reference ${b.bookie_ref}`
    : (b.bookie_ref
      ? `the log quotes reference ${b.bookie_ref}; no statement has been read`
      : 'logged bet with no statement reference');
  row.append(src);
  return row;
}

function renderLedger() {
  renderChips();
  renderHead();
  const rows = state.bets.filter(matches);
  const host = $('led-rows');
  if (!rows.length) {
    host.replaceChildren(el('div', 'no-match',
      'NO BET MATCHES THESE FILTERS'));
  } else {
    host.replaceChildren(...rows.map(ledgerRow));
  }
  $('match-count').textContent =
    `${rows.length} of ${state.bets.length}`;

  const staked = rows.reduce((t, b) => t + (b.stake ?? 0), 0);
  const returned = rows.reduce((t, b) => t + (b.returned ?? 0), 0);
  const priced = rows.filter((b) => b.clv !== null).length;
  const foot = $('led-foot');
  foot.replaceChildren();
  const item = (label, value, cls) => {
    const s = el('span');
    s.append(document.createTextNode(`${label} `));
    s.append(el('b', cls ?? null, value));
    return s;
  };
  foot.append(item('STAKED', money(staked)));
  foot.append(item('RETURNED', money(returned)));
  foot.append(item('P/L', money(returned - staked, { sign: true }),
    returned >= staked ? 'pos' : 'neg'));
  foot.append(item('WITH A BOOKED HORSE',
    String(rows.filter((b) => b.blackbook.length).length)));
  foot.append(el('span', 'right',
    `${priced} of ${rows.length} priced against the close`));
}

/* ── analysis ────────────────────────────────────────────────────────────── */

function sliceTable(slices, { first = 'SLICE' } = {}) {
  const table = el('table', 'slice-tab');
  const head = el('tr');
  [[first, ''], ['n', 'r'], ['STAKE', 'r'], ['RETURN', 'r'],
   ['STRIKE', 'r'], ['ROI', 'r'], ['95% CI ON ROI', '']]
    .forEach(([label, cls]) => head.append(el('th', cls || null, label)));
  const thead = el('thead');
  thead.append(head);
  table.append(thead);

  const body = el('tbody');
  slices.forEach((s) => {
    const tr = el('tr', `${s.thin ? 'thin' : ''}${s.clears_zero ? ' clears' : ''}`.trim());
    tr.append(el('td', 'label', s.label));
    tr.append(el('td', 'r', String(s.bets)));
    tr.append(el('td', 'r', money(s.staked)));
    tr.append(el('td', 'r', money(s.returned)));
    tr.append(el('td', 'r', s.strike_rate === null ? DASH
      : `${(s.strike_rate * 100).toFixed(0)}%`));
    // Colour only where the interval excludes zero. A green +392% on twenty
    // bets with an interval of [-85%, +1367%] is the lie this rule stops.
    const roiCls = `r roi${s.clears_zero ? (s.roi >= 0 ? ' pos' : ' neg') : ''}`;
    tr.append(el('td', roiCls, pct(s.roi, 0)));
    tr.append(el('td', 'ci', ciText(s.roi_ci)));
    if (s.thin) tr.title = `${s.bets} bets — under ${state.analysis.thin_bets}, `
      + 'so the interval is too wide for the point estimate to mean anything';
    body.append(tr);
  });
  table.append(body);
  return table;
}

function panel(title, note, { wide = false } = {}) {
  const box = el('section', `panel-box${wide ? ' wide' : ''}`);
  const hd = el('div', 'panel-hd');
  hd.append(el('span', 't', title));
  if (note) hd.append(el('span', 'n', note));
  box.append(hd);
  const body = el('div', 'panel-body');
  box.append(body);
  return { box, body };
}

/** The cumulative P/L curve. An SVG rather than a library: it is one path. */
function curve(series) {
  const host = el('div', 'curve');
  if (series.length < 2) {
    host.append(el('div', 'empty-line', 'ONE MEETING — NOTHING TO PLOT YET'));
    return host;
  }
  const W = 1000;
  const H = 168;
  const PAD = 8;
  const values = series.map((s) => s.cumulative);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = (hi - lo) || 1;
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (series.length - 1);
  const y = (v) => H - PAD - ((v - lo) / span) * (H - 2 * PAD);

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  const node = (name, attrs, cls) => {
    const n = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
    if (cls) n.setAttribute('class', cls);
    return n;
  };
  const path = series.map((s, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(s.cumulative).toFixed(1)}`).join(' ');
  svg.append(node('path', {
    d: `${path} L${x(series.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`,
  }, 'fill'));
  svg.append(node('line', { x1: PAD, y1: y(0), x2: W - PAD, y2: y(0) }, 'zero'));
  svg.append(node('path', { d: path }, 'line'));
  series.forEach((s, i) => {
    const dot = node('circle', { cx: x(i), cy: y(s.cumulative), r: 2.5 }, 'dot');
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    t.textContent = `${s.race_date} · ${s.bets} bets · `
      + `${money(s.staked)} staked · ${money(s.pnl, { sign: true })} on the day `
      + `· ${money(s.cumulative, { sign: true })} running`;
    dot.append(t);
    svg.append(dot);
  });
  host.append(svg);

  const axis = el('div', 'curve-axis');
  axis.append(el('span', null, series[0].race_date));
  axis.append(el('span', null,
    `n=${series.reduce((t, s) => t + s.bets, 0)} BETS OVER ${series.length} MEETINGS`));
  axis.append(el('span', null, series[series.length - 1].race_date));
  host.append(axis);
  return host;
}

function clvPanel(v) {
  const { box, body } = panel('CLOSING LINE VALUE',
    'BEATING THE CLOSE IS CLOSE TO THE DEFINITION OF A GOOD BET');
  if (!v.selections) {
    body.append(el('div', 'empty-line',
      'NO BET CAN BE PRICED — NO ODDS SNAPSHOT PRECEDES ANY WAGER'));
    return box;
  }
  const line = el('div', 'clv-line');
  line.append(el('span', `big${v.average >= 0 ? '' : ' neg'}`, pct(v.average, 2)));
  line.append(el('span', null, `95% CI ${ciText(v.ci)}`));
  line.append(el('span', null,
    `${(v.beat_share * 100).toFixed(0)}% beat the close`));
  line.append(el('span', 'n',
    `n=${v.selections} of ${v.of_selections} selections`));
  body.append(line);
  // Measured per SELECTION: a quinella backs three horses at three prices, so
  // there is no single price the ticket was struck at.
  body.append(el('div', 'caveat',
    'Measured per selection, not per ticket — a quinella backs three horses '
    + 'at three prices. Only a bet stamped on the day of its race can be '
    + `priced; the other ${v.of_selections - v.selections} selections carry a `
    + 'timestamp from when the row was written.'));
  return box;
}

function renderAnalysis() {
  const a = state.analysis;
  const host = $('analysis');
  host.replaceChildren();
  if (!a) { host.append(el('div', 'empty-line', 'LOADING')); return; }

  const rule = el('div', 'section-head');
  rule.append(el('span', 'title', 'HOW THE BETTING IS GOING'));
  rule.append(el('span', 'sub',
    `EVERY FIGURE CARRIES n AND AN INTERVAL · SLICES UNDER ${a.thin_bets} `
    + 'BETS ARE DIMMED'));
  host.append(rule);

  const panels = el('div', 'panels');

  const pl = panel('CUMULATIVE P/L',
    `PEAK ${money(a.cumulative.peak, { sign: true })} · `
    + `TROUGH ${money(a.cumulative.trough, { sign: true })}`);
  pl.body.append(curve(a.cumulative.series));
  panels.append(pl.box);

  panels.append(clvPanel(a.clv));

  const bt = panel('BY BET TYPE', 'TURNOVER FIRST', { wide: true });
  const btBox = el('div', 'table-box');
  btBox.append(sliceTable(a.by_type, { first: 'TYPE' }));
  bt.body.append(btBox);
  panels.append(bt.box);

  const au = panel('ALL-UP vs THE STRAIGHT BET IT REPLACED',
    `${a.all_up.meetings} MEETING${a.all_up.meetings === 1 ? '' : 'S'} `
    + 'AN ALL-UP WAS STRUCK', { wide: true });
  const auBox = el('div', 'table-box');
  auBox.append(sliceTable([a.all_up.all_up, a.all_up.straight]));
  au.body.append(auBox);
  au.body.append(el('div', 'caveat',
    'The straight side is restricted to the days a chain was struck. '
    + 'Comparing it against every straight bet in the ledger would compare '
    + 'two different sets of days as well as two different bet shapes.'));
  panels.append(au.box);

  const cc = panel('BY MARKET CONCENTRATION', 'DOES THE COVERAGE RULE HOLD',
    { wide: true });
  const ccBox = el('div', 'table-box');
  ccBox.append(sliceTable(
    [...a.concentration.bands, a.concentration.spanning_races], { first: 'BAND' }));
  cc.body.append(ccBox);
  cc.body.append(el('div', 'caveat',
    'Read off the closing prices, which every race has — only 17 of the 56 '
    + 'meetings carry an odds snapshot. An all-up spans races and so has no '
    + 'single market to be classified by.'));
  panels.append(cc.box);

  const fv = panel('FAVOURITE INCLUDED vs EXCLUDED',
    `${((a.favourite.included.share ?? 0) * 100).toFixed(0)}% OF TICKETS `
    + 'INCLUDED THE FAVOURITE', { wide: true });
  const fvBox = el('div', 'table-box');
  fvBox.append(sliceTable([a.favourite.included, a.favourite.excluded]));
  fv.body.append(fvBox);
  panels.append(fv.box);

  host.append(panels);

  const foot = el('div', 'section-foot');
  foot.append(el('span', null,
    `${a.overall.bets} bets · ${money(a.overall.staked)} staked`));
  foot.append(el('span', 'warn',
    a.overall.clears_zero
      ? 'the overall interval excludes zero'
      : `overall ROI ${pct(a.overall.roi)} with an interval of `
        + `${ciText(a.overall.roi_ci)} — not a profit that has been shown`));
  host.append(foot);
}

/* ── reconciliation ──────────────────────────────────────────────────────── */

function renderRecon() {
  const r = state.recon;
  const host = $('recon');
  host.replaceChildren();
  if (!r) { host.append(el('div', 'empty-line', 'LOADING')); return; }

  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'RECONCILIATION'));
  head.append(el('span', 'sub', 'IMPORTED STATEMENT ROWS AGAINST LOGGED BETS'));
  host.append(head);

  const tiles = el('div', 'recon-tiles');
  const tile = (k, v, pending) => {
    const t = el('div', `recon-tile${pending ? ' pending' : ''}`);
    t.append(el('span', 'k', k));
    t.append(el('span', 'v', String(v)));
    return t;
  };
  tiles.append(tile('CONFIRMED BY A STATEMENT', r.confirmed));
  tiles.append(tile('QUOTED, NOT READ', r.quoted_not_read, true));
  tiles.append(tile('NO REFERENCE', r.no_reference, true));
  tiles.append(tile('BLOCKS COMPARED', r.blocks));
  tiles.append(tile('DISAGREE', r.disagrees.length));
  host.append(tiles);

  // Imported rows keep a dashed border until a statement has actually been
  // read for them. Nothing is silently merged.
  host.append(el('div', 'caveat',
    'A reference the log quotes is not a statement that was read. '
    + `${r.quoted_not_read} bet(s) carry a reference recovered from the log's `
    + 'own notes; no statement has been imported for them, and they are not '
    + 'counted as reconciled.'));
  if (r.apportioned_note) host.append(el('div', 'caveat', r.apportioned_note));

  const files = panel('STATEMENTS READ', `${r.files.length} FILE`
    + `${r.files.length === 1 ? '' : 'S'}`);
  if (!r.files.length) {
    files.body.append(el('div', 'empty-line', 'NONE — EVERY ROW WAS LOGGED HERE'));
  } else {
    r.files.forEach((f) => {
      const row = el('div', 'file-row');
      row.append(el('span', 'n', `${f.bets} bets`));
      row.append(el('span', 'file', f.source_file));
      row.append(el('span', 'when', (f.imported_at ?? '').slice(0, 10)));
      files.body.append(row);
    });
  }
  host.append(files.box);

  const dis = panel('BLOCKS THE TWO DISAGREE ON',
    'THE CASE THIS SECTION EXISTS FOR');
  if (!r.disagrees.length) {
    dis.body.append(el('div', 'empty-line',
      'NONE — EVERY BLOCK READ MATCHES THE LEDGER'));
  } else {
    r.disagrees.forEach((d) => {
      const row = el('div', 'recon-row disagree');
      row.append(el('span', 'ref', d.bookie_ref));
      row.append(el('span', 'file', d.source_file));
      row.append(el('span', 'r', `LEDGER ${money(d.ledger[1])}`));
      row.append(el('span', 'r', `STMT ${money(d.statement[1])}`));
      row.append(el('span', 'state', `${d.bets} ROWS`));
      dis.body.append(row);
    });
  }
  host.append(dis.box);
}

/* ── render ──────────────────────────────────────────────────────────────── */

function render() {
  renderViewToggle();
  renderScope();
  renderSummary();
  $('view-entry').hidden = state.view !== 'entry';
  $('view-ledger').hidden = state.view !== 'ledger';
  $('view-analysis').hidden = state.view !== 'analysis';
  $('view-recon').hidden = state.view !== 'recon';
  if (state.view === 'entry') renderEntry($('entry-host'));
  if (state.view === 'ledger') renderLedger();
  if (state.view === 'analysis') renderAnalysis();
  if (state.view === 'recon') renderRecon();
}

function wireSearch() {
  const input = $('search');
  const clear = $('clear-search');
  input.addEventListener('input', () => {
    state.search = input.value;
    clear.hidden = !input.value;
    render();
  });
  clear.addEventListener('click', () => {
    input.value = '';
    state.search = '';
    clear.hidden = true;
    render();
  });
}

async function boot() {
  renderNav($('nav'), 'bets.html');
  wireSearch();
  installPalette();
  initEntry(render);
  context.onChange(async (what) => {
    if (what === 'meeting' || what === 'date') {
      await loadEntry(context.date, context.summary);
      render();
    }
  });
  await context.init();
  if (context.date) await loadEntry(context.date, context.summary);
  render();
  await loadLedger();
}

/** Everything the ledger, analysis and reconciliation views read.
 *
 *  One call for all three, with the same account and the same window, so they
 *  cannot disagree about what they are counting.
 */
async function loadLedger() {
  const q = new URLSearchParams({ period: state.period });
  if (state.account) q.set('account', state.account);
  // Anchored on the meeting in the header, not on today: "this week" while
  // looking at an April meeting means that April week.
  if (context.date) q.set('anchor', context.date);
  const qs = q.toString();
  const [ledger, analysis, recon] = await Promise.all([
    api.bets(`?limit=2000&${qs}`),
    api.betsAnalysis(qs),
    api.betsReconciliation(qs),
  ]);
  state.bets = ledger.bets;
  state.window = ledger.window;
  state.analysis = analysis;
  state.recon = recon;
  render();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});

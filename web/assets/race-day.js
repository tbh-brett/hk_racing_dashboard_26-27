/* Race Day — ported from web/design-source/Race Day.dc.html.
 *
 * The artboard's own structure, in order: race strip, blackbook band,
 * head-to-head band, race context bar, the 16-column card, a detail aside,
 * and a footer of standing facts. Keyboard: arrows move the selected runner,
 * digits switch race.
 *
 * The market price leads because it ranks horses better than every model here
 * (AUC .785 against .727), which the footer states outright.
 */
import { api, num } from './api.js';

const NAV = [
  ['Race Day', 'raceday.html'], ['Form Guide', 'form-guide.html'],
  ['Lookup', 'lookup.html'], ['Bets', 'bets.html'],
  ['Blackbook', 'blackbook.html'], ['Results', 'results.html'],
  ['Trials', 'trials.html'], ['Model Analysis', 'model-analysis.html'],
];

const $ = (id) => document.getElementById(id);
const DASH = '—';
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const svg = (tag, attrs) => {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
  return n;
};

const state = {
  date: null, race: 1, summary: null, card: null,
  selected: 0, sort: null, sortDir: 1, bbOpen: false, h2hOpen: true,
};

const COLS = [
  ['no', 'NO', 'c-num'], ['name', 'HORSE', ''], ['style', 'STYLE', ''],
  ['draw', 'DR', 'c-num'], ['jockey', 'JOCKEY', ''], ['trainer', 'TRAINER', ''],
  ['wt', 'WT', 'c-right'], ['odds', 'ODDS', 'c-right'],
  ['move', 'MOVE', 'c-right'], ['win', 'WIN%', 'c-right'],
  ['mkt', 'MKT', 'c-num'], ['sarr', 'SARR', 'c-num'],
  ['edge', 'EDGE', 'c-num'], ['fig', 'LAST FIGURE', ''],
  ['last', 'LAST RUN', ''], ['bb', 'BB', 'c-num'],
];

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderNav() {
  $('nav').replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === 'raceday.html') a.setAttribute('aria-current', 'page');
    return a;
  }));
}

async function renderFreshness() {
  try {
    const s = await api.status();
    $('meeting-context').textContent = s.latest_meeting
      ? `latest meeting · ${s.latest_meeting}` : 'no meetings loaded';
    $('freshness').replaceChildren(...Object.entries(s.tables).map(([t, i]) => {
      const b = el('span', 'src');
      b.append(el('span', 'name', t.replace('runner_', '')));
      b.append(el('span', i.current ? 'ok' : 'stale', i.rows ? (i.current ? '✓' : '⚠') : DASH));
      b.title = `${i.rows.toLocaleString()} rows, through ${i.through ?? 'never'}`;
      return b;
    }));
  } catch { /* informational */ }
}

/* ── race strip ──────────────────────────────────────────────────────────── */

function renderStrip() {
  const races = state.summary?.races ?? [];
  $('race-chips').replaceChildren(...races.map((r) => {
    const b = el('button', 'race-chip', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`
      + (r.band ? ` · ${r.band}` : '');
    b.addEventListener('click', () => { state.race = r.race_no; loadRace(); });
    return b;
  }));
  const withOdds = races.filter((r) => r.concentration !== null).length;
  $('strip-right').textContent =
    `${races.length} RACES · ${withOdds} PRICED`;
}

/* ── bands ───────────────────────────────────────────────────────────────── */

function renderBlackbookBand() {
  const host = $('band-bb');
  const row = el('div', 'band-row');
  const tag = el('div', 'band-tag');
  tag.append(el('span', 'dot'));
  tag.append(document.createTextNode('BLACKBOOK'));
  tag.append(el('span', 'n', '0'));
  tag.append(el('span', 'sub', 'TODAY'));
  row.append(tag);

  const body = el('div', 'band-body');
  // Blackbook storage does not exist yet. The band says so plainly rather
  // than rendering an empty strip that reads as "nothing is booked".
  body.append(el('div', 'band-empty', 'NO BLACKBOOK STORAGE YET'));
  row.append(body);
  host.replaceChildren(row);
}

function swingLabel(p) {
  if (p.swing === null || p.swing === undefined) return 'NO WT DATA';
  return `${p.swing}LB`;
}

function renderH2HBand() {
  const host = $('band-h2h');
  const pairs = state.card?.head_to_head ?? [];
  const row = el('div', 'band-row');
  const tag = el('div', 'band-tag');
  tag.append(el('span', 'dot'));
  tag.append(document.createTextNode('HEAD TO HEAD'));
  tag.append(el('span', 'n', String(pairs.length)));
  tag.append(el('span', 'sub', 'PAIRS MEET AGAIN'));
  row.append(tag);
  row.append(el('div', 'band-body'));

  const toggle = el('button', 'band-toggle',
    state.h2hOpen ? 'HIDE' : 'SHOW');
  toggle.addEventListener('click', () => { state.h2hOpen = !state.h2hOpen; render(); });
  row.append(toggle);
  host.replaceChildren(row);

  if (!state.h2hOpen || !pairs.length) return;
  const grid = el('div', 'h2h-grid');
  pairs.slice(0, 4).forEach((p) => {
    const c = el('div', 'h2h-cell');
    const l1 = el('div', 'h2h-line');
    l1.append(el('span', 'who', `${p.a_no} ${p.a_name.split(' ')[0]}`));
    l1.append(el('span', 'v', 'v'));
    l1.append(el('span', 'who', `${p.b_no} ${p.b_name.split(' ')[0]}`));
    l1.append(el('span', 'rec', p.record));
    c.append(l1);

    const l2 = el('div', 'h2h-meta');
    l2.append(el('span', 'k', 'LAST'));
    l2.append(el('span', 'v2', p.last_date));
    l2.append(el('span', null, p.last_cond));
    l2.append(el('span', 'v2', p.last_line));
    c.append(l2);

    const l3 = el('div', 'h2h-meta');
    l3.append(el('span', 'k', 'WT GAP'));
    l3.append(el('span', 'v2',
      `${p.gap_then ?? DASH} → ${p.gap_now ?? DASH}`));
    // Escalating tiers at 4, 6 and 8lb. Most pairs clear none of them, and
    // that is correct rather than a bug.
    l3.append(el('span', `swing swing-${p.swing_tier}`, swingLabel(p)));
    c.append(l3);

    const l4 = el('div', 'h2h-meta');
    l4.append(el('span', 'k', `GATE ${p.a_gate ?? DASH}`));
    l4.append(el('span', 'k', String(p.b_gate ?? DASH)));
    c.append(l4);
    grid.append(c);
  });
  host.append(grid);
}

/* ── race context bar ────────────────────────────────────────────────────── */

function renderRaceBar() {
  const c = state.card;
  const bar = $('race-bar');
  if (!c) { bar.replaceChildren(); return; }
  const nt = el('div', 'no-time');
  nt.append(el('span', 'rno', `R${c.race_no}`));
  nt.append(el('span', 'rtime', c.venue ?? ''));
  bar.replaceChildren(nt);

  const conds = el('div', 'conds');
  [['DIST', c.distance ? `${c.distance}m` : DASH],
   ['CLASS', c.race_class ?? DASH],
   ['GOING', c.going ?? DASH],
   ['COURSE', c.course ?? DASH],
   ['FIELD', c.field_size]].forEach(([k, v]) => {
    const d = el('div');
    d.append(el('span', 'k', `${k} `));
    d.append(document.createTextNode(String(v)));
    conds.append(d);
  });
  bar.append(conds);

  const conc = c.concentration ?? {};
  const box = el('div', 'conc-box');
  box.append(el('span', 'k', 'MKT CONCENTRATION'));
  if (conc.value === null || conc.value === undefined) {
    box.append(el('span', 'conc-badge conc-weak', 'NO ODDS'));
  } else {
    const bars = el('div', 'conc-bars');
    [7, 11, 16].forEach((h) => {
      const i = el('i');
      i.style.height = `${h}px`;
      bars.append(i);
    });
    box.append(bars);
    box.append(el('span', 'v', `${(conc.value * 100).toFixed(0)}%`));
    box.append(el('span', `conc-badge conc-${conc.band}`, conc.band.toUpperCase()));
    // A price captured hours before the off is not the price the coverage
    // rule was measured on, and must not read as though it were.
    if (conc.stale) {
      const s = el('span', 'conc-stale', `⚠ ${Math.round(conc.age_hours)}h EARLY`);
      s.title = conc.note ?? '';
      box.append(s);
    }
  }
  bar.append(box);
}

/* ── card ────────────────────────────────────────────────────────────────── */

function renderHead() {
  $('card-head').replaceChildren(...COLS.map(([key, label, cls]) => {
    const th = el('th', cls);
    th.append(document.createTextNode(label));
    if (state.sort === key) {
      th.append(el('span', 'ind', state.sortDir > 0 ? '▲' : '▼'));
    }
    th.addEventListener('click', () => {
      if (state.sort !== key) { state.sort = key; state.sortDir = 1; }
      else if (state.sortDir > 0) { state.sortDir = -1; }
      else { state.sort = null; }          // asc → desc → number order
      render();
    });
    return th;
  }));
}

const STYLE_ORDER = ['Leader', 'On-Pace', 'Midfield', 'Closer'];

function sortRunners(runners) {
  const rows = [...runners];
  if (!state.sort) return rows.sort((a, b) => (a.horse_no ?? 0) - (b.horse_no ?? 0));
  const key = state.sort;
  const val = (r) => {
    switch (key) {
      case 'no': return r.horse_no ?? 0;
      case 'name': return r.horse_name ?? '';
      // Never alphabetical: Closer, Leader, Midfield, On-Pace is meaningless.
      case 'style': return STYLE_ORDER.indexOf(r.last_run?.pace_style ?? '') + 1 || 99;
      case 'draw': return r.draw ?? 99;
      case 'jockey': return r.jockey ?? '';
      case 'trainer': return r.trainer ?? '';
      case 'wt': return r.actual_weight ?? 0;
      case 'odds': return r.win_odds ?? 9e9;
      case 'move': return r.movement?.change_pct ?? 0;
      case 'win': return -(r.win_pct ?? 0);
      case 'mkt': return r.market_rank ?? 99;
      case 'sarr': return r.sarr_rank ?? 99;
      case 'edge': return r.rank_delta ?? 0;
      case 'fig': return -(r.last_run?.figure ?? 0);
      case 'last': return r.last_run?.days_ago ?? 9e9;
      case 'bb': return 0;
      default: return 0;
    }
  };
  return rows.sort((a, b) => {
    const x = val(a); const y = val(b);
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * state.sortDir;
  });
}

function movementCell(r) {
  const m = r.movement;
  const td = el('td', 'c-right');
  const box = el('div', 'mv');

  if (!m) {
    box.append(el('span', 'mv-none', DASH));
  } else if (m.observed === false) {
    // Two captures a minute apart observed nothing. Reporting 0% from them
    // would claim the market held steady, which is unsupported.
    const n = el('span', 'mv-none', 'no window');
    n.title = `captures only ${m.window_minutes} min apart`;
    box.append(n);
  } else {
    const cls = `mv-${m.direction}`;
    box.append(el('span', 'prev', num(m.early, 1)));
    box.append(el('span', `arrow ${cls}`,
      m.direction === 'shortened' ? '▼' : m.direction === 'drifted' ? '▲' : '·'));
    box.append(el('span', `pct ${cls}`, `${Math.abs(m.change_pct).toFixed(0)}%`));
  }

  // The shape of the money, in the row -- the artboard draws it inside this
  // cell rather than as a column of its own.
  const spark = sparkline(r);
  if (spark) box.append(spark);
  td.append(box);
  return td;
}

function sparkline(r) {
  if (!r.spark || r.spark_points_n < 2) return null;
  const s = svg('svg', { viewBox: '0 0 66 18', width: 50, height: 16,
                         preserveAspectRatio: 'none' });
  const colour = r.movement?.observed === false ? 'var(--text-faint)'
    : r.movement?.direction === 'shortened' ? 'var(--win)'
    : r.movement?.direction === 'drifted' ? 'var(--loss)' : 'var(--text-faint)';
  s.append(svg('polyline', { points: r.spark, fill: 'none',
                             stroke: colour, 'stroke-width': 1.6 }));
  s.append(svg('circle', { cx: r.spark_dot[0], cy: r.spark_dot[1],
                           r: 2.2, fill: colour }));
  s.setAttribute('aria-hidden', 'true');
  return s;
}

function edgeCell(r) {
  const td = el('td', 'c-num');
  const box = el('div', 'edge-cell');
  const d = r.rank_delta;
  if (d === null || d === undefined) { box.append(el('span', 'v mv-none', DASH)); td.append(box); return td; }
  // Negative means the model likes it more than the market does.
  const colour = d <= -3 ? 'var(--edge)' : d >= 3 ? 'var(--text-faint)' : 'var(--text-dim)';
  const v = el('span', 'v', d > 0 ? `+${d}` : String(d));
  v.style.color = colour;
  box.append(v);
  const track = el('div', 'edge-track');
  track.style.justifyContent = d < 0 ? 'flex-start' : 'flex-end';
  const fill = el('i');
  fill.style.width = `${Math.min(100, Math.abs(d) * 12)}%`;
  fill.style.background = colour;
  track.append(fill);
  box.append(track);
  td.append(box);
  return td;
}

function cardRow(r, index) {
  const tr = el('tr');
  tr.setAttribute('aria-selected', String(index === state.selected));
  tr.addEventListener('mouseenter', () => { state.selected = index; renderDetail(); });
  tr.addEventListener('click', () => { state.selected = index; renderDetail(); });

  const no = el('td', 'td-no');
  no.append(el('span', 'n', String(r.horse_no ?? DASH)));
  tr.append(no);

  const name = el('td');
  const box = el('div', 'horse');
  box.append(el('span', 'nm', r.horse_name));
  const trip = (r.last_run?.tags ?? [])[0];
  if (trip) box.append(el('span', 'trip', trip.replace(/_/g, ' ')));
  name.append(box);
  tr.append(name);

  const st = el('td');
  const s = r.last_run?.pace_style;
  st.append(el('span', `style style-${(s ?? 'unknown').toLowerCase().replace('-', '')}`,
    s ?? DASH));
  tr.append(st);

  tr.append(el('td', 'c-num', String(r.draw ?? DASH)));
  tr.append(el('td', null, r.jockey ?? DASH));

  const trn = el('td', r.trainer_changed ? 'trainer-changed' : null);
  trn.append(el('span', 'tn', r.trainer ?? DASH));
  if (r.trainer_changed) trn.title = `was ${r.trainer_prev}`;
  tr.append(trn);

  tr.append(el('td', 'c-right', String(r.actual_weight ?? DASH)));

  const od = el('td', 'c-right');
  od.append(el('div', 'odds-win', r.win_odds ? num(r.win_odds, 1) : DASH));
  // Place odds are scraped, never derived: there is no fixed ratio to win.
  od.append(el('div', 'odds-place', r.place_odds ? num(r.place_odds, 1) : DASH));
  tr.append(od);

  tr.append(movementCell(r));
  tr.append(el('td', 'c-right', r.win_pct !== null && r.win_pct !== undefined
    ? `${r.win_pct}%` : DASH));
  tr.append(el('td', 'c-num', String(r.market_rank ?? DASH)));

  const sr = el('td', 'c-num');
  const sv = el('span', null, String(r.sarr_rank ?? DASH));
  if (r.sarr_rank === 1) sv.style.color = 'var(--edge)';
  sr.append(sv);
  tr.append(sr);

  tr.append(edgeCell(r));

  const fg = el('td');
  const f = el('div', 'fig-cell');
  const lr = r.last_run;
  if (lr?.figure !== null && lr?.figure !== undefined) {
    const colour = lr.figure >= 100 ? 'var(--win)' : 'var(--loss)';
    const v = el('span', 'v', num(lr.figure, 0));
    v.style.color = colour;
    f.append(v);
    const m = lr.figure_display?.match(/([+-][\d.]+L)/);
    if (m) { const l = el('span', 'len', m[1]); l.style.color = colour; f.append(l); }
    const c = lr.figure_display?.match(/(low|medium|high)/);
    if (c) f.append(el('span', 'conf', c[1].toUpperCase()));
  } else {
    f.append(el('span', 'mv-none', DASH));
  }
  fg.append(f);
  tr.append(fg);

  tr.append(el('td', null, lr
    ? `${lr.place ?? DASH} · ${lr.days_ago}d` : DASH));
  tr.append(el('td', 'c-num', ''));
  return tr;
}

/* ── detail aside ────────────────────────────────────────────────────────── */

function renderDetail() {
  const host = $('detail');
  const rows = sortRunners(state.card?.runners ?? []);
  const r = rows[state.selected];
  if (!r) { host.replaceChildren(el('div', 'empty', 'no runner selected')); return; }

  host.replaceChildren();
  const dh = el('div', 'dh');
  dh.append(el('span', 'no', String(r.horse_no ?? DASH)));
  dh.append(el('span', 'nm', r.horse_name));
  dh.append(el('span', 'od', r.win_odds ? num(r.win_odds, 1) : DASH));
  host.append(dh);

  const tr = el('section', r.trainer_changed ? 'trainer-changed' : null);
  const th = el('div', 'h2h-meta');
  th.append(el('span', 'k', 'TRAINER'));
  th.append(el('span', 'v2', r.trainer ?? DASH));
  if (r.trainer_changed) th.append(el('span', 'k', `since ${r.trainer_prev}`));
  tr.append(th);
  host.append(tr);

  const form = el('section');
  form.append(el('h6', null, 'LAST SIX'));
  const tbl = el('table');
  const runs = r.form ?? [];
  if (!runs.length) {
    form.append(el('div', 'empty', 'loading…'));
    loadForm(r, form);
  }
  form.append(tbl);
  host.append(form);

  const shape = el('section');
  shape.append(el('h6', null, 'MARKET SHAPE · WIN %'));
  const maxPct = Math.max(...rows.map((x) => x.win_pct ?? 0), 1);
  rows.forEach((x) => {
    const row = el('div', 'shape-row');
    row.append(el('span', 'no', String(x.horse_no ?? DASH)));
    const track = el('div', 'shape-track');
    const i = el('i');
    i.style.width = `${(100 * (x.win_pct ?? 0)) / maxPct}%`;
    if (x.horse_no === r.horse_no) i.style.background = 'var(--edge)';
    track.append(i);
    row.append(track);
    row.append(el('span', 'pct', x.win_pct !== null && x.win_pct !== undefined
      ? `${x.win_pct}` : DASH));
    shape.append(row);
  });
  host.append(shape);

  const dis = el('section');
  dis.append(el('h6', null, `MODEL vs MARKET · R${state.card.race_no}`));
  const disagreements = rows
    .filter((x) => x.rank_delta !== null && x.rank_delta !== undefined)
    .sort((a, b) => a.rank_delta - b.rank_delta).slice(0, 3);
  if (!disagreements.length) dis.append(el('div', 'empty', 'no model ranks'));
  disagreements.forEach((x) => {
    const row = el('div', 'dis-row');
    const e = el('span', 'e', x.rank_delta > 0 ? `+${x.rank_delta}` : String(x.rank_delta));
    e.style.color = x.rank_delta <= -3 ? 'var(--edge)' : 'var(--text-dim)';
    row.append(e);
    row.append(el('span', null, `${x.horse_no} ${x.horse_name}`));
    row.append(el('span', 'r', `SARR ${x.sarr_rank ?? DASH} · MKT ${x.market_rank ?? DASH}`));
    dis.append(row);
  });
  host.append(dis);
}

async function loadForm(runner, section) {
  try {
    const data = await api.horse(runner.horse_name, 6);
    runner.form = data.runs;
    const tbl = section.querySelector('table');
    section.querySelector('.empty')?.remove();
    tbl.replaceChildren(...data.runs.map((f) => {
      const tr = el('tr');
      tr.append(el('td', null, f.race_date?.slice(5) ?? DASH));
      tr.append(el('td', null, `${f.distance ?? DASH} ${f.going ?? ''}`));
      const pos = el('td', 'c-right', String(f.place ?? f.place_code ?? DASH));
      if (f.place === 1) pos.style.color = 'var(--win)';
      tr.append(pos);
      const fig = el('td', 'c-right', f.et_figure ? num(f.et_figure, 0) : DASH);
      if (f.et_figure) fig.style.color = f.et_figure >= 100 ? 'var(--win)' : 'var(--loss)';
      tr.append(fig);
      tr.append(el('td', 'c-right', f.win_odds ? num(f.win_odds, 1) : DASH));
      return tr;
    }));
  } catch {
    section.querySelector('.empty')?.replaceChildren(document.createTextNode('unavailable'));
  }
}

/* ── footer: standing facts, not per-race values ─────────────────────────── */

function renderFoot() {
  const c = state.card;
  const foot = $('card-foot');
  const bits = [];
  if (c?.overround !== null && c?.overround !== undefined) {
    bits.push(`OVERROUND ${c.overround}%`);
  }
  bits.push(c?.place_ratio_range
    ? `PLACE ODDS ARE SCRAPED, NEVER 3× WIN — RATIO RUNS ${c.place_ratio_range} ON THIS CARD`
    : 'PLACE ODDS ARE SCRAPED, NEVER DERIVED FROM WIN');
  bits.push('STYLE SORTS LEADER → ON-PACE → MIDFIELD → CLOSER');
  bits.push('MODEL AUC .727 · MARKET AUC .785');
  foot.replaceChildren(...bits.map((b) => el('span', null, b)));
  foot.append(el('span', 'keys', '↑↓ runner · 1–9 race · click header to sort'));
}

/* ── render / load ───────────────────────────────────────────────────────── */

function render() {
  renderStrip();
  renderBlackbookBand();
  renderH2HBand();
  renderRaceBar();
  renderHead();
  const rows = sortRunners(state.card?.runners ?? []);
  $('card-body').replaceChildren(...rows.map(cardRow));
  renderFoot();
  renderDetail();
}

async function loadRace() {
  try {
    state.card = await api.raceCard(state.date, state.race);
    state.selected = 0;
  } catch (e) {
    state.card = null;
    $('card-body').replaceChildren(el('tr', null, `failed to load: ${e.message}`));
    return;
  }
  render();
}

async function loadMeeting() {
  state.date = document.querySelector('#meeting-picker')?.value ?? state.date;
  state.summary = await api.raceDayMeeting(state.date);
  state.race = state.summary.races[0]?.race_no ?? 1;
  await loadRace();
}

function onKey(e) {
  const rows = sortRunners(state.card?.runners ?? []);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = state.selected + (e.key === 'ArrowDown' ? 1 : -1);
    state.selected = Math.max(0, Math.min(rows.length - 1, next));
    render();
  } else if (/^[1-9]$/.test(e.key)) {
    const no = Number(e.key);
    if ((state.summary?.races ?? []).some((r) => r.race_no === no)) {
      state.race = no;
      loadRace();
    }
  }
}

async function init() {
  renderNav();
  renderFreshness();
  const meetings = await api.meetings(60);
  const picker = el('select');
  picker.id = 'meeting-picker';
  picker.replaceChildren(...meetings.map((m) => {
    const o = el('option', null, `${m.race_date} · ${m.venue ?? ''} · ${m.races}R`);
    o.value = m.race_date;
    return o;
  }));
  picker.addEventListener('change', loadMeeting);
  $('strip-right').before(picker);
  state.date = meetings[0]?.race_date;
  document.addEventListener('keydown', onKey);
  await loadMeeting();
}

init();

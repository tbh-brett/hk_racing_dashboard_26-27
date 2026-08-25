/* Race Day — the card.
 *
 * The moment this is for is twenty minutes before a race. Four questions have
 * to be answerable at a glance: has the market moved and on which horse, do
 * the models disagree with the price, is anything here blackbooked, and is the
 * race concentrated enough to be worth covering.
 *
 * The market price is the best predictor available (AUC 0.785 against 0.727
 * for the best model here), so odds lead and the models sit beside them.
 */
import { api, num } from './api.js';
import { anchoredPanel } from './overlay.js';

const NAV = [
  ['Race Day', 'raceday.html'], ['Form Guide', 'form-guide.html'],
  ['Lookup', 'lookup.html'], ['Bets', 'bets.html'],
  ['Blackbook', 'blackbook.html'], ['Results', 'results.html'],
  ['Trials', 'trials.html'], ['Model Analysis', 'model-analysis.html'],
];

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const DASH = '—';
const teardowns = [];
let state = { date: null, race: 1, summary: null, card: null };

function renderNav(current) {
  $('nav').replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === current) a.setAttribute('aria-current', 'page');
    return a;
  }));
}

async function renderFreshness() {
  try {
    const s = await api.status();
    $('meeting-context').textContent = s.latest_meeting
      ? `latest meeting · ${s.latest_meeting}` : 'no meetings loaded';
    $('freshness').replaceChildren(...Object.entries(s.tables).map(([t, info]) => {
      const box = el('span', 'src');
      box.append(el('span', 'name', t.replace('runner_', '')));
      box.append(el('span', info.current ? 'ok' : 'stale',
        info.rows ? (info.current ? '✓' : '⚠') : DASH));
      box.title = `${info.rows.toLocaleString()} rows, through ${info.through ?? 'never'}`;
      return box;
    }));
  } catch { /* informational only */ }
}

/* ── movement: direction and magnitude, never one without the other ──────── */

function movementCell(move) {
  const box = el('span', 'c-move');
  if (!move) {
    box.append(el('span', 'move none', DASH));
    return box;
  }
  // Two captures a minute apart observed nothing. Showing 0% from them would
  // claim the market held steady, which is a different and unsupported claim.
  if (move.observed === false) {
    const n = el('span', 'move none', 'no window');
    n.title = `captures only ${move.window_minutes} min apart — `
      + 'too close together to observe movement';
    box.append(n);
    return box;
  }
  const arrow = move.direction === 'shortened' ? '▼'
    : move.direction === 'drifted' ? '▲' : '·';
  const w = el('span', `move ${move.direction}`);
  w.append(el('span', 'arrow', arrow));
  w.append(el('span', null, `${Math.abs(move.change_pct).toFixed(0)}%`));
  w.title = `${move.early} → ${move.late}`;
  box.append(w);
  return box;
}

/* ── model vs market ─────────────────────────────────────────────────────── */

function rankCell(runner) {
  const box = el('span', 'c-rank');
  const w = el('span', 'rank');
  const model = runner.sarr_rank;
  const mkt = runner.market_rank;
  if (!model && !mkt) { box.append(el('span', 'move none', DASH)); return box; }
  // A model rank well ahead of the market rank is the disagreement worth
  // seeing. It is not a recommendation: no model here beats the price.
  const edge = runner.rank_delta !== null && runner.rank_delta <= -3;
  w.append(el('span', edge ? 'edge' : 'model', model ?? DASH));
  w.append(el('span', 'sep', '/'));
  w.append(el('span', 'mkt', mkt ?? DASH));
  if (runner.rank_delta !== null) {
    w.title = `model ${model}, market ${mkt} (${runner.rank_delta > 0 ? '+' : ''}${runner.rank_delta})`;
  }
  box.append(w);
  return box;
}

function figureCell(runner) {
  const box = el('span', 'c-fig');
  const last = runner.last_run;
  const w = el('span', 'fig');
  if (!last || last.figure === null) {
    w.append(el('span', 'none', DASH));
  } else {
    // Never a bare number: the figure carries its length equivalent and the
    // sample size behind it.
    w.append(el('span', 'v', num(last.figure, 0)));
    w.append(el('span', 'ctx', ` ${last.figure_display?.match(/\(([^)]+)\)/)?.[1] ?? ''}`));
    w.title = last.figure_display ?? '';
  }
  box.append(w);
  return box;
}

function lastRunCell(runner) {
  const box = el('span', 'c-flags');
  const last = runner.last_run;
  const w = el('div', 'lastrun');
  if (!last) { w.append(el('span', 'days', DASH)); box.append(w); return box; }
  w.append(el('span', 'pl', last.place ?? DASH));
  w.append(el('span', 'days', `${last.days_ago}d`));
  [...(last.tags ?? []).slice(0, 1), ...(last.lane_notes ?? []).slice(0, 1)]
    .forEach((t, i) => w.append(el('span', `flag ${i === 0 ? 'trouble' : 'lane'}`,
      t.replace(/_/g, ' '))));
  box.append(w);
  return box;
}

function cardRow(runner) {
  const row = el('div', 'card-row');
  row.append(el('span', 'c-no', runner.horse_no ?? DASH));
  row.append(el('span', 'c-horse', runner.horse_name));
  row.append(el('span', 'c-draw', runner.draw ?? DASH));
  row.append(el('span', 'c-wt', runner.actual_weight ?? DASH));
  row.append(el('span', 'c-jockey', runner.jockey ?? DASH));

  const odds = el('span', 'c-odds');
  odds.append(el('span', 'win', runner.win_odds ? num(runner.win_odds, 1) : DASH));
  odds.append(el('span', 'place', runner.place_odds ? num(runner.place_odds, 1) : DASH));
  row.append(odds);

  row.append(movementCell(runner.movement));
  row.append(rankCell(runner));
  row.append(figureCell(runner));

  const style = el('span', 'c-style');
  const s = runner.last_run?.pace_style;
  style.append(el('span', `style style-${(s ?? 'unknown').toLowerCase().replace('-', '')}`,
    s ?? DASH));
  row.append(style);

  row.append(lastRunCell(runner));

  teardowns.push(anchoredPanel(row, () => runnerPanel(runner)));
  return row;
}

function runnerPanel(runner) {
  const p = el('div', 'fit');
  p.append(el('h5', null, runner.horse_name));
  const last = runner.last_run;
  const rows = [
    ['Trainer', runner.trainer ?? DASH],
    ['Draw', runner.draw ?? DASH],
    ['Market rank', runner.market_rank ?? DASH],
    ['SARR rank', runner.sarr_rank ?? DASH],
    ['Last run', last ? `${last.place ?? DASH} · ${last.days_ago}d ago` : DASH],
    ['Last figure', last?.figure_display ?? DASH],
  ];
  rows.forEach(([k, v]) => {
    const r = el('div', 'fit-row');
    r.append(el('span', 'k', k));
    r.append(el('span', '', String(v)));
    p.append(r);
  });
  if (last?.tags?.length) {
    p.append(el('div', 'fit-note', `Last run: ${last.tags.join(', ')}`));
  }
  return p;
}

/* ── race bar ────────────────────────────────────────────────────────────── */

function renderRaceBar(card) {
  const bar = $('race-bar');
  const c = card.concentration ?? {};
  bar.replaceChildren();
  bar.append(el('span', 'cond',
    `${card.distance ?? DASH}m · ${card.race_class ? `Class ${card.race_class}` : DASH}`
    + ` · ${card.going ?? DASH} · course ${card.course ?? DASH}`
    + ` · ${card.field_size} runners`));

  const conc = el('span', 'conc');
  conc.append(el('span', 'k', 'concentration'));
  if (c.value === null || c.value === undefined) {
    conc.append(el('span', 'band-weak', 'no odds'));
  } else {
    conc.append(el('span', `band-${c.band}`, `${c.value.toFixed(3)} ${c.band}`));
    // The coverage rule was measured on post-time prices. Saying how old this
    // one is costs nothing and stops a morning figure reading as post-time.
    if (c.stale) {
      const s = el('span', 'stale', `⚠ price ${Math.round(c.age_hours)}h early`);
      s.title = c.note ?? '';
      conc.append(s);
    }
  }
  bar.append(conc);
}

function renderStrip() {
  const races = state.summary?.races ?? [];
  $('race-strip').replaceChildren(...races.map((r) => {
    const b = el('button', 'race-btn', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`
      + (r.band ? ` · ${r.band}` : '');
    b.addEventListener('click', () => { state.race = r.race_no; loadRace(); });
    return b;
  }));
}

function render() {
  while (teardowns.length) teardowns.pop()();
  renderStrip();
  if (!state.card) { $('card').replaceChildren(el('div', 'note', 'no race loaded')); return; }
  renderRaceBar(state.card);
  // Market order: the price is the best available ranking, so the card leads
  // with it rather than by horse number.
  const runners = [...state.card.runners].sort(
    (a, b) => (a.win_odds ?? 9e9) - (b.win_odds ?? 9e9));
  $('card').replaceChildren(...runners.map(cardRow));
}

async function loadRace() {
  try {
    state.card = await api.raceCard(state.date, state.race);
  } catch (e) {
    state.card = null;
    $('card').replaceChildren(el('div', 'note', `failed to load: ${e.message}`));
    return;
  }
  render();
}

async function loadMeeting() {
  state.date = $('meeting-select').value;
  state.summary = await api.raceDayMeeting(state.date);
  state.race = state.summary.races[0]?.race_no ?? 1;
  await loadRace();
}

async function init() {
  renderNav('raceday.html');
  renderFreshness();
  const meetings = await api.meetings(60);
  $('meeting-select').replaceChildren(...meetings.map((m) => {
    const o = el('option', null, `${m.race_date} · ${m.venue ?? ''} · ${m.races}R`);
    o.value = m.race_date;
    return o;
  }));
  $('meeting-select').addEventListener('change', loadMeeting);
  await loadMeeting();
}

init();

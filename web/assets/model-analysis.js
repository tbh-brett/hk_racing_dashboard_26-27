/* Model Analysis — ported from web/design-source/Model Analysis.dc.html.
 *
 * The artboard's structure: a race strip carrying a MODEL VIEW toggle, then
 * stacked sections, each a sortable table with a footer of standing facts.
 * Its two sections are SARR and the blend; ET is a third, added at the user's
 * request after the artboard was drawn, and it keeps the same grammar.
 *
 * Design brief 05 §5: show WHY each model ranked each horse where it did. The
 * page doubles as documentation of each model's own limitations, which is why
 * the blend's fitted weight — zero on the fundamental stream — is on screen
 * rather than buried in a commit message.
 */
import { api, num, signed } from './api.js';
import { el, $, DASH, renderNav } from './vocab.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';

const VIEWS = [['sarr', 'SARR'], ['blend', 'BLEND'],
               ['backtest', 'DOES IT BEAT THE PRICE'], ['et', 'ET'],
               ['all', 'ALL']];
const WEIGHTS = [0, 0.1, 0.32, 1];


const state = {
  date: null, race: 1, races: [], view: 'all', weight: null,
  sarr: null, blend: null, et: null, backtest: null,
  sortS: { key: 'rank', dir: 1 }, sortB: { key: 'blended', dir: -1 },
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderViewToggle() {
  $('view-toggle').replaceChildren(...VIEWS.map(([key, label]) => {
    const b = el('button', null, label);
    b.setAttribute('aria-pressed', String(state.view === key));
    b.addEventListener('click', () => { state.view = key; render(); });
    return b;
  }));
}

function renderStrip() {
  $('race-chips').replaceChildren(...state.races.map((r) => {
    const b = el('button', 'race-chip', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`;
    b.addEventListener('click', () => context.setRace(r.race_no));
    return b;
  }));
}

/* ── shared table furniture ──────────────────────────────────────────────── */

/** A sortable header cell. `body` lets a column add a weight bar under its
 *  label without every column growing the same shape. */
function headCell(col, sort, onSort, body) {
  const th = el('th', col.align ?? '');
  if (col.width) th.style.width = col.width;
  if (sort.key === col.key) {
    th.setAttribute('aria-sort', sort.dir > 0 ? 'ascending' : 'descending');
  }
  const stack = el('div', 'stack');
  const label = el('span', null, col.label);
  if (sort.key === col.key) {
    label.append(el('span', 'ind', sort.dir > 0 ? '▲' : '▼'));
  }
  stack.append(label);
  if (body) stack.append(body);
  th.append(stack);
  th.title = col.title ?? '';
  th.addEventListener('click', () => onSort(col.key));
  return th;
}

function sortRows(rows, sort, value) {
  return [...rows].sort((a, b) => {
    const x = value(a, sort.key);
    const y = value(b, sort.key);
    // Nulls sort last in both directions: a missing figure is not a small one.
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * sort.dir;
  });
}

function cycle(sort, key, fallback) {
  if (sort.key !== key) return { key, dir: -1 };
  if (sort.dir === -1) return { key, dir: 1 };
  return { ...fallback };
}

/* ── SARR ────────────────────────────────────────────────────────────────── */

/** The two bars under a component's header. The upper is the fitted
 *  coefficient — what the model intends the term to be worth. The lower is
 *  mean |contribution| across every scored runner — what it is actually worth.
 *  They are separate because they disagree, and the disagreement is the point. */
function weightBars(c) {
  const box = el('div', `wbar${c.inert ? ' inert' : ''}`);
  const k1 = el('div', 'k');
  k1.append(el('span', null, `×${c.weight.toFixed(3)}`));
  box.append(k1);
  const t1 = el('div', 'track');
  const f1 = el('span', `fill weight${c.weight < 0 ? ' neg' : ''}`);
  f1.style.width = `${Math.max(4, c.weight_share * 100)}%`;
  t1.append(f1);
  box.append(t1);

  const k2 = el('div', 'k');
  k2.append(el('span', null, c.inert ? 'inert' : `±${c.mean_abs.toFixed(3)}`));
  box.append(k2);
  const t2 = el('div', 'track');
  const f2 = el('span', 'fill influence');
  f2.style.width = `${Math.max(c.influence_share > 0 ? 4 : 0,
                               c.influence_share * 100)}%`;
  t2.append(f2);
  box.append(t2);

  box.title = `weight ${c.weight.toFixed(4)} · realised mean |contribution| `
    + `${c.mean_abs.toFixed(4)} over ${c.rows.toLocaleString()} scored runners`
    + (c.inert ? ' · contributes nothing: no draw score is supplied' : '');
  return box;
}

function renderSarr() {
  const data = state.sarr;
  const head = $('sarr-head');
  const body = $('sarr-body');
  if (!data || !data.runners.length) {
    head.replaceChildren();
    body.replaceChildren(el('tr', null, ''));
    body.firstChild.append(el('td', 'model-empty',
      data ? 'NO SARR SCORES FOR THIS RACE' : 'LOADING'));
    body.firstChild.firstChild.colSpan = 12;
    $('sarr-foot').replaceChildren(el('span', null,
      data?.unscored?.length
        ? `NOT SCORED: ${data.unscored.join(', ')}` : ''));
    return;
  }

  const cols = [
    { key: 'no', label: 'NO', align: 'centre', width: '34px' },
    // no width: HORSE absorbs the slack
    { key: 'name', label: 'HORSE', align: 'left' },
    ...data.components.map((c) => ({
      key: c.component, label: c.component.toUpperCase(), comp: c, width: '78px' })),
    { key: 'score', label: 'SCORE', width: '66px' },
    { key: 'rank', label: 'RANK', align: 'centre', width: '46px' },
  ];
  const onSort = (key) => {
    state.sortS = cycle(state.sortS, key, { key: 'rank', dir: 1 });
    renderSarr();
  };
  head.replaceChildren(...cols.map((c) =>
    headCell(c, state.sortS, onSort, c.comp ? weightBars(c.comp) : null)));

  const value = (r, k) => {
    if (k === 'no') return r.horse_no;
    if (k === 'name') return r.horse_name;
    if (k === 'score') return r.sarr;
    if (k === 'rank') return r.sarr_rank;
    return r.components[k];
  };

  body.replaceChildren(...sortRows(data.runners, state.sortS, value).map((r) => {
    const tr = el('tr', r.sarr_rank <= 3 ? 'top3' : null);
    tr.append(el('td', 'centre no', String(r.horse_no ?? DASH)));
    tr.append(el('td', 'left horse', r.horse_name));
    data.components.forEach((c) => {
      const v = r.components[c.component];
      if (v === null || v === undefined) {
        tr.append(el('td', 'c-none', DASH));
        return;
      }
      // SARR is lower-is-better, so a NEGATIVE contribution helps. Colouring
      // by sign alone would read backwards.
      const cls = v < -0.02 ? 'c-helps' : v > 0.02 ? 'c-hurts' : 'c-flat';
      tr.append(el('td', cls, fmt3(v)));
    });
    const sc = el('td', 'score', fmt3(r.sarr));
    if (!r.components_sum_to_score) {
      sc.classList.add('c-hurts');
      sc.title = 'components do not sum to this score — rebuild runner_sarr';
    }
    tr.append(sc);
    const rk = el('td', 'centre rank', String(r.sarr_rank ?? DASH));
    rk.title = `${r.n_prior} prior runs`;
    tr.append(rk);
    return tr;
  }));

  // The footer states what the header bars show, and where they disagree.
  const inert = data.components.filter((c) => c.inert).map((c) => c.component);
  const byWeight = [...data.components].sort((a, b) => b.weight_share - a.weight_share)[0];
  const byInfluence = [...data.components].sort((a, b) => b.influence_share - a.influence_share)[0];
  const foot = $('sarr-foot');
  foot.replaceChildren();
  foot.append(el('span', null,
    'EACH HEADER CARRIES TWO BARS: THE FITTED COEFFICIENT, AND WHAT THE TERM '
    + 'ACTUALLY MOVES ACROSS EVERY SCORED RUNNER'));
  if (byWeight.component !== byInfluence.component) {
    foot.append(el('span', 'warn',
      `WIDEST COEFFICIENT IS ${byWeight.component.toUpperCase()}, LARGEST REALISED `
      + `INFLUENCE IS ${byInfluence.component.toUpperCase()} — THEY ARE NOT THE SAME TERM`));
  }
  if (inert.length) {
    foot.append(el('span', 'warn',
      `${inert.join(', ').toUpperCase()} CONTRIBUTES NOTHING: NO DRAW SCORE IS SUPPLIED`));
  }
  if (data.unscored.length) {
    foot.append(el('span', 'warn',
      `NOT SCORED: ${data.unscored.join(', ')}`));
  }
  foot.append(el('span', 'right',
    'LOWER SARR IS BETTER · BRIGHT HELPS THE SCORE, DIM HURTS IT · '
    + 'MODEL AUC .727 · MARKET AUC .785 · CLICK ANY COLUMN TO SORT'));
}

/** Three decimals, signed, with the leading zero dropped — the artboard's
 *  format, which keeps eight numeric columns readable at 12px. */
function fmt3(v) {
  if (v === null || v === undefined) return DASH;
  const s = Math.abs(v).toFixed(3).replace(/^0\./, '.');
  return (v > 0 ? '+' : v < 0 ? '−' : ' ') + s;
}

/* ── the blend ───────────────────────────────────────────────────────────── */

function renderWeightPicker() {
  const cal = state.blend?.calibration;
  const fitted = cal?.fitted_weight ?? 0;
  const host = $('weight-picker');
  host.replaceChildren(el('span', 'lab', 'FUND WEIGHT'));
  const seg = el('div', 'seg');
  WEIGHTS.forEach((w) => {
    const b = el('button', null, w.toFixed(2));
    const active = (state.weight ?? fitted) === w;
    b.setAttribute('aria-pressed', String(active));
    b.title = w === fitted ? 'the fitted weight'
      : `test log loss ${cal?.log_loss_by_weight?.[w.toFixed(2)] ?? '—'}`;
    b.addEventListener('click', () => { state.weight = w; loadBlend(); });
    seg.append(b);
  });
  host.append(seg);
}

function renderBlend() {
  const data = state.blend;
  const head = $('blend-head');
  const body = $('blend-body');
  if (!data || !data.runners.length) {
    head.replaceChildren();
    body.replaceChildren(el('tr', null, ''));
    body.firstChild.append(el('td', 'model-empty', data ? 'NO RUNNERS' : 'LOADING'));
    body.firstChild.firstChild.colSpan = 7;
    return;
  }
  renderWeightPicker();

  const cols = [
    { key: 'no', label: 'NO', align: 'centre', width: '34px' },
    { key: 'name', label: 'HORSE', align: 'left', width: '190px' },
    { key: 'fund', label: 'FUND PROB', width: '110px',
      title: 'SARR mapped to a win probability' },
    { key: 'raw', label: 'MKT PROB (RAW)', width: '130px',
      title: '1/odds, not normalised — the gap to 100% IS the overround' },
    { key: 'devig', label: 'MARKET (DE-VIGGED)', width: '150px' },
    // no width: BLENDED absorbs the slack, and the split bar uses it
    { key: 'blended', label: 'BLENDED (LIVE)' },
    { key: 'rank', label: 'RANK', align: 'centre', width: '46px' },
  ];
  const onSort = (key) => {
    state.sortB = cycle(state.sortB, key, { key: 'blended', dir: -1 });
    renderBlend();
  };
  head.replaceChildren(...cols.map((c) => headCell(c, state.sortB, onSort)));

  const value = (r, k) => ({
    no: r.horse_no, name: r.horse_name, fund: r.fundamental,
    raw: r.market_raw, devig: r.market_devig, blended: r.blended,
    rank: r.blend_rank === undefined ? null : -r.blend_rank,
  }[k]);

  body.replaceChildren(...sortRows(data.runners, state.sortB, value).map((r) => {
    const tr = el('tr', r.blend_rank <= 3 ? 'top3' : null);
    tr.append(el('td', 'centre no', String(r.horse_no ?? DASH)));
    tr.append(el('td', 'left horse', r.horse_name));
    tr.append(el('td', null, pct(r.fundamental)));
    tr.append(el('td', 'c-hurts', pct(r.market_raw)));
    tr.append(el('td', null, pct(r.market_devig)));

    const bl = el('td');
    if (r.blended === null) {
      bl.append(el('span', 'c-none', DASH));
    } else {
      const box = el('div', 'blend-cell');
      box.append(el('span', 'v', pct(r.blended)));
      // How much of THIS row's blend came from each stream. At the fitted
      // weight the amber half is empty, which is the finding made visible.
      const fundShare = data.weight && r.blended
        ? Math.max(0, Math.min(100, data.weight * r.fundamental / r.blended * 100))
        : 0;
      const split = el('div', 'split grow');
      const f = el('span', 'fund');
      f.style.width = `${fundShare}%`;
      const m = el('span', 'mkt');
      m.style.width = `${100 - fundShare}%`;
      split.append(f, m);
      split.title = `${fundShare.toFixed(0)}% fundamental · `
        + `${(100 - fundShare).toFixed(0)}% market`;
      box.append(split);
      bl.append(box);
    }
    tr.append(bl);
    tr.append(el('td', 'centre rank', String(r.blend_rank ?? DASH)));
    return tr;
  }));

  const cal = data.calibration;
  const foot = $('blend-foot');
  foot.replaceChildren();
  foot.append(el('span', null,
    `BLENDED = ${(data.weight * 100).toFixed(0)}% FUND + `
    + `${((1 - data.weight) * 100).toFixed(0)}% MARKET (DE-VIGGED) — `
    + 'THE BAR SPLITS EACH ROW BY SOURCE'));
  if (data.overround !== null) {
    foot.append(el('span', null,
      `MARKET (RAW) CARRIES OVERROUND ${data.overround}%; DE-VIGGING DIVIDES IT `
      + 'OUT PROPORTIONALLY SO THE COLUMN SUMS TO 100%'));
  }
  if (data.missing.unpriced || data.missing.unscored) {
    foot.append(el('span', 'warn',
      `${data.missing.unpriced} UNPRICED · ${data.missing.unscored} UNSCORED — `
      + 'BOTH STREAMS NEED THE WHOLE FIELD, SO THE SHORT ONE IS BLANK'));
  }
  foot.append(el('span', 'warn',
    (data.weight === cal.fitted_weight
      ? `FITTED WEIGHT ON THE FUNDAMENTAL IS ${cal.fitted_weight.toFixed(2)}: `
      : `YOU ARE VIEWING ${data.weight.toFixed(2)}; THE FITTED WEIGHT IS `
        + `${cal.fitted_weight.toFixed(2)}: `)
    + `OVER ${cal.test_races} WALK-FORWARD RACES THE MARKET ALONE SCORES `
    + `${cal.log_loss.market} AND EVERY POSITIVE WEIGHT IS WORSE `
    + `(0.10 → ${cal.log_loss_by_weight['0.10']}, `
    + `1.00 → ${cal.log_loss_by_weight['1.00']})`));
  foot.append(el('span', 'right',
    'THE BLEND LEANS ON A NUMBER THE MARKET ALREADY PROVIDES — THAT IS THE '
    + 'FINDING THIS PAGE DOCUMENTS, NOT A LIMITATION IT HIDES'));
}

function pct(v) {
  return v === null || v === undefined ? DASH : `${v.toFixed(1)}%`;
}

/** A PROPORTION as a percentage. Distinct from `pct` above, which takes a
 *  number already scaled 0-100 — passing 0.012 to that one prints "0.0%". */
function share(v, digits = 1) {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(digits)}%`;
}

/* ── does it beat the price ──────────────────────────────────────────────── */
//
// Two questions, and they are not the same one. A model can be well calibrated
// and unprofitable, or profitable and badly calibrated. The page shows both,
// and shows the negative answer as plainly as it would show a positive one.

function renderBacktest() {
  const b = state.backtest;
  if (!b) return;
  $('bt-sub').textContent = b.usable
    ? `WALK-FORWARD · SPLIT ${b.split_date} · TRAIN ${b.train_races} / `
      + `TEST ${b.test_races} RACES · ${b.calibration.runners.toLocaleString()} RUNNERS`
    : 'NOT ENOUGH SCORED RACES TO BACKTEST';
  if (!b.usable) {
    $('bt-body').replaceChildren();
    return;
  }

  const c = b.calibration;
  $('bt-head').replaceChildren(...[
    ['PREDICTED BAND', ''], ['RUNNERS', 'num'], ['WINS', 'num'],
    ['MODEL SAYS', 'num'], ['ACTUAL', 'num'], ['95% CI', ''], ['READ', ''],
  ].map(([label, cls]) => el('th', cls || null, label)));

  $('bt-body').replaceChildren(...c.bins.map((bin) => {
    const tr = el('tr', bin.thin ? 'thin' : null);
    tr.append(el('td', null, `${share(bin.lo, 0)}–${share(bin.hi, 0)}`));
    tr.append(el('td', 'num', bin.runners.toLocaleString()));
    tr.append(el('td', 'num', String(bin.wins)));
    tr.append(el('td', 'num', share(bin.predicted)));
    tr.append(el('td', 'num', share(bin.actual)));
    tr.append(el('td', null, bin.ci
      ? `[${share(bin.ci[0])}, ${share(bin.ci[1])}]` : DASH));
    // "Off" means the model's own prediction falls outside the interval its
    // outcomes support. That is a miscalibration, not a near miss.
    tr.append(el('td', bin.off ? 'warn' : null,
      bin.thin ? `n<${c.min_bin}` : bin.off ? 'OUTSIDE THE INTERVAL' : 'inside'));
    return tr;
  }));

  const foot = $('bt-foot');
  foot.replaceChildren();
  foot.append(el('span', null, `Brier ${num(c.brier, 5)}`));
  foot.append(el('span', null, `log loss ${num(c.log_loss, 4)}`));
  foot.append(el('span', c.off_bins ? 'warn' : null,
    c.off_bins
      ? `${c.off_bins} bin(s) outside their interval`
      : 'every bin falls inside the interval its outcomes support'));

  // The second question, and the one that decides whether any of this is
  // worth having.
  const host = $('bt-value');
  host.replaceChildren();
  const v = b.value;
  const line = el('div', 'bt-verdict');
  if (!v.bets) {
    line.append(el('b', null, 'NOTHING TO BET ON. '));
    line.append(document.createTextNode(
      `At the fitted weight of ${num(b.weight, 2)} the model IS the de-vigged `
      + 'market, so there are no value bets by construction — not a small '
      + 'edge, none.'));
  } else {
    line.append(el('b', v.roi > 0 ? 'pos' : 'neg',
      `${v.bets.toLocaleString()} BETS · ROI ${signed(v.roi * 100, 1)}%. `));
    line.append(document.createTextNode(
      `Strike ${share(v.strike_rate)} on ${v.staked.toLocaleString()} staked. `
      + v.note));
  }
  host.append(line);

  // What happens as the model is asked to disagree with the market more.
  const m = b.measured;
  if (m && m.value_by_weight.length) {
    const tbl = el('table', 'model-grid');
    const head = el('tr');
    [['WEIGHT ON THE MODEL', ''], ['EDGE REQUIRED', 'num'], ['BETS', 'num'],
     ['STRIKE', 'num'], ['ROI', 'num']]
      .forEach(([l, cls]) => head.append(el('th', cls || null, l)));
    const thead = el('thead');
    thead.append(head);
    tbl.append(thead);
    const body = el('tbody');
    m.value_by_weight.forEach((r) => {
      const tr = el('tr');
      tr.append(el('td', null, num(r.weight, 2)));
      tr.append(el('td', 'num', share(r.edge, 0)));
      tr.append(el('td', 'num', r.bets.toLocaleString()));
      tr.append(el('td', 'num', share(r.strike)));
      tr.append(el('td', 'num neg', `${signed(r.roi * 100, 1)}%`));
      body.append(tr);
    });
    tbl.append(body);
    const box = el('div', 'table-scroll');
    box.append(tbl);
    host.append(box);
    host.append(el('div', 'bt-reading', m.reading));
  }
}

/* ── ET ──────────────────────────────────────────────────────────────────── */

/* Brief 01 lists "big hero numbers and dashboard-y KPI tiles" under Explicitly
   Avoid. Coverage is real information and stays; it reads as one dense line in
   the same grammar as every other footer on the page, not six tiles. */
async function renderEtSummary() {
  const s = await api.etSummary();
  const conf = s.confidence ?? {};
  const host = $('et-summary');
  host.replaceChildren();
  const part = (text, cls) => host.append(el('span', cls ?? null, text));
  part(`${s.rows.toLocaleString()} ROWS · ${(s.coverage * 100).toFixed(1)}% OF RUNNERS`);
  part(`CONFIDENCE ${(conf.high ?? 0).toLocaleString()} HIGH · `
    + `${(conf.medium ?? 0).toLocaleString()} MEDIUM · `
    + `${(conf.low ?? 0).toLocaleString()} LOW`);
  part(`THROUGH ${s.last_date ?? DASH}`, 'right');
}

function renderEt() {
  const data = state.et;
  const body = $('et-body');
  if (!data) {
    body.replaceChildren(el('tr', null, ''));
    return;
  }
  /* A par is a property of a race, not a runner. v4 used weight_band as a
     lookup key and handed runners in one race pars up to 1.98s apart, which is
     what made it rate a beaten horse fastest in 28 of 51 races. */
  const onePar = data.distinct_pars === 1;
  const rows = data.runners.filter((r) => r.finish_time != null && r.figure != null);
  const sorted = [...rows].sort((a, b) => a.finish_time - b.finish_time);
  const monotonic = sorted.every((r, i) => i === 0 || sorted[i - 1].figure >= r.figure);
  $('et-invariants').replaceChildren(...[
    [onePar, `one par per race (${data.distinct_pars} distinct)`],
    [monotonic, 'faster time → better figure'],
  ].map(([ok, label]) =>
    el('span', `inv ${ok ? 'pass' : 'fail'}`, `${ok ? '✓' : '✕'} ${label}`)));

  if (!data.runners.length) {
    body.replaceChildren(el('tr', null, ''));
    body.firstChild.append(el('td', 'model-empty', 'NO ET FIGURES FOR THIS RACE'));
    body.firstChild.firstChild.colSpan = 10;
    return;
  }
  body.replaceChildren(...data.runners.map((r) => {
    const tr = el('tr');
    const cells = [
      ['centre', r.horse_no ?? DASH],
      ['left horse', r.horse_name ?? DASH],
      ['centre', r.place ?? DASH],
      ['', fmtTime(r.finish_time)],
      ['', num(r.figure, 1)],
      ['', signed(r.len_vs_par)],
      ['', signed(r.len_vs_race)],
      ['', r.et_n_eff ?? DASH],
      [`left ${r.confidence ? `conf-${r.confidence}` : 'thin'}`, r.confidence ?? DASH],
      ['left thin', r.et_level ?? DASH],
    ];
    cells.forEach(([cls, text], i) => {
      const td = el('td', cls, String(text));
      /* Sample size drives visual weight: a thin cell must not read as
         authoritative as a well-evidenced one. */
      if (r.et_n_eff !== null && r.et_n_eff < 10 && i >= 4) td.classList.add('thin');
      if (i === 5 || i === 6) td.classList.add(Number(text) >= 0 ? 'pos' : 'neg');
      tr.append(td);
    });
    return tr;
  }));
}

function fmtTime(seconds) {
  if (seconds === null || seconds === undefined) return DASH;
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return m >= 1 ? `${m}:${s.toFixed(2).padStart(5, '0')}` : s.toFixed(2);
}

/* ── loading ─────────────────────────────────────────────────────────────── */

function render() {
  renderViewToggle();
  renderStrip();
  const show = (id, on) => { $(id).hidden = !on; };
  show('sec-sarr', state.view === 'sarr' || state.view === 'all');
  show('sec-blend', state.view === 'blend' || state.view === 'all');
  show('sec-backtest', state.view === 'backtest' || state.view === 'all');
  show('sec-et', state.view === 'et' || state.view === 'all');
  renderSarr();
  renderBlend();
  renderBacktest();
  renderEt();
}

/** Each section reports its own failure. One section erroring must not blank
 *  the other two — that is how a page ends up looking like there is no data
 *  when one endpoint is down. */
async function settle(promise, onError) {
  try { return await promise; } catch (e) { onError(e); return null; }
}

async function loadBlend() {
  state.blend = await settle(
    api.blendRace(state.date, state.race, state.weight ?? undefined),
    (e) => { $('blend-foot').replaceChildren(el('span', 'warn', `blend: ${e.message}`)); });
  renderBlend();
}

async function loadBacktest() {
  state.backtest = await settle(api.modelBacktest(), (e) => {
    $('bt-sub').textContent = `backtest: ${e.message}`;
  });
  renderBacktest();
}


async function loadRace() {
  const [sarr, et] = await Promise.all([
    settle(api.sarrRace(state.date, state.race),
      (e) => { $('sarr-foot').replaceChildren(el('span', 'warn', `sarr: ${e.message}`)); }),
    settle(api.etRace(state.date, state.race),
      (e) => { $('et-body').replaceChildren(el('tr', null, `failed to load: ${e.message}`)); }),
  ]);
  state.sarr = sarr;
  state.et = et;
  await loadBlend();
  render();
}

async function onContext(_ctx, what) {
  state.date = context.date;
  state.races = context.races;
  state.race = context.race;
  if (what === 'date') {
    state.sarr = null;
    state.blend = null;
    state.et = null;
    render();
    return;
  }
  await loadRace();
}

async function onRebuild() {
  const btn = $('rebuild-et');
  const out = $('job-result');
  btn.disabled = true;
  btn.textContent = 'REBUILDING…';
  out.hidden = false;
  out.className = 'job-result';
  out.textContent = 'Rebuilding ET references…';
  try {
    const r = await api.rebuildEt(24);
    /* Report counts, never a bare "done": a zero has to be visible immediately. */
    out.className = 'job-result ok';
    out.textContent =
      `${r.rows_written.toLocaleString()} runner_et rows from ${r.runs_loaded.toLocaleString()} runs · `
      + `window ${r.window?.[0]} to ${r.window?.[1]} · `
      + `${r.sec_per_length?.toFixed(4)} sec/length`;
    // The rebuild changes what Layer 1's freshness chips report, so it
    // re-renders the global strip rather than a page-local copy.
    context.status = await api.status().catch(() => context.status);
    context.render();
    // The backtest is over the whole archive, not this race, so it loads once
    // rather than on every race change.
    await Promise.all([renderEtSummary(), loadBacktest(), loadRace()]);
  } catch (e) {
    out.className = 'job-result err';
    out.textContent = `rebuild failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'REBUILD ET';
  }
}

function onKey(e) {
  if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (!/^[1-9]$/.test(e.key)) return;
  const no = Number(e.key);
  context.setRace(no);
}

async function init() {
  renderNav($('nav'), 'model-analysis.html');
  renderViewToggle();
  installPalette();
  $('rebuild-et').addEventListener('click', onRebuild);
  document.addEventListener('keydown', onKey);

  await settle(renderEtSummary(), () => {});
  // Over the whole archive rather than this race, so it loads once here and
  // not again on every race change.
  await loadBacktest();
  context.onChange(onContext);
  await context.init();
  await onContext(context, 'meeting');
}

init();

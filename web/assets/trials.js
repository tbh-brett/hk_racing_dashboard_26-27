/* Trials — ported from web/design-source/Trials.dc.html.
 *
 * Three views: the recent batches, the standouts feed, and what the rating
 * actually predicts.
 *
 * The artboard states the design twice, and the second time as the reason the
 * page is worth having:
 *
 *     "One engine, two surfaces: the same finish + margin + comment rating
 *      used inline on a horse's own trial line, aggregated here as a live feed
 *      — not a separately curated list."
 *
 * So nothing here rates a trial. The mark comes from the API, which calls
 * derive/trial_quality — the same function the Form Guide's inline band reads
 * through. A second rating written in this file would drift from that one
 * within a season, and the two surfaces would quietly disagree about the same
 * horse.
 *
 * The CALIBRATION view exists because a rating nobody can check is a rating
 * nobody should act on. It recomputes, live, what each band went on to do at
 * the races: STANDOUT 21.0% next-start wins against a baseline of 8.2%,
 * NEGATIVE 3.2%, and UNTESTED on the baseline at 7.7% — which is the intent,
 * not a shortcoming, because a trial the horse was not asked to win says
 * nothing about it either way.
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
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const VIEWS = [['batches', 'BATCHES'], ['standouts', 'STANDOUTS'],
               ['calibration', 'DOES IT HOLD']];

const BANDS = ['STANDOUT', 'POSITIVE', 'NEUTRAL', 'NEGATIVE', 'UNTESTED'];
const VENUES = [['all', 'ALL'], ['ST', 'ST'], ['HV', 'HV']];

const COLS = [
  ['', ''], ['P', 'r'], ['HORSE', ''], ['MARGIN', 'r'], ['TIME', 'r'],
  ['POSITIONS', ''], ['GEAR', ''], ['COMMENT', ''], ['NEXT ACTUAL START', ''],
];

const state = {
  view: 'batches', batches: [], standouts: null, calibration: null,
  search: '', band: null, venue: 'all',
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderNav() {
  $('nav').replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === 'trials.html') a.setAttribute('aria-current', 'page');
    return a;
  }));
}

function renderViewToggle() {
  $('view-toggle').replaceChildren(...VIEWS.map(([key, label]) => {
    const b = el('button', null, label);
    b.setAttribute('aria-pressed', String(state.view === key));
    b.addEventListener('click', () => { state.view = key; render(); });
    return b;
  }));
}

function pct(v, digits = 1) {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(digits)}%`;
}

function qMark(band, mark) {
  return el('span', `q q-${band.toLowerCase()}`, mark);
}

/** What the horse did at the RACES after this trial. Never another trial. */
function nextStart(nxt) {
  const box = el('div', 'next');
  if (!nxt) {
    box.append(el('span', 'none', 'no start since'));
    return box;
  }
  const placed = nxt.place <= (nxt.field_size >= 7 ? 3 : 2);
  const cls = nxt.place === 1 ? 'won' : placed ? 'plc' : null;
  box.append(el('span', null, `${nxt.race_date} `));
  box.append(el('span', cls, `${nxt.place}/${nxt.field_size}`));
  box.append(el('span', null,
    ` ${nxt.venue} ${nxt.distance ?? ''}m${nxt.race_class ? ` Cl${nxt.race_class}` : ''}`));
  box.title = `${nxt.race_date} R${nxt.race_no} — finished ${nxt.place} of `
    + `${nxt.field_size}${nxt.win_odds ? ` at ${nxt.win_odds}` : ''}`;
  return box;
}

/* ── filters ─────────────────────────────────────────────────────────────── */

function chip(label, on, onClick, count) {
  const b = el('button', `chip${on ? ' on' : ''}`);
  b.append(document.createTextNode(label));
  if (count !== undefined) b.append(el('span', 'n', ` ${count}`));
  b.addEventListener('click', onClick);
  return b;
}

function renderChips() {
  const counts = new Map();
  state.batches.forEach((b) => b.runners.forEach((r) => {
    counts.set(r.quality_band, (counts.get(r.quality_band) ?? 0) + 1);
  }));
  $('band-chips').replaceChildren(...BANDS.map((band) =>
    chip(band, state.band === band, () => {
      state.band = state.band === band ? null : band;
      render();
    }, counts.get(band) ?? 0)));
  $('venue-chips').replaceChildren(...VENUES.map(([key, label]) =>
    chip(label, state.venue === key, () => {
      state.venue = key;
      load();
    })));
}

function matches(r) {
  if (state.band && r.quality_band !== state.band) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  return `${r.horse_name} ${r.comment ?? ''}`.toLowerCase().includes(q);
}

/* ── batches ─────────────────────────────────────────────────────────────── */

function runnerRow(r) {
  const row = el('div', 'tr-row');
  row.append(qMark(r.quality_band, r.quality_mark));
  row.append(el('div', 'r', r.place === null ? DASH : String(r.place)));
  const horse = el('div', 'horse');
  horse.append(document.createTextNode(r.horse_name));
  horse.title = `${r.quality_band}${r.quality_reasons.length
    ? ` — ${r.quality_reasons.join('; ')}` : ''}`;
  row.append(horse);
  // Margin is shown because it is useful context on a row. It does not move
  // the band: measured, it carries no signal once the finish is held constant.
  row.append(el('div', 'r',
    r.margin === null || r.margin === undefined ? DASH : `${num(r.margin, 1)}L`));
  row.append(el('div', 'r',
    r.finish_time === null ? DASH : num(r.finish_time, 2)));
  row.append(el('div', null, r.running_positions.join(' ') || DASH));
  row.append(el('div', null, r.gear || DASH));
  const comment = el('div', 'comment', r.comment ?? DASH);
  comment.title = r.comment ?? '';
  row.append(comment);
  row.append(nextStart(r.next_start));
  return row;
}

function renderBatches() {
  renderChips();
  const host = $('batches');
  host.replaceChildren();
  let shown = 0;
  let total = 0;
  state.batches.forEach((b) => {
    const runners = b.runners.filter(matches);
    total += b.runners.length;
    if (!runners.length) return;
    shown += runners.length;
    const box = el('div', 'batch');
    const head = el('div', 'batch-head');
    head.append(el('span', 'd', b.trial_date));
    head.append(el('span', 'no', `T${b.trial_no}`));
    head.append(el('span', null, `${b.venue} ${b.surface}`));
    head.append(el('span', 'k', 'RUNNERS'));
    head.append(el('span', null, String(b.field_size)));
    head.append(el('span', 'k', 'WIN TIME'));
    head.append(el('span', null,
      b.winning_time === null ? DASH : num(b.winning_time, 2)));
    if (b.section_times.length) {
      head.append(el('span', 'k', 'SPLITS'));
      // One set of sectionals per trial, repeated on every row by HKJC. Shown
      // once, on the batch, rather than four times as if measured per runner.
      head.append(el('span', 'splits',
        b.section_times.map((t) => num(t, 1)).join(' · ')));
    }
    // HKJC publishes no trial distance, and inferring one from the clock would
    // be a guess dressed as a fact.
    head.append(el('span', 'right', 'no distance is published for a trial'));
    box.append(head);

    const cols = el('div', 'tr-head');
    COLS.forEach(([label, cls]) => cols.append(el('div', cls || null, label)));
    box.append(cols);
    runners.forEach((r) => box.append(runnerRow(r)));
    host.append(box);
  });
  if (!shown) {
    host.replaceChildren(el('div', 'no-match',
      state.search ? `No trial run matches "${state.search}".`
        : 'NO TRIAL CLEARS THIS QUALITY FILTER IN THE CURRENT SLICE.'));
  }
  $('match-count').textContent = `${shown} of ${total}`;
  const active = $('active-filters');
  active.replaceChildren(el('span', 'lab', 'SHOWING'));
  active.append(el('span', 'none',
    `${state.batches.length} most recent batches`
    + (state.venue === 'all' ? '' : ` at ${state.venue}`)));
}

/* ── standouts ───────────────────────────────────────────────────────────── */

function renderStandouts() {
  const s = state.standouts;
  const host = $('standouts');
  if (!s) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const note = el('div', 'so-note');
  note.append(el('b', null, `${s.shown} TRIALS SHOWN. `));
  note.append(document.createTextNode(
    'One engine, two surfaces: the same finish and comment rating used inline '
    + "on a horse's own trial line, aggregated here as a live feed — not a "
    + `separately curated list. ${s.considered} trials since ${s.since} were `
    + 'rated; these are the ones that came out '
    + `${s.bands.join(' or ').toLowerCase()}.`));
  host.append(note);

  if (!s.runs.length) {
    host.append(el('div', 'no-match',
      'NO TRIAL CLEARS THIS QUALITY FILTER IN THE CURRENT SLICE.'));
    return;
  }

  s.runs.filter(matches).forEach((r) => {
    const box = el('div', 'standout');
    const line = el('div', 'so-line');
    line.append(qMark(r.quality_band, r.quality_mark));
    line.append(el('span', 'name', r.horse_name));
    line.append(el('span', 'where',
      `${r.trial_date} T${r.trial_no} · ${r.venue} ${r.surface} · `
      + `finished ${r.place ?? DASH} of ${r.field_size}`
      + (r.margin ? ` · ${num(r.margin, 1)}L` : '')));
    if (r.blackbook) {
      const badge = el('span', 'so-badge',
        `IN THE BLACKBOOK · ${r.blackbook.status}`);
      badge.title = `added ${r.blackbook.added_date}`;
      line.append(badge);
    }
    line.append(el('span', 'right'));
    line.append(nextStart(r.next_start));
    box.append(line);
    // The reasons, because a mark nobody can check is a mark nobody should
    // act on.
    box.append(el('div', 'so-reasons', r.quality_reasons.join(' · ')));
    if (r.comment) box.append(el('div', 'so-comment', r.comment));

    // What the REST of the batch did next is the check on the rating: a
    // standout out of a batch whose other five all won next start says more
    // about the batch than about the horse.
    if (r.batch_next && r.batch_next.length) {
      const rest = el('div', 'so-batch');
      rest.append(el('span', 'k', 'WHAT THE REST OF THE BATCH DID NEXT'));
      r.batch_next.forEach((o) => {
        const row = el('div', 'row');
        row.append(el('span', null, o.place === null ? DASH : String(o.place)));
        row.append(el('span', 'n', o.horse_name));
        row.append(el('span', null, '→'));
        if (!o.next_start) {
          row.append(el('span', null, 'no start since'));
        } else {
          const placed = o.next_start.place
            <= (o.next_start.field_size >= 7 ? 3 : 2);
          row.append(el('span', o.next_start.place === 1 ? 'won' : null,
            `${o.next_start.place}/${o.next_start.field_size}`
            + `${placed && o.next_start.place !== 1 ? ' placed' : ''}`
            + ` ${o.next_start.race_date}`));
        }
        rest.append(row);
      });
      box.append(rest);
    }
    host.append(box);
  });
}

/* ── calibration ─────────────────────────────────────────────────────────── */

function renderCalibration() {
  const c = state.calibration;
  const host = $('calibration');
  if (!c) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'DOES THE RATING HOLD'));
  head.append(el('span', 'sub',
    'WHAT EACH BAND WENT ON TO DO AT THE RACES · RECOMPUTED, NOT QUOTED'));
  host.append(head);

  const base = c.overall.next_win_rate ?? 0;
  const top = Math.max(base, ...Object.values(c.bands)
    .map((v) => v.next_win_rate ?? 0)) || 1;

  const table = el('table', 'cal-tab');
  const hr = el('tr');
  [['BAND', ''], ['TRIALS', 'r'], ['WITH A NEXT START', 'r'],
   ['NEXT-START WIN %', 'r'], ['', ''], ['NEXT-START PLACE %', 'r']]
    .forEach(([label, cls]) => hr.append(el('th', cls || null, label)));
  const thead = el('thead');
  thead.append(hr);
  table.append(thead);

  const body = el('tbody');
  const row = (label, v, cls) => {
    const tr = el('tr', cls ?? null);
    tr.append(el('td', 'label', label));
    tr.append(el('td', 'r', v.trials.toLocaleString()));
    tr.append(el('td', 'r', v.with_next.toLocaleString()));
    const win = el('td', 'r', pct(v.next_win_rate));
    if (v.next_win_ci && v.next_win_ci.length === 2) {
      win.title = `95% CI [${pct(v.next_win_ci[0])}, ${pct(v.next_win_ci[1])}]`
        + (v.clears_baseline ? ' — excludes the baseline'
          : ' — contains the baseline');
    }
    tr.append(win);
    const bar = el('td');
    const track = el('span', 'bar');
    // Colour is earned by an interval that excludes the baseline, not by
    // landing on one side of it: NEUTRAL at 7.9% against a baseline of 8.2%
    // is the same number.
    const fill = el('i', !v.clears_baseline ? 'flat'
      : v.next_win_rate > base ? 'up' : 'down');
    fill.style.width = `${((v.next_win_rate ?? 0) / top) * 100}%`;
    track.append(fill);
    bar.append(track);
    tr.append(bar);
    tr.append(el('td', 'r', pct(v.next_place_rate)));
    return tr;
  };
  c.order.forEach((band) => body.append(row(band, c.bands[band])));
  body.append(row('ALL TRIALS · BASELINE', c.overall, 'baseline'));
  table.append(body);
  const box = el('div', 'table-box');
  box.append(table);
  host.append(box);

  host.append(el('div', 'caveat',
    'The rating is only worth showing if the bands separate, so this is here '
    + 'rather than a claim that they do. UNTESTED landing on the baseline is '
    + 'the intent, not a shortcoming: a trial the horse was not asked to win '
    + 'says nothing about it either way, and every phrase in that family was '
    + 'measured on the baseline before being treated as one.'));
  host.append(el('div', 'caveat',
    'Margin is shown on every row and is not in the score. Holding the finish '
    + 'constant, mid-pack finishers go 7.2% next-start wins within two lengths '
    + 'of the winner and 5.7% at fourteen or more — a trial winner is often '
    + 'not extended, so the field’s margin measures how hard the winner '
    + 'was ridden more than how well the rest went.'));
}

/* ── render ──────────────────────────────────────────────────────────────── */

function renderSummary() {
  const host = $('tr-sum');
  host.replaceChildren();
  const c = state.calibration;
  if (!c) return;
  const stat = (value, label) => {
    const box = el('span');
    box.append(el('b', null, value));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  host.append(stat(c.overall.trials.toLocaleString(), 'TRIALS RATED'));
  const so = c.bands.STANDOUT;
  host.append(stat(pct(so.next_win_rate), 'STANDOUT NEXT-WIN'));
  host.append(stat(pct(c.overall.next_win_rate), 'BASELINE'));
}

function render() {
  renderViewToggle();
  renderSummary();
  VIEWS.forEach(([key]) => { $(`view-${key}`).hidden = state.view !== key; });
  const filtersApply = state.view !== 'calibration';
  document.querySelector('.filter-bar').hidden = !filtersApply;
  document.querySelector('.chip-bar').hidden = !filtersApply;
  if (state.view === 'batches') renderBatches();
  if (state.view === 'standouts') renderStandouts();
  if (state.view === 'calibration') renderCalibration();
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

async function load() {
  const venue = state.venue === 'all' ? undefined : state.venue;
  const [batches, standouts, calibration] = await Promise.all([
    api.trials(12, venue),
    api.trialStandouts(),
    api.trialCalibration(),
  ]);
  state.batches = batches.batches;
  state.standouts = standouts;
  state.calibration = calibration;
  render();
}

async function boot() {
  renderNav();
  wireSearch();
  installPalette();
  await context.init();
  render();
  await load();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});

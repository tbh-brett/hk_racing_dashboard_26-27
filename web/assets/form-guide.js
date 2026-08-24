/* Form Guide — three states over one column grid.
 *
 * Brief 02's diagnosis was that the old guide read as chaotic because every
 * horse block invented its own layout, not because it was dense. So collapsed,
 * expanded and deep all share the same grid at the same widths, and row height
 * is fixed regardless of what a row contains.
 *
 * Multiple horses can be open at once, and multiple runs within them. That
 * state survives switching races, because comparing two horses is the actual
 * task and losing the comparison on every navigation makes it impossible.
 */
import { api, num, signed } from './api.js';
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

/** Expansion state, keyed by horse and by run. Survives race changes. */
const openHorses = new Set();
const openRuns = new Set();
const teardowns = [];

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
  } catch { /* the strip is informational; the page still works without it */ }
}

const styleClass = (s) => `style style-${(s ?? 'unknown').toLowerCase().replace('-', '')}`;

/** The figure sequence carries the trend. No second sparkline — brief 03 §7. */
function figureSequence(runs) {
  const wrap = el('div', 'seq');
  const figs = runs.map((r) => r.et_figure).filter((f) => f !== null);
  if (!figs.length) {
    wrap.append(el('span', 'none', DASH));
    return wrap;
  }
  // Oldest to newest, so the eye reads improvement left to right.
  [...runs].reverse().forEach((r, i, arr) => {
    if (r.et_figure === null) { wrap.append(el('span', 'none', DASH)); return; }
    const prev = arr.slice(0, i).reverse().find((x) => x.et_figure !== null);
    const cls = !prev ? '' : r.et_figure > prev.et_figure ? 'up'
      : r.et_figure < prev.et_figure ? 'down' : '';
    const span = el(i === arr.length - 1 ? 'b' : 'span', cls, num(r.et_figure, 0));
    span.title = r.figure_display ?? '';
    wrap.append(span);
  });
  return wrap;
}

function flagsFor(runner, history) {
  const flags = [];
  const last = history[0];
  if (last) {
    const trouble = (last.tags ?? []).filter((t) =>
      !['sampling', 'vet_routine', 'no_report'].includes(t));
    if (trouble.length) flags.push(['trouble', trouble[0].replace(/_/g, ' ')]);
    (last.lane_notes ?? []).slice(0, 1).forEach((l) =>
      flags.push(['lane', l.replace(/_/g, ' ')]));
  }
  if (runner.gear) flags.push(['', runner.gear]);
  const box = el('div', 'flags');
  flags.slice(0, 3).forEach(([kind, text]) => box.append(el('span', `flag ${kind}`, text)));
  if (!flags.length) box.append(el('span', 'flag', DASH));
  return box;
}

/* ── collapsed row ───────────────────────────────────────────────────────── */

function collapsedRow(runner, history, ctx) {
  const row = el('div', 'fg-row');
  row.setAttribute('role', 'row');
  row.dataset.horse = runner.horse_name;
  row.setAttribute('aria-expanded', String(openHorses.has(runner.horse_name)));

  row.append(el('span', 'c-no', runner.horse_no ?? DASH));
  row.append(el('span', 'c-horse', runner.horse_name));
  row.append(el('span', 'c-draw', runner.draw ?? DASH));
  row.append(el('span', 'c-wt', runner.actual_weight ?? DASH));
  row.append(el('span', 'c-jockey', runner.jockey ?? DASH));
  row.append(el('span', 'c-trainer', runner.trainer ?? DASH));

  const style = el('span', 'c-style');
  style.append(el('span', styleClass(history[0]?.pace_style),
    history[0]?.pace_style ?? DASH));
  row.append(style);

  row.append(el('span', 'c-odds', runner.win_odds ? num(runner.win_odds, 1) : DASH));

  const seq = el('span', 'c-seq');
  seq.append(figureSequence(history));
  row.append(seq);

  const flags = el('span', 'c-flags');
  flags.append(flagsFor(runner, history));
  row.append(flags);

  // Condition fit on hover, through the shared overlay: viewport-fixed,
  // collision-aware, rendered at the document root so it cannot move this row.
  teardowns.push(anchoredPanel(row, () => conditionPanel(runner, ctx)));

  row.addEventListener('click', () => {
    if (openHorses.has(runner.horse_name)) openHorses.delete(runner.horse_name);
    else openHorses.add(runner.horse_name);
    render();
  });
  return row;
}

/* ── condition fit panel ─────────────────────────────────────────────────── */

function conditionPanel(runner, ctx) {
  const panel = el('div', 'fit');
  panel.append(el('h5', null, `${runner.horse_name} · today's conditions`));
  const body = el('div');
  body.append(el('div', 'fit-note', 'loading…'));
  panel.append(body);

  const q = new URLSearchParams({ before: ctx.date });
  if (ctx.distance) q.set('distance', ctx.distance);
  if (ctx.course) q.set('course', ctx.course);
  if (ctx.going) q.set('going', ctx.going);

  fetch(`/api/condition-fit/${encodeURIComponent(runner.horse_name)}?${q}`)
    .then((r) => r.json())
    .then((data) => {
      body.replaceChildren();
      const head = el('div', 'fit-row');
      ['', 'starts', 'win', 'fig'].forEach((h, i) =>
        head.append(el('span', i === 0 ? 'k' : '', h)));
      body.append(head);
      data.cells.forEach((c) => {
        const r = el('div', `fit-row${c.is_thin ? ' thin' : ''}`);
        r.append(el('span', 'k', c.label));
        r.append(el('span', '', String(c.starts)));
        r.append(el('span', '', c.win_display));
        r.append(el('span', '', c.avg_figure === null ? DASH : num(c.avg_figure, 1)));
        body.append(r);
      });
      body.append(el('div', 'fit-note',
        'How this horse fits today. Context, not an edge — the market prices '
        + 'conditions efficiently, and thin cells are greyed for that reason.'));
    })
    .catch((e) => { body.replaceChildren(el('div', 'fit-note', `unavailable: ${e.message}`)); });

  return panel;
}

/* ── expanded: six-run table ─────────────────────────────────────────────── */

const COLS = ['Date', 'Trk', 'Crs', 'Dist', 'Going', 'Cl', 'Dr', 'Wt',
              'Fin', 'Margin', 'Time', 'Figure', 'Positions', 'Style'];

function detailBlock(runner, history, ctx) {
  const box = el('div', 'fg-detail');

  // Fixed-height band: collapses to one muted line rather than disappearing, so
  // the page rhythm is identical whether a horse has trialled or not.
  const band = el('div', 'trial-band');
  band.append(el('span', null, 'no recent trial'));
  box.append(band);

  const table = el('table', 'runs');
  const thead = el('thead');
  const hr = el('tr');
  COLS.forEach((c) => hr.append(el('th', null, c)));
  thead.append(hr);
  table.append(thead);

  const tbody = el('tbody');
  if (!history.length) {
    const tr = el('tr');
    const td = el('td', 'missing', 'no prior runs');
    td.colSpan = COLS.length;
    tr.append(td);
    tbody.append(tr);
  }
  history.forEach((run) => {
    const key = `${run.race_date}|${run.race_no}|${run.horse_no}`;
    const tr = el('tr');
    const cells = [
      run.race_date, run.venue, run.course, run.distance, run.going,
      run.race_class, run.draw, run.actual_weight,
      run.place ?? run.place_code, run.lengths_behind === null ? null
        : num(run.lengths_behind, 2),
      run.finish_time_display, num(run.et_figure, 1),
      run.running_positions.join(' '), run.pace_style,
    ];
    cells.forEach((v, i) => {
      // Missing values render an explicit dash, never a collapsed column.
      const td = el('td', [3, 6, 7, 8, 9, 10, 11].includes(i) ? 'num' : null,
        v === null || v === undefined || v === '' ? DASH : String(v));
      if (v === null || v === undefined || v === '') td.classList.add('missing');
      if (i === 11 && run.figure_display) td.title = run.figure_display;
      tr.append(td);
    });
    tr.addEventListener('click', (e) => {
      e.stopPropagation();
      if (openRuns.has(key)) openRuns.delete(key); else openRuns.add(key);
      render();
    });
    tbody.append(tr);
    if (openRuns.has(key)) {
      const tr2 = el('tr');
      const td2 = el('td');
      td2.colSpan = COLS.length;
      td2.append(deepBlock(run));
      tr2.append(td2);
      tbody.append(tr2);
    }
  });
  table.append(tbody);
  box.append(table);
  return box;
}

/* ── deep: one run ───────────────────────────────────────────────────────── */

function deepBlock(run) {
  const box = el('div', 'deep');
  box.append(el('h4', null,
    `${run.race_date} R${run.race_no} · ${run.distance ?? DASH}m ${run.going ?? ''}`));

  if (run.section_times.length) {
    const splits = el('div', 'splits');
    run.section_times.forEach((t, i) => {
      const s = el('div', 'split');
      // Sectionals stay plain seconds; only cumulative race times get m:ss.xx.
      s.append(el('span', 't', t.toFixed(2)));
      s.append(el('span', 'p', run.running_positions[i] ?? DASH));
      splits.append(s);
    });
    box.append(splits);
  }

  if (run.running_comment) {
    const c = el('div', 'comment');
    c.append(el('b', null, 'Running: '));
    c.append(document.createTextNode(run.running_comment));
    box.append(c);
  }
  if (run.incident_comment) {
    const c = el('div', 'comment');
    c.append(el('b', null, 'Stewards: '));
    c.append(document.createTextNode(run.incident_comment));
    box.append(c);
  }

  const quality = el('div', 'quality');
  quality.append(el('h4', null, 'Race quality — top finishers and their next start'));
  box.append(quality);
  fetch(`/api/race-quality/${run.race_date}/${run.race_no}`)
    .then((r) => r.json())
    .then((data) => {
      data.finishers.forEach((f) => {
        const line = el('div', 'line');
        line.append(el('span', null, `${f.place}${f.dead_heat ? '=' : '.'}`));
        line.append(el('span', null, f.horse_name));
        const nextCls = f.next_place === null ? 'next-none'
          : f.next_place <= 3 ? 'next-good' : 'next-poor';
        line.append(el('span', nextCls, f.next_place === null
          ? (f.next_place_code ?? 'not run since')
          : `→ ${f.next_place} (${f.next_date})`));
        quality.append(line);
      });
    })
    .catch(() => quality.append(el('div', 'fit-note', 'retrospective unavailable')));

  return box;
}

/* ── render ──────────────────────────────────────────────────────────────── */

let current = null;

function render() {
  while (teardowns.length) teardowns.pop()();
  const host = $('runners');
  if (!current) { host.replaceChildren(el('div', 'note', 'no race loaded')); return; }

  const { race, history } = current;
  const ctx = { date: race.race_date, distance: race.distance,
                course: race.course, going: race.going };

  const nodes = [];
  race.runners.forEach((runner) => {
    const runs = history[runner.horse_name] ?? [];
    nodes.push(collapsedRow(runner, runs, ctx));
    if (openHorses.has(runner.horse_name)) nodes.push(detailBlock(runner, runs, ctx));
  });
  host.replaceChildren(...nodes);

  $('race-context').textContent =
    `${race.venue ?? ''} ${race.race_date} · Race ${race.race_no} · `
    + `${race.distance ?? DASH}m · ${race.race_class ? `Class ${race.race_class} · ` : ''}`
    + `${race.going ?? ''} · course ${race.course ?? DASH} · ${race.field_size} runners`;
}

async function load() {
  const date = $('meeting-select').value;
  const no = $('race-select').value;
  if (!date || !no) return;
  try {
    current = await api.formGuide(date, Number(no));
    render();
  } catch (e) {
    current = null;
    $('runners').replaceChildren(el('div', 'note', `failed to load: ${e.message}`));
  }
}

async function loadMeetings() {
  const meetings = await api.meetings(60);
  $('meeting-select').replaceChildren(...meetings.map((m) => {
    const o = el('option', null, `${m.race_date} · ${m.venue ?? ''} · ${m.races}R`);
    o.value = m.race_date;
    return o;
  }));
  const count = meetings[0]?.races ?? 11;
  $('race-select').replaceChildren(...Array.from({ length: count }, (_, i) => {
    const o = el('option', null, `Race ${i + 1}`);
    o.value = String(i + 1);
    return o;
  }));
}

function init() {
  renderNav('form-guide.html');
  $('meeting-select').addEventListener('change', load);
  $('race-select').addEventListener('change', load);
  $('expand-all').addEventListener('click', () => {
    const all = current?.race.runners ?? [];
    if (openHorses.size >= all.length) openHorses.clear();
    else all.forEach((r) => openHorses.add(r.horse_name));
    render();
  });
  renderFreshness();
  loadMeetings().then(load);
}

init();

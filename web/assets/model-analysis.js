/* Model Analysis — ET section.
 *
 * Design brief 05 §5: show why each model produced the figure it did, not just
 * the figure. The page is meant to double as documentation of the model's own
 * limitations, which is why the invariant checks are on screen rather than
 * hidden in a test suite.
 */
import { api, num, signed } from './api.js';

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
    $('freshness').replaceChildren(...Object.entries(s.tables).map(([table, info]) => {
      const box = el('span', 'src');
      box.append(el('span', 'name', table.replace('runner_', '')));
      box.append(el('span', info.current ? 'ok' : 'stale',
        info.rows ? (info.current ? '✓' : '⚠') : '—'));
      box.title = `${info.rows.toLocaleString()} rows, through ${info.through ?? 'never'}`;
      return box;
    }));
    $('meeting-context').textContent = s.latest_meeting
      ? `latest meeting · ${s.latest_meeting}` : 'no meetings loaded';
  } catch (e) {
    $('freshness').textContent = `status unavailable: ${e.message}`;
  }
}

async function renderSummary() {
  const s = await api.etSummary();
  const conf = s.confidence ?? {};
  const stats = [
    ['rows', s.rows.toLocaleString()],
    ['coverage', `${(s.coverage * 100).toFixed(1)}%`],
    ['high conf', (conf.high ?? 0).toLocaleString()],
    ['medium', (conf.medium ?? 0).toLocaleString()],
    ['low', (conf.low ?? 0).toLocaleString()],
    ['through', s.last_date ?? '—'],
  ];
  $('et-summary').replaceChildren(...stats.map(([k, v]) => {
    const s2 = el('div', 'stat');
    s2.append(el('span', 'v', v), el('span', 'k', k));
    return s2;
  }));
  $('et-version').textContent = s.version ? `version ${s.version}` : '';
}

function renderInvariants(data) {
  /* A par is a property of a race, not a runner. v4 used weight_band as a
     lookup key and handed runners in one race pars up to 1.98s apart, which is
     what made it rate a beaten horse fastest in 28 of 51 races. */
  const onePar = data.distinct_pars === 1;
  const rows = data.runners.filter((r) => r.finish_time != null && r.figure != null);
  const sorted = [...rows].sort((a, b) => a.finish_time - b.finish_time);
  const monotonic = sorted.every((r, i) => i === 0 || sorted[i - 1].figure >= r.figure);

  const checks = [
    [onePar, `one par per race (${data.distinct_pars} distinct)`],
    [monotonic, 'faster time → better figure'],
  ];
  $('et-invariants').replaceChildren(...checks.map(([ok, label]) =>
    el('span', `inv ${ok ? 'pass' : 'fail'}`, `${ok ? '✓' : '✕'} ${label}`)));
}

function confClass(c) {
  return c ? `conf-${c}` : 'thin';
}

function renderTable(data) {
  const body = $('et-body');
  if (!data.runners.length) {
    body.replaceChildren(el('tr', null, 'no runners'));
    return;
  }
  body.replaceChildren(...data.runners.map((r) => {
    const tr = el('tr');
    const cells = [
      ['num', r.horse_no ?? '—'],
      ['name', r.horse_name ?? '—'],
      ['num', r.place ?? '—'],
      ['num', fmtTime(r.finish_time)],
      ['num', num(r.figure, 1)],
      ['num', signed(r.len_vs_par)],
      ['num', signed(r.len_vs_race)],
      ['num', r.et_n_eff ?? '—'],
      [confClass(r.confidence), r.confidence ?? '—'],
      ['thin', r.et_level ?? '—'],
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

/* Cumulative race times get m:ss.xx; a sectional split stays plain seconds. */
function fmtTime(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return m >= 1 ? `${m}:${s.toFixed(2).padStart(5, '0')}` : s.toFixed(2);
}

async function loadRace() {
  const date = $('meeting-select').value;
  const no = $('race-select').value;
  if (!date || !no) return;
  try {
    const data = await api.etRace(date, Number(no));
    renderInvariants(data);
    renderTable(data);
  } catch (e) {
    $('et-body').replaceChildren(el('tr', null, `failed to load: ${e.message}`));
  }
}

async function loadMeetings() {
  const meetings = await api.meetings(60);
  $('meeting-select').replaceChildren(...meetings.map((m) => {
    const o = el('option', null, `${m.race_date} · ${m.venue ?? ''} · ${m.races}R`);
    o.value = m.race_date;
    return o;
  }));
  if (meetings.length) await loadRaces(meetings[0].races);
}

async function loadRaces(count) {
  const n = count ?? 11;
  $('race-select').replaceChildren(...Array.from({ length: n }, (_, i) => {
    const o = el('option', null, `Race ${i + 1}`);
    o.value = String(i + 1);
    return o;
  }));
}

async function onRebuild() {
  const btn = $('rebuild-et');
  const out = $('job-result');
  btn.disabled = true;
  btn.textContent = 'Rebuilding…';
  out.hidden = false;
  out.className = 'job-result';
  out.textContent = 'Rebuilding ET references…';
  try {
    const r = await api.rebuildEt(24);
    /* Report counts, never a bare "done": a zero has to be visible immediately. */
    out.className = 'job-result ok';
    out.textContent =
      `${r.rows_written.toLocaleString()} runner_et rows from ${r.runs_loaded.toLocaleString()} runs · ` +
      `window ${r.window?.[0]} to ${r.window?.[1]} · ` +
      `${r.sec_per_length?.toFixed(4)} sec/length`;
    await Promise.all([renderSummary(), renderFreshness(), loadRace()]);
  } catch (e) {
    out.className = 'job-result err';
    out.textContent = `rebuild failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Rebuild ET';
  }
}

async function init() {
  renderNav('model-analysis.html');
  $('rebuild-et').addEventListener('click', onRebuild);
  $('meeting-select').addEventListener('change', loadRace);
  $('race-select').addEventListener('change', loadRace);
  await Promise.all([renderFreshness(), renderSummary().catch(() => {})]);
  await loadMeetings();
  await loadRace();
}

init();

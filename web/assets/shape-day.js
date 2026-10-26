/* Race shape — TODAY'S RACES: what a meeting's finished races say, beside the record (Speed Map).
 *
 * For each race with a result: the first three, where each was at the first call and its draw
 * (HKJC's), and Brett's read of where it came in the straight -- rail, middle or outside -- saved
 * on the click (a second click takes it back; CLEAR removes a race's reads). FETCH asks HKJC for
 * the results of races that have gone off and have none stored.
 *
 * Then the day so far: how often the winner came from the front third against what the races'
 * simulations expected, the placed horses' lanes, and the day's bias fitted on its finished races,
 * in lengths, with its error. And the record, first, because it says how to read the rest: on the
 * archive a meeting's early races have not told its later ones anything the price did not. The
 * read never moves a simulation by itself; SET THE SCENARIO TO TODAY'S READ puts it on the sliders
 * of the race on screen, for Brett to run if he wants to.
 *
 * Each surface is its own section (26 Oct): a card's AWT races are read apart from its turf ones,
 * against the AWT's own record, and a read is only set on a race of its own surface.
 */
import { api } from './api.js';
import { el, DASH } from './vocab.js';

const LANES = [['rail', 'RAIL'], ['middle', 'MIDDLE'], ['outside', 'OUTSIDE']];
const NAME = { AWT: 'AWT', Turf: 'TURF' };
const title = (s) => String(s || '').split(/\s+/).map((w) => (w ? w[0] + w.slice(1).toLowerCase() : w)).join(' ');
const ord = (n) => ({ 1: '1st', 2: '2nd', 3: '3rd' }[n] || `${n}th`);
const pct = (v) => (v == null ? DASH : `${Math.round(100 * v)}%`);
const lens = (r) => `${r.lengths > 0 ? '+' : r.lengths < 0 ? '−' : ''}${Math.abs(r.lengths).toFixed(1)} L ± ${r.lengths_se.toFixed(1)}`;
const rs = (v) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}`;

export function createDayPanel(host, { onApply }) {
  let date = null;
  let data = null;
  let open = false;
  let fetched = null;         // what the last FETCH said, kept across the redraw it causes

  async function show(d) {
    if (d !== date) fetched = null;
    date = d;
    try {
      data = await api.shapeDay(date);
    } catch (e) {
      data = null;
      host.replaceChildren(el('p', 'shape-empty', `Today's races: ${e.message}`));
      return;
    }
    render();
  }

  function render() {
    const run = data.races.filter((r) => r.status === 'result');
    const off = data.races.filter((r) => r.status === 'off');
    const box = el('div', 'shape-day');
    const head = el('div', 'sc-head');
    const toggle = el('button', 'shape-btn', open ? 'HIDE' : 'SHOW');
    toggle.type = 'button';
    toggle.addEventListener('click', () => { open = !open; render(); });
    head.append(el('span', 'label', `TODAY'S RACES · ${run.length} OF ${data.races.length} WITH A RESULT`), toggle);
    if (off.length) {
      const f = el('button', 'shape-btn', `FETCH ${off.length} RESULT${off.length === 1 ? '' : 'S'} SO FAR`);
      f.type = 'button';
      f.addEventListener('click', () => fetchResults(f));
      head.append(f);
    }
    if (fetched) head.append(el('span', 'small', fetched));
    box.append(head);
    if (!data.record) box.append(el('p', 'shape-line', 'No record yet of whether a day’s bias carries over (jobs.fit_day_bias).'));
    const mixed = data.surfaces.length > 1;
    for (const S of data.surfaces) {
      const mine = run.filter((r) => r.surface === S.surface);
      if (mixed) box.append(el('p', 'day-surface', `THE ${NAME[S.surface] || S.surface} RACES · ${mine.length} OF ${S.races} WITH A RESULT`));
      if (S.record) box.append(recordLine(S.record));
      if (open) {
        box.append(...mine.map(raceRow), ...tally(S, mine));
      } else if (mine.length) {
        box.append(el('p', 'shape-line', summary(S)));
      }
    }
    host.replaceChildren(box);
  }

  function recordLine(R) {
    const p = el('p', 'shape-line');
    const P = R.persistence, Q = R.prediction;
    const rq = `leaders r ${rs(P.front.r)}, inside gates ${rs(P.inside.r)}, the rail ${rs(P.rail.r)}`;
    const carry = `carrying the read into the next race made its chances ${Q.gain_per_race < 0 ? 'worse' : 'no worse'} than `
      + `the price's alone (${Math.abs(Q.gain_per_race).toFixed(4)} ${Q.gain_per_race < 0 ? 'more' : 'less'} log loss a race, `
      + `± ${Q.se}; better in ${Q.seasons_better} season${Q.seasons_better === 1 ? '' : 's'} of ${Q.seasons})`;
    if (R.surface === 'AWT') {
      p.append(el('b', null, 'THE AWT RECORD: '),
        `on the ${P.meetings} nights since ${R.seasons[0]} with ${R.min_races} or more AWT races, a night's first AWT `
        + `races have not predicted its later ones beyond the price — ${rq} — and ${carry}. But ${P.meetings} nights `
        + `reliably show only nights that differ by about ${R.detectable_lengths} lengths between leading and racing last, `
        + 'so a smaller AWT night bias is not ruled out. Read the night as what happened, not as what will.');
      return p;
    }
    p.append(el('b', null, 'THE RECORD: '),
      `over ${P.meetings} meetings since ${R.seasons[0]}, a meeting's first half has not predicted its second `
      + `beyond the price — ${rq}`
      + (R.detectable_lengths ? ` (the test finds days that differ by about ${R.detectable_lengths} lengths between `
        + 'leading and racing last; smaller ones it can miss)' : '')
      + ` — and ${carry}. Read the day as what happened, not as what will.`);
    return p;
  }

  function summary(S) {
    const T = S.tally;
    return `Winners from the front third ${T.winner_front_third} of ${T.races}`
      + (T.expected_races ? ` (the simulations expected about ${T.expected_front_third.toFixed(1)})` : '')
      + (S.read && S.read.front ? ` · the day's read: leaders ${lens(S.read.front)}` : '')
      + ' · SHOW to enter lanes';
  }

  function raceRow(r) {
    const row = el('div', 'day-race');
    const label = el('div', 'day-no');
    label.append(el('b', null, `R${r.no}`), el('span', 'small', ` ${r.off ?? ''}`));
    row.append(label);
    for (const h of r.placed) {
      const cell = el('div', 'day-horse');
      const chip = el('span', 'sc', String(h.no));
      chip.style.setProperty('--c', `var(--sc-${((h.no - 1) % 14) + 1})`);
      cell.append(el('span', 'small', `${ord(h.place)} `), chip, el('span', 'nm', ` ${title(h.name)}`),
        el('span', 'small', ` · gate ${h.draw ?? DASH} · ${h.first_call ? `${ord(h.first_call)} early` : 'no first call'}`));
      const seg = el('div', 'shape-seg');
      for (const [k, txt] of LANES) {
        const b = el('button', null, txt);
        b.type = 'button';
        b.setAttribute('aria-pressed', String(h.lane === k));
        b.title = `Where ${title(h.name)} came in the straight`;
        b.addEventListener('click', () => saveLane(r.no, h.no, h.lane === k ? null : k));
        seg.append(b);
      }
      cell.append(seg);
      row.append(cell);
    }
    const clear = el('button', 'shape-btn', 'CLEAR');
    clear.type = 'button';
    clear.disabled = !r.placed.some((h) => h.lane);
    clear.title = 'Remove this race’s lane reads';
    clear.addEventListener('click', () => clearRace(r.no));
    row.append(clear);
    return row;
  }

  function tally(S, run) {
    const name = NAME[S.surface] || S.surface;
    if (!run.length) return [el('p', 'shape-line', `No ${name} race of this meeting has a result yet.`)];
    const T = S.tally, U = S.usual || {};
    const lanesN = T.placed_lanes.rail + T.placed_lanes.middle + T.placed_lanes.outside;
    const p1 = el('p', 'shape-line');
    p1.append(el('b', null, 'THE DAY SO FAR: '), `winners from the front third at the first call ${T.winner_front_third} of ${T.races}`
      + (T.expected_races ? ` — the simulations expected about ${T.expected_front_third.toFixed(1)}` : '')
      + (U.winner_front_third != null ? `; usually ${pct(U.winner_front_third)} of ${name} winners` : '') + '. '
      + (lanesN ? `Your lane reads of the placed horses: rail ${T.placed_lanes.rail}, middle ${T.placed_lanes.middle}, `
        + `outside ${T.placed_lanes.outside}` + (U.placed_on_rail != null ? ` (on the archive ${pct(U.placed_on_rail)} of placed `
        + 'horses are on the rail turning in, where the comments say)' : '') + '.' : 'No lane reads entered yet.'));
    const out = [p1];
    const R = S.read;
    if (R && R.front) {
      const p2 = el('p', 'shape-line');
      p2.append(el('b', null, `THE DAY'S ${name} READ (${R.races} races, shrunk towards no bias): `),
        `leaders ${lens(R.front)} · inside gates ${lens(R.inside)}` + (R.rail ? ` · the rail ${lens(R.rail)}` : '')
        + `, in lengths of these races' own spread of results (${R.sigma} L). An error as wide as the number is a read of nothing.`);
      const apply = el('button', 'shape-btn', `SET THE SCENARIO TO TODAY'S ${name} READ`);
      apply.type = 'button';
      apply.title = `Puts the read on the scenario sliders of the race on screen, if it is a ${name} race; nothing runs until you re-simulate`;
      apply.addEventListener('click', () => {
        const q = (v) => Math.round(v * 4) / 4;
        const said = onApply({ front: q(R.front.lengths), lane: R.rail ? q(R.rail.lengths) : 0, surface: S.surface });
        apply.textContent = {
          none: 'THE RACE ON SCREEN HAS NO SCENARIO (NO BORROWED CLOCKS STORED)',
          surface: `THE RACE ON SCREEN IS NOT ${name === 'AWT' ? 'AN' : 'A'} ${name} RACE — PICK ONE FIRST`,
          held: 'SET ON THE SCENARIO BELOW, HELD TO THE SLIDERS’ RANGE — RE-SIMULATE TO RUN IT',
        }[said] || 'SET ON THE SCENARIO BELOW — RE-SIMULATE TO RUN IT';
      });
      out.push(p2, apply);
    } else if (R && R.note) {
      out.push(el('p', 'shape-line', R.note));
    }
    return out;
  }

  async function saveLane(race, horse, lane) {
    await api.saveDayLane({ race_date: date, race_no: race, horse_no: horse, lane });
    await show(date);
  }

  async function clearRace(race) {
    await api.clearDayLanes({ race_date: date, race_no: race });
    await show(date);
  }

  async function fetchResults(btn) {
    btn.disabled = true;
    btn.textContent = 'ASKING HKJC…';
    try {
      fetched = (await api.fetchDayResults(date)).summary;
    } catch (e) {
      fetched = `fetch failed: ${e.message}`;
    }
    await show(date);
  }

  return { show };
}

/* Form Guide — ported from web/design-source/Form Guide.dc.html.
 *
 * The artboard's central idea is ONE grid template, shared by the card header,
 * every horse row and every run row inside an expanded horse. A run lines up
 * column-for-column under the horse it belongs to, so the eye tracks straight
 * down instead of re-finding the columns at each level. It is in formguide.css
 * as --fg-grid and nothing here overrides it.
 *
 * Three levels of detail, each a click deeper: the card, a horse's six runs,
 * one run's sectionals and the quality of the race it came from.
 */
import { api, num, signed } from './api.js';
import { el, $, DASH, MINUS, renderNav, styleClass, styleOrdinal,
         replayUrl, trialReplayUrl, externalLink, compactDate,
         tripTags, tripTagChips, ordinal, classCell, classLabel, eszCell,
         paceCell, positionsText, conditionLabel, drawText } from './vocab.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';
import { loadTags, renderReview } from './review.js';


/* The trend tint's threshold, measured rather than chosen. Over 12,540 six-run
   windows across 1,967 horses, (mean of the newest three) − (mean of the oldest
   three) has mean +0.08 and sd 5.89 — it is noise centred on zero. A 1.5-point
   cut would tint 75% of runners, which is how a decoration comes to look like a
   signal. One standard deviation tints 23%, so a tinted horse is genuinely
   moving against the population rather than merely varying. */
const TREND_THRESHOLD = 5.9;

const COLS = [
  { key: 'no', label: 'NO', cls: 'c' },
  { key: 'name', label: 'HORSE' },
  { key: 'style', label: 'STYLE' },
  { key: 'draw', label: 'DR', cls: 'c' },
  { key: 'jockey', label: 'JOCKEY' },
  { key: 'trainer', label: 'TRAINER' },
  { key: 'wt', label: 'WT', cls: 'r' },
  { key: 'odds', label: 'ODDS', cls: 'r' },
  { key: 'seq', label: 'LAST 6 FIGURES · NEWEST LEFT · BAR = TREND' },
  { key: 'flags', label: 'FLAGS' },
];

/* Positional, front to back. Never an alphabetical sort — "Closer" before
   "Leader" would put the back of the field first. */
const STYLE_ORD = { Leader: 0, 'On-Pace': 1, Midfield: 2, Closer: 3 };

const state = {
  date: null, race: 1, races: [], guide: null, pace: null,
  open: new Set(), openRuns: new Set(), focus: null,
  sort: null, sortDir: 1, fit: null, fitFor: null,
  notes: {}, h2h: {}, quality: {}, trials: {}, popover: null,
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderStrip() {
  $('race-chips').replaceChildren(...state.races.map((r) => {
    const b = el('button', 'race-chip', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`;
    b.addEventListener('click', () => selectRace(r.race_no));
    return b;
  }));

  const sortBox = $('sort-state');
  if (state.sort) {
    const chip = el('button', 'sort-chip');
    const col = COLS.find((c) => c.key === state.sort);
    chip.append(document.createTextNode(
      `SORTED ${col?.label ?? state.sort} ${state.sortDir > 0 ? '▲' : '▼'} `));
    chip.append(el('span', 'clear', '× NUMBER'));
    chip.addEventListener('click', () => { state.sort = null; render(); });
    sortBox.replaceChildren(chip);
  } else {
    sortBox.replaceChildren();
  }
  $('open-count').textContent = `${state.open.size} EXPANDED`;
}

/* Design note 03 §7: one value for the whole race, five steps, blue to red,
   and a different axis from a horse's running style. Measured from sectionals
   where the race has been run; projected from the field's styles where it has
   not, and said to be a projection either way it goes. */
const PACE_STEPS = ['Very Slow', 'Slow', 'Neutral', 'Fast', 'Very Fast'];

function renderRaceHeader() {
  const race = state.guide?.race;
  const host = $('race-hdr');
  if (!race) { host.replaceChildren(); return; }

  const box = el('div', 'no-box');
  box.append(el('span', 'no', `R${race.race_no}`));
  box.append(el('span', 'off', race.off_time ?? DASH));

  const facts = el('div', 'facts');
  const fact = (k, v) => {
    const d = el('div');
    d.append(el('k', null, `${k} `));
    d.append(document.createTextNode(v ?? DASH));
    return d;
  };
  facts.append(fact('DIST', race.distance ? `${race.distance}m` : null));
  // The same words the run rows use. Printed raw this said "CLASS 0" for a
  // Group race and "CLASS Griffin Race" for a Griffin, so the header and the
  // rows beneath it named the same race two different ways.
  facts.append(fact('CLASS', classLabel(race.race_class)));
  facts.append(fact('GOING', race.going));
  facts.append(fact('COURSE', race.course));
  facts.append(fact('FIELD', String(race.field_size)));

  const p = state.pace;
  const pace = el('div', `pace${p && !p.confident ? ' unsure' : ''}`);
  pace.append(el('k', null, 'RACE PACE'));
  const step = p?.band ? PACE_STEPS.indexOf(p.band) : -1;
  const scale = el('div', 'pace-scale');
  PACE_STEPS.forEach((_, i) => {
    const cell = el('i', `p${i + 1}${i === step ? ' on' : ''}`);
    cell.title = PACE_STEPS[i];
    scale.append(cell);
  });
  pace.append(scale);
  pace.append(el('span', `pace-name${step >= 0 ? ` p${step + 1}` : ''}`,
    p?.band ?? 'no read'));
  // Measured and projected are different claims and the header says which.
  if (p?.band) pace.append(el('span', 'qual', p.measured ? 'measured' : 'projected'));
  if (p) {
    pace.title = !p.band
      ? 'no runner in this race has an established running style'
      : p.measured
        ? `measured: this race's early sectional is ${p.z >= 0 ? '+' : ''}${p.z} sd `
          + `against ${p.peers} races at ${state.guide?.race?.distance}m`
        : `projected from ${p.field_size - p.unknown} of ${p.field_size} classified `
          + `runners · pressure ${p.pressure} · leaders: ${p.leaders.join(', ') || 'none'}`
          + (p.confident ? '' : ' · too few classified to read confidently');
  }
  facts.append(pace);

  host.replaceChildren(box, facts, el('div', 'note',
    'FIGURE 100 = PAR · SIX RUNS, NEWEST LEFT · TINT UNDER THE SEQUENCE IS THE TREND'));
}

/* ── the card ────────────────────────────────────────────────────────────── */

function renderHead() {
  $('fg-head').replaceChildren(...COLS.map((c) => {
    const b = el('button', c.cls ?? null);
    b.append(document.createTextNode(c.label));
    if (state.sort === c.key) {
      b.setAttribute('aria-sort', state.sortDir > 0 ? 'ascending' : 'descending');
      b.append(el('span', 'ind', state.sortDir > 0 ? '▲' : '▼'));
    }
    b.addEventListener('click', () => {
      if (state.sort === c.key) {
        // asc → desc → back to race-card order, which is the real default.
        if (state.sortDir > 0) state.sortDir = -1;
        else { state.sort = null; state.sortDir = 1; }
      } else { state.sort = c.key; state.sortDir = 1; }
      render();
    });
    return b;
  }));
}

function history(runner) {
  return state.guide?.history?.[runner.horse_name] ?? [];
}

function sortValue(r, key) {
  const runs = history(r);
  switch (key) {
    case 'no': return r.horse_no;
    case 'name': return r.horse_name;
    case 'style': return STYLE_ORD[runs[0]?.pace_style] ?? 9;
    case 'draw': return r.draw ?? 99;
    case 'jockey': return r.jockey ?? '';
    case 'trainer': return r.trainer ?? '';
    case 'wt': return r.actual_weight ?? 0;
    // Best figure first when sorting by form, so ascending means "best".
    case 'odds': return r.win_odds ?? 9e9;
    case 'seq': return -(runs[0]?.et_figure ?? 0);
    case 'flags': return -flagsFor(r).length;
    default: return 0;
  }
}

function sortedRunners() {
  const runners = [...(state.guide?.race?.runners ?? [])];
  if (!state.sort) return runners.sort((a, b) => a.horse_no - b.horse_no);
  return runners.sort((a, b) => {
    const x = sortValue(a, state.sort);
    const y = sortValue(b, state.sort);
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * state.sortDir;
  });
}

/** The row's flags. Each is a fact about THIS horse in THIS race, and each is
 *  earned from data rather than decorative. */
function flagsFor(runner) {
  const out = [];
  const bb = state.guide?.blackbook?.[runner.horse_name];
  if (bb) out.push({ kind: 'bb', text: 'BLACKBOOK', bb });

  const runs = history(runner);
  const last = runs[0];
  if (last?.tags?.length) {
    // `tripTags` is the rule. Written out here it kept only two of the four
    // routine tags, so this flag fired on a run where the stewards reported
    // nothing at all.
    const trip = tripTags(last.tags);
    if (trip.length) out.push({ kind: 'trip', text: 'TRIP', title: trip.join(' · ') });
  }
  const race = state.guide?.race;
  if (last && race?.distance && last.distance) {
    const step = race.distance - last.distance;
    if (Math.abs(step) >= 200) {
      out.push({ kind: 'cond', text: `DIST ${step > 0 ? '↑' : '↓'}`,
                 title: `${last.distance}m last start, ${race.distance}m today` });
    }
  }
  if (last && race?.going && last.going && race.going !== last.going) {
    out.push({ kind: 'cond', text: 'GOING ↔',
               title: `${last.going} last start, ${race.going} today` });
  }
  // Days since the last run only when it is long enough to matter.
  if (last && race) {
    const days = daysBetween(last.race_date, race.race_date);
    if (days !== null && days >= 84) {
      out.push({ kind: 'lay', text: `${days}d LAY`, title: `last ran ${last.race_date}` });
    }
  }
  return out;
}

function daysBetween(a, b) {
  const x = Date.parse(a);
  const y = Date.parse(b);
  return Number.isNaN(x) || Number.isNaN(y)
    ? null : Math.round((y - x) / 86400000);
}

/** The six-figure sequence, newest left, with the trend as a 2px tint. Drawn
 *  as a tint rather than a line because six figures do not support a slope
 *  claimed to more precision than that. */
function sequenceCell(runner) {
  const cell = el('div');
  const runs = history(runner).slice(0, 6);
  const box = el('div', 'seq');
  const figures = runs.map((r) => r.et_figure);
  figures.forEach((f) => {
    if (f === null || f === undefined) { box.append(el('span', 'none', DASH)); return; }
    const cls = f >= 103 ? 'best' : f >= 98 ? 'mid' : 'weak';
    box.append(el('span', cls, f.toFixed(0)));
  });
  for (let i = runs.length; i < 6; i += 1) box.append(el('span', 'none', DASH));
  cell.append(box);

  // Newest first, so a rising horse has its BIGGEST figure at index 0.
  const known = figures.filter((f) => f !== null && f !== undefined);
  const slope = el('div', 'slope');
  // Six figures at a time, so the halves are three and three. Fewer than six
  // is not a trend: with four runs each half is two, and two ET figures
  // routinely differ by more than the threshold on their own.
  if (known.length >= 6) {
    const half = Math.floor(known.length / 2);
    const recent = known.slice(0, half).reduce((a, b) => a + b, 0) / half;
    const older = known.slice(-half).reduce((a, b) => a + b, 0) / half;
    const move = recent - older;
    if (move >= TREND_THRESHOLD) slope.classList.add('up');
    else if (move <= -TREND_THRESHOLD) slope.classList.add('down');
    slope.title = `recent three ${recent.toFixed(1)} vs earlier three `
      + `${older.toFixed(1)} (${move >= 0 ? '+' : ''}${move.toFixed(1)}); `
      + `tinted beyond ${TREND_THRESHOLD}, one sd of the population`;
    if (!slope.classList.contains('up') && !slope.classList.contains('down')) {
      slope.title += ' — flat, so no tint';
    }
  } else if (known.length) {
    slope.title = `${known.length} figures — a trend needs six`;
  } else {
    slope.title = 'no figures on record';
  }
  cell.append(slope);
  return cell;
}

function horseRow(runner) {
  const runs = history(runner);
  const last = runs[0];
  const bb = state.guide?.blackbook?.[runner.horse_name];
  const open = state.open.has(runner.horse_no);

  const row = el('div', `fg-row${open ? ' open' : ''}${bb ? ' booked' : ''}`);
  row.setAttribute('role', 'row');
  row.addEventListener('click', () => toggleHorse(runner.horse_no));
  row.addEventListener('mouseenter', () => focusHorse(runner));

  row.append(el('div', 'no', String(runner.horse_no ?? DASH)));

  const nameCell = el('div', 'name-cell');
  nameCell.append(el('span', 'caret', open ? '▼' : '▶'));
  const nm = el('span', `nm${bb ? ' booked' : ''}`, runner.horse_name);
  nameCell.append(nm);
  row.append(nameCell);

  const st = el('div');
  st.append(el('span', styleClass(last?.pace_style), last?.pace_style ?? 'UNKNOWN'));
  row.append(st);

  row.append(el('div', 'dr', drawText(runner.draw)));
  row.append(el('div', 'jockey', runner.jockey ?? DASH));

  const changed = last?.trainer && runner.trainer && last.trainer !== runner.trainer;
  const tr = el('div', `trainer${changed ? ' changed' : ''}`, runner.trainer ?? DASH);
  if (changed) tr.title = `was ${last.trainer} last start`;
  row.append(tr);

  row.append(el('div', 'wt', String(runner.actual_weight ?? DASH)));
  row.append(el('div', 'odds', runner.win_odds ? num(runner.win_odds, 1) : DASH));
  row.append(sequenceCell(runner));

  const flags = el('div', 'flags');
  flagsFor(runner).forEach((f) => {
    const chip = el('span', `flag ${f.kind}`, f.text);
    if (f.title) chip.title = f.title;
    if (f.bb) {
      chip.addEventListener('mouseenter', (e) => showBlackbookNote(e, f.bb));
      chip.addEventListener('mouseleave', hidePopover);
    }
    flags.append(chip);
  });
  row.append(flags);
  return row;
}

/* ── expanded horse ──────────────────────────────────────────────────────── */

function trialBand(runner) {
  const band = el('div', 'trial-band');
  const found = state.trials[runner.horse_name];
  // The band keeps its height when there is nothing to show — the artboard's
  // own empty state. A collapsing band would change the page's rhythm from
  // horse to horse.
  if (!found || !found.length) {
    band.append(el('div', 'empty', 'NO TRIAL BEFORE THIS RACE'));
    return band;
  }
  // The same rating the Trials page shows, from the same function. A second
  // rating written here would drift from that one within a season.
  found.forEach((t) => {
    const row = el('div', 'trial-line');
    row.append(el('span', `q q-${t.quality_band.toLowerCase()}`, t.quality_mark));
    row.append(el('span', 'd', t.trial_date));
    row.append(el('span', 'v', `${t.venue} ${t.surface}`));
    row.append(el('span', 'p',
      t.place === null ? DASH : `${t.place}/${t.field_size}`));
    if (t.margin !== null && t.margin !== undefined) {
      row.append(el('span', 'm', `${num(t.margin, 1)}L`));
    }
    const txt = el('span', 'c', t.comment ?? '');
    // The reasons, so a mark nobody can check is not a mark anyone acts on.
    txt.title = `${t.quality_band}${t.quality_reasons.length
      ? ` — ${t.quality_reasons.join('; ')}` : ''}`;
    row.append(txt);

    // What YOU wrote about this trial, on the Trials page. HKJC's comment says
    // what happened; the note says why you cared, and the second is the reason
    // the horse is worth a look now that it is in a race.
    if (t.note) {
      const mine = el('span', 'own-note', `✎ ${t.note.note}`);
      mine.title = `your note — ${t.trial_date} T${t.trial_no}`;
      row.append(mine);
    }

    // The footage the mark is a summary of. A ++ nobody can watch is a score
    // taken on trust, which is the opposite of how every other figure here is
    // treated.
    const turl = trialReplayUrl(t.trial_date, t.trial_no, t.venue,
                                { archived: t.archived });
    if (turl) {
      const play = externalLink(turl, '▶', 'icon play');
      play.title = `trial replay — ${t.trial_date} batch ${t.trial_no}`;
      row.append(play);
    }
    band.append(row);
  });
  return band;
}

function swingTier(lb) {
  if (lb === null || lb === undefined) return '';
  const v = Math.abs(lb);
  return v >= 8 ? 't3' : v >= 6 ? 't2' : v >= 4 ? 't1' : '';
}

function h2hBand(runner) {
  const pairs = state.h2h[runner.horse_name];
  if (!pairs || !pairs.length) return null;
  const band = el('div', 'h2h-band');
  const lab = el('span', 'lab');
  lab.append(document.createTextNode('HEAD TO HEAD'));
  lab.append(el('b', null, ` ${pairs.length}`));
  band.append(lab);
  // A scrolling rail, like Race Day's. A horse that has met six of today's
  // field has six cards, and squeezing them into the width made every one
  // unreadable rather than showing fewer honestly.
  const rail = el('span', 'pair-rail');
  pairs.forEach((p) => {
    const box = el('span', 'pair');

    const top = el('span', 'pl');
    top.append(el('span', 'who', `v ${p.other}`));
    top.append(el('span', 'rec',
      `${p.record}${p.meetings ? ` · ${p.meetings}` : ''}`));
    box.append(top);

    const mid = el('span', 'pl');
    mid.append(el('span', 'last',
      `${compactDate(p.last_date)} ${p.last_cond} · ${p.last_line}`));
    box.append(mid);

    // The verdict before the arithmetic.
    const ver = el('span', 'pl');
    if (p.favours) {
      ver.append(el('span', `won-wt ${p.favours}`,
        p.favours === 'mine' ? 'THIS HORSE' : p.favours_name));
      ver.append(el('span', 'by', `${p.favours_lb}lb better off`));
    } else {
      ver.append(el('span', 'level',
        p.swing === null ? 'NO WT DATA' : 'SAME WEIGHTS'));
    }
    ver.append(el('span', `swing ${swingTier(p.swing)}`,
      p.swing === null || p.swing === undefined ? '' : `${p.swing}LB`));
    box.append(ver);

    const gates = el('span', 'pl gates');
    gates.append(el('span', 'k', 'GATE'));
    gates.append(el('span', 'g', gateMove(p.gate_then, p.gate_now)));
    gates.append(el('span', 'k', `v ${p.other_no}`));
    gates.append(el('span', 'g', gateMove(p.other_gate_then, p.other_gate_now)));
    box.append(gates);

    rail.append(box);
  });
  band.append(rail);
  return band;
}

/** Where a horse jumps from today against where it jumped from last time.
 *  Wider or inside NAMED, because a signed number leaves the reader working
 *  out which direction is which on a track they may not have in mind. */
function gateMove(then, now) {
  if (then == null || now == null) return String(now ?? then ?? DASH);
  const d = now - then;
  if (d === 0) return `${then}→${now} =`;
  return `${then}→${now} ${d > 0 ? `+${d} W` : `${MINUS}${-d} IN`}`;
}

/* The run row's own columns.
 *
 * It no longer borrows `--fg-grid` from the runner rows above it. That grid
 * was ten columns wide and this is twelve, and the two were never describing
 * the same things anyway: the runner row's fourth column is today's draw,
 * the run row's is the draw of a race two months ago. Sharing the template
 * bought a vertical alignment between quantities that do not correspond, and
 * cost every column that had nowhere to go — POSITIONS, ESZ and the pace of
 * the race — being crushed into one trailing cell.
 *
 * Order set by the owner, and it reads as a sentence: when and where, how it
 * was ridden, who rode it, what it carried, where it jumped from and how fast
 * it got away, where it sat, where it finished, and what the clock said.
 */
const RUN_HEAD = [
  ['RUN', 'c'], ['DATE · TRK CRS DIST GOING CL', ''], ['STYLE', ''],
  ['JOCKEY', ''], ['TRAINER', ''], ['WT', 'r'], ['DR', 'c'], ['ESZ', 'r'],
  ['POSITIONS', ''], ['FIN', 'r'],
  ['FIGURE · MARGIN · TIME · PLACE DIV', ''],
  ['PACE · GEAR · TRIP · BB · NOTE · VID', ''],
];

function runKey(horseNo, run) {
  return `${horseNo}:${run.race_date}:${run.race_no}`;
}

function finClass(run) {
  if (run.place === 1) return 'win';
  if (run.place !== null && run.place <= 3) return 'placed';
  return 'unplaced';
}

function runRow(runner, run, index) {
  const key = runKey(runner.horse_no, run);
  const open = state.openRuns.has(key);
  const row = el('div', `run-row${open ? ' open' : ''}`);
  row.addEventListener('click', (e) => {
    if (e.target.closest('.marks')) return;   // icons own their clicks
    toggleRun(key, runner, run);
  });

  row.append(el('div', 'n', String(index + 1)));

  // ── when and where ──────────────────────────────────────────────────────
  const when = el('div', 'when');
  when.append(el('span', 'caret', open ? '▼' : '▶'));
  // One date format, defined once — brief 08 §1. An ISO date on screen is
  // a storage format that escaped.
  when.append(el('span', null, compactDate(run.race_date)));
  // Track, course, distance and going as text; the class as its own chip, so
  // it can be read at a glance and so "0" and "Griffin Race" stop rendering
  // as "C0" and "CGriffin Race". `conditionLabel` still builds the whole
  // string for tooltips and popover titles, where a chip cannot go.
  const where = [run.venue, run.course, run.distance ? `${run.distance}m` : null,
                 run.going].filter(Boolean).join(' ');
  if (where) when.append(el('span', 'cond', where));
  const cl = classCell(run.race_class);
  if (cl) when.append(cl);
  // The whole condition in words, for the reader whose window is narrow
  // enough that the cell has clipped it.
  when.title = conditionLabel(run);
  row.append(when);

  // ── how it was ridden ───────────────────────────────────────────────────
  // Once. It used to appear here as a badge and again in the trailing cell as
  // a bare initial, which read as two facts and was one.
  const st = el('div', 'st');
  st.append(el('span', styleClass(run.pace_style), run.pace_style ?? 'UNKNOWN'));
  row.append(st);

  // Clips at the tail rather than widening the row — with the whole name on
  // the tooltip, because "R Kingsc…" and "R Kingston" are different people.
  const jk = el('div', 'jockey', run.jockey ?? DASH);
  if (run.jockey) jk.title = run.jockey;
  row.append(jk);

  const runs = history(runner);
  const prev = runs[index + 1];
  const changed = prev?.trainer && run.trainer && prev.trainer !== run.trainer;
  const tr = el('div', `trainer${changed ? ' changed' : ''}`, run.trainer ?? DASH);
  if (changed) tr.title = `was ${prev.trainer} the run before`;
  else if (run.trainer) tr.title = run.trainer;
  row.append(tr);

  row.append(el('div', 'wt', String(run.actual_weight ?? DASH)));
  row.append(el('div', 'dr', drawText(run.draw)));

  // ── how fast away, and where it sat ─────────────────────────────────────
  // The draw says where it started; ESZ says what it did with it. A wide gate
  // and a fast beginning is a different run from a wide gate and a slow one,
  // and the positions that follow are unreadable without it.
  const esz = state.guide?.esz?.[`${run.race_date}:${run.race_no}:${run.horse_no}`];
  const eszCol = el('div', 'esz-col');
  eszCol.append(eszCell(esz));
  row.append(eszCol);

  // Four calls fit; the five-call route line ellipsises, so the sequence is
  // repeated whole on the tooltip.
  const pos = el('div', 'pos', positionsText(run.running_positions));
  if ((run.running_positions ?? []).length) {
    pos.title = `at each call: ${run.running_positions.join(' → ')}`;
  }
  row.append(pos);

  // A non-finisher shows its code, not a blank: WV and PU mean different things.
  row.append(el('div', `fin ${finClass(run)}`,
    run.place !== null && run.place !== undefined
      ? `${run.place}${run.dead_heat ? '=' : ''}` : (run.place_code ?? DASH)));

  const fg = el('div', 'fig-group');
  const above = run.et_figure !== null && run.et_figure >= 100;
  const figCls = run.et_figure === null || run.et_figure === undefined
    ? '' : above ? 'above' : 'below';
  fg.append(el('span', `fig ${figCls}`, run.et_figure === null
    || run.et_figure === undefined ? DASH : run.et_figure.toFixed(0)));
  fg.append(el('span', `len ${figCls}`, run.lengths_behind === null
    || run.lengths_behind === undefined ? DASH : `${run.lengths_behind.toFixed(2)}L`));
  fg.append(el('span', 't', run.finish_time_display ?? DASH));
  // What a $10 place ticket on this run paid. A placed run at 4.5 and a placed
  // run at 60 are not the same result, and FIN alone hides the difference —
  // which is why the design puts the dividend on the line beside it. Blank,
  // not a dash, when the horse did not place: there was no ticket to pay.
  if (run.place_dividend !== null && run.place_dividend !== undefined) {
    const div = el('span', 'plc-div', `$${run.place_dividend.toFixed(1)}`);
    div.title = `place dividend, per $10 — ${run.race_date} race ${run.race_no}`;
    fg.append(div);
  }
  row.append(fg);

  // ── the trailing cell ───────────────────────────────────────────────────
  const trail = el('div', 'trail');

  // How fast the RACE was run, which the running style alone cannot tell you:
  // led in a crawl and led in a sprint are opposite facts. Measured pace only
  // — the server sends nothing where it could not measure it, and a dash is
  // the honest reading of that.
  const pace = state.guide?.pace?.[`${run.race_date}:${run.race_no}`];
  const paced = paceCell(pace);
  paced.title = pace
    ? `the race was run ${pace.band.toLowerCase()} for the distance`
      + ` — ${pace.z > 0 ? '+' : ''}${pace.z} sd over ${pace.peers} races`
    : 'no sectionals for this race, so its pace was never measured';
  trail.append(paced);

  trail.append(gearCell(run, prev));

  // The stewards' prose as the tags derived from it. The full sentence stays
  // on the tooltip and in the expanded run — but a row that has to be READ is
  // a row that gets skipped, and the tag is the part that is comparable
  // between runs. A veterinary finding sorts to the front.
  const comment = run.incident_comment || run.running_comment || null;
  // Two, as on every other page. Three fit only by ellipsis, and "bum…",
  // "raced k…", "shifte…" is less readable than two whole tags and a count —
  // the tooltip carries the full list either way.
  const chips = tripTagChips(run.tags, { comment, limit: 2 });
  if (chips) {
    trail.append(chips);
  } else if (!(run.tags ?? []).length
             && (comment || (run.lane_notes ?? []).length)) {
    // Prose only when the vocabulary saw NOTHING. Tags that exist but are all
    // routine mean the stewards reported a clean run, and printing the
    // sentence they wrote — "No report." — puts a note in the trip column of
    // a horse that had no trip to report.
    const trip = el('span', 'trip', comment || run.lane_notes.join(' · '));
    trip.title = trip.textContent;
    trail.append(trip);
  }

  const marks = el('div', 'marks');
  const bb = state.guide?.blackbook?.[runner.horse_name];
  if (bb && bb.source_date === run.race_date && bb.source_race_no === run.race_no) {
    const dot = el('span', 'bb-src');
    dot.title = 'source run for a blackbook entry';
    marks.append(dot);
  }
  const note = noteFor(runner.horse_name, run);
  const pen = el('button', `icon${note ? ' has' : ''}`, '✎');
  pen.title = note ? note.note : 'note on this run';
  pen.addEventListener('click', (e) => { e.stopPropagation(); showNote(e, runner, run); });
  marks.append(pen);

  // Watch the run. Design note 04 §4 asks for a play control per run and gives
  // the URL pattern; it earns its keep most on the rows a trip tag flags,
  // because that is the moment you want to see what actually happened.
  const url = replayUrl(run.race_date, run.race_no);
  if (url) {
    const play = externalLink(url, '▶', 'icon play');
    play.title = `replay — ${run.race_date} race ${run.race_no}`;
    play.addEventListener('click', (e) => e.stopPropagation());
    marks.append(play);
  }
  trail.append(marks);

  row.append(trail);
  return row;
}


/** Gear, and what changed about it since the run before.
 *
 * Design note 03 §3: flag any run where gear differs from the previous run,
 * and mark FIRST-TIME gear distinctly — it is "one of the more reliable public
 * signals bettors watch for". Comparing against the immediately preceding run
 * is what makes it a signal; against some earlier run it is noise.
 */
function gearCell(run, prev) {
  const cell = el('span', 'gear');
  const now = (run.gear || '').trim();
  cell.append(document.createTextNode(now || DASH));

  // No previous run on record is not the same as no gear before — a horse's
  // first appearance in the archive cannot support a first-time claim.
  if (!prev) return cell;

  const before = new Set((prev.gear || '').split('/').map((g) => g.trim()).filter(Boolean));
  const after = new Set(now.split('/').map((g) => g.trim()).filter(Boolean));
  const added = [...after].filter((g) => !before.has(g));
  const removed = [...before].filter((g) => !after.has(g));

  const firstHere = state.guide?.gear_first?.[run.horse_name]
    ?.[`${run.race_date}:${run.race_no}`] ?? [];
  added.forEach((g) => {
    // First-time over the whole record, not just the six runs on screen.
    const firstEver = firstHere.includes(g);
    const chip = el('span', `gear-flag${firstEver ? ' first' : ''}`,
      `${g}${firstEver ? ' 1ST' : ''}`);
    chip.title = firstEver
      ? `${g} applied for the first time on record`
      : `${g} back on, off last start`;
    cell.append(chip);
  });
  removed.forEach((g) => {
    const chip = el('span', 'gear-flag off', g);
    chip.title = `${g} removed since last start`;
    cell.append(chip);
  });
  return cell;
}

function noteFor(horseName, run) {
  return (state.notes[horseName] ?? []).find(
    (n) => n.race_date === run.race_date && n.race_no === run.race_no) ?? null;
}

/* ── one run, expanded ───────────────────────────────────────────────────── */

const SEGMENT_LABELS = ['1st 400', '2nd 400', '3rd 400', '4th 400', 'FINAL 400'];

function runDetail(runner, run) {
  const box = el('div', 'run-detail');

  const left = el('div', 'left');
  left.append(el('div', 'cap', 'SECTIONALS · POSITION AT EACH'));
  const splits = el('div', 'splits');
  const times = run.section_times ?? [];
  const positions = run.running_positions ?? [];
  if (!times.length) {
    splits.append(el('div', 'cap', 'NO SECTIONAL TIMES RECORDED FOR THIS RUN'));
  } else {
    const fastest = Math.min(...times);
    const slowest = Math.max(...times);
    times.forEach((t, i) => {
      const cell = el('div', 'split-box');
      // The final split is the one that names itself; the rest count forward.
      const label = i === times.length - 1 ? 'FINAL 400'
        : SEGMENT_LABELS[i] ?? `SEG ${i + 1}`;
      cell.append(el('div', 'seg', label));
      const val = el('div', 'val');
      const cls = t === fastest ? 'fast' : t === slowest ? 'slow' : 'even';
      val.append(el('span', `t ${cls}`, t.toFixed(2)));
      val.append(el('span', 'p', positions[i] ? `P${positions[i]}` : DASH));
      cell.append(val);
      const bar = el('div', 'bar');
      const fill = el('i', cls);
      // Longer bar = faster split, so the eye reads "more" as "better".
      const span = slowest - fastest || 1;
      fill.style.width = `${20 + 80 * (slowest - t) / span}%`;
      bar.append(fill);
      cell.append(bar);
      splits.append(cell);
    });
  }
  left.append(splits);

  const facts = el('div', 'run-facts');
  const fact = (k, v, cls) => {
    const d = el('div');
    d.append(el('k', null, `${k} `));
    d.append(el('span', cls ?? 'v', v ?? DASH));
    return d;
  };
  const first = positions[0];
  // JUMP is where it was at the first call; ESZ is how fast it got there,
  // standardised inside this race. The position alone cannot tell a slow
  // beginner from one that was simply drawn wide in a fast-run race.
  const esz = state.guide?.esz?.[`${run.race_date}:${run.race_no}:${run.horse_no}`];
  facts.append(fact('JUMP', first ? `P${first}` : null));
  const eszFact = fact('ESZ', esz == null ? null
    : `${esz > 0 ? '+' : MINUS}${Math.abs(esz).toFixed(2)}`);
  if (esz != null) {
    eszFact.classList.add(esz >= 0 ? 'esz-fast' : 'esz-slow');
    eszFact.title = esz >= 0
      ? 'faster away than this race\u2019s field, in standard deviations'
      : 'slower away than this race\u2019s field, in standard deviations';
  }
  facts.append(eszFact);
  facts.append(fact('LANE', (run.lane_notes ?? []).join(' · ') || null));
  facts.append(fact('GEAR', run.gear || null));
  facts.append(fact('SARR', run.sarr === null || run.sarr === undefined
    ? null : `${run.sarr.toFixed(3)} (rank ${run.sarr_rank ?? DASH})`));
  facts.append(fact('FIGURE', run.figure_display));
  left.append(facts);
  box.append(left);

  const q = el('div', 'quality');
  const capRow = el('div', 'cap-row');
  capRow.append(el('span', 'cap', 'RACE QUALITY · TOP 5 AND WHAT THEY DID NEXT'));
  const finishers = state.quality[`${run.race_date}:${run.race_no}`];
  if (finishers === undefined) {
    capRow.append(el('span', 'badge', 'LOADING'));
    q.append(capRow);
  } else {
    // "Won a race" becomes "won a race whose form held up". A horse that has
    // not run since is not a poor next run and must not count as one.
    const ran = finishers.filter((f) => f.next_place !== null);
    const good = ran.filter((f) => f.next_place <= 3).length;
    const verdict = !ran.length ? 'NOT YET TESTED'
      : good / ran.length >= 0.4 ? 'FORM HELD' : 'MODEST';
    const badge = el('span',
      `badge ${verdict === 'FORM HELD' ? 'held' : verdict === 'MODEST' ? 'modest' : ''}`,
      verdict);
    badge.title = `${good} of ${ran.length} top-five finishers placed next start`
      + (ran.length < finishers.length
        ? ` · ${finishers.length - ran.length} have not run since` : '');
    capRow.append(badge);
    q.append(capRow);

    finishers.forEach((f) => {
      const line = el('div', 'fin');
      line.append(el('span', 'p', String(f.place)));
      line.append(el('span', 'nm', f.horse_name));
      line.append(el('span', 'arrow', '→'));
      const unrun = f.next_place === null;
      const cls = unrun ? 'unrun' : f.next_place <= 3 ? 'good' : 'poor';
      line.append(el('span', `next ${cls}`,
        unrun ? DASH : String(f.next_place ?? f.next_place_code ?? DASH)));
      line.append(el('span', 'when', unrun ? 'not run' : f.next_date.slice(5)));
      const bar = el('div', 'bar');
      const fill = el('i', unrun ? '' : cls);
      fill.style.width = unrun ? '0%'
        : `${Math.max(10, 100 - (f.next_place - 1) * 12)}%`;
      bar.append(fill);
      line.append(bar);
      q.append(line);
    });
  }
  box.append(q);
  return box;
}

function detailBlock(runner) {
  const box = el('div', 'horse-detail');
  box.append(trialBand(runner));
  const h2h = h2hBand(runner);
  if (h2h) box.append(h2h);

  const head = el('div', 'run-head');
  // Two of these labels are longer than the column beneath them can be
  // without taking width from something that must not lose it. They clip, and
  // carry themselves whole on the tooltip.
  RUN_HEAD.forEach(([label, cls]) => {
    const cell = el('div', cls || null, label);
    cell.title = label;
    head.append(cell);
  });
  box.append(head);

  const runs = history(runner);
  if (!runs.length) {
    box.append(el('div', 'trial-band', '').appendChild(
      el('div', 'empty', 'NO PRIOR RUNS ON RECORD')).parentElement);
    return box;
  }
  runs.forEach((run, i) => {
    box.append(runRow(runner, run, i));
    if (state.openRuns.has(runKey(runner.horse_no, run))) {
      box.append(runDetail(runner, run));
    }
  });
  return box;
}

/* ── condition-fit aside ─────────────────────────────────────────────────── */

function renderAside() {
  const host = $('fit-aside');
  const runner = state.focus;
  if (!runner) {
    host.replaceChildren(el('div', 'fit-sub', 'HOVER A RUNNER'));
    return;
  }
  const bb = state.guide?.blackbook?.[runner.horse_name];
  const hd = el('div', 'fit-hd');
  hd.append(el('span', 'no', String(runner.horse_no)));
  hd.append(el('span', `nm${bb ? ' booked' : ''}`, runner.horse_name));
  hd.append(el('span', 'odds', runner.win_odds ? num(runner.win_odds, 1) : DASH));

  const race = state.guide.race;
  const sub = el('div', 'fit-sub');
  sub.append(el('div', 'cap', 'HOW THIS HORSE FITS TODAY'));
  sub.append(el('div', 'cond',
    [race.venue, race.distance ? `${race.distance}m` : null,
     classLabel(race.race_class), race.going,
     race.course ? `COURSE ${race.course}` : null,
     runner.draw == null ? null : `DRAW ${runner.draw}`]
      .filter(Boolean).join(' · ')));

  const parts = [hd, sub];
  const cells = state.fitFor === runner.horse_name ? state.fit : null;
  if (!cells) {
    parts.push(el('div', 'fit-sub', 'LOADING'));
  } else if (!cells.length) {
    parts.push(el('div', 'fit-sub', 'NO CONDITION SLICES FOR TODAY'));
  } else {
    cells.forEach((c) => {
      const cell = el('div', `fit-cell${c.is_thin ? ' thin' : ''}`);
      const line = el('div', 'line');
      line.append(el('span', 'label', c.label));
      line.append(el('span', 'n', `n=${c.starts}`));
      line.append(el('span', 'w', `${c.wins}W`));
      line.append(el('span', 'p', `${c.places}P`));
      const figCls = c.avg_figure === null ? ''
        : c.avg_figure >= 100 ? 'above' : 'below';
      line.append(el('span', `fig ${figCls}`,
        c.avg_figure === null ? DASH : c.avg_figure.toFixed(1)));
      cell.append(line);

      // Wilson interval on the win rate. A wide bar IS the message on a small
      // sample — the point is that the cell cannot support a conclusion.
      const [lo, hi] = wilson(c.wins, c.starts);
      const meter = el('div', 'meter');
      const track = el('div', 'track');
      const ci = el('div', 'ci');
      ci.style.left = `${lo * 100}%`;
      ci.style.width = `${Math.max(1, (hi - lo) * 100)}%`;
      track.append(ci);
      const mark = el('div', 'mark');
      mark.style.left = `${(c.starts ? c.wins / c.starts : 0) * 100}%`;
      track.append(mark);
      meter.append(track);
      meter.append(el('span', 'note', c.starts
        ? `${(100 * c.wins / c.starts).toFixed(0)}% ±${(100 * (hi - lo) / 2).toFixed(0)}`
        : 'no starts'));
      cell.append(meter);
      parts.push(cell);
    });
  }

  parts.push(el('div', 'fit-foot',
    'WINS ARE THE HEADLINE; PLACE RATE SITS QUIETER FOR CONTEXT. THIN CELLS ARE '
    + 'DIMMED AND CARRY A WIDE INTERVAL. ACROSS 153 CONDITION CELLS, 8 CLEARED '
    + 'SIGNIFICANCE WHERE 7.0 WERE EXPECTED BY CHANCE — READ THIS PANEL AS FIT, '
    + 'NOT AS AN EDGE.'));
  host.replaceChildren(...parts);
}

/** Wilson score interval — it does not collapse to a point at 0/n the way the
 *  normal approximation does, which is exactly the case this panel is full of. */
function wilson(wins, n, z = 1.96) {
  if (!n) return [0, 1];
  const p = wins / n;
  const d = 1 + z * z / n;
  const centre = p + z * z / (2 * n);
  const spread = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n));
  return [Math.max(0, (centre - spread) / d), Math.min(1, (centre + spread) / d)];
}

/* ── popovers ────────────────────────────────────────────────────────────── */

function placePopover(event) {
  const pop = $('popover');
  pop.hidden = false;
  const r = event.currentTarget.getBoundingClientRect();
  const w = 344;
  pop.style.left = `${Math.min(r.left, window.innerWidth - w - 12)}px`;
  pop.style.top = `${Math.min(r.bottom + 4, window.innerHeight - 200)}px`;
}

function hidePopover() {
  $('popover').hidden = true;
  state.popover = null;
}

function showBlackbookNote(event, bb) {
  const pop = $('popover');
  const hd = el('div', 'hd');
  hd.append(document.createTextNode('HORSE NOTE · WHY BLACKBOOKED'));
  hd.append(el('span', 'meta', `ADDED ${bb.added_date}`));
  pop.replaceChildren(hd);
  pop.append(el('div', 'body', bb.reasoning || 'no reason recorded'));
  pop.append(el('div', 'trace', [
    bb.source_race ? `TRIGGERED BY ${bb.source_race}` : null,
    (bb.tags ?? []).join(' · ').toUpperCase() || null,
    `${bb.status.toUpperCase()} · ${bb.confidence?.toUpperCase() ?? ''}`,
  ].filter(Boolean).join(' — ')));
  placePopover(event);
}

function showNote(event, runner, run) {
  // One form, one module — the Results page calls the same one. Its artboard
  // asks for exactly that: "same form as the Form Guide — reviewing and
  // booking is one action". See review.js.
  renderReview($('popover'), {
    horseName: runner.horse_name,
    run,
    existingNote: noteFor(runner.horse_name, run),
    booked: state.guide?.blackbook?.[runner.horse_name],
    onSaved: (saved) => {
      const list = (state.notes[runner.horse_name] ?? []).filter(
        (n) => !(n.race_date === run.race_date && n.race_no === run.race_no));
      state.notes[runner.horse_name] = [saved, ...list];
      hidePopover();
      render();
    },
    onPromoted: (entry) => {
      state.guide.blackbook[runner.horse_name] = entry;
      hidePopover();
      render();
    },
    onClose: hidePopover,
  });
  placePopover(event);
}


/* ── render ──────────────────────────────────────────────────────────────── */

function renderFoot() {
  const foot = $('fg-foot');
  foot.replaceChildren();
  const add = (text, cls) => foot.append(el('span', cls ?? null, text));
  add('CLICK A COLUMN TO SORT · CLICK A HORSE FOR ITS SIX RUNS · CLICK A RUN FOR SECTIONALS');
  add('■ BLACKBOOK', 'book');
  add('TRIP TROUBLE', 'trip');
  add('■ TRAINER CHANGE', 'violet');
  add('WEIGHT SWING', 'swing');
  add('✎ RUN NOTE', 'book');
  add('TIMES m:ss.xx · SPLITS ss.xx');
  add('FIGURE 100 = PAR', 'right');
}

function render() {
  renderStrip();
  renderRaceHeader();
  renderHead();

  const host = $('runners');
  if (!state.guide) {
    host.replaceChildren(el('div', 'fit-sub', 'LOADING'));
    renderAside();
    return;
  }
  const rows = [];
  sortedRunners().forEach((r) => {
    rows.push(horseRow(r));
    if (state.open.has(r.horse_no)) rows.push(detailBlock(r));
  });
  host.replaceChildren(...(rows.length ? rows : [el('div', 'fit-sub', 'NO RUNNERS')]));
  renderAside();
  renderFoot();
}

/* ── interaction ─────────────────────────────────────────────────────────── */

function toggleHorse(no) {
  if (state.open.has(no)) state.open.delete(no);
  else {
    state.open.add(no);
    const runner = state.guide.race.runners.find((r) => r.horse_no === no);
    if (runner) loadH2H(runner);
  }
  render();
}

async function toggleRun(key, runner, run) {
  if (state.openRuns.has(key)) {
    state.openRuns.delete(key);
    render();
    return;
  }
  state.openRuns.add(key);
  render();
  const qk = `${run.race_date}:${run.race_no}`;
  if (state.quality[qk] === undefined) {
    try {
      const body = await api.raceQuality(run.race_date, run.race_no);
      state.quality[qk] = body.finishers;
    } catch { state.quality[qk] = []; }
    render();
  }
}

let fitToken = 0;

async function focusHorse(runner) {
  if (state.focus?.horse_name === runner.horse_name) return;
  state.focus = runner;
  renderAside();
  const race = state.guide.race;
  const token = ++fitToken;
  try {
    const q = [
      race.distance ? `distance=${race.distance}` : null,
      race.course ? `course=${encodeURIComponent(race.course)}` : null,
      race.going ? `going=${encodeURIComponent(race.going)}` : null,
      race.surface ? `surface=${encodeURIComponent(race.surface)}` : null,
      `before=${race.race_date}`,
    ].filter(Boolean).join('&');
    const body = await api.conditionFit(runner.horse_name, `?${q}`);
    // A slower earlier hover must not overwrite the panel the user is on now.
    if (token !== fitToken) return;
    state.fit = body.cells;
    state.fitFor = runner.horse_name;
  } catch {
    if (token !== fitToken) return;
    state.fit = [];
    state.fitFor = runner.horse_name;
  }
  renderAside();
}

/** Head to head against the rest of TODAY's field. One call per opponent, so
 *  it runs only when a horse is actually expanded. */
async function loadH2H(runner) {
  if (state.h2h[runner.horse_name]) return;
  const race = state.guide.race;
  const others = race.runners.filter((r) => r.horse_name !== runner.horse_name);
  const found = [];
  await Promise.all(others.map(async (other) => {
    try {
      const body = await api.headToHead(runner.horse_name, other.horse_name,
                                        race.race_date);
      if (!body.meetings.length) return;
      const last = body.meetings[0];
      // The swing is the gap BETWEEN the pair, not each horse's own weight --
      // both going up 5lb have not changed relative to one another.
      const gapNow = (runner.actual_weight !== null && other.actual_weight !== null)
        ? runner.actual_weight - other.actual_weight : null;
      const gapThen = body.last_weight_gap;
      // WHO the change favours. The gap is THIS horse minus the other, so a
      // fall means this horse carries less relative to it than last time and
      // is the one better off. Working that out from two signed numbers is
      // the step that was going wrong.
      const move = (gapThen === null || gapNow === null)
        ? null : gapNow - gapThen;
      found.push({
        other_no: other.horse_no,
        other: `${other.horse_no} ${other.horse_name}`,
        record: `${body.record.a}-${body.record.b}`,
        meetings: body.meetings.length,
        last_date: last.race_date,
        last_cond: `${last.distance ?? DASH}m ${last.going ?? ''}`.trim(),
        last_line: `${ordinal(last.pa)}${last.wa ? ` (${last.wa})` : ''}`
          + ` · ${ordinal(last.pb)}${last.wb ? ` (${last.wb})` : ''}`,
        gap_then: gapThen === null ? null : signed(gapThen, 0),
        gap_now: gapNow === null ? null : signed(gapNow, 0),
        swing: move === null ? null : Math.abs(move),
        // `mine` when this horse came down relative to the other.
        favours: move === null || move === 0 ? null
          : (move < 0 ? 'mine' : 'theirs'),
        favours_name: move === null || move === 0 ? null
          : (move < 0 ? runner.horse_name : other.horse_name),
        favours_lb: move === null ? null : Math.abs(move),
        gate_then: last.da, gate_now: runner.draw,
        other_gate_then: last.db, other_gate_now: other.draw,
      });
    } catch { /* a pair with no shared history is the norm, not an error */ }
  }));
  state.h2h[runner.horse_name] = found.sort(
    (a, b) => (b.last_date ?? '').localeCompare(a.last_date ?? ''));
  render();
}

function selectRace(no) {
  context.setRace(no);
}

async function loadRace() {
  state.open.clear();
  state.openRuns.clear();
  state.h2h = {};
  state.trials = {};
  state.focus = null;
  state.fit = null;
  state.fitFor = null;
  hidePopover();
  try {
    const [guide, pace] = await Promise.all([
      api.formGuide(state.date, state.race, 6),
      api.racePace(state.date, state.race).catch(() => null),
    ]);
    state.guide = guide;
    state.pace = pace;
  } catch (e) {
    state.guide = null;
    $('runners').replaceChildren(el('div', 'fit-sub', `failed to load: ${e.message}`));
    return;
  }
  render();

  // Blackbook and notes are per-card and load after the grid is on screen, so
  // a slow lookup never delays the form itself.
  const names = state.guide.race.runners.map((r) => r.horse_name);
  const [book, notes, trials] = await Promise.all([
    api.meetingBlackbook(state.date).catch(() => null),
    api.notes(names).catch(() => ({ notes: {} })),
    // `before` the race being reviewed: a trial run after it was not available
    // when it was run, and showing it would let hindsight into a form guide.
    api.trialsForHorses(names, state.date).catch(() => ({ trials: {} })),
  ]);
  state.guide.blackbook = {};
  (book?.entries ?? [])
    .filter((e) => e.race_no === state.race)
    .forEach((e) => { state.guide.blackbook[e.horse_name] = e; });
  state.notes = notes.notes ?? {};
  state.trials = trials.trials ?? {};
  render();
}

/* Layer 1 owns the meeting; the page reacts to it. */
async function onContext(_ctx, what) {
  state.date = context.date;
  state.races = context.races;
  state.race = context.race;
  if (what === 'date') {
    state.guide = null;
    render();
    return;
  }
  await loadRace();
}

function onKey(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA'
      || e.target.tagName === 'SELECT') return;
  if (e.key === 'Escape') { hidePopover(); return; }
  if (!/^[1-9]$/.test(e.key)) return;
  const no = Number(e.key);
  if (state.races.some((r) => r.race_no === no)) selectRace(no);
}

async function init() {
  renderNav($('nav'), 'form-guide.html');
  installPalette();
  $('expand-all').addEventListener('click', () => {
    (state.guide?.race?.runners ?? []).forEach((r) => {
      state.open.add(r.horse_no);
      loadH2H(r);
    });
    render();
  });
  $('collapse-all').addEventListener('click', () => {
    state.open.clear();
    state.openRuns.clear();
    render();
  });
  document.addEventListener('keydown', onKey);

  await loadTags();
  context.onChange(onContext);
  await context.init();
  await onContext(context, 'meeting');
}

init();

/* Results — ported from web/design-source/Results.dc.html.
 *
 * What actually happened: the finishing order, the sectionals, the dividends,
 * the stewards' report, whether the money on the race came back, and which
 * booked horses ran.
 *
 * Two things on this page are claims rather than records, and both are drawn
 * as claims. The race-quality verdict is provisional by construction — design
 * brief 04: "Whether the first five go on to win is what settles this — those
 * runs have not happened yet. The verdict above is provisional and will be
 * revised, not backfilled silently." And an all-up passing through this race
 * is money at risk on this result whose settlement belongs to the whole
 * ticket, so it is shown and marked rather than summed into a race P/L that
 * would claim money was won or lost here that was not.
 *
 * A race that has not been run is not a race in which nobody finished. The
 * page says which, rather than rendering an empty grid that reads as the
 * second.
 */
import { api, num } from './api.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';
import { loadTags, renderReview } from './review.js';

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

const COLS = [
  ['FIN', 'r'], ['NO', 'r'], ['HORSE', ''], ['JOCKEY', ''], ['DR', 'r'],
  ['TRAINER', ''], ['STYLE', ''], ['WT', 'r'], ['MARGIN', 'r'],
  ['WIN SP', 'r'], ['PLC SP', 'r'], ['ET FIGURE', 'r'], ['POSITIONS', ''],
];

const state = {
  meeting: null, result: null, loading: false,
  open: new Set(), notes: {},
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderNav() {
  $('nav').replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === 'results.html') a.setAttribute('aria-current', 'page');
    return a;
  }));
}

function money(v, { sign = false } = {}) {
  if (v === null || v === undefined) return DASH;
  const n = Math.round(Math.abs(v)).toLocaleString();
  return sign ? `${v >= 0 ? '+' : MINUS}$${n}` : `$${n}`;
}

function renderPicker() {
  const m = state.meeting;
  const host = $('race-picker');
  if (!m) { host.replaceChildren(); return; }
  host.replaceChildren(...m.races.map((r) => {
    const b = el('button', r.run ? null : 'pending', String(r.race_no));
    b.setAttribute('aria-pressed', String(r.race_no === context.race));
    // A race not yet run is dimmed and still clickable: the page has something
    // to say about it, which is that it has not been run.
    b.title = r.run ? `R${r.race_no} · ${r.distance}m · Class ${r.race_class}`
      : `R${r.race_no} has not been run`;
    b.addEventListener('click', () => context.setRace(r.race_no));
    return b;
  }));
  $('re-sum').replaceChildren();
  const stat = (value, label) => {
    const box = el('span');
    box.append(el('b', null, value));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  $('re-sum').append(stat(`${m.run} OF ${m.total}`, 'RUN'));
  const money_ = state.result?.money;
  if (money_ && money_.bets) {
    $('re-sum').append(stat(String(money_.bets), 'BETS ON THIS RACE'));
    $('re-sum').append(stat(money(money_.pnl, { sign: true }), 'RACE P/L'));
  }
}

function renderRaceLine() {
  const r = state.result;
  const host = $('race-line');
  host.replaceChildren();
  if (!r) return;
  const race = r.race;
  const bit = (k, v, cls) => {
    host.append(el('span', 'k', k));
    host.append(el('span', cls ?? 'v', v ?? DASH));
  };
  host.append(el('span', 'big', `R${race.race_no}`));
  bit('DIST', race.distance ? `${race.distance}m` : null);
  bit('CLASS', race.race_class);
  bit('GOING', race.going);
  bit('COURSE', `${race.venue ?? ''} ${race.course ?? ''}`.trim());
  bit('FIELD', String(race.field_size ?? DASH));
  if (r.run) {
    bit('WIN TIME', r.winning_time_display, 'big');
    if (r.pace && r.pace.band) {
      bit('RACE PACE', `${r.pace.band}${r.pace.confident ? '' : ' (thin)'}`);
    }
  }
  host.append(el('span', 'right',
    r.run ? 'every figure on this page is computed once, in query/'
      : 'not run'));
}

/* ── the finishing order ─────────────────────────────────────────────────── */

/* ── one runner, expanded ────────────────────────────────────────────────── */

/** Sectionals with the position held at each, which is the pair that says
 *  where a race was won. A time without a position is a clock reading; a
 *  position without a time is a shape.
 *
 *  Where derive/sectionals has decomposed the race, each cell also carries how
 *  the section compared to the field and how many places changed hands over
 *  it — the two together, because a fastest closing section from last that
 *  gained nothing is not a run at the race, and the rank alone implies it was.
 */
function sectionalBand(runner) {
  const decomposed = state.result?.sectionals?.by_horse?.[String(runner.horse_no)];
  const times = runner.section_times ?? [];
  const positions = runner.running_positions ?? [];
  if (!times.length && !positions.length) return null;

  const band = el('div', 'sect-band');
  const cap = el('span', 'k');
  cap.append(document.createTextNode('SECTIONALS · POSITION AT EACH'));
  if (decomposed) {
    cap.append(el('span', 'sub',
      ` · RANK IN FIELD · PLACES GAINED · ${DASH}0.25s/400m OR BETTER IS MARKED`));
  }
  band.append(cap);

  const strip = el('div', 'sect');
  const count = Math.max(times.length, positions.length,
    decomposed?.sections?.length ?? 0);
  for (let i = 0; i < count; i += 1) {
    const s = decomposed?.sections?.[i];
    const cell = el('div', `seg${s?.notable ? ' notable' : ''}`);
    cell.append(el('span', 't',
      times[i] === undefined ? DASH : num(times[i], 2)));
    cell.append(el('span', 'p',
      positions[i] === undefined ? DASH : `P${positions[i]}`));
    if (s) {
      const meta = el('span', 'm');
      meta.append(el('span', 'rk', s.rank === null ? DASH : `#${s.rank}`));
      // No places on the opening section: every horse starts level, so there
      // is no earlier position to have gained on.
      if (s.places_gained !== null && s.places_gained !== undefined) {
        meta.append(el('span',
          s.places_gained > 0 ? 'up' : s.places_gained < 0 ? 'down' : null,
          `${s.places_gained > 0 ? '+' : ''}${s.places_gained}`));
      }
      cell.append(meta);
      cell.title = `${s.length_m}m in ${num(s.seconds, 2)}s — `
        + `${num(s.per_400, 2)}s per 400m, `
        + `${s.dev === null ? 'no field median' : `${s.dev > 0 ? '+' : ''}`
          + `${num(s.dev, 2)}s vs the field`}`
        + `, rank ${s.rank ?? DASH} of the field`;
    }
    strip.append(cell);
  }
  band.append(strip);

  if (decomposed?.read) {
    band.append(el('div', 'sect-read', decomposed.read));
  }
  return band;
}

function factRow(label, value, title) {
  const row = el('div', 'fact');
  row.append(el('span', 'k', label));
  const v = el('span', 'v', value ?? DASH);
  if (title) v.title = title;
  row.append(v);
  return row;
}

function detailBlock(runner, stewardsFor, booked) {
  const box = el('div', 'result-detail');
  const band = sectionalBand(runner);
  if (band) box.append(band);

  const facts = el('div', 'facts');
  facts.append(factRow('GEAR', runner.gear || null));
  facts.append(factRow('DRAW', runner.draw ? String(runner.draw) : null));
  facts.append(factRow('WEIGHT',
    runner.actual_weight ? `${runner.actual_weight} lb` : null));
  // Lane comes from the Comments on Running page, and only about a quarter of
  // comments carry a descriptor at all — so it is absent, not zero, when HKJC
  // did not say.
  facts.append(factRow('LANE', (runner.lane_notes ?? []).join(' · ') || null,
    'from HKJC Comments on Running — absent where none was published'));
  facts.append(factRow('FIGURE', runner.figure_display || null));
  facts.append(factRow('SARR', runner.sarr === null || runner.sarr === undefined
    ? null : `${num(runner.sarr, 1)}${runner.sarr_rank ? ` · rank ${runner.sarr_rank}` : ''}`));
  facts.append(factRow('TAGS', (runner.tags ?? []).join(' · ') || null));
  box.append(facts);

  if (stewardsFor.length) {
    const stew = el('div', 'run-stewards');
    stew.append(el('span', 'k', 'STEWARDS'));
    stewardsFor.forEach((s) => stew.append(el('div', 'text', s.comment_text)));
    box.append(stew);
  }

  const actions = el('div', 'run-actions');
  const note = el('button', 'act',
    booked ? '■ IN THE BLACKBOOK · NOTE THIS RUN' : 'RUN NOTE · ADD TO BLACKBOOK');
  note.addEventListener('click', (e) => openReview(e, runner, booked));
  actions.append(note);
  actions.append(el('span', 'hint',
    'same form as the Form Guide — reviewing and booking is one action'));
  box.append(actions);
  return box;
}

function hidePopover() {
  $('popover').hidden = true;
}

function openReview(event, runner, booked) {
  const race = state.result.race;
  renderReview($('popover'), {
    horseName: runner.horse_name,
    run: {
      race_date: race.race_date, race_no: race.race_no, venue: race.venue,
      course: race.course, distance: race.distance, going: race.going,
      race_class: race.race_class,
    },
    existingNote: (state.notes[runner.horse_name] ?? []).find(
      (n) => n.race_date === race.race_date && n.race_no === race.race_no),
    booked: booked ? { added_date: booked.added_date,
                       tags: (booked.tags ?? '').split(',').filter(Boolean) }
      : null,
    onSaved: (saved) => {
      const list = (state.notes[runner.horse_name] ?? []).filter(
        (n) => !(n.race_date === saved.race_date && n.race_no === saved.race_no));
      state.notes[runner.horse_name] = [saved, ...list];
      hidePopover();
      render();
    },
    onPromoted: () => {
      hidePopover();
      // Re-read rather than patching local state: the booked panel is a join,
      // and a horse promoted now changes what that join returns.
      loadRace();
    },
    onClose: hidePopover,
  });
  const pop = $('popover');
  pop.hidden = false;
  const r = event.currentTarget.getBoundingClientRect();
  pop.style.left = `${Math.min(r.left, window.innerWidth - 356)}px`;
  pop.style.top = `${Math.min(r.bottom + 4, window.innerHeight - 220)}px`;
}

function resultRow(runner, booked, backed) {
  const row = el('div', 'res-row');
  const placed = runner.place !== null
    && runner.place <= (runner.field_size >= 7 ? 3 : 2);
  const fin = el('div', 'fin',
    runner.place_display ?? runner.place_code ?? runner.place ?? DASH);
  if (runner.place === 1) fin.classList.add('win');
  else if (placed) fin.classList.add('plc');
  row.append(fin);
  row.append(el('div', 'r', String(runner.horse_no)));

  const horse = el('div', 'horse');
  horse.append(document.createTextNode(runner.horse_name));
  if (booked) {
    const chip = el('span', 'bb', 'BB');
    chip.title = `in the blackbook since ${booked.added_date}`
      + (booked.tags ? ` — ${booked.tags}` : '');
    horse.append(chip);
  }
  // Money was on this horse. A join, not something anyone had to log.
  if (backed) horse.append(el('span', 'backed', '$'));
  horse.title = runner.horse_name;
  row.append(horse);

  row.append(el('div', 'jockey', runner.jockey ?? DASH));
  row.append(el('div', 'r', runner.draw ? String(runner.draw) : DASH));
  row.append(el('div', 'trainer', runner.trainer ?? DASH));
  row.append(el('div', null, runner.pace_style ?? DASH));
  row.append(el('div', 'r',
    runner.actual_weight ? String(runner.actual_weight) : DASH));
  row.append(el('div', 'r',
    runner.lengths_behind === null || runner.lengths_behind === undefined
      ? DASH : num(runner.lengths_behind, 2)));
  row.append(el('div', 'r', runner.win_odds ? num(runner.win_odds, 1) : DASH));
  row.append(el('div', 'r', runner.place_odds ? num(runner.place_odds, 1) : DASH));

  const fig = el('div', 'fig',
    runner.et_figure === null || runner.et_figure === undefined
      ? DASH : num(runner.et_figure, 1));
  // The figure never renders bare: what it is against, and on what sample.
  if (runner.figure_display) fig.title = runner.figure_display;
  row.append(fig);

  const pos = el('div', 'pos', (runner.running_positions ?? []).join(' ') || DASH);
  if (runner.section_times && runner.section_times.length) {
    pos.title = `sectionals ${runner.section_times.map((t) => num(t, 2)).join(' · ')}`;
  }
  row.append(pos);
  row.addEventListener('click', () => {
    if (state.open.has(runner.horse_no)) state.open.delete(runner.horse_no);
    else state.open.add(runner.horse_no);
    render();
  });
  if (state.notes[runner.horse_name]?.length) {
    horse.append(el('span', 'noted', '✎'));
  }
  return row;
}

function renderResult() {
  const r = state.result;
  const host = $('res-rows');
  const head = $('res-head');
  if (!r) {
    head.replaceChildren();
    host.replaceChildren(el('div', 'empty-line', 'LOADING'));
    return;
  }
  if (!r.run) {
    head.replaceChildren();
    const box = el('div', 'not-run');
    box.append(document.createTextNode(
      `RACE ${r.race.race_no} HAS NOT BEEN RUN.`));
    box.append(el('span', 'sub', 'Nothing to review yet — this is not a race '
      + 'in which nobody finished, and the page will not draw it as one.'));
    host.replaceChildren(box);
    return;
  }
  head.replaceChildren(...COLS.map(([label, cls]) => el('div', cls || null, label)));

  const booked = new Map((r.booked ?? []).map((b) => [b.horse_name, b]));
  const backed = new Set((r.booked ?? [])
    .filter((b) => b.backed).map((b) => b.horse_name));
  // Finishing order, which is what a result IS. get_race returns the card in
  // horse-number order because that is the order a card is read before the
  // race; after it, the number is the least interesting thing on the row.
  // Non-finishers sort last, keeping their own order.
  const order = [...r.race.runners].sort((a, b) => {
    if (a.place === b.place) return a.horse_no - b.horse_no;
    if (a.place === null || a.place === undefined) return 1;
    if (b.place === null || b.place === undefined) return -1;
    return a.place - b.place;
  });
  const byHorse = new Map();
  (r.stewards ?? []).forEach((s) => {
    if (!byHorse.has(s.horse_no)) byHorse.set(s.horse_no, []);
    byHorse.get(s.horse_no).push(s);
  });
  host.replaceChildren();
  order.forEach((x) => {
    host.append(resultRow(x, booked.get(x.horse_name), backed.has(x.horse_name)));
    if (state.open.has(x.horse_no)) {
      host.append(detailBlock(x, byHorse.get(x.horse_no) ?? [],
                              booked.get(x.horse_name)));
    }
  });
}

/* ── panels ──────────────────────────────────────────────────────────────── */

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

function dividendsPanel(rows) {
  const { box, body } = panel('DIVIDENDS PAID', 'PER $10 UNIT');
  if (!rows.length) {
    body.append(el('div', 'empty-line', 'NO DIVIDEND RECORDED FOR THIS RACE'));
    return box;
  }
  rows.forEach((d) => {
    const row = el('div', 'div-row');
    row.append(el('span', 'pool', d.pool));
    row.append(el('span', 'combo', d.combination));
    // Shown per $10, as HKJC publishes them: the number on the page should be
    // the number on the ticket.
    row.append(el('span', `amt${(d.dividend_per_10 ?? 0) >= 500 ? ' big' : ''}`,
      d.dividend_per_10 === null ? DASH : `$${num(d.dividend_per_10, 1)}`));
    body.append(row);
  });
  return box;
}

function moneyPanel(m) {
  const { box, body } = panel('DID MY BETS HIT',
    m && m.bets ? `${m.bets} TICKET${m.bets === 1 ? '' : 'S'}` : '');
  if (!m || !m.bets) {
    body.append(el('div', 'empty-line', 'NO BETS LOGGED ON THIS RACE'));
    return box;
  }
  m.tickets.forEach((t) => {
    const row = el('div', `bet-row${t.spans_races ? ' spanning' : ''}`);
    row.append(el('span', 'type', t.bet_type.replace('_BANKER', '·B')));
    const sel = el('span', 'sel', t.selections
      .map((s) => `${s.horse_name ?? s.horse_no}${s.place ? `(${s.place})` : ''}`)
      .join(' · '));
    sel.title = sel.textContent;
    row.append(sel);
    row.append(el('span', 'r', money(t.stake)));
    row.append(el('span', 'r', money(t.returned)));
    if (t.spans_races) {
      // Its stake rides on other races too, so its P/L is not this race's to
      // claim.
      const mark = el('span', 'r spans', 'ALL-UP');
      mark.title = 'this ticket spans other races — its settlement belongs to '
        + 'the whole ticket, so it is not in the race P/L';
      row.append(mark);
    } else {
      row.append(el('span', `r pnl ${(t.pnl ?? 0) >= 0 ? 'pos' : 'neg'}`,
        money(t.pnl, { sign: true })));
    }
    body.append(row);
  });
  const foot = el('div', 'caveat',
    `Race P/L ${money(m.pnl, { sign: true })} on ${money(m.staked)} staked`
    + (m.spanning
      ? `. ${m.spanning} all-up ticket(s) pass through this race and are not `
        + 'counted in it — their stake rides on other races too.'
      : '.'));
  body.append(foot);
  return box;
}

function bookedPanel(rows) {
  const { box, body } = panel('BLACKBOOK HORSES THAT RAN',
    rows.length ? `${rows.length} OF THE BOOK` : '');
  if (!rows.length) {
    body.append(el('div', 'empty-line', 'NONE OF THE BOOK WAS ENGAGED HERE'));
    return box;
  }
  let missed = 0;
  rows.forEach((b) => {
    const backed = Boolean(b.backed);
    if (!backed) missed += 1;
    const row = el('div', `book-row${backed ? '' : ' missed'}`);
    row.append(el('span', null, String(b.horse_no ?? DASH)));
    const name = el('span', 'name', b.horse_name);
    name.title = b.reasoning ?? '';
    row.append(name);
    const fin = el('span', `fin${b.place === 1 ? ' win' : ''}`,
      b.place === null ? DASH : String(b.place));
    row.append(fin);
    row.append(el('span', backed ? 'backed' : 'missed-mark',
      backed ? 'BACKED' : 'NOT BACKED'));
    row.append(el('span', 'tags', b.tags ?? ''));
    body.append(row);
  });
  if (missed) {
    // Design brief 06 calls this the single most important feature: the system
    // detects a non-bet from the join, so nobody has to remember to log one.
    body.append(el('div', 'caveat',
      `${missed} booked horse${missed === 1 ? '' : 's'} ran here with no money `
      + `on ${missed === 1 ? 'it' : 'them'}. That is found by a join, not by `
      + 'anyone remembering to record an absence.'));
  }
  return box;
}

function qualityPanel(q) {
  const { box, body } = panel('RACE QUALITY',
    'WHAT THE FIRST FIVE DID NEXT', { wide: true });
  if (!q || !q.runners.length) {
    body.append(el('div', 'empty-line', 'NO RETROSPECTIVE YET'));
    return box;
  }
  q.runners.forEach((r) => {
    const row = el('div', 'q-row');
    row.append(el('span', 'p', String(r.place ?? DASH)));
    row.append(el('span', 'name', r.horse_name));
    const next = el('span', 'next');
    if (!r.next_start) {
      next.append(el('span', 'none', 'has not run since'));
    } else {
      const n = r.next_start;
      next.append(el('span', n.place === 1 ? 'won' : null,
        `${n.race_date} — finished ${n.place}`));
      if (n.field_size) next.append(document.createTextNode(` of ${n.field_size}`));
    }
    row.append(next);
    body.append(row);
  });
  if (q.provisional) {
    body.append(el('div', 'provisional', 'PROVISIONAL'));
  }
  body.append(el('div', 'caveat', q.note));
  return box;
}

function stewardsPanel(rows) {
  const { box, body } = panel("STEWARDS' REPORT",
    rows.length ? `${rows.length} ENTRIES` : '', { wide: true });
  if (!rows.length) {
    body.append(el('div', 'empty-line', 'NO REPORT RECORDED FOR THIS RACE'));
    return box;
  }
  rows.forEach((s) => {
    const row = el('div', 'stew-row');
    const who = el('div', 'who');
    who.append(el('span', null, String(s.horse_no)));
    who.append(el('span', 'name', s.horse_name ?? ''));
    if (s.place) who.append(el('span', null, `finished ${s.place}`));
    who.append(el('span', null, s.source ?? ''));
    row.append(who);
    // Kept as prose. The trip tags derived from it live on the runner already;
    // deriving them again here would give the page two versions of one
    // judgement.
    row.append(el('div', 'text', s.comment_text));
    body.append(row);
  });
  return box;
}

function renderPanels() {
  const r = state.result;
  const host = $('panels');
  host.replaceChildren();
  if (!r || !r.run) return;
  host.append(dividendsPanel(r.dividends));
  host.append(moneyPanel(r.money));
  host.append(bookedPanel(r.booked));
  host.append(qualityPanel(r.quality));
  host.append(stewardsPanel(r.stewards));
}

/* ── loading ─────────────────────────────────────────────────────────────── */

function render() {
  renderPicker();
  renderRaceLine();
  renderResult();
  renderPanels();
}

async function loadRace() {
  if (!context.date || !context.race) return;
  state.loading = true;
  try {
      state.result = await api.raceResult(context.date, context.race);
    // Notes need the names, so they follow rather than run alongside. The
    // grid is already on screen by then — a slow lookup never delays it.
    const names = state.result.race.runners.map((x) => x.horse_name);
    render();
    const notes = await api.notes(names).catch(() => ({ notes: {} }));
    state.notes = notes.notes ?? {};
  } catch (e) {
    state.result = null;
    $('res-rows').replaceChildren(
      el('div', 'no-match', `FAILED TO LOAD — ${e.message}`));
    return;
  } finally {
    state.loading = false;
  }
  render();
}

async function loadMeeting() {
  state.meeting = await api.meetingResults(context.date).catch(() => null);
  render();
}

/* Layer 1 owns the meeting; the page reacts to it. */
async function onContext(_ctx, what) {
  if (what === 'date') {
    state.result = null;
    state.open.clear();
    await loadMeeting();
  }
  await loadRace();
}

async function boot() {
  renderNav();
  installPalette();
  await loadTags();
  context.onChange(onContext);
  await context.init();
  await loadMeeting();
  await loadRace();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});

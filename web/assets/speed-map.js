/* Speed Map — where the field will be at the first call, before the off.
 *
 * A gate ladder: one row per runner, innermost gate at the top, because the
 * gate is the one thing on this page that is not a projection. Everything else
 * is read against it.
 *
 * TWO AXES THAT MUST NOT MERGE, and the page's whole shape follows from it:
 *
 *   the BAR   how quickly this horse habitually gets away — the ESZ trait,
 *             ranked inside this race. Nothing else. It is drawn in the amber
 *             edge hue.
 *   PROFILE   its habitual running style, as a chip in the --style-* hues.
 *   PROJ      where it is projected to SETTLE, which folds the bar together
 *             with the gate and the style.
 *
 * A horse can be quick away and still settle midfield from gate 14, so a bar
 * that quietly included the draw would make the ladder unreadable — the gate is
 * already the y-axis. Design brief: pace and style are independent axes and
 * must not share a hue family.
 *
 * THE ESZ HERE IS THE TRAIT, NOT THE RUN. `vocab.js:eszCell` renders one run's
 * jump, standardised inside that race, and positive is faster there. This page
 * shows a horse's HABITUAL early speed across its history, where lower is
 * quicker — the race has not been run, so there is no jump to measure.
 * `tests/test_esz.py:test_it_is_not_the_sarr_component` exists to stop the two
 * being conflated, and calling eszCell here would do exactly that.
 */
import { api } from './api.js';
import { el, $, DASH, renderNav, styleBadge, drawText } from './vocab.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';

const BAND_TITLE = {
  LEAD: 'projected to lead or sit outside the leader',
  PACE: 'projected to race on the pace',
  MID: 'projected to settle midfield',
  BACK: 'projected to settle at the back',
};

const state = { date: null, race: 1, races: [], map: null, mae: null,
                error: null };


/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderStrip() {
  $('race-chips').replaceChildren(...state.races.map((r) => {
    const b = el('button', 'race-chip', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`;
    b.addEventListener('click', () => context.setRace(r.race_no));
    return b;
  }));
}

/** The error bar, stated in positions rather than on the normalised scale.
 *
 *  Recomputed per race because a 14-runner field and an 8-runner field are not
 *  the same number of positions, and quoting one number for both would be
 *  wrong in the direction that flatters the projection. */
function renderPrecision(race) {
  const n = race?.field_size ?? 0;
  const places = state.mae !== null && n > 1 ? state.mae * (n - 1) : null;
  $('precision').textContent = places === null
    ? 'A PROJECTION, NOT A PREDICTION.'
    : `A PROJECTION, NOT A PREDICTION — IT CARRIES ABOUT ± ${places.toFixed(1)} `
      + `POSITIONS OF ERROR IN THIS ${n}-RUNNER FIELD. IT SAYS WHERE THE FIELD `
      + 'WILL BE AT THE FIRST CALL, NOT WHO WINS.';
}


/* ── the ladder ──────────────────────────────────────────────────────────── */

/** How quickly it gets away, as a bar. `esz_rank` is 0 for the quickest in the
 *  race, so the bar is its complement: a long bar is a fast beginner. */
function awayBar(runner) {
  const wrap = el('div', 'away');
  if (runner.esz_rank === null || runner.esz_rank === undefined) {
    wrap.append(el('span', 'away-none', runner.reason ?? 'not projected'));
    return wrap;
  }
  const share = 1 - runner.esz_rank;
  const track = el('div', 'away-track');
  // Four steps by rank inside this race. `esz_rank` is 0 for the quickest, so
  // the top quarter of the field is `fast` and the bottom quarter `slow`.
  const band = share >= 0.75 ? 'fast'
    : share >= 0.5 ? 'quick'
      : share >= 0.25 ? 'steady' : 'slow';
  const fill = el('div', `away-fill ${band}`);
  fill.style.width = `${(share * 100).toFixed(1)}%`;
  track.append(fill);
  wrap.append(track);
  const pct = el('span', 'away-rank', `${Math.round(share * 100)}`);
  pct.title = `quicker away than ${Math.round(share * 100)}% of this field, `
            + 'across its own recent form';
  wrap.dataset.away = band;
  wrap.append(pct);
  return wrap;
}

function bandCell(runner) {
  if (!runner.settle_band) return el('span', 'band band-none', DASH);
  const cell = el('span', `band band-${runner.settle_band.toLowerCase()}`,
                  runner.settle_band);
  cell.title = BAND_TITLE[runner.settle_band] ?? '';
  return cell;
}

function ladderRow(runner) {
  const row = el('div', 'rung');
  if (runner.settle === null) row.classList.add('rung-unprojected');

  row.append(el('div', 'gate', drawText(runner.draw)));

  const who = el('div', 'who');
  who.append(el('span', 'no', String(runner.horse_no)));
  who.append(el('span', 'name', runner.horse_name));
  row.append(who);

  row.append(el('div', 'jockey', runner.jockey ?? DASH));

  const profile = el('div', 'profile');
  profile.append(styleBadge(runner.style));
  row.append(profile);

  row.append(awayBar(runner));
  const proj = el('div', 'proj');
  proj.append(bandCell(runner));
  row.append(proj);
  return row;
}

function renderLadder(race) {
  const host = $('ladder');
  if (!race || !race.runners.length) {
    host.replaceChildren(el('p', 'empty', state.error
      ? `Could not load the speed map: ${state.error}`
      : 'No projection stored for this card yet — '
        + 'jobs.project_card --pending writes it.'));
    return;
  }
  const head = el('div', 'rung rung-head');
  for (const [cls, label] of [['gate', 'GATE'], ['who', 'HORSE'],
                              ['jockey', 'JOCKEY'], ['profile', 'PROFILE'],
                              ['away', 'AWAY'], ['proj', 'PROJ']]) {
    head.append(el('div', cls, label));
  }
  host.replaceChildren(head, ...race.runners.map(ladderRow));
}

/** The runners the model will not place, with the reason for each.
 *
 *  `query/model.py:_unscored` is the pattern: name them, do not count them. A
 *  season opener is full of first-starters, and a page that silently showed 108
 *  of 114 would be reporting a complete card. */
function renderUnprojected(race) {
  const host = $('unprojected');
  const rows = race?.unprojected ?? [];
  host.hidden = rows.length === 0;
  if (!rows.length) return;
  const head = el('div', 'unproj-head',
    `${rows.length} RUNNER${rows.length === 1 ? '' : 'S'} NOT PROJECTED`);
  host.replaceChildren(head, ...rows.map((r) => {
    const line = el('div', 'unproj-row');
    line.append(el('span', 'gate', drawText(r.draw)));
    line.append(el('span', 'name', r.horse_name));
    line.append(el('span', 'why', r.reason ?? 'no projection'));
    return line;
  }));
}


/* ── loading ─────────────────────────────────────────────────────────────── */

async function load() {
  if (!state.date) return;
  state.error = null;
  try {
    const out = await api.speedMap(state.date);
    state.map = out;
    state.mae = out.mae ?? null;
  } catch (e) {
    // Say what went wrong. A silently empty ladder is indistinguishable from a
    // card nobody has projected yet, and those need different fixes.
    state.map = null;
    state.mae = null;
    state.error = e.message;
  }
  render();
}

function render() {
  renderStrip();
  const race = state.map?.races?.find((r) => r.race_no === state.race) ?? null;
  renderPrecision(race);
  renderLadder(race);
  renderUnprojected(race);
  $('strip-note').textContent = state.map?.derive_version
    ? state.map.derive_version.toUpperCase() : '';
}

async function onContext(ctx, what) {
  state.date = ctx.date;
  state.race = ctx.race;
  state.races = ctx.races ?? [];
  if (what === 'meeting' || !state.map || state.map.race_date !== state.date) {
    await load();
  } else {
    render();
  }
}

function onKey(e) {
  if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (!/^[1-9]$/.test(e.key)) return;
  context.setRace(Number(e.key));
}

async function init() {
  renderNav($('nav'), 'speed-map.html');
  installPalette();
  document.addEventListener('keydown', onKey);
  context.onChange(onContext);
  await context.init();
  await onContext(context, 'meeting');
}

init();

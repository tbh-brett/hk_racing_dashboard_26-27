/* Race shape — THE COURSE: one line of what the archive says of a race's course and trip (Speed Map).
 *
 * Brett (26 Oct) for the all-weather, then every course (30 Oct): which horses suit it. Every figure
 * is against the price (A/E, wins over the wins the price expected; 1.00 is the price exactly), over
 * every season since 2019 and then in its two halves, to 2022/23 and since: by running profile -- the
 * thirds of the field by where a horse usually races early, known before the race -- by the draw, by
 * body weight at the venue (its thirds, and where they start), and the margins. A figure the same side
 * of 1.00 in both halves has held; most don't. Measured 30 Oct, none of it improved on the price or
 * v2 when tried on later seasons, so it is context for reading a race, never a rating, and it moves
 * nothing: the simulation's own tables already carry each course's average trip.
 */
import { el } from './vocab.js';

const PLACE = { HV: 'HAPPY VALLEY', ST: 'SHA TIN' }, NAMED = { HV: 'Happy Valley', ST: 'Sha Tin' };
const x = (v) => (v == null ? '—' : `${v.toFixed(2)}×`);
const pc = (v) => (v == null ? '—' : `${Math.round(100 * v)}%`);
const span = (r, f) => (r ? `${f(r[0])}–${f(r[1])}` : '—');
const L = (v) => (v == null ? '—' : v.toFixed(1));
const lb = (v) => `${Math.round(v).toLocaleString()} lb`;
// a cell: all seasons, then its two halves
const cell = (c) => (c ? `${x(c.ae)} (${c.early == null ? '—' : c.early.toFixed(2)} / ${c.late == null ? '—' : c.late.toFixed(2)})` : '—');

/** The line for a race whose course and trip has facts on the archive, or null. */
export function tripFacts(data) {
  const F = data.trip_facts;
  if (!F || !F.profile) return null;          // a record fitted before 30 Oct has no profile: hidden until the next fit
  const awt = data.race.surface === 'AWT';
  const where = awt ? 'THE AWT' : PLACE[data.race.venue] || data.race.venue;
  const P = F.profile || {}, B = F.body, T = F.turf || {};
  const p = el('p', 'shape-line');
  p.append(el('b', null, `${where} AT ${data.race.distance} M SINCE 2019 (${F.races} RACES): `),
    'against the price (1.00× is what the odds expected), all seasons, then to 2022/23 / since. By where a horse '
    + `usually races, known before the race: the front third ${cell(P.front)}, the middle ${cell(P.middle)}, `
    + `the back third ${cell(P.back)}. Gates 1–3 won ${pc(F.inside_win)} of their runs, ${cell(F.inside)}; `
    + `10–14 ${pc(F.wide_win)}, ${cell(F.wide)}. `
    + (B ? `By body weight at ${NAMED[data.race.venue] || data.race.venue}: the lightest third (under ${lb(B.cuts[0])}) `
      + `${cell(B.light)}, the heaviest (over ${lb(B.cuts[1])}) ${cell(B.heavy)}. ` : '')
    + `Won by 2 lengths or more in ${pc(F.won_by_2)} of races${awt ? ` (turf ${span(T.won_by_2, pc)})` : ''}; `
    + `the last horse a median ${L(F.last_median)} L behind${awt ? ` (turf ${span(T.last_median, L)} L)` : ''}. `
    + 'A figure the same side of 1.00 in both halves has held; none of it improved on the price when tried on later '
    + 'seasons. What happened on the archive, not a rating of this race.');
  return p;
}

/* Race shape — ON THE AWT: one line of what the archive says of an AWT race's course and trip.
 *
 * Brett (26 Oct): the all-weather is heavily biased and its margins are wider than turf's. The
 * archive since 2019 agrees on the margins and on leaders at 1650 and 1800, not on closers, and
 * says the draw's advantage is mostly in the price. So the line gives winners against the price
 * (A/E: wins over the wins the price expected; 1.00 is the price exactly) by where they raced early
 * and, known before the race, by habitual early speed; the draw; and the margins against turf's.
 * Each figure has its race count. Context only: it moves nothing, and the simulation's AWT tables
 * already carry the surface's average.
 */
import { el } from './vocab.js';

const x = (v) => (v == null ? '—' : `${v.toFixed(2)}×`);
const pc = (v) => (v == null ? '—' : `${Math.round(100 * v)}%`);
const span = (r, f) => (r ? `${f(r[0])}–${f(r[1])}` : '—');
const L = (v) => (v == null ? '—' : v.toFixed(1));

/** The line for an AWT race with its trip's facts, or null. */
export function tripFacts(data) {
  const F = data.trip_facts;
  if (!F || data.race.surface !== 'AWT') return null;
  const T = F.turf || {};
  const p = el('p', 'shape-line');
  p.append(el('b', null, `ON THE AWT AT ${data.race.distance} SINCE 2019 (${F.races} RACES): `),
    `horses that led early won ${x(F.led_ae)} what their price expected (turf trips ${span(T.led_ae, (v) => v.toFixed(2))}), `
    + `closers ${x(F.closer_ae)}; known before the race, the habitual front third ${x(F.habit_front_ae)}. `
    + `Gates 1–3 won ${pc(F.inside_win)} of their runs and 10–14 ${pc(F.wide_win)}, against the price `
    + `${x(F.inside_ae)} and ${x(F.wide_ae)}. Won by 2 lengths or more in ${pc(F.won_by_2)} of races `
    + `(turf ${span(T.won_by_2, pc)}); the last horse a median ${L(F.last_median)} L behind `
    + `(turf ${span(T.last_median, L)} L). What happened on the archive, not a rating of this race.`);
  return p;
}

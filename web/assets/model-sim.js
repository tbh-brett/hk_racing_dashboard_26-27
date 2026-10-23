/* The SIM view on Model Analysis — what the race simulation has earned (racesim-SPEC section 9.3).
 *
 * Every number is read from /api/shape/record: each race's simulation as it stood at the off
 * (jobs/sim_card never simulates a race after it), scored once the race was run. Each measure
 * stands beside what it has to beat -- a guess, the season-before rate, the Speed Map, v2 -- so
 * the view says what the simulation is good at and, as plainly, what it is not: win and top 3
 * should TIE v2, because the simulation is anchored to it.
 */
import { el, DASH, compactDate } from './vocab.js';

const pct = (v, d = 1) => (v === null || v === undefined ? DASH : `${(100 * v).toFixed(d)}%`);
const r3 = (v) => (v === null || v === undefined ? DASH : v.toFixed(3));
const r4 = (v) => (v === null || v === undefined ? DASH : v.toFixed(4));

function table(title, sub, cols, rows) {
  const box = el('div', 'gbm-block');
  const head = el('div', 'gbm-title', title);
  if (sub) head.append(el('span', 'gbm-sub', ` · ${sub}`));
  box.append(head);
  const t = el('table', 'model-grid');
  const tr = el('tr');
  cols.forEach(([label, align]) => tr.append(el('th', align ?? '', label)));
  const thead = el('thead');
  thead.append(tr);
  const tbody = el('tbody');
  rows.forEach((cells) => {
    const r = el('tr');
    cells.forEach((c, i) => r.append(el('td', cols[i][1] ?? '', c ?? DASH)));
    tbody.append(r);
  });
  t.append(thead, tbody);
  box.append(t);
  return box;
}

// skill only when the gain over the rate is more than twice its standard error (query/shape_record)
const brier = (b) => (b ? [r4(b.brier), r4(b.base_rate_brier), `${b.said}/${b.horses}`, b.skill ? 'SKILL' : 'NOT YET']
  : [DASH, DASH, DASH, DASH]);

export function renderSim(host, foot, d) {
  if (!d) { host.replaceChildren(); return; }
  if (!d.season) {
    host.replaceChildren(el('p', 'gbm-note', 'No simulated race has been run yet: the record starts with the '
      + 'first meeting whose races were simulated before their off.'));
    foot.replaceChildren();
    return;
  }
  const rows = [['SEASON', d.season], ...d.meetings.map((m) => [compactDate(m.date), m])];
  const who = rows.map(([label]) => label);
  host.replaceChildren(
    table('WHO LEADS', 'log loss of the horse that led at the first call; lower is better',
      [['', 'left'], ['RACES'], ['HORSES'], ['SIMULATION'], ['A GUESS'], ['TOP PICK LED'], ['SPEED MAP’S TOP PICK LED']],
      rows.map(([, m], i) => [who[i], String(m.races), String(m.horses), r3(m.p_lead.log_loss), r3(m.p_lead.uniform),
                             pct(m.p_lead.top_pick_led, 0), pct(m.p_lead.speed_map_top_pick_led, 0)])),
    table('P(LEAD), THE SEASON', 'horses by the chance the simulation gave them to lead, and how many did',
      [['SHOWN', 'left'], ['HORSES'], ['MEAN SHOWN'], ['LED']],
      d.season.p_lead.calibration.map((c) => [c.band, String(c.horses), pct(c.mean_p, 1), String(c.led)])),
    table('WHERE THEY SETTLE', 'first-call position, the miss as a share of the field; Brier for the front and back third',
      [['', 'left'], ['SIMULATION'], ['SPEED MAP'], ['MID-FIELD'], ['80% RANGE HELD'], ['FRONT THIRD'], ['(BASE)'],
       ['BACK THIRD'], ['(BASE)']],
      rows.map(([, m], i) => [who[i], pct(m.first_call.mean_miss), pct(m.first_call.speed_map),
                             pct(m.first_call.mid_field), pct(m.first_call.band80_covered, 0),
                             r4(m.first_call.front_third_brier), r4(m.first_call.front_third_base),
                             r4(m.first_call.back_third_brier), r4(m.first_call.back_third_base)])),
    table('THE TRIP', 'Brier against the Comments on Running (runs with a comment), beside the season-before '
      + 'rate; SKILL only when the gain over the rate is more than twice its standard error',
      [['', 'left'], ['3+ WIDE'], ['(RATE)'], ['SAID'], [''], ['NO COVER'], ['(RATE)'], ['SAID'], [''],
       ['HELD UP'], ['(RATE)'], ['SAID'], ['']],
      rows.map(([, m], i) => [who[i], ...brier(m.wide3), ...brier(m.nocover), ...brier(m.held)])),
    table('PACE', 'the share of the simulated races at the band each race was run at, beside how often races run at it',
      [['', 'left'], ['RACES'], ['SIMULATION'], ['BASE RATE']],
      rows.map(([, m], i) => [who[i], String(m.pace.races), pct(m.pace.sim_share_at_the_band),
                             pct(m.pace.base_share_at_the_band)])),
    table('WHO WINS', 'anchored to v2, so these should tie: a gap is the simulation drifting, not information',
      [['', 'left'], ['WIN LOG LOSS: SIM'], ['v2'], ['A GUESS'], ['TOP 3 BRIER: SIM'], ['v2 PLACE'], ['BASE']],
      rows.map(([, m], i) => [who[i], r3(m.win.log_loss_sim), r3(m.win.log_loss_v2), r3(m.win.uniform),
                             r4(m.top3.brier_sim), r4(m.top3.brier_v2_place), r4(m.top3.base)])),
  );
  foot.replaceChildren(el('span', null, 'Each race’s simulation as it stood at the off (racesim-SPEC 9.3). '
    + 'One meeting is not a record; it is why the Speed Map says what it says about the simulation.'));
}

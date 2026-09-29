/* The MODEL view on Model Analysis — what the fundamental model has earned.
 *
 * gbm-SPEC §7 and §14: every number here is read from /api/model/gbm/record
 * (the live model's walk-forward record, built by jobs/fit_gbm), never typed.
 * The page's purpose is to show what a number has earned, so the first thing
 * on it is which model is live and what the gate said about the newest fit,
 * then the record that says what MODEL% and GAP are worth on Race Day.
 *
 * Nothing here ranks or colours by the gap: the deciles are shown in their
 * own order, and their verdict is the price's, not the model's.
 */
import { num } from './api.js';
import { el, DASH, compactDate } from './vocab.js';

const pct = (v, d = 1) => (v === null || v === undefined ? DASH : `${(100 * v).toFixed(d)}%`);
const r2 = (v) => (v === null || v === undefined ? DASH : v.toFixed(3));
const ae = (a) => (!a || a.ae === null || a.ae === undefined ? DASH
  : `${a.ae.toFixed(2)} [${a.ae_lo.toFixed(2)}–${a.ae_hi.toFixed(2)}]`);
const count = (v) => (v === null || v === undefined ? DASH : Number(v).toLocaleString('en-US'));

/** One titled table: `cols` are [label, align], `rows` arrays of cell text. */
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

function status(d) {
  const box = el('div', 'gbm-status');
  const lv = d.live;
  box.append(el('div', null, `LIVE ${lv.version} · TRAINED THROUGH `
    + `${compactDate(lv.trained_through)} · PROMOTED ${lv.promoted_at?.slice(0, 10) ?? DASH} · `
    + `${lv.rounds} TREES · LIGHTGBM ${lv.lightgbm ?? DASH}`));
  const f = d.last_fit;
  if (f && f.version !== lv.version) {
    box.append(el('div', 'held', `NEWEST FIT ${f.version} · HELD BACK — `
      + `${f.gate?.reason ?? 'no reason recorded'}. The page keeps the live model.`));
  } else if (f?.gate) {
    const w = f.gate.window ? ` on ${f.gate.window.join(' to ')}` : '';
    box.append(el('div', null, `LAST CHECK${w}: ${f.gate.reason}`));
  }
  return box;
}

export function renderGbm(host, foot, d) {
  if (!d) {
    host.replaceChildren(el('div', 'empty', 'no model has been promoted yet'));
    foot.replaceChildren();
    return;
  }
  const wf = d.walk_forward ?? {};
  const blocks = [status(d)];

  blocks.push(table('BY SEASON', 'EACH SCORED BY A MODEL THAT NEVER SAW IT',
    [['SEASON'], ['RACES', 'num'], ['MODEL R²', 'num'], ['PRICE R²', 'num'],
     ['MODEL TOP PICK WON', 'num'], ['FAVOURITE WON', 'num']],
    [...(wf.seasons ?? []), { season: 'ALL', ...(wf.pooled ?? {}) }].map((s) => [
      s.season, count(s.races), r2(s.r2), r2(s.r2_base), pct(s.top_pick), pct(s.top_pick_base)])));

  const cal = wf.calibration ?? [];
  const bands = [...new Set(cal.map((c) => c.band))];
  const get = (band, who) => cal.find((c) => c.band === band && c.who === who);
  blocks.push(table('CALIBRATION', 'WHEN IT SAYS X%, HOW OFTEN THEY WON',
    [['BAND'], ['MODEL RUNNERS', 'num'], ['MODEL SAID', 'num'], ['WON', 'num'],
     ['PRICE RUNNERS', 'num'], ['PRICE SAID', 'num'], ['WON', 'num']],
    bands.map((b) => {
      const m = get(b, 'model'); const p = get(b, 'price');
      return [b, count(m?.runs), pct(m?.said), pct(m?.won), count(p?.runs), pct(p?.said), pct(p?.won)];
    })));

  blocks.push(table('BY SEGMENT', 'ONE POOLED MODEL, READ PER COURSE AND TRIP',
    [['SEGMENT'], ['RACES', 'num'], ['MODEL R²', 'num'], ['PRICE R²', 'num']],
    (wf.segments ?? []).map((s) => [s.segment.replace('_', ' '), count(s.races), r2(s.r2),
      r2(s.r2_base)])));

  blocks.push(table('THE GAP, BY DECILE', 'MODEL% LESS WIN%, AND WHO WAS RIGHT: A/E AGAINST THE PRICE',
    [['DECILE', 'num'], ['GAP FROM', 'num'], ['TO', 'num'], ['RUNS', 'num'], ['WON', 'num'],
     ['PRICE EXPECTED', 'num'], ['A/E (PRICE)', 'num']],
    (wf.gap_deciles ?? []).map((g) => [String(g.decile), num(100 * g.gap_from, 1),
      num(100 * g.gap_to, 1), count(g.runs), count(g.won),
      num(g.price?.expected_wins, 0), ae(g.price)])));
  if (d.gap_note) blocks.push(el('div', 'gbm-note', d.gap_note));

  blocks.push(table('FLAGS', 'WHERE FIVE SEASONS SAY THE MODEL IS OFF',
    [['FLAG'], ['RUNS', 'num'], ['WON', 'num'], ['MODEL A/E', 'num'], ['PRICE A/E', 'num'], ['WHAT IT MEANS']],
    (wf.flags ?? []).map((f) => [f.label, count(f.runs), count(f.won), ae(f.model), ae(f.price), f.note])));

  blocks.push(table('THE DRAW IT READS WRONGLY', 'WINS AGAINST WHAT EACH EXPECTED',
    [['COURSE'], ['GATES'], ['RUNS', 'num'], ['WON', 'num'], ['MODEL EXPECTED', 'num'],
     ['PRICE EXPECTED', 'num']],
    (wf.draw_courses ?? []).map((x) => [x.course, x.gates, count(x.runs), count(x.won),
      num(x.model?.expected_wins, 1), num(x.price?.expected_wins, 1)])));

  const meets = d.meetings ?? [];
  blocks.push(table('MEETING BY MEETING', meets.length
    ? 'WHAT THE PAGE SHOWED AT THE OFF, THE FIRST READ, AND THE PRICE'
    : 'NONE SETTLED SINCE THE MODEL WENT LIVE',
    [['DATE'], ['RACES', 'num'], ['R² SHOWN', 'num'], ['R² FIRST READ', 'num'], ['R² PRICE', 'num'],
     ['TOP PICK WON', 'num'], ['FAVOURITE WON', 'num'], ['WINNER IN TOP 3', 'num'], ['PRICE TOP 3', 'num']],
    [...meets].reverse().map((m) => [compactDate(m.date), count(m.races), r2(m.r2_model),
      r2(m.r2_card), r2(m.r2_market), count(m.top_pick_won), count(m.favourite_won),
      count(m.winner_top3_model), count(m.winner_top3_market)])));

  host.replaceChildren(...blocks);
  foot.replaceChildren(
    el('span', null, `TIME ENTERS THROUGH ${(d.time_inputs ?? []).length} INPUTS, EACH A RUN `
      + `AGAINST THE MEDIAN OF ITS OWN RACE: ${(d.time_inputs ?? []).join(' · ')}`),
    el('span', null, `TWELVE GROUPS: ${(d.groups ?? []).join(' · ').toUpperCase()}`),
    el('span', null, 'IT NEVER SEES TODAY’S PRICE, TRIALS, TRACKWORK OR GEAR'));
}

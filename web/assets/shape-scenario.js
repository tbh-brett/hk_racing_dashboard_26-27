/* Race shape — a scenario: the race run again in the browser under Brett's own assumptions.
 *
 * Three levers, each one the simulator already has, so the vendored engine is not touched:
 *   early pace   which borrowed clocks the races may use. The simulator charges a horse for its
 *                early position against the pace (early_tempo), so fast early clocks cost the
 *                leaders and slow ones the closers. Only the early pace changes who wins in this
 *                model; the later sections come with the clock.
 *   leaders      lengths added for being at the back rather than in front (the cost table's
 *                `early`): + favours horses up with the pace.
 *   the rail     lengths added for being off the rail turning in, the same for every lane off it
 *                (`turn` 2, 3 and 4+): + favours the rail. That is what the day's read measures
 *                (on the rail against off it), so the two mean one thing. The 20,000 races know a
 *                horse's lane on the turn, not in the straight; a horse wide on the turn comes wide
 *                in the straight.
 *
 * Both sliders reach 3 lengths either way on a trip with turf's spread of results, and further in
 * proportion where results spread more (4 at AWT 1650, 3.5 at 1200, whose winners win by more): a length means
 * less there against the luck of the race.
 *
 * It uses the race's stored anchor and runs twice on the same random numbers -- once neutral, once
 * under the scenario -- so the difference is the scenario and not noise (0.3 s each). The win
 * chances that move are YOUR assumption's consequence, not information: the record says a day's
 * bias has not carried over beyond the price. They are shown in v2's order, never coloured, never
 * sorted by the change.
 */
import { el, DASH } from './vocab.js';

export const PACE_GROUPS = {
  any: null,
  fast: ['Very Fast', 'Fast'],
  neutral: ['Neutral'],
  slow: ['Slow', 'Very Slow'],
};
const N = 20000;
export const RANGE = 3;          // lengths either way on both sliders, on turf's spread of results
const SPREAD = 4.0;              // the pooled spread the 3 is set against (sim_tables noise, 25 Oct)

/** The sliders' reach for a race: RANGE, widened where its trip's results spread more. */
export function rangeFor(data) {
  const sd = data?.tables?.noise?.sd_lengths;
  return sd ? Math.max(RANGE, Math.round((2 * RANGE * sd) / SPREAD) / 2) : RANGE;
}
const pct = (v, d = 1) => (v == null ? DASH : `${(100 * v).toFixed(d)}%`);
const title = (s) => String(s || '').split(/\s+/).map((w) => (w ? w[0] + w.slice(1).toLowerCase() : w)).join(' ');
const lens = (v) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)} L`;

/** The simulator's context for a race under a scenario {pace, front, lane}. */
export function scenarioContext(S, data, sc) {
  const group = data.race.cost_group;
  const C = JSON.parse(JSON.stringify(data.tables.costs[group]));
  C.early = (C.early || 0) + sc.front;
  C.turn = { ...C.turn };
  for (const k of ['2', '3', '4']) C.turn[k] = (C.turn[k] || 0) + sc.lane;
  const T = { ...data.tables, costs: { [group]: C } };
  const want = PACE_GROUPS[sc.pace];
  const tps = want ? data.templates.filter((t) => want.includes(t.band)) : data.templates;
  return { ctx: S.context(data.race, T, 0, data.settle_sd, tps.length ? tps : data.templates), clocks: tps.length };
}

export const neutral = { pace: 'any', front: 0, lane: 0 };
const isNeutral = (sc) => sc.pace === 'any' && sc.front === 0 && sc.lane === 0;

/** The SCENARIO box for one race. `loadEngine` loads the vendored files; `onChange(ctx|null)`
 *  hands the run viewer the scenario's context, or null when it is neutral. */
export function createScenario(host, { loadEngine, onChange }) {
  let data = null;
  let sc = { ...neutral };
  let out = null;
  const box = el('div', 'shape-scenario');
  host.replaceChildren(box);

  function show(d) {
    data = d;
    out = null;
    const r = rangeFor(d);          // the levers carry from race to race, within this race's reach
    sc = { ...sc, front: Math.max(-r, Math.min(r, sc.front)), lane: Math.max(-r, Math.min(r, sc.lane)) };
    render();
  }

  /** Put levers on the box; true when one had to be held to the race's range. */
  function set(next) {
    const r = rangeFor(data);
    const held = Object.fromEntries(Object.entries(next).map(([k, v]) => [k,
      typeof v === 'number' && k !== 'pace' ? Math.max(-r, Math.min(r, v)) : v]));
    sc = { ...sc, ...held };
    out = null;
    onChange(null);                 // the run viewer runs what the table shows: neutral until re-run
    render();
    return Object.keys(next).some((k) => held[k] !== next[k]);
  }

  function slider(label, key, min, max, step, hint) {
    const wrap = el('label', 'sc-slider');
    const input = el('input');
    Object.assign(input, { type: 'range', min, max, step, value: String(sc[key]) });
    const val = el('b', null, lens(sc[key]));
    input.addEventListener('input', () => { val.textContent = lens(Number(input.value)); });
    input.addEventListener('change', () => set({ [key]: Number(input.value) }));
    wrap.append(el('span', 'label', label), input, val, el('span', 'small', hint));
    return wrap;
  }

  function render() {
    if (!data) return;
    const counts = Object.fromEntries(Object.entries(PACE_GROUPS).map(([k, g]) => [k,
      g ? data.templates.filter((t) => g.includes(t.band)).length : data.templates.length]));
    const head = el('div', 'sc-head');
    head.append(el('span', 'label', 'SCENARIO · THE RACE RUN AGAIN UNDER YOUR ASSUMPTIONS'));
    const pace = el('div', 'shape-seg');
    for (const [k, label] of [['any', 'ANY PACE'], ['fast', 'FAST EARLY'], ['neutral', 'NEUTRAL'], ['slow', 'SLOW EARLY']]) {
      const b = el('button', null, `${label} (${counts[k]})`);
      b.type = 'button';
      b.disabled = counts[k] === 0;
      b.title = k === 'any' ? 'Every borrowed clock, as the stored simulation used them'
        : `Only the borrowed clocks run at this early pace: ${counts[k]} of ${data.templates.length}`;
      b.setAttribute('aria-pressed', String(sc.pace === k));
      b.addEventListener('click', () => set({ pace: k }));
      pace.append(b);
    }
    const controls = el('div', 'sc-controls');
    controls.append(pace,
      slider('LEADERS', 'front', -rangeFor(data), rangeFor(data), 0.25, 'lengths for leading against racing last; + favours the pace'),
      slider('THE RAIL', 'lane', -rangeFor(data), rangeFor(data), 0.25, 'lengths for being off the rail turning in; + favours the rail'));
    const run = el('button', 'shape-btn', isNeutral(sc) ? 'NEUTRAL — NOTHING TO RE-RUN' : 'RE-SIMULATE');
    run.type = 'button';
    run.disabled = isNeutral(sc);
    run.addEventListener('click', simulate);
    const reset = el('button', 'shape-btn', 'BACK TO NEUTRAL');
    reset.type = 'button';
    reset.addEventListener('click', () => set({ ...neutral }));
    controls.append(run, reset);
    box.replaceChildren(head, controls, ...(out ? result() : []));
  }

  async function simulate() {
    const btn = box.querySelector('.sc-controls .shape-btn');
    btn.disabled = true;
    btn.textContent = 'SIMULATING 2 × 20,000 RACES…';
    await loadEngine();
    await new Promise((r) => setTimeout(r, 30));            // let the button repaint first
    const S = window.SimCore;
    const t0 = performance.now();
    const base = S.context(data.race, data.tables, 0, data.settle_sd, data.templates);
    const { ctx, clocks } = scenarioContext(S, data, sc);
    out = { neutral: S.summarise(base, data.anchor.a, N), scenario: S.summarise(ctx, data.anchor.a, N), clocks,
            ms: performance.now() - t0, sc: { ...sc } };
    onChange(ctx);
    render();
  }

  function result() {
    const { neutral: a, scenario: b, sc: s } = out;
    const by = (sum) => new Map(sum.runners.map((x) => [x.no, x]));
    const A = by(a), B = by(b);
    const t = el('table', 'shape-grid sc-grid');
    const hr = el('tr');
    for (const [h, cls] of [['', 'l'], ['HORSE', 'l'], ['WIN · NEUTRAL', 'v2'], ['WIN · IF THIS HOLDS', ''],
      ['TOP 3 · NEUTRAL', ''], ['TOP 3 · IF THIS HOLDS', 'sim'], ['FRONT THIRD', '']]) {
      hr.append(el('th', cls, h));
    }
    t.append(el('thead'));
    t.tHead.append(hr);
    const body = el('tbody');
    for (const h of [...data.runners].sort((x, y) => y.v2_win - x.v2_win)) {
      const x = A.get(h.no), y = B.get(h.no);
      const tr = el('tr');
      const c = el('td', 'l');
      const chip = el('span', 'sc', String(h.no));
      chip.style.setProperty('--c', `var(--sc-${((h.no - 1) % 14) + 1})`);
      c.append(chip);
      tr.append(c, el('td', 'l nm', title(h.name)), el('td', 'v2', pct(x.win)), el('td', null, pct(y.win)),
        // the levers act on the finish, not the settle, so where a horse races early is the same both ways
        el('td', null, pct(x.top3)), el('td', 'sim', pct(y.top3)), el('td', null, pct(y.front, 0)));
      body.append(tr);
    }
    t.append(body);
    const wrap = el('div', 'shape-scroll');
    wrap.append(t);
    const paceTxt = s.pace === 'any' ? 'every borrowed clock' : `only the ${out.clocks} ${s.pace} early clocks`;
    const note = el('p', 'shape-line');
    note.append(el('b', null, 'IF THIS HOLDS: '), `${paceTxt}, leaders ${lens(s.front)}, the rail ${lens(s.lane)} — `
      + `${N.toLocaleString()} races each way on the same random numbers (${(out.ms / 1000).toFixed(1)} s in this browser). `
      + 'The change in the win columns is your assumption run through the model, not a reading of the race: on the '
      + 'archive a day’s bias has not carried from its early races to its later ones beyond the price. '
      + '"Watch a run" now runs under this scenario.');
    return [note, wrap];
  }

  return { show, set, current: () => ({ ...sc }) };
}

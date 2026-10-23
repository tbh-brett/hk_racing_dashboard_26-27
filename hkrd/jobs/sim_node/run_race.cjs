// Simulate one race of a stored card (racesim-SPEC §10.2). The one program jobs/sim_card runs in node.
//
//   node run_race.cjs <ENGINE_DIR>  < {card, tables, race_no, settings}  > one JSON document
//
// ENGINE_DIR is web/assets/shape: the vendored engine (engine.js) and simulator (sim-core.js), the same files the
// pages run. It is the body of the lab's run_sim.js loop for one race: the card-wide settle spread first (it is set
// on the whole card), then anchor the abilities so each horse wins as often as v2 says, summarise N_SUM races without
// the engine, run N_ENGINE untripped races through the engine (each horse's own average ground and hold-up cost),
// and N_COUPLED races in which the animated trip decides the finish. The settings come from stdin, nothing is written
// to disk and nothing is read from the clock, so identical input gives byte-identical output -- which is what
// `sim_card --verify` compares. Any error: exit 1 with a message on stderr.
"use strict";
const fs = require("fs"), path = require("path");

function fail(msg) {
  process.stderr.write(`run_race: ${msg}\n`);
  process.exit(1);
}

function need(obj, keys, what) {
  for (const k of keys) if (obj[k] == null) fail(`${what} has no ${k}`);
}

function simulate(S, C, input) {
  need(input, ["card", "tables", "race_no", "settings"], "the input");
  const { card, tables: T, race_no: raceNo, settings } = input;
  need(settings, ["N_SUM", "N_ANCHOR", "PASSES", "N_ENGINE", "N_COUPLED", "SIGMA_MULT"], "settings");
  if (!Array.isArray(card.races) || !card.races.length) fail("the card has no races");
  const race = card.races.find(r => r.no === raceNo);
  if (!race) fail(`the card has no race ${raceNo}`);
  if (!race.runners.length) fail(`race ${raceNo} has no runners`);
  const { N_SUM, N_ANCHOR, PASSES, N_ENGINE, N_COUPLED, SIGMA_MULT } = settings;

  const spread = S.settleSpread(card.races, T);
  const ctx = S.context(race, T, 0, spread.sd);
  if (!ctx.tps.length) fail(`no template races for ${race.template_key}`);
  const anc = S.anchor(ctx, N_ANCHOR, PASSES);
  const sum = S.summarise(ctx, anc.a, N_SUM);
  const n = race.runners.length, z = () => new Array(n).fill(0);
  // 1. untripped races through the engine: each horse's average trip (ground, hold-ups) and how much it varies
  const K0 = { md: sum.runners.map(x => x.cost_lane + x.cost_held), sigma: ctx.sigma };
  const A = { g: z(), g2: z(), h: z(), h2: z(), d: z(), d2: z(), gh: z() };
  for (let k = 0; k < N_ENGINE; k++) {
    const o = S.coupled(ctx, anc.a, k + 1, C, K0);
    o.trip.forEach((x, i) => {
      const c = o.sr.runners[i].sim.cLane + o.sr.runners[i].sim.cHeld, r = x.ground + x.heldL;
      A.g[i] += x.ground; A.h[i] += x.heldL; A.gh[i] += r * r; A.g2[i] += r; A.d[i] += c; A.d2[i] += c * c;
    });
  }
  const mg = A.g.map(v => v / N_ENGINE), mh = A.h.map(v => v / N_ENGINE);
  const varReal = A.gh.map((v, i) => v / N_ENGINE - (A.g2[i] / N_ENGINE) ** 2);
  const varDrawn = A.d2.map((v, i) => v / N_ENGINE - (A.d[i] / N_ENGINE) ** 2);
  const vr = varReal.reduce((x, y) => x + y, 0) / n, vd = varDrawn.reduce((x, y) => x + y, 0) / n;
  // the finishing noise, set on the card as a whole (x1.045 of the anchored one, 20 Oct) so that over many runs the
  // spread of results stays v2's: the steered trip works against a horse's luck (run_sim.js has the measurement)
  const sigma = SIGMA_MULT * ctx.sigma;
  const K = { mg: mg.map(v => +v.toFixed(3)), mh: mh.map(v => +v.toFixed(3)), md: K0.md.map(v => +v.toFixed(3)), sigma: +sigma.toFixed(3),
    sigma0: +ctx.sigma.toFixed(3), var_trip: +vr.toFixed(3), var_drawn: +vd.toFixed(3), sigma_mult: SIGMA_MULT };
  // 2. races whose finish the trip decides: the engine columns, and the check that v2's chances hold
  const E = { held: z(), heldSec: z(), nocover: z(), ground: z(), contact: z(), simHeld: z(), bothHeld: z(), run: z(), round: z(), through: z(), heldThrough: z(),
    win: z(), top3: z(), cost: z(), bad: z(), winBad: z(), good: z(), winGood: z() };
  let winnerChanged = 0, shift = 0;
  for (let k = 0; k < N_COUPLED; k++) {
    const o = S.coupled(ctx, anc.a, k + 1, C, K), sr = o.sr, R = o.R, D = sr.distance;
    sr.runners.forEach((h, i) => {
      if (h.place === 1) E.win[i]++;
      if (h.place <= 3) E.top3[i]++;
      const tc = h.sim.tripCost; E.cost[i] += tc;
      if (tc >= 1) { E.bad[i]++; if (h.place === 1) E.winBad[i]++; }
      if (tc <= -1) { E.good[i]++; if (h.place === 1) E.winGood[i]++; }
      shift += Math.abs(h.place - h.sim.place0);
      if (h.place === 1 && h.sim.place0 !== 1) winnerChanged++;
    });
    R.H.forEach((x, i) => {
      let held = 0, contact = false;
      for (let q = 0; q < R.K; q++) {
        const d = D - R.XD[i][q];
        if (d < -5) break;
        if (R.FL[i][q] & 4) contact = true;
        if (!R.geo.straight && d < R.geo.Lh && d > 0 && (R.FL[i][q] & 1)) held += C.DT;
      }
      const sm = R.summary[i];
      if (held >= 0.5) E.held[i]++;
      E.heldSec[i] += held;
      if (sm.noCoverShare != null && sm.noCoverShare >= 0.5) E.nocover[i]++;
      E.ground[i] += sm.groundLengths;
      if (contact) E.contact[i]++;
      if (o.sr1.runners[i].sim.held) { E.simHeld[i]++; if (held >= 0.5) E.bothHeld[i]++; }
      const ru = R.runs ? R.runs[i] : null;                        // engine v3: the run from the turn
      if (ru && ru.mover && ru.how) { E.run[i]++; if (ru.how === "round") E.round[i]++; else { E.through[i]++; if (held >= 1) E.heldThrough[i]++; } }
    });
  }
  const NC = N_COUPLED;
  const engine = race.runners.map((x, i) => ({ no: x.no, held: E.held[i] / NC, held_sec: E.heldSec[i] / NC,
    nocover_most: E.nocover[i] / NC, ground_lengths: E.ground[i] / NC, contact: E.contact[i] / NC,
    shown_held_when_sim_held: E.simHeld[i] ? E.bothHeld[i] / E.simHeld[i] : null,
    run_share: E.run[i] / NC, round_share: E.round[i] / NC, through_share: E.through[i] / NC,
    held_when_through: E.through[i] >= 20 ? E.heldThrough[i] / E.through[i] : null,
    win: E.win[i] / NC, top3: E.top3[i] / NC, trip_cost_mean: E.cost[i] / NC,
    bad_trip: E.bad[i] / NC, win_if_bad_trip: E.bad[i] >= 15 ? E.winBad[i] / E.bad[i] : null,
    good_trip: E.good[i] / NC, win_if_good_trip: E.good[i] >= 15 ? E.winGood[i] / E.good[i] : null,
    mean_ground: mg[i], mean_held_cost: mh[i] }));
  const coupledCheck = { runs: NC, winner_changed: winnerChanged / NC, mean_place_shift: shift / (NC * n),
    max_win_gap_to_v2: Math.max(...race.runners.map((h, i) => Math.abs(E.win[i] / NC - h.p_win))),
    mean_win_gap_to_v2: race.runners.reduce((acc, h, i) => acc + Math.abs(E.win[i] / NC - h.p_win), 0) / n,
    mc_sd_mean: race.runners.reduce((acc, h) => acc + Math.sqrt(h.p_win * (1 - h.p_win) / NC), 0) / n };
  return { id: race.id, anchor: { a: anc.a.map(v => +v.toFixed(3)), max_err: +anc.maxErr.toFixed(4), passes: anc.passes, sims: anc.sims },
    summary: sum, engine, coupling: K, coupled_check: coupledCheck, templates: ctx.tps.map(t => t.id), settle_sd: ctx.sd, sigma: ctx.sigma,
    settle_spread: { sd: spread.sd, miss: +spread.miss.toFixed(4) } };
}

try {
  const dir = process.argv[2];
  if (!dir) fail("usage: node run_race.cjs <ENGINE_DIR> < input.json");
  const S = require(path.resolve(dir, "sim-core.js"));
  const C = require(path.resolve(dir, "engine.js"));
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, "utf8"));
  } catch (e) {
    fail(`stdin is not one JSON document: ${e.message}`);
  }
  process.stdout.write(JSON.stringify(simulate(S, C, input)));
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
}

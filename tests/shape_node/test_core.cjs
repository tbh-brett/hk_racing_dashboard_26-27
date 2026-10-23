// Copied from the lab (race-shape-2d/replay/test_core.js) for tests/test_shape_engine.py (racesim-SPEC §11.1): the engine is the vendored
// one (ENGINE_DIR, web/assets/shape), and every input and output path is given on the command line.
// Checks the replay core (v3) on every race:
//  1. along the track: section marks hit at the recorded times, nobody runs backwards, speeds sane
//  2. across it: no sideways jumps, sideways speed, bodies overlapping, horses pushed off a stated lane (a run round
//     from the turn leaves its lane on purpose: counted apart)
//  3. stated lanes honoured at the anchors; horses the comments put on the rail stay there
//  4. cover worked out from the layout, against what the stewards wrote
//  5. Brett's video corrections (replay/db_pull/corrections/*.json, if pulled): how close the replay
//     gets without them, and that it honours them when they're applied
//   node replay/test_core.js replay/races.json [out.json]
const fs = require("fs"), path = require("path");
const C = require(path.resolve(process.env.ENGINE_DIR, "engine.js"));
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const OUT = process.argv[3];
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + "%" : "–";

// Brett's corrections, as the page stores them
const corr = {};
const mpath = process.env.MARKS;
if (fs.existsSync(mpath)) for (const v of JSON.parse(fs.readFileSync(mpath, "utf8"))) {
  if (v && v.race && v.horse != null && v.phase) ((corr[v.race] ||= {})[v.horse] ||= {})[v.phase] = v;
}

let marks = 0, markErr = 0, back = 0, fast = 0;
const disp = { marks: 0, within05: 0, within1: 0, maxDev: 0, sumDev: 0, back: 0, fast: 0, steps: 0, lagged: 0 };
const orderAgree = { n: 0, same: 0 };
const lat = { steps: 0, jumps: 0, jumpsNoContact: 0, maxVy: 0, fastVy: 0, pairs: 0, overlap: 0, runs: 0, pushedOff: 0, roundOff: 0, rounds: 0 };
const runs = {};                               // v3: the run from the turn, by way: how many, held up (engine, comments)
const addRun = (k, held, said) => { const r = (runs[k] ||= { n: 0, held2: 0, said: 0, both: 0, sec: 0 }); r.n++; if (held >= 2) r.held2++; if (said) r.said++; if (held >= 2 && said) r.both++; r.sec += held; };
const lane = { n: 0, exact: 0, within1: 0 };
const rail = { n: 0, under15: 0, under2: 0 };
const cover = { nocover: { n: 0, none: 0 }, cover: { n: 0, cover: 0 }, all: { n: 0, none: 0, cover: 0 } };
const brett = {};
const addB = (k, e) => { const s = (brett[k] ||= { n: 0, exact: 0, within1: 0, sum: 0 }); s.n++; if (e === 0) s.exact++; if (Math.abs(e) <= 1) s.within1++; s.sum += e; };
const brettCover = {};
const addBC = (k, ok) => { const s = (brettCover[k] ||= { n: 0, agree: 0 }); s.n++; if (ok) s.agree++; };
const anchorFrame = (R, race, d) => C.frame(R, Math.min(...R.H.map(x => C.timeAt(x.tl, race.distance - d))));
const lanesCap = l => Math.max(1, Math.min(8, Math.round(l)));

for (const race of data.races) {
  const R = C.build(race, null);
  const D = race.distance;
  // 1. along the track
  R.H.forEach(x => {
    let cum = 0, s = 0;
    race.sections.forEach((L, k) => {
      cum += x.h.secs[k]; s += L; marks++;
      if (Math.abs(C.posAt(x.tl, cum) - s) > 0.05) markErr++;
    });
    let prev = 0, prevT = 0;
    for (let tt = 0.1; tt <= x.tl.end + 2; tt += 0.1) {
      const p = C.posAt(x.tl, tt);
      if (p < prev - 1e-6) back++;
      if ((p - prev) / (tt - prevT) > 20.5) fast++;
      prev = p; prevT = tt;
    }
  });
  race.sections.forEach((L, k) => {
    const cumT = R.H.map(x => x.h.secs.slice(0, k + 1).reduce((a, b) => a + b, 0));
    const rank = cumT.map(v => 1 + cumT.filter(w => w < v - 1e-9).length);
    R.H.forEach((x, i) => { if (x.h.pos[k]) { orderAgree.n++; if (Math.abs(rank[i] - x.h.pos[k]) <= 1) orderAgree.same++; } });
  });
  // 1b. the displayed track: easing to tuck in or wait must not move a horse off its section marks
  const XDat = (i, tt) => { const kf = Math.min(R.K - 1.001, tt / C.DT), k = Math.floor(kf), f = kf - k; return R.XD[i][k] + (R.XD[i][k + 1] - R.XD[i][k]) * f; };
  R.H.forEach((x, i) => {
    let cum = 0, s = 0;
    race.sections.forEach((L, k) => {
      cum += x.h.secs[k]; s += L; disp.marks++;
      const dev = Math.abs(XDat(i, cum) - s);
      if (dev <= 0.5) disp.within05++; if (dev <= 1.0) disp.within1++;
      disp.maxDev = Math.max(disp.maxDev, dev); disp.sumDev += dev;
    });
    for (let k = 1; k < R.K; k++) {
      if (R.X[i][k] > D + 5) break;
      disp.steps++;
      const dv = (R.XD[i][k] - R.XD[i][k - 1]) / C.DT;
      if (dv < -1e-3) disp.back++;
      if (dv > 20.5) disp.fast++;
      if (R.X[i][k] - R.XD[i][k] > 2) disp.lagged++;
    }
  });
  // 2. across the track
  const kPost = R.H.map((x, i) => { let k = 0; while (k < R.K - 1 && R.XD[i][k] < D) k++; return k; });
  R.H.forEach((x, i) => {
    lat.runs++;
    let pushed = false, roundOff = false;
    const ru = R.runs ? R.runs[i] : null, roundFrom = ru && ru.mover && ru.how === "round" && ru.from != null ? ru.from : -1;
    if (roundFrom > 0) lat.rounds++;
    for (let k = 10; k < kPost[i]; k++) {
      lat.steps++;
      const vy = Math.abs(R.Y[i][k] - R.Y[i][k - 1]) / C.DT;
      lat.maxVy = Math.max(lat.maxVy, vy);
      if (vy > 1.5) lat.fastVy++;
      if (k + 10 < R.K && Math.abs(R.Y[i][k + 10] - R.Y[i][k]) > 1.5) {
        lat.jumps++;
        let c = false; for (let q = k; q <= k + 10; q++) if (R.FL[i][q] & 4) c = true;
        if (!c) lat.jumpsNoContact++;
      }
      const d = D - R.XD[i][k];
      if (R.XD[i][k] > 300 && d >= R.geo.Lh) {
        const tg = C.planTarget(R.H[i].plan, d, false, null);
        if (tg && tg.w >= 1 && R.Y[i][k] - tg.lane > 1.5) { if (d <= roundFrom) roundOff = true; else pushed = true; }
      }
    }
    if (pushed) lat.pushedOff++;
    if (roundOff) lat.roundOff++;
    if (ru && !R.geo.straight) {
      let held = 0;
      for (let k = 1; k < R.K; k++) { const d = D - R.XD[i][k]; if (d < 0) break; if (d < R.geo.Lh && (R.FL[i][k] & C.F_HELD)) held += C.DT; }
      if (x.h.cor) addRun(ru.mover ? `${ru.how} (${ru.src === "cor" ? "the comment" : ru.src === "you" ? "Brett" : ru.src === "pocketed" ? "pocketed" : "the model"})` : "no run", held, !!x.h.p.traffic_straight);
    }
  });
  for (let k = 10; k < R.K; k += 2) {
    for (let i = 0; i < R.H.length; i++) for (let j = i + 1; j < R.H.length; j++) {
      if (k >= kPost[i] || k >= kPost[j]) continue;
      const dx = R.XD[j][k] - R.XD[i][k];
      if (Math.abs(dx) >= C.BODY) continue;
      lat.pairs++;
      const dy = R.Y[j][k] - R.Y[i][k], q = Math.sqrt((dx / C.BODY) ** 2 + (dy / C.SIDE) ** 2);
      if (q < 0.8) lat.overlap++;
    }
  }
  // 3 and 4. stated lanes, rail horses, cover, at each phase's anchor
  for (const ph of ["settle", "mid", "turn"]) {
    const d = R.anchors[ph];
    if (d == null) continue;
    const F = anchorFrame(R, race, d);
    R.H.forEach((x, i) => {
      const p = x.h.p[ph];
      if (!p) return;
      if (p.lane != null) { lane.n++; const e = Math.abs(F[i].lane - p.lane); if (e < 0.5) lane.exact++; if (e < 1.5) lane.within1++; }
      if (ph === "turn") return;
      if (F[i].cover !== "lead") { cover.all.n++; if (F[i].cover === "none") cover.all.none++; else cover.all.cover++; }
      if (p.nocover) { cover.nocover.n++; if (F[i].cover === "none") cover.nocover.none++; }
      if (p.cover) { cover.cover.n++; if (F[i].cover === "cover") cover.cover.cover++; }
    });
  }
  R.H.forEach((x, i) => {
    const ls = ["settle", "mid", "turn"].filter(ph => R.anchors[ph] != null).map(ph => x.h.p[ph] && x.h.p[ph].lane);
    if (!ls.length || ls.some(l => l !== 1)) return;
    let mx = 0;
    const ru = R.runs ? R.runs[i] : null, until = ru && ru.mover && ru.how === "round" && ru.from != null ? ru.from : R.geo.Lh;   // until a run round
    for (let k = 0; k < R.K; k++) { const d = D - R.XD[i][k]; if (d <= R.anchors.settle && d >= Math.max(R.geo.Lh, until)) mx = Math.max(mx, R.Y[i][k]); }
    rail.n++; if (mx <= 1.5) rail.under15++; if (mx <= 2.0) rail.under2++;
  });
  // 5. Brett's corrections
  if (corr[race.id]) {
    const R1 = C.build(race, corr[race.id]);
    for (const ph of C.PHASES) {
      const d = R.anchors[ph];
      if (d == null) continue;
      const F0 = anchorFrame(R, race, d), F1 = anchorFrame(R1, race, d);
      R.H.forEach((x, i) => {
        const c = (corr[race.id][x.h.no] || {})[ph];
        if (!c) return;
        const p = x.h.p[ph] || {}, grp = ph === "straight" ? "straight" : (p.lane != null ? "before_straight_stated" : "before_straight_guessed");
        if (c.lane) {
          addB(`without_corrections_${grp}`, Math.min(lanesCap(F0[i].lane), 5) - c.lane);     // the page offered lanes 1-5
          addB(`with_corrections_${ph}`, Math.min(lanesCap(F1[i].lane), 5) - c.lane);
        }
        if (ph !== "straight" && (c.cover === "cover" || c.cover === "none")) {
          addBC(`without_corrections_said_${c.cover}`, F0[i].cover === c.cover || (c.cover === "none" && F0[i].cover === "lead"));
          addBC(`with_corrections_said_${c.cover}`, F1[i].cover === c.cover || (c.cover === "none" && F1[i].cover === "lead"));
        }
      });
    }
    // Brett's rail horses: every phase before the straight on the rail
    R1.H.forEach((x, i) => {
      const c = corr[race.id][x.h.no] || {};
      const ls = ["settle", "mid", "turn"].filter(ph => R.anchors[ph] != null).map(ph => c[ph] && c[ph].lane);
      if (!ls.length || ls.some(l => l !== 1)) return;
      let mx = 0;
      for (let k = 0; k < R1.K; k++) { const d = D - R1.XD[i][k]; if (d <= R1.anchors.settle && d >= R1.geo.Lh) mx = Math.max(mx, R1.Y[i][k]); }
      addB("brett_rail_horses_max_lane_over_1_5", mx > 1.5 ? 1 : 0);
    });
  }
}
const evAll = data.races.reduce((a, r) => a + (r.events || []).length, 0);
const evPush = data.races.reduce((a, r) => a + (r.events || []).filter(e => e.push).length, 0);
const fmt = s => ({ n: s.n, exact: pct(s.exact, s.n), within_one_lane: pct(s.within1, s.n), mean_signed: (s.sum / s.n).toFixed(2) });
const out = {
  races: data.races.length,
  section_marks_hit: `${marks - markErr}/${marks}`,
  backwards_steps: back, steps_over_20_5_ms: fast,
  displayed_track: {
    section_marks_within_half_a_metre: pct(disp.within05, disp.marks), within_1m: pct(disp.within1, disp.marks),
    mean_deviation_m: (disp.sumDev / disp.marks).toFixed(2), max_deviation_m: disp.maxDev.toFixed(2),
    backwards_steps: disp.back, steps_over_20_5_ms: disp.fast, steps_more_than_2m_behind_curve: pct(disp.lagged, disp.steps),
  },
  recorded_order_vs_sectional_order_within_1: pct(orderAgree.same, orderAgree.n),
  sideways: {
    jumps_over_1_5_lanes_in_1s: `${lat.jumps} of ${lat.steps} horse-steps (${pct(lat.jumps, lat.steps)}); without a stewards' contact ${lat.jumpsNoContact}`,
    max_lanes_per_s: lat.maxVy.toFixed(2), steps_over_1_5_lanes_per_s: pct(lat.fastVy, lat.steps),
    bodies_overlapping: `${lat.overlap} of ${lat.pairs} alongside pairs (${pct(lat.overlap, lat.pairs)})`,
    runs_pushed_over_1_5_lanes_off_a_stated_lane: `${lat.pushedOff} of ${lat.runs} (${pct(lat.pushedOff, lat.runs)})`,
    runs_round_from_the_turn_wider_than_a_stated_lane_by_1_5: `${lat.roundOff} of ${lat.rounds} (on purpose)`,
  },
  stated_lane_honoured: { n: lane.n, exact: pct(lane.exact, lane.n), within_one_lane: pct(lane.within1, lane.n) },
  stewards_contacts: { sentences_placed: evAll, that_move_a_horse: evPush },
  rail_horses_stay_on_rail_until_a_run_round: { n: rail.n, max_lane_le_1_5: pct(rail.under15, rail.n), max_lane_le_2: pct(rail.under2, rail.n) },
  runs_from_the_turn: Object.fromEntries(Object.entries(runs).sort().map(([k, r]) => [k, { runs: r.n, held_2s_plus: pct(r.held2, r.n), comments_say_held: pct(r.said, r.n),
    both: r.both, mean_held_s: (r.sec / r.n).toFixed(1) }])),
  derived_cover_vs_stewards: {
    said_no_cover: { n: cover.nocover.n, replay_no_cover: pct(cover.nocover.none, cover.nocover.n) },
    said_cover: { n: cover.cover.n, replay_cover: pct(cover.cover.cover, cover.cover.n) },
    all_horses: { n: cover.all.n, share_none: (cover.all.none / cover.all.n).toFixed(3), share_cover: (cover.all.cover / cover.all.n).toFixed(3) },
  },
};
if (Object.keys(brett).length) {
  out.brett_corrections = { races: Object.keys(corr).length, lanes: {}, cover: {} };
  for (const k of Object.keys(brett).sort()) {
    if (k.startsWith("brett_rail")) out.brett_corrections.rail_horses_pushed_off_rail = `${brett[k].sum} of ${brett[k].n}`;
    else out.brett_corrections.lanes[k] = fmt(brett[k]);
  }
  for (const k of Object.keys(brettCover).sort()) out.brett_corrections.cover[k] = { n: brettCover[k].n, agree: pct(brettCover[k].agree, brettCover[k].n) };
}
fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
console.log(JSON.stringify(out, null, 1));

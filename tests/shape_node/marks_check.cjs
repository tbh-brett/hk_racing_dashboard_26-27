// Copied from the lab (race-shape-2d/replay/marks_check.js) for tests/test_shape_engine.py (racesim-SPEC §11.1): the engine is the vendored
// one (ENGINE_DIR, web/assets/shape), and every input and output path is given on the command line.
// Brett's marks for a meeting against the replay: how close the engine's guesses were before them, how well the
// replay honours them, and what his runs and traffic say against the engine's own (22 Oct).
//   node replay/marks_check.js <races.json> <marks.json> [out.json]
const fs = require("fs"), path = require("path");
const C = require(path.resolve(process.env.ENGINE_DIR, "engine.js"));
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const cdir = process.argv[3], OUT = process.argv[4];
const corr = {};
for (const v of JSON.parse(fs.readFileSync(cdir, "utf8"))) {
  if (v && v.race && v.horse != null && v.phase) ((corr[v.race] ||= {})[v.horse] ||= {})[v.phase] = v;
}
const cap = l => Math.max(1, Math.min(8, Math.round(l)));
const at = (R, race, d) => C.frame(R, Math.min(...R.H.map(x => C.timeAt(x.tl, race.distance - d))));
const acc = {}, add = (k, e) => { const s = (acc[k] ||= { n: 0, exact: 0, within1: 0, sum: 0, abs: 0 }); s.n++; if (e === 0) s.exact++; if (Math.abs(e) <= 1) s.within1++; s.sum += e; s.abs += Math.abs(e); };
const cov = {}, addC = (k, ok) => { const s = (cov[k] ||= { n: 0, agree: 0 }); s.n++; if (ok) s.agree++; };
const runs = { marked: 0, engine_same_way: 0, engine_no_run: 0, engine_other_way: 0, rows: [] };
const traffic = { held: { n: 0, engine_held: 0 }, clear: { n: 0, engine_held: 0 } };
const worst = [];
const heldSec = (R, i) => { let s = 0; for (let k = 0; k < R.K; k++) { const d = R.race.distance - R.XD[i][k]; if (d < 0) break; if (!R.geo.straight && d < R.geo.Lh && (R.FL[i][k] & C.F_HELD)) s += C.DT; } return s; };
for (const race of data.races) {
  const cr = corr[race.id];
  if (!cr) continue;
  const R0 = C.build(race, null), R1 = C.build(race, cr);
  for (const ph of C.PHASES) {
    const d = R0.anchors[ph];
    if (d == null) continue;
    const F0 = at(R0, race, d), F1 = at(R1, race, d);
    R0.H.forEach((x, i) => {
      const c = (cr[x.h.no] || {})[ph];
      if (!c) return;
      if (c.lane) {
        const e0 = cap(F0[i].lane) - c.lane, e1 = cap(F1[i].lane) - c.lane;
        add(`guess_${ph}`, e0); add(`with_marks_${ph}`, e1);
        if (Math.abs(e1) >= 2) worst.push({ race: race.id, no: x.h.no, name: x.h.name, phase: ph, marked: c.lane, shown: +F1[i].lane.toFixed(1) });
      }
      if (ph !== "straight" && (c.cover === "cover" || c.cover === "none")) {
        const ok = f => f.cover === c.cover || (c.cover === "none" && f.cover === "lead");
        addC(`guess_${ph}_${c.cover}`, ok(F0[i])); addC(`with_marks_${ph}_${c.cover}`, ok(F1[i]));
      }
    });
  }
  R0.H.forEach((x, i) => {
    const c = cr[x.h.no] || {};
    const way = (c.turn && c.turn.run) || (c.straight && c.straight.run);
    if (way === "round" || way === "through") {
      runs.marked++;
      const r = R0.runs[i];
      if (!r || !r.mover || !r.how) runs.engine_no_run++;
      else if (r.how === way) runs.engine_same_way++; else runs.engine_other_way++;
      runs.rows.push({ race: race.id, no: x.h.no, marked: way, engine: r && r.mover ? r.how : "no run", src: r && r.src, p_round: r && r.p != null ? +r.p.toFixed(2) : null });
    }
    const tr = c.straight && c.straight.traffic;
    if (tr === "held" || tr === "clear") { traffic[tr].n++; if (heldSec(R0, i) >= 0.5) traffic[tr].engine_held++; }
  });
}
const fmt = s => ({ n: s.n, exact: +(s.exact / s.n).toFixed(3), within_one: +(s.within1 / s.n).toFixed(3), mean_signed: +(s.sum / s.n).toFixed(2), mean_abs: +(s.abs / s.n).toFixed(2) });
const out = { races: Object.keys(corr), lanes: Object.fromEntries(Object.entries(acc).sort().map(([k, v]) => [k, fmt(v)])),
  cover: Object.fromEntries(Object.entries(cov).sort().map(([k, v]) => [k, { n: v.n, agree: +(v.agree / v.n).toFixed(3) }])),
  runs, traffic, not_honoured: worst };
if (OUT) fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
console.log(JSON.stringify({ ...out, runs: { ...runs, rows: undefined } }, null, 1));
console.log("runs:", runs.rows.map(r => `${r.race.slice(-2)}#${r.no} ${r.marked}/${r.engine}${r.p_round != null ? " p" + r.p_round : ""}`).join("  "));

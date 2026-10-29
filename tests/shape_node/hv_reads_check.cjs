// Brett's reads of Happy Valley videos (29 Oct) against the replay: each race played from its comments, with no
// corrections, and every check scored as the engine has it. For tests/test_shape_engine.py and for calibrating the
// Happy Valley course.
//   ENGINE_DIR=web/assets/shape node hv_reads_check.cjs <races.json> <reads.json> <out.json>
"use strict";
const fs = require("fs"), path = require("path");
const C = require(path.resolve(process.env.ENGINE_DIR, "engine.js"));
const races = JSON.parse(fs.readFileSync(process.argv[2], "utf8")).races;
const reads = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).checks;
const byId = Object.fromEntries(races.map(r => [r.id, r]));
const built = {};
const R = id => (built[id] ||= C.build(byId[id], null));
const laneAt = (Rr, i, d) => {               // the horse's lane as it passes d metres to go
  const D = Rr.race.distance;
  let k = 0; while (k < Rr.K - 1 && D - Rr.XD[i][k] > d) k++;
  return Rr.Y[i][k];
};
const heldS = (Rr, i) => {
  let s = 0;
  for (let k = 0; k < Rr.K; k++) { const d = Rr.race.distance - Rr.XD[i][k]; if (d < 0) break; if (d < Rr.geo.Lh && (Rr.FL[i][k] & C.F_HELD)) s += C.DT; }
  return s;
};
const out = { races: 0, checks: 0, agree: 0, rows: [] };
const seen = new Set();
for (const c of reads) {
  if (!byId[c.race]) continue;
  seen.add(c.race);
  const Rr = R(c.race), idx = n => Rr.H.findIndex(x => x.h.name === n), i = c.horse ? idx(c.horse) : -1;
  let ok, shown;
  if (c.held_none) { const h = Rr.H.map((x, j) => heldS(Rr, j)); ok = Math.max(...h) < 0.5; shown = `longest hold-up ${Math.max(...h).toFixed(1)} s`; }
  else if (i < 0) { ok = false; shown = "horse not found"; }
  else if (c.lane) { const l = laneAt(Rr, i, c.d); ok = l >= c.lane[0] && l <= c.lane[1]; shown = +l.toFixed(1); }
  else if (c.out) { const a = laneAt(Rr, i, c.out[0]), b = laneAt(Rr, i, c.out[1]); ok = b - a >= c.by; shown = `${a.toFixed(1)} -> ${b.toFixed(1)}`; }
  else if (c.inside_of) { const j = idx(c.inside_of), a = laneAt(Rr, i, c.d), b = laneAt(Rr, j, c.d); ok = a < b - 0.3; shown = `${a.toFixed(1)} against ${b.toFixed(1)}`; }
  else if (c.run) { const r = Rr.runs[i]; ok = !!r && r.how === c.run; shown = r ? r.how : null; }
  else if (c.held) { const s = heldS(Rr, i); ok = s >= 0.5; shown = `${s.toFixed(1)} s held`; }
  out.checks++; if (ok) out.agree++;
  out.rows.push({ race: c.race, horse: c.horse || null, d: c.d ?? null, ok, shown, said: c.said });
}
out.races = seen.size;
fs.writeFileSync(process.argv[4], JSON.stringify(out, null, 1));
for (const r of out.rows) console.log(`${r.ok ? "ok  " : "MISS"} ${r.race} ${(r.horse || "").padEnd(18)} ${r.d == null ? "" : r.d + " m"}  engine ${r.shown}  | ${r.said}`);
console.log(`${out.agree} of ${out.checks} checks agree, ${out.races} races`);

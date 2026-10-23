// Copied from the lab (race-sim/engine_traffic_check.js) for tests/test_shape_engine.py (racesim-SPEC §11.1): the engine is the vendored
// one (ENGINE_DIR, web/assets/shape), and every input and output path is given on the command line.
// On this season's real races: does the replay engine's "no way through" in the straight match the comments' held up?
//   node engine_traffic_check.js [../race-shape-2d/replay/races.json]  -> out/engine_traffic_check.json
const fs = require("fs"), path = require("path");
const C = require(path.resolve(process.env.ENGINE_DIR, "engine.js"));
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const rows = [];
for (const race of data.races) {
  const R = C.build(race, null, { said: false }), D = race.distance;            // the engine's own traffic, not the comments'
  if (R.geo.straight) continue;
  R.H.forEach((x, i) => {
    let held = 0;
    for (let q = 0; q < R.K; q++) {
      const d = D - R.XD[i][q];
      if (d < 0) break;
      if (d < R.geo.Lh && (R.FL[i][q] & 1)) held += C.DT;
    }
    if (x.h.cor) rows.push({ held, said: x.h.p.traffic_straight ? 1 : 0 });
  });
}
const pos = rows.filter(x => x.said).map(x => x.held), neg = rows.filter(x => !x.said).map(x => x.held);
let auc = 0; for (const p of pos) for (const q of neg) auc += p > q ? 1 : p === q ? 0.5 : 0; auc /= pos.length * neg.length;
const at = th => rows.filter(x => x.held >= th).length / rows.length;
const out = { races: data.races.length, runs_with_comment: rows.length, comments_say_held_in_straight: +(pos.length / rows.length).toFixed(4),
  engine_no_way_through_0_5s: +at(0.5).toFixed(4), engine_no_way_through_5s: +at(5).toFixed(4), auc: +auc.toFixed(3),
  note: "Comments on Running and the stewards' report (the replay's parse); the engine run with no corrections and without the comments' held-ups (since 18 Oct the replay plays those)." };
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 1));
console.log(JSON.stringify(out));

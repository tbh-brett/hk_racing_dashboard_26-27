/* Race simulator v0 (lab, 16 Oct 2026). Pure functions: runs in node and in the page (window.SimCore).
   A simulated race comes out in the same shape as a replayed one, so ReplayCore steers and draws it unchanged.

   One simulated race:
   1. Clock. A real race at the same course and trip, of a similar class, lends its leader's time at each section
      mark, its spread of lengths behind the leader by rank at each mark, and its pace (a template).
   2. Start. A horse may start slowly, at its own rate in the comments, shrunk towards the population's.
   3. Settle order at the first call. The study's predicted early position plus noise, the noise set so the chance of
      leading matches the study's P(lead). A slow start drops a horse back by the measured amount.
   4. Lanes and cover. Settling lane from the study's expected lane; no cover from its P(no cover); the leader takes
      the rail; a horse coming from well back may go a lane wider on the turn.
   5. Finish. Performance in lengths = ability + noise - what this trip cost: the 16 Oct study's Part A3 costs for
      lane, no cover and early position x pace, plus a slow start and being held up in the straight (both fitted).
      Abilities are set so the simulated win chances equal production's v2: v2 decides how good each horse is,
      the simulation decides how the race is run and what that does to the result.
   6. Middle calls. Positions between the first call and the finish (fitted), times from the template's gaps by rank.
   7. Contacts. The stewards' rate per runner by part of the race (this season), between neighbours. No cost in v0.
*/
(function (root) {
  "use strict";
  const LEN = 2.4;
  const PH = ["start", "mid", "straight"];

  // ---------------------------------------------------------------- random numbers (seeded, repeatable)
  function rng(seed) {
    let a = seed >>> 0;
    const u = () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    let spare = null;
    const n = () => { if (spare !== null) { const s = spare; spare = null; return s; } let x, y, r; do { x = 2 * u() - 1; y = 2 * u() - 1; r = x * x + y * y; } while (!r || r >= 1); const f = Math.sqrt(-2 * Math.log(r) / r); spare = y * f; return x * f; };
    return { u, n };
  }
  const hash = s => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };

  const clsNum = c => /^G/.test(String(c)) ? 0.5 : isFinite(+c) ? +c : 3.5;
  const laneCat = l => (l <= 1 ? null : l === 2 ? "2" : l === 3 ? "3" : "4");
  const sorted = (arr, key) => arr.map((v, i) => [key(v, i), i]).sort((a, b) => a[0] - b[0]).map(x => x[1]);

  /** Templates for a race: same course and trip, class within one; widened if there are too few. */
  function pickTemplates(T, race, max = 120) {
    const all = (T.templates[race.template_key] || []).filter(t => t.gaps.length === race.sections.length);
    const c = clsNum(race.cls);
    for (const w of [1, 1.5, 2.5, 9]) {
      const sel = all.filter(t => Math.abs(clsNum(t.cls) - c) <= w);
      if (sel.length >= 40 || w === 9) return sel.slice(-max);
    }
    return all.slice(-max);
  }

  /** Lengths behind the leader for rank r (1-based) of n, read from a template's gaps at mark k. */
  function gapAt(tp, k, r, n) {
    const g = tp.gaps[k], m = g.length;
    if (n <= 1) return 0;
    const x = (r - 1) * (m - 1) / (n - 1), i = Math.floor(x), f = x - i;
    return i >= m - 1 ? g[m - 1] : g[i] + (g[i + 1] - g[i]) * f;
  }

  const SETTLE_MAE = 0.197;      // the study's settle model on 26/27: mean miss, as a share of the field

  /** The leader from the study's P(lead) (among horses that began on terms); the rest by predicted early position
   *  plus noise. Returns the order (ranks, 1 = in front). */
  function settleOrder(h, slow, d, sd, extra) {
    const n = h.length;
    let tot = 0;
    for (let i = 0; i < n; i++) if (!slow[i]) tot += h[i].p_lead;
    let leader = -1;
    if (tot > 0) { let x = d.lead * tot; for (let i = 0; i < n; i++) { if (slow[i]) continue; x -= h[i].p_lead; if (x <= 0) { leader = i; break; } } }
    if (leader < 0) { let bv = Infinity; for (let i = 0; i < n; i++) { const v = h[i].pred_early + (slow[i] ? extra : 0); if (v < bv) { bv = v; leader = i; } } }
    const score = h.map((x, i) => i === leader ? -Infinity : x.pred_early + sd * d.settle[i] + (slow[i] ? extra : 0));
    const rank = new Array(n); sorted(score, v => v).forEach((i, k) => { rank[i] = k + 1; });
    return rank;
  }

  const SD_GRID = [0.05, 0.08, 0.11, 0.14, 0.17, 0.2, 0.24, 0.28, 0.33, 0.4, 0.5];

  /** How far the simulated early positions miss the prediction, for each noise level on the grid. */
  function settleMiss(race, T, sims = 1500) {
    const h = race.runners, n = h.length, extra = T.slow.early_frac_extra || 0.12, R = hash(race.id);
    return SD_GRID.map(sd => {
      const r = rng(R ^ 0x5e771e);
      let err = 0;
      for (let s = 0; s < sims; s++) {
        const d = draws(r, n, 1);
        const slow = h.map((x, i) => d.slow[i] < x.slow_rate);
        const rank = settleOrder(h, slow, d, sd, extra);
        for (let i = 0; i < n; i++) err += Math.abs((rank[i] - 1) / Math.max(1, n - 1) - h[i].pred_early);
      }
      return err / (sims * n);
    });
  }

  /** One noise level for the whole card, set so the simulated early positions miss the prediction on average by as
   *  much as the settle model does in real races (a race with a clear early order then misses by less). */
  function settleSpread(races, T) {
    const miss = races.map(r => settleMiss(r, T));
    let best = 0.2, bestGap = Infinity, at = null;
    SD_GRID.forEach((sd, k) => {
      const m = miss.reduce((a, x) => a + x[k], 0) / miss.length, gap = Math.abs(m - SETTLE_MAE);
      if (gap < bestGap) { bestGap = gap; best = sd; at = m; }
    });
    return { sd: best, miss: at };
  }

  /** Weight and noise for a middle call, by its distance from the post. */
  function callW(T, togo) {
    const b = T.calls.by_togo, key = String(Math.min(1600, Math.max(400, Math.round(togo / 400) * 400)));
    return b && b[key] ? b[key] : { w: Math.pow(Math.max(0, 1 - togo / 2000), T.calls.power), sd: T.calls.noise_sd };
  }

  function heldRate(T, turnFrac, field) {
    const tb = T.traffic.turn_bins, fb = T.traffic.field_bins;
    let a = 0; while (a < tb.length - 2 && turnFrac > tb[a + 1]) a++;
    let b = 0; while (b < fb.length - 2 && field > fb[b + 1]) b++;
    const v = T.traffic.rate[`${a}_${b}`];
    return v == null ? T.traffic.base : v;
  }

  /** One simulated race's draws for every horse, from a seeded stream (common random numbers for the anchor). */
  function draws(r, n, nT) {
    const d = { tp: Math.floor(r.u() * nT), lead: r.u(), slow: [], settle: [], lane: [], nc: [], wide: [], perf: [], held: [], mid: [], ev: [] };
    for (let i = 0; i < n; i++) {
      d.slow.push(r.u()); d.settle.push(r.n()); d.lane.push(r.n()); d.nc.push(r.u()); d.wide.push(r.u());
      d.perf.push(r.n()); d.held.push(r.u()); d.mid.push([r.n(), r.n(), r.n(), r.n(), r.n()]);
      d.ev.push([r.u(), r.u(), r.u(), r.u(), r.u(), r.u()]);
    }
    return d;
  }

  /** The race's shape and result for one set of draws, given abilities a (lengths). */
  function shape(ctx, a, d) {
    const { race, T, tps, sd, C } = ctx, h = race.runners, n = h.length, tp = tps[d.tp];
    const tempo = tp.tempo == null ? 0 : tp.tempo;
    const slow = h.map((x, i) => d.slow[i] < x.slow_rate);
    const extra = (T.slow.early_frac_extra || 0.12);
    const eRank = settleOrder(h, slow, d, sd, extra);
    const eFrac = eRank.map(r => (r - 1) / Math.max(1, n - 1));
    const lane = h.map((x, i) => eRank[i] === 1 ? 1 : Math.max(1, Math.min(6, Math.round(x.exp_lane + 0.65 * d.lane[i]))));
    const nc = h.map((x, i) => eRank[i] > 1 && d.nc[i] < x.p_nocover);
    const cLane = new Array(n).fill(0), cHeld = new Array(n).fill(0);
    const base = h.map((x, i) => {
      let c = 0;
      const sc = laneCat(lane[i]); if (sc) { c += C.settle[sc] || 0; cLane[i] += C.settle[sc] || 0; }
      if (nc[i]) c += C.nocover || 0;
      c += (C.early || 0) * eFrac[i] + (C.early_tempo || 0) * eFrac[i] * tempo;
      if (slow[i]) c += T.slow.cost_lengths[0] || 0;
      return c;
    });
    // provisional finish, then who was held up in the straight (by position turning in), then the finish
    const perf0 = h.map((x, i) => a[i] + ctx.sigma * d.perf[i] - base[i]);
    const fRank0 = new Array(n); sorted(perf0, v => -v).forEach((i, k) => { fRank0[i] = k + 1; });
    const nm = race.sections.length;
    const turnK = nm - 2;                                     // the call before the straight
    const wT = turnK >= 1 ? callW(T, race.distance - ctx.s[turnK]).w : 1;
    const turnFrac = h.map((x, i) => (1 - wT) * eFrac[i] + wT * (fRank0[i] - 1) / Math.max(1, n - 1));
    const held = h.map((x, i) => !ctx.straightCourse && d.held[i] < heldRate(T, turnFrac[i], n));
    const turnLane = h.map((x, i) => {
      if (eRank[i] === 1) return 1;
      const coming = eRank[i] - fRank0[i];
      return lane[i] + (coming >= 3 && d.wide[i] < 0.5 ? 1 : 0);
    });
    const cost = h.map((x, i) => {
      let c = base[i];
      const tc = laneCat(turnLane[i]); if (tc) { c += C.turn[tc] || 0; cLane[i] += C.turn[tc] || 0; }
      if (held[i]) { c += T.traffic.cost_lengths[0] || 0; cHeld[i] = T.traffic.cost_lengths[0] || 0; }
      return c;
    });
    const perf = h.map((x, i) => a[i] + ctx.sigma * d.perf[i] - cost[i]);
    const fRank = new Array(n); sorted(perf, v => -v).forEach((i, k) => { fRank[i] = k + 1; });
    return { tp, tempo, slow, eRank, eFrac, lane, nc, held, turnLane, cost, cLane, cHeld, perf, fRank, eps: d.perf };
  }

  function context(race, T, seedBase, sd, tpsGiven) {
    const tps = tpsGiven || pickTemplates(T, race);
    const L = race.sections, s = []; L.reduce((acc, x) => { s.push(acc + x); return acc + x; }, 0);
    const D = race.distance, u = s.map(x => (x - s[0]) / Math.max(1, D - s[0]));
    const R = hash(race.id) ^ (seedBase || 0);
    const C = T.costs[race.cost_group] || T.costs["ST 1200"];
    return { race, T, tps, C, s, u, R, sd: sd == null ? settleSpread([race], T).sd : sd, sigma: T.noise.sd_lengths * 0.95,
      straightCourse: race.venue === "ST" && race.surface === "Turf" && D === 1000 };
  }

  /** Abilities (lengths) so the simulated win chances match v2. Common random numbers across passes. */
  function anchor(ctx, sims = 3000, passes = 45) {
    const h = ctx.race.runners, n = h.length;
    const r = rng(ctx.R ^ 0xa5c0), D = [];
    for (let s = 0; s < sims; s++) D.push(draws(r, n, ctx.tps.length));
    const tgt = h.map(x => Math.max(1e-4, x.p_win));
    const sum = tgt.reduce((x, y) => x + y, 0);
    const p = tgt.map(x => x / sum);
    let a = p.map(x => ctx.sigma * 0.6 * Math.log(x * n));
    let err = 1;
    for (let it = 0; it < passes; it++) {
      const w = new Array(n).fill(0);
      for (const d of D) { const S = shape(ctx, a, d); w[S.fRank.indexOf(1)]++; }
      const q = w.map(x => Math.max(x, 0.5) / sims);
      err = Math.max(...p.map((x, i) => Math.abs(x - w[i] / sims)));
      a = a.map((x, i) => x + 0.55 * ctx.sigma * 0.5 * (Math.log(p[i]) - Math.log(q[i])));
      const m = a.reduce((x, y) => x + y, 0) / n; a = a.map(x => x - m);
    }
    return { a, maxErr: err, sims, passes };
  }

  /** Many races: chances and trip shares per horse (no drawing). */
  function summarise(ctx, a, sims = 20000, seed = 1) {
    const h = ctx.race.runners, n = h.length, r = rng(ctx.R ^ (0x51ed + seed));
    const z = () => new Array(n).fill(0);
    const S = { win: z(), top3: z(), lead: z(), front: z(), mid: z(), back: z(), slow: z(), nc: z(), wide3: z(), held: z(),
      cost: z(), cLane: z(), cHeld: z(), winLed: z(), winHeld: z(), winNc: z(), pos1: z() };
    const H1 = h.map(() => new Array(n).fill(0)), HF = h.map(() => new Array(n).fill(0));   // first-call and finishing positions
    const band = {};
    for (let s = 0; s < sims; s++) {
      const d = draws(r, n, ctx.tps.length), X = shape(ctx, a, d);
      const b = X.tp.band || "unknown"; band[b] = (band[b] || 0) + 1;
      for (let i = 0; i < n; i++) {
        const won = X.fRank[i] === 1;
        if (won) S.win[i]++;
        if (X.fRank[i] <= 3) S.top3[i]++;
        if (X.eRank[i] === 1) { S.lead[i]++; if (won) S.winLed[i]++; }
        const ef = X.eFrac[i]; if (ef < 1 / 3) S.front[i]++; else if (ef < 2 / 3) S.mid[i]++; else S.back[i]++;
        if (X.slow[i]) S.slow[i]++;
        if (X.nc[i]) { S.nc[i]++; if (won) S.winNc[i]++; }
        if (X.lane[i] >= 3) S.wide3[i]++;
        if (X.held[i]) { S.held[i]++; if (won) S.winHeld[i]++; }
        S.cost[i] += X.cost[i]; S.cLane[i] += X.cLane[i]; S.cHeld[i] += X.cHeld[i]; S.pos1[i] += X.eRank[i];
        H1[i][X.eRank[i] - 1]++; HF[i][X.fRank[i] - 1]++;
      }
    }
    const f = v => v / sims;
    const runners = h.map((x, i) => ({
      no: x.no, v2_win: x.p_win, v2_place: x.p_place, win: f(S.win[i]), top3: f(S.top3[i]), lead: f(S.lead[i]),
      front: f(S.front[i]), mid: f(S.mid[i]), back: f(S.back[i]), first_call_pos: S.pos1[i] / sims,
      slow: f(S.slow[i]), nocover: f(S.nc[i]), wide3: f(S.wide3[i]), held: f(S.held[i]), trip_cost: S.cost[i] / sims,
      cost_lane: S.cLane[i] / sims, cost_held: S.cHeld[i] / sims,
      win_if_led: S.lead[i] ? S.winLed[i] / S.lead[i] : null, win_if_held: S.held[i] ? S.winHeld[i] / S.held[i] : null,
      win_if_nocover: S.nc[i] ? S.winNc[i] / S.nc[i] : null, led_n: S.lead[i], held_n: S.held[i], nc_n: S.nc[i],
      first_call_dist: H1[i].map(v => +(v / sims).toFixed(4)), finish_dist: HF[i].map(v => +(v / sims).toFixed(4)) }));
    for (const k of Object.keys(band)) band[k] /= sims;
    return { sims, runners, pace: band, settle_sd: ctx.sd, templates: ctx.tps.length };
  }

  /** The leader's time at each mark (cumulative): the template's pace against its own class standard and day (its
   *  ratio, 20 Oct), put on this race's standard for a standard day. Without a standard, the template's times as run. */
  function clockOf(rc, tp) {
    if (tp.ratio && rc.par && rc.par.length === tp.ratio.length) {
      let c = 0;
      return rc.par.map((x, k) => { c += x; return c * tp.ratio[k]; });
    }
    return tp.lead;
  }

  /** The race as each horse would run it before its trip: ability and noise, less the costs the engine doesn't play
   *  (no cover, early position against the pace, a slow start) and its average lane and held-up cost (K.md). The
   *  animated trip then decides the rest (coupled, 20 Oct). */
  function untripped(ctx, a, X, K) {
    const n = X.perf.length;
    const perf = X.perf.map((v, i) => a[i] + K.sigma * X.eps[i] - (X.cost[i] - X.cLane[i] - X.cHeld[i]) - K.md[i]);
    const fRank = new Array(n); sorted(perf, v => -v).forEach((i, k) => { fRank[i] = k + 1; });
    return { ...X, perf, fRank };
  }

  /** One simulated race, built as a replay race (sections, times, calls, lane plan, contacts). With K (the coupling,
   *  20 Oct), the finish is the untripped one, for the engine to steer and the trip to decide. */
  function race(ctx, a, seed, K) {
    const { race: rc, T, s } = ctx, h = rc.runners, n = h.length, nm = rc.sections.length;
    const r = rng(ctx.R ^ (0xc0ffee + seed * 7919));
    const d = draws(r, n, ctx.tps.length), X0 = shape(ctx, a, d), X = K ? untripped(ctx, a, X0, K) : X0, tp = X.tp;
    const p = T.calls.power, Lh = rc.venue === "HV" ? 310 : (rc.surface === "AWT" ? 380 : 430), D = rc.distance;
    // ranks at every mark: the first call, the finish, and the calls between
    const ranks = [];
    for (let k = 0; k < nm; k++) {
      if (k === 0) { ranks.push(X.eRank.slice()); continue; }
      if (k === nm - 1) { ranks.push(X.fRank.slice()); continue; }
      const cw = callW(T, D - s[k]), w = cw.w, sdk = cw.sd;
      const sc = h.map((x, i) => (1 - w) * X.eFrac[i] + w * (X.fRank[i] - 1) / Math.max(1, n - 1) + sdk * d.mid[i][k % 5]);
      const rk = new Array(n); sorted(sc, v => v).forEach((i, q) => { rk[i] = q + 1; });
      ranks.push(rk);
    }
    // times: the leader at each mark (the template's pace on this race's standard) plus the lengths behind for the rank;
    // the finish from the margins
    const clock = clockOf(rc, tp);
    const best = Math.max(...X.perf);
    const cum = h.map(() => new Array(nm).fill(0));
    for (let k = 0; k < nm; k++) {
      const v = s[k] / clock[k];
      for (let i = 0; i < n; i++) {
        const gap = k === nm - 1 ? best - X.perf[i] : gapAt(tp, k, ranks[k][i], n);
        cum[i][k] = clock[k] + gap * LEN / v;
      }
    }
    // keep every section believable: no faster than 92% or slower than 140% of the leader's
    for (let i = 0; i < n; i++) {
      for (let k = 1; k < nm; k++) {
        const lead = clock[k] - clock[k - 1];
        const sec = cum[i][k] - cum[i][k - 1];
        if (sec < 0.92 * lead) cum[i][k] = cum[i][k - 1] + 0.92 * lead;
        if (sec > 1.4 * lead) cum[i][k] = cum[i][k - 1] + 1.4 * lead;
      }
    }
    const secs = cum.map(c => c.map((t, k) => +(k ? t - c[k - 1] : t).toFixed(2)));
    const pos = [];
    for (let k = 0; k < nm; k++) { const rk = new Array(n); sorted(cum.map(c => c[k]), v => v).forEach((i, q) => { rk[i] = q + 1; }); pos.push(rk); }
    const finOrder = pos[nm - 1];                 // the order the times give (a section clamp can swap a close pair)
    const runners = h.map((x, i) => {
      const fin = cum[i][nm - 1];
      const straight = { lane: null, src: null, nocover: 0, cover: 0, keen: 0 };   // the engine plans the straight (a held horse: behind one, then a gap)
      const ph = (lane, extra) => ({ lane, src: "sim", nocover: X.nc[i] ? 1 : 0, cover: X.nc[i] || X.eRank[i] === 1 ? 0 : 1, keen: 0, ...extra });
      return {
        no: x.no, name: x.name, draw: x.draw, jockey: x.jockey, trainer: x.trainer, wt: x.wt, odds: null,
        place: finOrder[i], code: null, finish: +fin.toFixed(2), lbw: finOrder[i] === 1 ? null : +((fin - Math.min(...cum.map(c => c[nm - 1]))) * (s[nm - 1] / clock[nm - 1]) / LEN).toFixed(2),
        secs: secs[i], pos: pos.map(rk => rk[i]), cor: null, inc: null,
        p: { settle: ph(X.lane[i]), mid: ph(X.lane[i]), turn: ph(X.turnLane[i]), straight, slow: X.slow[i] ? 1 : 0,
          traffic_pre: 0, traffic_straight: X.held[i] ? 1 : 0, led: X.eRank[i] === 1 ? 1 : 0,
          at: [],
          // a horse the simulation holds up in the straight was coming through (the engine decides the others' way); the
          // engine sits it in behind the horse in front and lets it out at the first gap (18 Oct; it used to hold its turn
          // lane to 150 m and step out 1.5 lanes, and was shown held in under half of those races)
          ...(X.held[i] ? { run: { how: "through", src: "sim" } } : {}) },
        sim: { slow: X.slow[i], held: X.held[i], nocover: X.nc[i], lane: X.lane[i], turnLane: X.turnLane[i], cost: +X.cost[i].toFixed(2),
          perf: X.perf[i], eps: X.eps[i], cLane: X.cLane[i], cHeld: X.cHeld[i], costAll: X.cost[i] },
      };
    });
    // contacts between neighbours, at the stewards' rate for each part of the race
    const events = [], cr = T.contacts.per_runner, outShare = T.contacts.direction.out / Math.max(1, T.contacts.direction.out + T.contacts.direction.in);
    const byDraw = sorted(h, x => x.draw), byE = sorted(X.eRank, v => v), byF = sorted(X.fRank, v => v);
    h.forEach((x, i) => {
      const e = d.ev[i];
      for (const [q, ph] of PH.entries()) {
        if (e[q] >= (cr[ph] || 0)) continue;
        if (ph === "straight" && ctx.straightCourse) continue;
        const dir = e[3] < outShare ? 1 : -1;
        const order = ph === "start" ? byDraw : ph === "mid" ? byE : byF;
        const at = order.indexOf(i), j = order[at + (ph === "start" ? dir : (e[4] < 0.5 ? 1 : -1))];
        if (j == null) continue;
        const dd = ph === "start" ? D - 15 - Math.round(e[5] * 60) : ph === "mid" ? Math.round(Lh + 60 + e[5] * Math.max(0, D - 260 - Lh - 60)) : Math.round(60 + e[5] * (Lh - 100));
        events.push({ d: dd, a: x.no, b: h[j].no, dir, push: 1, kind: dir > 0 ? "shifted out" : "shifted in", by: x.no,
          text: `Simulated: ${dir > 0 ? "shifts out" : "shifts in"} into #${h[j].no}, at the stewards' rate for this part of the race.` });
      }
    });
    return { id: `${rc.id}~sim${seed}`, date: rc.date, no: rc.no, venue: rc.venue, course: rc.course, surface: rc.surface, going: rc.going,
      distance: D, cls: rc.cls, name: rc.name, sections: rc.sections, runners, events: events.sort((x, y) => y.d - x.d), dropped: [],
      sim: { seed, template: tp.id, pace: tp.band, tempo: X.tempo, clock: clock.map(x => +x.toFixed(3)) } };
  }

  // ---------------------------------------------------------------- the animated trip decides the finish (20 Oct)
  /** What the steered trip cost each horse: ground lost on the bends (lengths, against the rail, from the engine) and
   *  what a hold-up in the straight took off it (lengths: its own pace less the pace of the horse it was stuck behind,
   *  while it was held). Core: the replay engine (ReplayCore). */
  function tripOf(R, Core) {
    const D = R.race.distance, zone = R.geo.straight ? 400 : R.geo.Lh, out = [];
    for (let i = 0; i < R.H.length; i++) {
      let heldM = 0, heldS = 0;
      for (let k = 1; k < R.K; k++) {
        const d = D - R.XD[i][k];
        if (d < 0) break;
        if (d >= zone || !R.AH) continue;
        const b = R.AH[i][k];
        if (b < 0) continue;
        heldS += Core.DT;
        heldM += Math.max(0, (R.X[i][k] - R.X[i][k - 1]) - (R.XD[b][k] - R.XD[b][k - 1]));
      }
      out.push({ ground: R.summary[i].groundLengths, heldL: heldM / LEN, heldS });
    }
    return out;
  }

  /** The finish again from new performances (lengths, one per runner): the race to the last mark as it was, the last
   *  section re-timed from the new margins (and kept between 92% and 140% of the leader's, as when it was built). */
  function retime(sr, perf) {
    const nm = sr.sections.length, D = sr.distance, clock = sr.sim.clock, n = sr.runners.length;
    const v = D / clock[nm - 1], best = Math.max(...perf), lastLead = clock[nm - 1] - clock[nm - 2];
    const prev = sr.runners.map(h => h.secs.slice(0, nm - 1).reduce((x, y) => x + y, 0));
    const fin = sr.runners.map((h, i) => {
      const f = clock[nm - 1] + (best - perf[i]) * LEN / v;
      return Math.max(prev[i] + 0.92 * lastLead, Math.min(prev[i] + 1.4 * lastLead, f));
    });
    const order = sorted(fin, x => x), place = new Array(n); order.forEach((i, q) => { place[i] = q + 1; });
    const f0 = Math.min(...fin);
    const runners = sr.runners.map((h, i) => {
      const secs = h.secs.slice(); secs[nm - 1] = +(fin[i] - prev[i]).toFixed(2);
      const pos = h.pos.slice(); pos[nm - 1] = place[i];
      return { ...h, secs, pos, place: place[i], finish: +fin[i].toFixed(2), lbw: place[i] === 1 ? null : +((fin[i] - f0) * v / LEN).toFixed(2),
        p: { ...h.p }, sim: { ...h.sim } };
    });
    return { ...sr, runners };
  }

  /** One run whose finish is decided by its animated trip. A first steer of the generated race gives each horse's trip
   *  (ground, hold-ups); its finish then swaps the drawn lane and held-up costs for that trip, each measured against the
   *  horse's own average trip (K: mg, mh = the engine's average ground and hold-up cost for the horse; md = the drawn
   *  costs' average; sigma = the finishing noise, trimmed so the spread of results stays as anchored). Over many runs
   *  every horse keeps its v2 chance; within a run, a wide trip or a hold-up costs what it cost. The last 400 m is
   *  re-timed and steered again, with the horses held up the first time held up again. */
  function coupled(ctx, a, seed, Core, K) {
    const sr = race(ctx, a, seed, K);
    const R1 = Core.build(sr, null);
    const trip = tripOf(R1, Core);
    if (!K || !K.mg) return { sr, R: R1, trip, coupled: false };
    const perf = sr.runners.map((h, i) => h.sim.perf - (trip[i].ground - K.mg[i]) - (trip[i].heldL - K.mh[i]));
    const sr2 = retime(sr, perf);
    sr2.runners.forEach((h, i) => {
      const r1 = R1.runs ? R1.runs[i] : null;
      const held = trip[i].heldS >= 0.5;
      h.sim.held = held; h.sim.trip = trip[i]; h.sim.place0 = sr.runners[i].place;
      h.sim.tripCost = (trip[i].ground - K.mg[i]) + (trip[i].heldL - K.mh[i]);
      h.p.traffic_straight = held ? 1 : 0;
      if (r1 && r1.mover && r1.how) h.p.run = { how: r1.how, src: "sim" };          // the way it went the first time
      else if (held) h.p.run = { how: "through", src: "sim" };
      else delete h.p.run;
    });
    sr2.sim = { ...sr.sim, coupled: true };
    const R2 = Core.build(sr2, null);
    return { sr: sr2, R: R2, trip, R1, sr1: sr, coupled: true };
  }

  const api = { rng, hash, pickTemplates, settleSpread, settleMiss, settleOrder, callW, context, anchor, summarise, race, shape, draws, SETTLE_MAE,
    clockOf, tripOf, retime, coupled, untripped };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SimCore = api;
})(typeof window !== "undefined" ? window : globalThis);

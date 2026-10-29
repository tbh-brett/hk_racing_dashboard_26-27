/* Race replay core, v3 (18 Oct; v2 of 16 Oct, after Brett's video corrections, is replay_core_v2.js). Pure
   functions, no DOM: runs in the page (window.ReplayCore) and in node.

   v3, from Brett (18 Oct): horses were forced onto their lanes and leant on horses already there; and from the
   turn, a midfielder or closer with momentum goes round for a clear run, while one on the rail or one out may
   save ground and come between horses, at the risk of traffic. So:
     - no leaning: a lane taken by a horse alongside is waited for, a little short of it, not pushed into; a
       horse behind (from its quarters back) doesn't push the one in front sideways; settling lanes the
       simulator draws are packed so two horses aren't sent for one spot;
     - the run: who makes one, which way (Brett's mark, the comment, else a model of the comments), from where;
       round goes out past slower horses on the turn, through holds its line;
     - foresight: closing speeds over the next 1.5 s (2.8 s on a run) from both horses' curves, so a horse
       ahead that is itself making ground but is slower still blocks;
     - the straight planned: one line each, a horse's width from those it will meet; one with no free line
       waits for a gap and takes it when it opens; a horse that switches keeps its new line.

   Along the track (unchanged from v1): each horse's distance from the start over time, from its own
   sectional times. The first section starts from a standing start; later sections use a monotone
   cubic through the section marks, so no horse ever moves backwards.

   Across the track: a lateral position in lanes (1 = the rail), continuous, stepped every 0.1 s.
   Each horse steers towards the lane its plan gives for that point of the race, at a limited
   sideways speed. v1 packed lanes afresh every 25 m, so a blocked horse could jump four lanes
   (Kempes, Le Zonda). Two rules replace that:
     - bodies don't overlap: a horse alongside another can't move through it, so a horse caught
       wide stays wide until it is a length clear, and only then crosses;
     - a horse closing on one directly ahead must find room inside or outside, or it is held up.
   The plan: Brett's correction for a phase if there is one; else the lane the comments give, at the
   phase or at the distance they name ("from 700M", "into the straight"); else free (cross towards the
   rail leaving the gates, then hold; fan out in the straight).
   Contact: only where the stewards' report says one horse shifted into or bumped another. There the
   shifting horse may move into the other, which is pushed, and the replay marks it.

   Cover: a horse directly in front (within 0.75 of a lane), within three lengths, from the layout.
   Three lengths, not two: on Brett's six corrected races it matched his video calls far better. */
(function (root) {
  "use strict";
  const LEN = 2.4;              // metres per length
  const LANE_M = 1.0;           // metres per lane, for ground lost on the bends
  const DT = 0.1;               // seconds per step
  const BODY = 2.3;             // m: nearer than this along the track, two horses are alongside
  const SIDE = 0.9;             // lanes between the centres of two horses fully alongside
  const COVER_M = 7.2;          // m: a horse this close directly ahead gives cover (three lengths)
  const COVER_DY = 0.75;        // lanes: "directly" ahead (the two bodies overlap, seen from behind)
  const GATE_W = 0.9;           // lanes per starting stall
  const TAU = 1.4;              // s: standing-start pick-up
  const AMAX = 2.5;             // lanes/s/s: sideways acceleration
  const VFORCE = 2.0;           // lanes/s: the fastest a horse is pushed sideways
  const LAG_MAX = 3.0;          // m: a horse may ease this far behind its sectional curve to tuck in or wait
  const LAG_RUN = 5.0;          // m: two lengths, for a horse making its run that has to come off a horse to get out
  const EASE = 1.2;             // m/s: how fast it can ease back
  const CATCH = 0.6;            // m/s: how fast it makes the ground back (1.5 in the 150 m before a section mark)
  const PHASES = ["settle", "mid", "turn", "straight"];
  const F_HELD = 1, F_SQUEEZED = 2, F_CONTACT = 4, F_SWITCH = 8, F_ROUND = 16, F_WAIT = 32;
  const PUSH = 0.8, TOUCH = 0.55;          // a stewards' shift: how far at most, and how close it comes to the horse it shifts onto
  // v3 (18 Oct): how far ahead a horse plans, in seconds of closing; one making its run plans further
  const H_BASE = 1.5, H_RUN = 2.8;
  const LOOK_ROUND = 20;        // m: a horse going round on the turn clears every slower horse this far ahead
  const ROUND_CLEAR = 0.1;      // lanes: and sits this far outside the widest of them (a horse's width and 10 cm)
  const FAN = [0.5, 1.4];       // lanes the field spreads in the straight: near the lead, the rest (v2's, set on Brett's straight lanes)
  const ST_WIN = { none: 2.5, through: 2.8, round: 2.5 };     // lanes: a horse's line for the straight stays this far out of its entry lane
  const ST_IN = 0.6;                                          // ... and this far in
  const ST_OFF = { through: 1.6, round: 0 };                  // lanes off its entry lane a horse making its run wants (round: and outside those it passes)
  const LANE_MAX = 9;
  const GAP = 0.1;              // lanes: a horse waiting for a lane stops this far short of the one in it (about 10 cm)
  const PACK_EASE = 1.5;        // m: settling, a horse just behind one in its lane eases this much to sit in behind it
  const TAIL = 0.8;             // bodies: a horse further back than this is behind, not alongside; the one in front keeps its lane
  const CLIP = 0.5;             // ... while that horse is within half a length of it (alongside, not at its quarters)

  /* The run from the turn (v3, 18 Oct). Brett: a midfielder or closer with momentum starts its run on the turn and goes
     round for a clear run, covering more ground; one settled on the rail (or one out) may save ground and wait for a gap
     between runners, at the risk of traffic. A horse in front that is itself making ground still blocks a quicker one.
     Which way: Brett's mark first, then the comment (turns/runway.py), else this model of the Comments on Running
     (turns/run_choice.py, fitted on 2025-26 and 26/27: lane before the run, position, trip; AUC 0.85 on 26/27 fitted on
     2025-26. The jockey, finishing style, momentum, bunching and pace add 0.006 between them out of sample, so they are
     left out). When: the trip's start points for each way, from the comments ("angled out 500M", "improved from 600M"),
     the middle half. Only horses that make ground (2+ places from the call before the run to the post) make a run.
     Labels second version, 18 Oct: the first read 'shifted out' and '300m out' as going round. */
  const RUN_MODEL = { b0: -1.593, pos: 0.4899, lane: { rail: 0, one: 2.5893, wide: 4.1635 },
    trip: { "AWT": 0.4629, "HV 1000-1200": -0.0534, "HV 1650+": 0.5882, "ST 1200": -0.1629, "ST 1400": 0, "ST 1600-1800": 0.2417, "ST 2000+": 0.8686 } };
  const RUN_START = {                                                     // metres to go, quartiles of the comments' start points
    "AWT": { round: [500, 600], through: [250, 400] }, "HV 1000-1200": { round: [350, 450], through: [150, 300] },
    "HV 1650+": { round: [400, 550], through: [200, 400] }, "ST 1200": { round: [500, 550], through: [300, 450] },
    "ST 1400": { round: [500, 600], through: [300, 450] }, "ST 1600-1800": { round: [500, 600], through: [250, 450] },
    "ST 2000+": { round: [500, 700], through: [200, 450] } };           // ST 2000+ through: 15 named, the pooled quartiles
  /* Carried wide round the home bend (29 Oct). Brett: at Happy Valley a horse making its run on the turn goes wider. The
     comments agree beyond 1600 m: horses making 3+ places into the turn come round 0.7 to 0.8 of a lane wider than the
     field there, against a twentieth of a lane at Sha Tin and 0.2 in the Happy Valley sprints, which the engine already
     gave. So a horse going round on the HV home bend at those trips sits this many lanes outside where it entered it.
     Measured on the 1 Nov 1650 and 1800 (300 runs): those movers turn into the straight 0.36 of a lane wider than the
     field, from 0.21 with none -- half the comments' figure, because only a horse already going round is carried. More
     would push the whole field out (3 lanes: the field 0.4 wider too); Brett's marked races are the next measure. */
  const DRIFT = { "HV 1650+": 1.5 };
  function runTrip(race) {
    const v = race.venue, s = race.surface, D = race.distance;
    if (v === "ST" && s === "AWT") return "AWT";
    if (v === "ST") return D === 1000 ? null : D === 1200 ? "ST 1200" : D === 1400 ? "ST 1400" : D <= 1800 ? "ST 1600-1800" : "ST 2000+";
    return D <= 1200 ? "HV 1000-1200" : "HV 1650+";
  }
  function pRound(trip, lane, posShare) {
    const L = lane < 1.5 ? RUN_MODEL.lane.rail : lane < 2.5 ? RUN_MODEL.lane.one : RUN_MODEL.lane.wide;
    const z = RUN_MODEL.b0 + RUN_MODEL.pos * posShare + L + (RUN_MODEL.trip[trip] || 0);
    return Math.min(0.9, Math.max(0.1, 1 / (1 + Math.exp(-z))));     // some go either way
  }
  const unit = s => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return ((h >>> 0) % 1e6) / 1e6; };

  /* Trips that start in a chute: straight from the gates, off the oval and onto the back straight, so the home
     bend is the only bend. Sha Tin turf 1400, 1600 and 1800 (Brett, 18 Oct) and the all-weather 1200. The
     Comments on Running agree: at these trips they name a first turn or the back straight 1 to 4 times per 1,000
     comments, against 17 and 31 at the Sha Tin 2000 (two turns). Until 18 Oct the 1600 and 1800 started on the
     bend after the post, 220 m and 420 m of a bend that isn't there. */
  const CHUTE = { "ST Turf": [1400, 1600, 1800], "ST AWT": [1200] };

  /* Happy Valley as it is (29 Oct): traced from HKJC's course map, scaled to the A course's 1,450 m lap and fitted as
     straights and arcs, 2.8 m from the traced rail on average. Until 29 Oct it was Sha Tin's shape made smaller, two
     equal 415 m bends of 132 m; HKJC's map has the home turn 162 degrees in 320 m and tightest in its middle (83 m), the
     bend after the post 30 m past the post, a gentle left side, a sharp top-left corner (61 m) and a 307 m back
     straight. Each part from the post going back: [name, metres, degrees turned]. Brett (29 Oct): the 1000 starts in a
     chute straight onto the back straight (so does the 2400, 12 m into it), the 1200 in the chute at the bottom left,
     straight, then up the left side; HV_CHUTE is where each joins the course, metres to go. */
  const HV_COURSE = [["home straight", 310, 0], ["home bend", 171.5, 84.6], ["home bend", 65.3, 45.1], ["home bend", 83.5, 32.2],
    ["back straight", 307.2, 0], ["top-left bend", 45.1, 42.0], ["left side", 210.6, 44.8], ["bend after the post", 226.9, 111.3],
    ["after the post", 29.9, 0]];
  const HV_CHUTE = { 1000: 937.5, 1200: 1150, 2400: 937.5 + 1450 };
  const HV_PARTS = HV_COURSE.map(([name, len, deg]) => ({ name, len, deg, R: deg ? len / (deg * Math.PI / 180) : Infinity }));
  // the drawing's rail, a point a metre from the post back: x, y (metres, y down, the post at 0,0, the home straight
  // running right to left to it) and the unit direction away from the post, for track-view.js
  const HV_PTS = (() => {
    const out = new Float64Array(4 * 1451);
    let x = 0, y = 0, h = 0, s = 0, j = 0;
    const put = () => { out[4 * j] = x; out[4 * j + 1] = -y; out[4 * j + 2] = Math.cos(h); out[4 * j + 3] = -Math.sin(h); j++; };
    put();
    for (const p of HV_PARTS) {                // going back from the post the course turns left, y up; drawn y down
      const n = Math.round(p.len * 20), dl = p.len / n, dh = (p.deg * Math.PI / 180) / n;
      for (let q = 1; q <= n; q++) {
        x += dl * Math.cos(h + dh / 2); y += dl * Math.sin(h + dh / 2); h += dh; s += dl;
        while (j <= 1450 && s >= j - 1e-9) put();
      }
    }
    // the fit closes within 3 m of the post; spread the gap round the lap so the drawn rail meets itself
    const ex = out[4 * (j - 1)], ey = out[4 * (j - 1) + 1];
    for (let q = 0; q < j; q++) { out[4 * q] -= ex * q / (j - 1); out[4 * q + 1] -= ey * q / (j - 1); }
    return out.subarray(0, 4 * j);
  })();

  function geometry(race) {
    const D = race.distance;
    if (race.venue === "HV") {
      const J = HV_CHUTE[D], chute = J != null && D > J ? { from: J, to: D } : null, B = 171.5 + 65.3 + 83.5;
      return { straight: false, C: 1450, Lh: 310, B, Lb: 307.2, R: B / ((84.6 + 45.1 + 32.2) * Math.PI / 180), chute,
        label: "Happy Valley" + (chute ? ", chute start" : ""), parts: HV_PARTS, pts: HV_PTS };
    }
    if (race.venue === "ST" && race.surface === "Turf" && D === 1000) {
      return { straight: true, C: Infinity, Lh: D, B: 0, Lb: 0, R: Infinity, chute: null, label: "Sha Tin 1000 m straight" };
    }
    let C = 1899, Lh = 430, label = "Sha Tin turf";
    if (race.venue === "HV") { C = 1450; Lh = 310; label = "Happy Valley"; }
    else if (race.surface === "AWT") { C = 1560; Lh = 380; label = "Sha Tin all-weather"; }
    const Lb = Lh, B = (C - 2 * Lh) / 2, J = Lh + B + Lb;          // J: where the back straight meets the bend after the post
    const chute = (CHUTE[`${race.venue} ${race.surface}`] || []).includes(D) && D > J ? { from: J, to: D } : null;
    if (chute) label += ", chute start, one turn";
    return { straight: false, C, Lh, B, Lb, R: B / Math.PI, chute, label };
  }

  /** Bends as intervals of distance to go, nearest the post first, each with its radius. None in a chute. */
  function bends(geo, D) {
    if (geo.straight) return [];
    const out = [], lim = geo.chute ? geo.chute.from : D;
    for (let k = 0; k * geo.C < D; k++) {
      let o = k * geo.C;
      if (geo.parts) {                       // Happy Valley: each arc of the course
        for (const p of geo.parts) { if (p.deg) out.push({ from: o, to: o + p.len, name: k === 0 ? p.name : "bend", R: p.R }); o += p.len; }
        continue;
      }
      out.push({ from: o + geo.Lh, to: o + geo.Lh + geo.B, name: k === 0 ? "home bend" : "bend", R: geo.R });
      out.push({ from: o + geo.Lh + geo.B + geo.Lb, to: o + geo.C, name: k === 0 ? "bend after the post" : "bend", R: geo.R });
    }
    return out.filter(b => b.from < lim).map(b => ({ ...b, to: Math.min(b.to, lim) }));
  }

  function onBend(bs, d) {
    for (const b of bs) if (d >= b.from && d <= b.to) return b;
    return null;
  }

  /** Anchors (distance to go) where the comment's phases are pinned; entry = the top of the straight. */
  function anchors(geo, D) {
    const a = { settle: D - (D <= 1000 ? 200 : 250), mid: 800, turn: geo.straight ? null : geo.Lh + 0.25 * geo.B, straight: 200,
      entry: geo.straight ? null : geo.Lh - 20 };
    if (a.settle - a.mid < 150) a.mid = null;
    if (a.turn !== null && a.turn > (a.mid ?? a.settle) - 100) a.turn = null;
    return a;
  }

  /** The phase the leader is in at distance to go d, in the stewards' terms. */
  function phaseAt(D, d) {
    if (d < 350) return "straight";
    if (d < 650) return "turn";
    if (d <= Math.min(1000, D - 150)) return "mid";
    return "settle";
  }

  /* The first section, from a standing start: v(t) = (V0 + k t)(1 - exp(-t/TAU)), with V0 and k set so
     the horse covers the section in its recorded time and leaves it at v1, halfway between its
     running speed in the section and its average over the next one. */
  const startA = t1 => t1 - TAU * (1 - Math.exp(-t1 / TAU));
  const startB = t1 => t1 * t1 / 2 - TAU * TAU * (1 - Math.exp(-t1 / TAU)) + TAU * t1 * Math.exp(-t1 / TAU);

  function timeline(race, h) {
    const L = race.sections, n = L.length;
    const t = [0], s = [0];
    for (let k = 0; k < n; k++) { t.push(t[k] + h.secs[k]); s.push(s[k] + L[k]); }
    const del = [];
    for (let i = 0; i < n; i++) del.push((s[i + 1] - s[i]) / (t[i + 1] - t[i]));
    const V = s[1] / startA(t[1]);
    let v1 = n >= 2 ? 0.5 * (V + del[1]) : V;
    v1 = Math.min(Math.max(v1, 0.85 * V), 1.15 * Math.max(V, del[1] || V));
    const E = 1 - Math.exp(-t[1] / TAU), A1 = startA(t[1]), B1 = startB(t[1]);
    let k0 = (s[1] - v1 * A1 / E) / (B1 - t[1] * A1);
    let V0 = v1 / E - k0 * t[1];
    if (!(V0 > 0)) { V0 = V; k0 = 0; v1 = V * E; }
    const m = new Array(n + 1).fill(0);
    m[1] = v1;
    for (let i = 2; i < n; i++) m[i] = del[i - 1] > 0 && del[i] > 0 ? 2 / (1 / del[i - 1] + 1 / del[i]) : 0;
    m[n] = n >= 2 ? del[n - 1] : v1;
    for (let i = 1; i < n; i++) {               // keep each cubic monotone (Fritsch-Carlson)
      const a = m[i] / del[i], b = m[i + 1] / del[i], r = a * a + b * b;
      if (r > 9) { const k = 3 / Math.sqrt(r); m[i] = k * a * del[i]; m[i + 1] = k * b * del[i]; }
    }
    return { t, s, m, V0, k0, end: t[n], dist: s[n] };
  }

  function posAt(tl, time) {
    const { t, s, m } = tl, n = t.length - 1;
    if (time <= 0) return 0;
    if (time <= t[1]) return tl.V0 * startA(time) + tl.k0 * startB(time);
    if (time >= t[n]) {                          // past the post: easing down at 1.5 m/s/s
      const v = m[n], dt = Math.min(time - t[n], v / 1.5);
      return s[n] + v * dt - 0.75 * dt * dt;
    }
    let i = 1;
    while (i < n - 1 && time > t[i + 1]) i++;
    const h = t[i + 1] - t[i], u = (time - t[i]) / h, u2 = u * u, u3 = u2 * u;
    return (2 * u3 - 3 * u2 + 1) * s[i] + (u3 - 2 * u2 + u) * h * m[i] + (-2 * u3 + 3 * u2) * s[i + 1] + (u3 - u2) * h * m[i + 1];
  }

  function timeAt(tl, dist) {
    if (dist <= 0) return 0;
    let lo = 0, hi = tl.end + 8;
    for (let k = 0; k < 44; k++) { const mid = (lo + hi) / 2; if (posAt(tl, mid) < dist) lo = mid; else hi = mid; }
    return (lo + hi) / 2;
  }

  const smooth = f => { const u = Math.max(0, Math.min(1, f)); return u * u * (3 - 2 * u); };

  /** A horse's plan: lanes by distance to go. pre = before the straight (the gate first), st = in it.
   *  Brett's correction for a phase (w 2) beats the comments (w 1) at that phase's anchor and anywhere
   *  else in that part of the race. */
  function planOf(race, h, A, corr, geo, fan) {
    const D = race.distance, pre = [], st = [];
    const gate = 1 + (Math.max(1, h.draw || 1) - 1) * GATE_W;
    const cl = ph => (corr && corr[ph] && corr[ph].lane ? corr[ph].lane : null);
    const put = a => ((geo.straight || (a.d >= geo.Lh && a.ph !== "straight")) ? pre : st).push(a);
    pre.push({ d: D, lane: gate, w: 3, src: "gate", ph: "gate" });
    const early = h.p.early;
    if (early && early.lane != null && !cl("settle")) put({ d: D - 120, lane: early.lane, w: 1, src: early.src || "cor", ph: "early" });
    for (const ph of PHASES) {
      const d = A[ph];
      if (d == null) continue;
      let lane = null, w = 0, src = null;
      if (cl(ph)) { lane = cl(ph); w = 2; src = "you"; }
      else if (h.p[ph] && h.p[ph].lane != null && !(ph === "straight" && h.p[ph].src === "inc")) { lane = h.p[ph].lane; w = 1; src = h.p[ph].src || "cor"; }
      else if ((ph === "settle" || ph === "mid") && h.p.led && h.pos[0] === 1) { lane = 1; w = 0.5; src = "led"; }
      put({ d, lane, w, src, ph });
    }
    for (const at of h.p.at || []) {                      // "from 700M", "near the 500M": a lane at a distance
      const [N, lane, src] = at;
      if (!(N > 0 && N < D - 100) || cl(phaseAt(D, N))) continue;
      if (src === "inc" && N < geo.Lh) continue;          // the stewards' 'shifted to the outside of' in the straight is a contact, played as one
      put({ d: N, lane, w: 1, src: src || "cor", ph: "at" });
    }
    // a horse in front at a call before the straight is on the rail there, unless something says otherwise
    let s = 0;
    race.sections.forEach((L, k) => {
      s += L;
      const dk = D - s;
      if (h.pos[k] !== 1 || k === race.sections.length - 1 || dk < geo.Lh || dk > D - 150) return;
      if ([...pre, ...st].some(a => a.lane != null && a.w >= 1 && Math.abs(a.d - dk) < 200)) return;
      put({ d: dk, lane: 1, w: 0.5, src: "led", ph: "at" });
    });
    const en = h.p.entry;
    if (A.entry != null && en && en.lane != null && en.src !== "inc" && !cl("straight")) st.push({ d: A.entry, lane: en.lane, w: 1, src: en.src || "cor", ph: "entry" });
    pre.sort((a, b) => b.d - a.d);
    st.sort((a, b) => b.d - a.d);
    return { pre, st, gate, fan, Lh: geo.Lh };
  }

  /** The plan's lane at distance to go d: {lane, w, src, cross} or null (free). yIn: the horse's lane
   *  as it entered the straight. */
  function planTarget(pl, d, inStraight, yIn) {
    if (!inStraight) {
      let a = null, b = null;
      for (const p of pl.pre) { if (p.d >= d) { if (p.lane != null) a = p; } else if (p.lane != null) { b = p; break; } }
      if (!a) return null;
      if (a.ph === "gate") {
        if (!b) return null;
        if (b.ph === "at" && d > b.d + 120) return null;            // "from 700M": free until shortly before
        return { lane: b.lane, w: b.w, src: b.src, cross: true };
      }
      if (!b) return { lane: a.lane, w: a.w, src: a.src };
      const s = b.ph === "at" ? smooth((b.d + 120 - d) / 120) : smooth((a.d - d) / (a.d - b.d));
      return { lane: a.lane + s * (b.lane - a.lane), w: Math.min(a.w, b.w), src: s < 0.5 ? a.src : b.src };
    }
    let st = pl.st.filter(p => p.lane != null);
    // a lane stated only for turning in ('very wide turning in') holds to that point; from there the straight is planned
    const en = st.length === 1 && st[0].ph === "entry" && pl.stLane != null ? st[0] : null;
    if (en) st = [];
    if (!st.length && pl.stLane != null && yIn != null) {
      let lane = pl.stLane;                          // one waiting for a gap: its first line, then the gap from where it opens
      if (pl.stLane2 != null && pl.stAt != null && d < pl.stAt + 5) lane += (pl.stLane2 - pl.stLane) * smooth((pl.stAt + 5 - d) / Math.max(20, 14 * Math.abs(pl.stLane2 - pl.stLane)));
      if (en) {
        if (d > en.d) return { lane: yIn + (en.lane - yIn) * smooth((pl.Lh - d) / Math.max(1, pl.Lh - en.d)), w: en.w, src: en.src };
        return { lane: en.lane + (lane - en.lane) * smooth((en.d - d) / 100), w: 0.8, src: null, planned: true };
      }
      return { lane: yIn + (lane - yIn) * smooth((pl.Lh + 80 - d) / 120), w: 0.8, src: null, planned: true };
    }
    if (!st.length) return pl.fan != null && yIn != null ? { lane: yIn + pl.fan * smooth((pl.Lh - d) / 150), w: 0.3, src: null } : null;
    const first = st[0];
    if (d > first.d) {
      const y0 = yIn != null ? yIn : first.lane, s = smooth((pl.Lh - d) / Math.max(1, pl.Lh - first.d));
      return { lane: y0 + s * (first.lane - y0), w: first.w, src: first.src };
    }
    for (let k = 0; k < st.length - 1; k++) {
      const a = st[k], b = st[k + 1];
      if (d <= a.d && d > b.d) { const s = smooth((a.d - d) / (a.d - b.d)); return { lane: a.lane + s * (b.lane - a.lane), w: Math.min(a.w, b.w), src: s < 0.5 ? a.src : b.src }; }
    }
    const last = st[st.length - 1];
    return { lane: last.lane, w: last.w, src: last.src };
  }

  /** Build a replay: lateral positions every 0.1 s, how far each horse sits behind its own sectional
   *  curve (easing to tuck in or waiting behind another), flags, contacts and summaries. */
  function simulate(race, corrections, stPlan, opts) {
    const D = race.distance, geo = geometry(race), bs = bends(geo, D), A = anchors(geo, D);
    const N = race.runners.length, n = race.sections.length;
    const trip = geo.straight ? null : runTrip(race);
    const H = race.runners.map((h, hi) => {
      const corr = corrections ? corrections[h.no] : null;
      const posT = h.pos[Math.max(0, n - 2)] || N, gain = posT - (h.place || N);
      // the run: who makes one, which way, from where
      const mark = corr && ((corr.turn && corr.turn.run) || (corr.straight && corr.straight.run)) || null;      // Brett: round, through, none
      const said = h.p.run && h.p.run.how ? h.p.run : null;                                                      // the comment, or the simulator
      const before = h.pos[Math.max(0, n - 3)] || N, made = before - (h.place || N);
      const mover = !!trip && mark !== "none" && (mark === "round" || mark === "through" || !!said || (made >= 2 && before > 2));
      // held up in the straight with no way named: it was in among horses, so it was coming through
      const heldSaid = !said && !(mark === "round" || mark === "through") && !(corr && corr.straight && corr.straight.traffic === "clear") &&
        !!((corr && corr.straight && corr.straight.traffic === "held") || (!(opts && opts.said === false) && h.p.traffic_straight));
      const run = { mover, how: mark === "round" || mark === "through" ? mark : said ? said.how : heldSaid ? "through" : null,
        src: mark === "round" || mark === "through" ? "you" : said ? (said.src || "cor") : heldSaid ? "held" : null, from: said && said.from ? said.from : null,
        u: unit(`${race.id}|${h.no}|way`), u2: unit(`${race.id}|${h.no}|from`), p: null, decided: false,
        dDecide: trip ? Math.max(RUN_START[trip].round[1], geo.Lh + 120) + 60 : 0,
        // held up in the straight: Brett's mark, else the comment (or the simulation's draw); 'clear run' overrides
        held: corr && corr.straight && corr.straight.traffic ? corr.straight.traffic === "held" : !!(!(opts && opts.said === false) && h.p.traffic_straight) };
      // the straight: a horse making its run takes its own line (round or through); the rest spread a little
      const fan = run.mover ? 0 : posT / N <= 0.15 ? FAN[0] : FAN[1];
      const plan = planOf(race, h, A, corr, geo, fan);
      if (stPlan && stPlan[hi]) { plan.stLane = stPlan[hi].lane; plan.stLane2 = stPlan[hi].lane2; plan.stAt = stPlan[hi].at; plan.wait = stPlan[hi].wait; }
      return { h, tl: timeline(race, h), plan, corr, run };
    });
    const idx = Object.fromEntries(H.map((x, i) => [x.h.no, i]));
    const settleWish = H.map(x => { const e = x.plan.pre.find(q => q.ph === "settle"); return e && e.lane != null && e.w >= 1 ? e.lane : null; });
    const coverWant = H.map(x => {
      const out = {};
      for (const ph of PHASES) {
        const c = x.corr && x.corr[ph] && x.corr[ph].cover, p = x.h.p[ph] || {};
        out[ph] = c === "cover" || c === "none" ? c : p.nocover ? "none" : p.cover ? "cover" : null;
      }
      return out;
    });
    const events = (race.events || []).map(e => ({ ...e, ia: idx[e.a], ib: e.b == null ? null : idx[e.b], done: false })).filter(e => e.ia != null);
    const lastTime = Math.max(...H.map(x => x.tl.end)), winnerTime = Math.min(...H.map(x => x.tl.end));
    const K = Math.ceil((lastTime + 3) / DT) + 2;
    const X = H.map(x => { const a = new Float32Array(K); for (let k = 0; k < K; k++) a[k] = posAt(x.tl, k * DT); return a; });
    // settling, packed (v3): where each horse can sit when the field settles, given the horses around it. A lane the
    // simulator drew, or none at all (cross towards the rail), becomes the nearest one free at that point: two horses
    // aren't sent for the same spot. A lane from the comments is kept (the one behind tucks in), and Brett's always.
    // 19 Oct: a lane is taken only by a horse level with it (within 0.9 m); one just ahead it sits in behind, easing up
    // to 1.5 m (the engine eases to tuck in). Horses level with each other keep their order from the gates, inside
    // first. Before, a horse anywhere within 2.9 m blocked the lane, so a horse drawn 1 that drew the rail was sent 3 or
    // 4 wide in 15% of simulated races (Brett: Beauty Bolt, R11 on 19 Oct), and some drawn 10+ were packed onto the rail.
    if (!geo.straight && A.settle != null) {
      let kS = 1;
      while (kS < K - 1 && D - Math.max(...X.map(a => a[kS])) > A.settle) kS++;
      const LEVEL = BODY + 0.1 - PACK_EASE;
      const byX = [...Array(N).keys()].sort((a, b) => X[b][kS] - X[a][kS]), order = [];
      const simWish = i => { const e = H[i].plan.pre.find(q => q.ph === "settle"); return e && e.lane != null && e.src === "sim" ? e : null; };
      for (let s = 0; s < byX.length;) {
        let e2 = s + 1;
        while (e2 < byX.length && X[byX[e2 - 1]][kS] - X[byX[e2]][kS] < LEVEL) e2++;
        const group = byX.slice(s, e2).sort((p, q) => (H[p].h.draw || 0) - (H[q].h.draw || 0));
        // level horses the simulator gave crossing lanes (the one drawn inside wider) keep the lanes but swap them,
        // inside draw to inside lane: neither could have crossed the other
        const sims = group.filter(simWish), lanes = sims.map(i => simWish(i).lane).sort((p, q) => p - q);
        sims.forEach((i, q) => { const e = simWish(i), d = lanes[q] - e.lane; if (!d) return; e.lane = lanes[q];
          for (const p of H[i].plan.pre) if (p.src === "sim" && (p.ph === "mid" || p.ph === "turn")) p.lane += d; });
        order.push(...group);
        s = e2;
      }
      const placed = [];
      for (const i of order) {
        const pl = H[i].plan, e = pl.pre.find(q => q.ph === "settle");
        const kept = e && e.lane != null && (e.src === "you" || e.src === "cor" || e.src === "inc" || e.src === "led");
        let wish = e && e.lane != null ? e.lane : 1;
        if (!kept && e && e.src === "sim") {                     // the simulator's draws only: the replay's free horses tuck in
          let best = wish, bestC = Infinity;
          for (let c = 1; c <= LANE_MAX; c += 0.1) {
            let ok = true;
            for (const j of placed) {
              if (Math.abs(X[j][kS] - X[i][kS]) >= LEVEL) continue;          // clear of it: one sits in behind the other
              if (Math.abs(c - placed.lane[j]) < SIDE + 0.08) { ok = false; break; }
              if ((H[j].h.draw || 0) < (H[i].h.draw || 0) && c < placed.lane[j]) { ok = false; break; }   // level, drawn inside: it stays inside
            }
            if (!ok) continue;
            const cost = Math.abs(c - wish) + (c > wish ? 0.05 : 0);
            if (cost < bestC) { bestC = cost; best = c; }
          }
          if (e) { e.packed = best; e.lane = best; } else { pl.pre.push({ d: A.settle, lane: best, w: 0.5, src: "pack", ph: "settle" }); pl.pre.sort((a, b) => b.d - a.d); }
          // the simulator's mid-race and turn lanes move with it
          for (const q of pl.pre) if (q.src === "sim" && (q.ph === "mid" || q.ph === "turn") && e && e.packed != null) q.lane += best - wish;
          wish = best;
        }
        (placed.lane ||= {})[i] = wish; placed.push(i);
      }
    }
    const XD = H.map(() => new Float32Array(K)), Y = H.map(() => new Float32Array(K)), FL = H.map(() => new Uint8Array(K));
    const AH = H.map(() => new Int8Array(K).fill(-1));            // who it is held up behind, frame by frame (20 Oct: the simulator's trip cost)
    const y = H.map(x => x.plan.gate), v = H.map(() => 0), yIn = H.map(() => null), o = H.map(() => 0), yBend = H.map(() => null);
    const push = H.map(() => null);            // active contact: {until, dir, victim}
    const contacts = [];
    y.forEach((yy, i) => { Y[i][0] = yy; });
    for (let k = 1; k < K; k++) {
      const t = k * DT, xc = X.map(a => a[k]), xd = xc.map((x, i) => x + o[i]);
      const vx = X.map((a, i) => (a[k] - a[k - 1]) / DT + (o[i] < -0.01 ? CATCH : 0));
      for (const e of events) {                // a stewards' contact starts when the shifting horse reaches it
        if (e.done || D - xd[e.ia] > e.d) continue;
        e.done = true;
        const ib = e.ib;
        let dir = e.dir;
        if (!dir && ib != null && e.push) dir = y[ib] >= y[e.ia] ? 1 : -1;
        if (!dir) {                          // a check or a bump with no direction given: marked, nothing moved
          contacts.push({ t, i: e.ia, j: null, kind: e.kind, d: e.d });
          for (let q = k; q < Math.min(K, k + 10); q++) FL[e.ia][q] |= F_CONTACT;
          continue;
        }
        const near = ib != null && Math.abs(xd[ib] - xd[e.ia]) < BODY * 1.6 && Math.abs(y[ib] - y[e.ia]) < 2.2;
        push[e.ia] = { until: t + 0.9, dir, victim: near ? ib : null, y0: y[e.ia] };
        contacts.push({ t: t + 0.35, i: e.ia, j: near ? ib : null, kind: e.kind, d: e.d, dir });
      }
      const order = [...Array(N).keys()].sort((a, b) => xd[b] - xd[a]);
      for (const i of order) {
        const d = D - xd[i], inStraight = !geo.straight && d < geo.Lh;
        if (d < -5) {                                               // past the post: hold the lane
          o[i] = Math.min(0, o[i] + 2.5 * DT); xd[i] = xc[i] + o[i];
          XD[i][k] = xd[i]; Y[i][k] = y[i]; continue;
        }
        if (inStraight && yIn[i] === null) yIn[i] = y[i];
        // off the turn, a horse starts across to its line for the straight (the plan), if it has one and nothing says otherwise
        const offTurn = !inStraight && H[i].plan.stLane != null && d < geo.Lh + 80 && !geo.straight;
        if (offTurn && yIn[i] === null) yIn[i] = y[i];
        const tg = offTurn ? (planTarget(H[i].plan, d, false, null) && planTarget(H[i].plan, d, false, null).w >= 1 ? planTarget(H[i].plan, d, false, null)
          : planTarget(H[i].plan, d, true, yIn[i])) : planTarget(H[i].plan, d, inStraight, yIn[i]);
        // the run (v3): which way is settled at the decision point, then it is steered that way from its start
        const run = H[i].run;
        if (run.mover && !run.decided && d <= run.dDecide) {
          if (!run.how) {
            let ahead0 = 0;
            for (let j = 0; j < N; j++) if (j !== i && xd[j] > xd[i]) ahead0++;
            run.p = pRound(trip, y[i], ahead0 / Math.max(1, N - 1));
            run.how = run.u < run.p ? "round" : "through"; run.src = "model";
            // a horse pocketed when it goes for its run still goes round: it can't come out through the horse outside it
            // (no leaning), so it waits until that one has gone on or dropped back, then comes out (until 18 Oct it was
            // sent through instead, and was held up in half of them; the comments had 1 in 10 of those held)
          }
          if (!run.from) { const q = RUN_START[trip][run.how]; run.from = q[0] + run.u2 * (q[1] - q[0]); }
          run.decided = true;
        }
        const running = run.mover && run.decided && d <= run.from;
        const Hh = running ? H_RUN : H_BASE;
        // closing speed over the horizon, from both horses' own curves: the replay knows where each will be, so a horse
        // ahead that quickens is allowed for (one making ground but still slower blocks; one quicker does not)
        const kH = Math.min(K - 1, k + Math.max(1, Math.round(Hh / DT))), span = (kH - k) * DT || DT;
        const closeF = j => ((X[i][kH] - X[i][k]) - (X[j][kH] - X[j][k])) / span;
        // any easing must be made back by the next section mark, so the marks keep their recorded times
        const tm = H[i].tl.t;
        let m = 1;
        while (m < tm.length - 1 && tm[m] <= t) m++;
        const tToMark = tm[m] - t, lagLim = Math.max(0, Math.min(running ? LAG_RUN : LAG_MAX, 1.2 * (tToMark - 0.4)));
        // the start model is the least sure part; a horse making its run may also go on a little to get out
        const leadLim = xd[i] < 450 || running ? Math.max(0, Math.min(running && xd[i] >= 450 ? 1.2 : 1.5, 1.2 * (tToMark - 0.4))) : 0;
        let target, vmax, T, flag = 0;
        const crossing = xd[i] < 350 && !inStraight;
        if (t < 0.6) { vmax = 0.1; T = 1; }
        else if (inStraight) { vmax = 1.0; T = 1.0; }
        else if (crossing) { vmax = tg ? 1.0 : 0.8; T = tg ? 1.0 : 2.0; }
        else { vmax = tg ? 0.6 : 0.4; T = tg ? 1.5 : 2.0; }
        target = tg ? tg.lane : (inStraight || !crossing ? y[i] : 1);      // free: cross towards the rail, then hold
        // crossing from the gates (23 Oct): two horses whose settling lanes keep their gate order keep it, even when the
        // outside one is a little quicker away. Until 23 Oct the quicker one cut inside and the other couldn't get across
        // (Brett's marks, 19 Oct R4: Watch Legend inside Coogee Bay and on to the rail, shown four wide)
        if (crossing && tg && settleWish[i] != null) {
          for (let j = 0; j < N; j++) {
            if (j === i || settleWish[j] == null || !(H[j].h.draw < H[i].h.draw) || !(settleWish[j] < settleWish[i])) continue;
            if (Math.abs(xd[j] - xd[i]) > 2 * BODY || y[j] <= settleWish[j] + 0.05) continue;     // clear of it, or it is across
            target = Math.max(target, y[j] + SIDE);
          }
        }
        // cover, when the plan leaves room for it (free, or within half a lane of the horse in front)
        const want = !inStraight && t > 3 ? coverWant[i][phaseAt(D, d)] : null;
        if (want) {
          let cov = null, side = null;
          for (let j = 0; j < N; j++) {
            if (j === i) continue;
            const dx = xd[j] - xd[i];
            if (!(dx > 0.3 && dx <= COVER_M)) continue;
            const dy = y[j] - y[i];
            if (Math.abs(dy) < COVER_DY && dx > BODY) cov = j; else if (Math.abs(dy) < 1.3 && (side === null || Math.abs(dy) < Math.abs(y[side] - y[i]))) side = j;
          }
          // to sit behind a horse half a lane across, it may move up to 0.45 of a lane off its stated lane (same lane name)
          if (want === "cover" && cov === null && side !== null) {
            if (!tg || tg.w < 1) target = y[side];
            else if (Math.abs(y[side] - tg.lane) <= 1.3) target = Math.max(tg.lane - 0.45, Math.min(tg.lane + 0.45, y[side]));
          }
          if (want === "none" && cov !== null && (!tg || tg.w < 1)) target = y[cov] + 1;
        }
        const ownLane = tg && (tg.w >= 2 || (inStraight && tg.w >= 1 && tg.src));       // Brett's lane, or the comment's in the straight
        if (running && !ownLane) {
          if (run.how === "round") {
            // round: on the turn, out past every slower horse in the next few lengths; in the straight, its planned line
            // (planned outside the horses it passes), switching only if it runs up behind one
            if (!inStraight) {
              let need = y[i];
              for (let j = 0; j < N; j++) {
                if (j === i) continue;
                const dx = xd[j] - xd[i];
                if (dx < -0.5 * BODY || dx > LOOK_ROUND || y[j] < y[i] - 0.6) continue;
                if (dx > BODY && closeF(j) <= 0.05) continue;              // ahead and no slower: not in the way
                need = Math.max(need, y[j] + SIDE + ROUND_CLEAR);
              }
              // carried wider round a home bend where the trip says so (DRIFT), from the lane it entered the bend in
              const hb = DRIFT[trip] ? onBend(bs, d) : null;
              if (hb && hb.name === "home bend") {
                if (yBend[i] === null) yBend[i] = y[i];
                need = Math.max(need, yBend[i] + DRIFT[trip]);
              }
              target = Math.min(LANE_MAX, Math.max(target, need));
              vmax = Math.max(vmax, 0.8); T = Math.min(T, 1.2);
            }
            flag |= F_ROUND;
          } else if (!(tg && tg.planned)) target = Math.min(target, y[i] + 0.2);   // through: keeps its line (in the straight, the plan's)
        }
        // traffic: how long before it runs up behind a slower horse in a lane (a horse ahead that is making ground still
        // counts if it is slower), looking further ahead when it is making its run
        const blockedIn = c => {
          let tb = Infinity;
          for (let j = 0; j < N; j++) {
            if (j === i || Math.abs(y[j] - c) >= SIDE * 0.9) continue;
            const dx = xd[j] - xd[i];
            if (dx <= -0.3) continue;
            if (dx < BODY) {                                                         // alongside in that lane
              if (Math.abs(c - y[i]) <= 0.3) continue;
              if (running && dx > 0.3 && xd[j] - BODY - xc[i] >= -lagLim) continue;      // making its run: it can come off it
              return 0;
            }
            const rel = closeF(j);
            if (rel > 0.02) tb = Math.min(tb, (dx - BODY) / rel);
          }
          return tb;
        };
        let cap = Infinity, easeCap = 0, surgeCap = 0, ahead = null, noWay = false;
        if (t > 1) {
          let gap = Infinity;
          for (let j = 0; j < N; j++) {
            if (j === i) continue;
            const dx = xd[j] - xd[i];
            if (dx <= 0 || Math.abs(y[j] - y[i]) >= SIDE * 0.9) continue;
            if (dx - BODY < gap) { gap = dx - BODY; ahead = j; }
          }
          const close0 = ahead === null ? 0 : vx[i] - vx[ahead];
          if (ahead !== null && !running && !inStraight) {
            // settled, not making its run (v2's rule): it sits in behind a slower horse (cover) unless it is about to run
            // into it, and then takes a free lane either side
            if ((gap < 0.4 && close0 > -0.05) || (close0 > 0.05 && gap / close0 < 1.5)) {
              const want2 = tg ? tg.lane : y[i];
              let best = null, bestS = Infinity;
              for (const c of [y[ahead] + SIDE, y[ahead] - SIDE]) {
                if (c < 1) continue;
                let free = true;
                for (let j = 0; j < N; j++) {
                  if (j === i || j === ahead) continue;
                  if (Math.abs(xd[j] - xd[i]) < BODY + 1.5 && Math.abs(y[j] - c) < SIDE * 0.95) { free = false; break; }
                }
                if (!free) continue;
                const sc = Math.abs(c - want2) + 0.5 * Math.abs(c - y[i]) + (c < y[i] ? 0.3 : 0);
                if (sc < bestS) { bestS = sc; best = c; }
              }
              const stay = tg && tg.w >= 1 && Math.abs(tg.lane - y[i]) < 0.5;
              if (best === null || (stay && xd[ahead] - BODY - 0.1 - xc[i] > -lagLim)) { cap = xd[ahead] - BODY - 0.1 - xc[i]; noWay = best === null; }
              else { target = best; vmax = Math.max(vmax, 1.2); T = 0.6; flag |= F_SWITCH; }
            }
          }
          const tb0 = ahead === null || (!running && !inStraight) ? Infinity : blockedIn(y[i]);
          if (ahead !== null && (running || inStraight) && (tb0 < Hh || (gap < 0.4 && close0 > -0.05))) {
            // a better line one or two horses' widths either side: clear for longer, at a small cost per lane and, on a
            // bend, for the extra ground. Going round it looks outside first; coming through, inside.
            const onB = !!onBend(bs, d);
            let best = null, bestS = Math.min(tb0, Hh);
            for (const dc of [SIDE, -SIDE, 2 * SIDE, -2 * SIDE]) {
              const c = y[i] + dc;
              if (c < 1 || c > LANE_MAX) continue;
              let s = Math.min(blockedIn(c), Hh) - 0.25 * Math.abs(dc) - (onB && dc > 0 ? 0.2 * dc : 0);
              if (running && run.how === "round") s += dc > 0 ? 0.35 : -0.2;
              if (running && run.how === "through" && dc < 0) s += 0.25;
              if (s > bestS + 0.2) { bestS = s; best = c; }
            }
            // the comment or Brett keeps it in this lane; in the straight, Brett's own lane keeps even a horse making its run
            // on its line (23 Oct: one switching out from its marked rail line crossed the one he marked clear outside it)
            const stay = tg && tg.w >= 1 && Math.abs(tg.lane - y[i]) < 0.5 && (!running || (inStraight && tg.src === "you"));
            if (best === null || (stay && xd[ahead] - BODY - 0.1 - xc[i] > -lagLim)) { cap = xd[ahead] - BODY - 0.1 - xc[i]; noWay = best === null; }
            else {
              target = best; vmax = Math.max(vmax, 1.2); T = 0.6; flag |= F_SWITCH;
              // in the straight a horse that has switched keeps its new line: it doesn't come back in behind the one it left
              if (inStraight && tg && tg.planned) { H[i].plan.stLane = best; H[i].plan.stLane2 = null; H[i].plan.stAt = null; H[i].plan.wait = false; yIn[i] = best; }
            }
          }
        }
        // tucking in: a horse whose lane is across the path of one alongside and ahead of it eases back
        // behind that horse, rather than being carried wider (or narrower)
        if (t > 1 && !(flag & F_SWITCH) && ((tg && tg.w >= 1) || crossing || (!tg && !inStraight) || running)) {
          for (let j = 0; j < N; j++) {
            const dx = xd[j] - xd[i];
            if (j === i || Math.abs(dx) >= BODY || dx < -0.5) continue;
            const req = SIDE * Math.sqrt(1 - (dx / BODY) * (dx / BODY));
            const inside = y[j] < y[i] - 1e-6 || (Math.abs(y[j] - y[i]) <= 1e-6 && dx > 0);
            if (inside ? y[j] + req <= target + 0.15 : y[j] - req >= target - 0.15) continue;
            if (running && leadLim > 0 && dx <= 0.3 && closeF(j) > 0.05 && xd[j] + BODY + 0.05 - xc[i] <= leadLim) {
              surgeCap = Math.max(surgeCap, xd[j] + BODY + 0.05 - xc[i]);   // quicker than it: goes on past it, then across
            } else if (dx > 0.3 || leadLim === 0) {     // it is ahead or level: ease back behind it
              const need = xd[j] - BODY - 0.05 - xc[i];
              if (need >= -lagLim) easeCap = Math.min(easeCap, need);
            } else {                                    // this one is ahead: leaving the gates, go on and cross in front
              const need = xd[j] + BODY + 0.05 - xc[i];
              if (need <= leadLim) surgeCap = Math.max(surgeCap, need);
              else { const back = xd[j] - BODY - 0.05 - xc[i]; if (back >= -lagLim) easeCap = Math.min(easeCap, back); }
            }
          }
        }
        const dCurve = xc[i] - X[i][k - 1];
        const rate = Math.min(2.5, Math.max(CATCH, Math.abs(o[i]) / Math.max(0.3, tToMark - 0.3)));
        let oNew = o[i] < 0 ? Math.min(0, o[i] + Math.min(rate, Math.max(0, 20.5 - dCurve / DT)) * DT) : Math.max(0, o[i] - rate * DT);
        if (easeCap < 0) oNew = Math.min(oNew, Math.max(easeCap, o[i] - EASE * DT));
        else if (surgeCap > 0) oNew = Math.max(oNew, Math.min(surgeCap, o[i] + EASE * DT));
        const oFree = Math.min(oNew, leadLim);
        oNew = Math.min(oNew, cap, leadLim);
        oNew = Math.max(oNew, -lagLim, XD[i][k - 1] + 0.5 * dCurve - xc[i]);     // made back in time; never backwards
        // held up: no line to take, and the horse in front is actually stopping it
        if (noWay && ahead !== null && oNew < oFree - 0.02 && closeF(ahead) > 0.1) flag |= F_HELD;
        // said to be held up (Brett, the comment, the simulation): while it waits in behind for its gap, it is
        if (run.held && inStraight && H[i].plan.wait && (H[i].plan.stAt == null || d > H[i].plan.stAt) && ahead !== null && xd[ahead] - (xc[i] + oNew) - BODY < 1.2) flag |= F_HELD;
        o[i] = oNew; xd[i] = xc[i] + oNew;
        const pu = push[i] && t <= push[i].until ? push[i] : null;
        if (pu) {
          // a shift is a bounded move from where it began, as far as touching the horse it shifted onto (23 Oct: re-aimed
          // 0.8 of a lane past wherever it was each frame, GHORGAN went 1.5 lanes in, through the one it crowded)
          let tgt = pu.y0 + pu.dir * PUSH;
          if (pu.victim != null && (pu.dir > 0 ? y[pu.victim] > pu.y0 : y[pu.victim] < pu.y0)) tgt = pu.dir > 0 ? Math.min(tgt, y[pu.victim] - TOUCH) : Math.max(tgt, y[pu.victim] + TOUCH);
          target = pu.dir > 0 ? Math.max(y[i], tgt) : Math.min(y[i], tgt); vmax = 1.6; T = 0.4; flag |= F_CONTACT;
        }
        else {
          // no leaning (v3): a lane taken by a horse alongside is waited for a little short of it, not pushed into; the
          // horse slides across behind it as it draws clear
          for (let j = 0; j < N; j++) {
            if (j === i) continue;
            const dx = xd[j] - xd[i];
            if (Math.abs(dx) >= CLIP * BODY) continue;              // past its quarters, it may angle in behind it
            const req = SIDE * Math.sqrt(1 - (dx / BODY) * (dx / BODY));
            if (y[j] > y[i] + 1e-6 && target > y[j] - req - GAP) { target = Math.max(y[i] - 0.3, y[j] - req - GAP); flag |= F_WAIT; }
            else if (y[j] < y[i] - 1e-6 && target < y[j] + req + GAP) { target = Math.min(y[i] + 0.3, y[j] + req + GAP); flag |= F_WAIT; }
          }
        }
        const vDes = Math.max(-vmax, Math.min(vmax, (target - y[i]) / T));
        v[i] += Math.max(-AMAX * DT, Math.min(AMAX * DT, vDes - v[i]));
        const yProp = y[i] + v[i] * DT;
        // bodies don't overlap: the horses alongside bound how far in or out this one can be
        let lb = 1, ub = 99;
        for (let j = 0; j < N; j++) {
          if (j === i || (pu && j === pu.victim)) continue;
          const dx = xd[j] - xd[i];
          if (Math.abs(dx) >= BODY || dx <= -TAIL * BODY) continue;        // one behind, at its quarters or further back, doesn't push it
          const req = SIDE * Math.sqrt(1 - (dx / BODY) * (dx / BODY));
          if (y[j] < y[i] - 1e-6 || (Math.abs(y[j] - y[i]) <= 1e-6 && dx > 0)) lb = Math.max(lb, y[j] + req);
          else if (y[j] > y[i] + 1e-6) ub = Math.min(ub, y[j] - req);
        }
        let yNew;
        if (lb <= ub) yNew = Math.max(lb, Math.min(ub, yProp));
        else { yNew = (lb + ub) / 2; flag |= F_SQUEEZED; }
        const lim = VFORCE * DT;
        if (yNew > y[i] + lim) yNew = y[i] + lim;
        if (yNew < y[i] - lim) yNew = y[i] - lim;
        yNew = Math.max(1, yNew);
        if (Math.abs(yNew - yProp) > 0.01) v[i] = (yNew - y[i]) / DT;
        y[i] = yNew; Y[i][k] = yNew; XD[i][k] = xd[i]; FL[i][k] |= flag;
        if ((flag & F_HELD) && ahead !== null) AH[i][k] = ahead;
        if (pu && pu.victim != null && Math.abs(xd[pu.victim] - xd[i]) < BODY) FL[pu.victim][k] |= F_CONTACT;
      }
    }
    // summaries: share of the run to the straight without cover, ground lost on the bends
    const summary = H.map((x, i) => {
      let nc = 0, cnt = 0, ground = 0;
      for (let k = 1; k < K; k++) {
        const d = D - XD[i][k];
        if (d < -5) break;
        const b = onBend(bs, d);
        if (b) ground += (Y[i][k] - 1) * LANE_M * (XD[i][k] - XD[i][k - 1]) / b.R;      // the bend's own radius
        if (k % 5 || d < geo.Lh || XD[i][k] < 150) continue;
        cnt++;
        if (coverOf(XD.map(a => a[k]), Y.map(a => a[k]), i) === "none") nc++;
      }
      return { noCoverShare: cnt ? nc / cnt : null, groundLengths: ground / LEN };
    });
    return { race, geo, bends: bs, anchors: A, H, K, X, XD, Y, FL, AH, contacts, summary, winnerTime, lastTime,
      runs: H.map(x => ({ mover: x.run.mover, how: x.run.decided ? x.run.how : null, src: x.run.src, from: x.run.from ? Math.round(x.run.from) : null,
        p: x.run.p, held: x.run.held })) };
  }

  /** Lanes for the straight (v3, 18 Oct). The replay knows every horse's curve, so it knows who will be within a length
   *  of whom, and who passes whom, between the top of the straight and the post. Placed front to back as they turn in,
   *  stated lanes first, each horse gets the free line nearest what it wants: a horse's width from every horse it will
   *  be that close to (as the first pass ran them), within a window of where it turned in (ST_WIN out, ST_IN in).
   *  Wants: one coming through ST_OFF.through off its entry lane, one going round its entry lane and outside those it
   *  passes, the rest as v2 spread. If no line is free all the way, it waits for a gap: it holds a first line (one
   *  coming through, its own) and takes the first line that is free from some point on. That wait is the through
   *  horse's risk; if no gap opens, it shares the line with the fewest seconds alongside and the second pass holds it
   *  up or finds it a way. A lane stated only for turning in counts there, not as the line. Before the window a closer
   *  that passed ten horses was sent seven lanes wide to clear them all; Brett's video had them 2 to 5 wide. */
  function straightPlan(R0) {
    const { race, H, X, XD, Y, K, geo } = R0, D = race.distance, N = H.length, Lh = geo.Lh;
    const kIn = H.map((x, i) => { for (let k = 1; k < K; k++) if (D - XD[i][k] < Lh) return k; return K - 1; });
    const kEnd = H.map((x, i) => { for (let k = kIn[i]; k < K; k++) if (D - X[i][k] < -2) return k; return K - 1; });
    const e = H.map((x, i) => Y[i][kIn[i]]);
    const near = H.map(() => new Array(N).fill(null)), passes = H.map(() => new Array(N).fill(false));
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      const a = Math.max(kIn[i], kIn[j]), b = Math.min(kEnd[i], kEnd[j]);
      if (b <= a) continue;
      const ks = [];
      for (let k = a; k <= b; k += 2) if (Math.abs(XD[i][k] - XD[j][k]) < BODY + 0.3) ks.push(k);    // within a length
      if (!ks.length) continue;
      near[i][j] = near[j][i] = ks;
      const d0 = XD[i][a] - XD[j][a], d1 = X[i][b] - X[j][b];
      if (d0 < 0 && d1 > 0) passes[i][j] = true; else if (d0 > 0 && d1 < 0) passes[j][i] = true;
    }
    const line = new Array(N).fill(null);                 // {a, b, k}: lane a, then lane b from step k
    const laneAt = (j, k) => (k < line[j].k ? line[j].a : line[j].b);
    const clash = (i, c, k0) => {                          // seconds alongside a placed horse in the same line, from step k0
      let s = 0;
      for (let j = 0; j < N; j++) {
        if (j === i || !near[i][j] || !line[j]) continue;
        for (const k of near[i][j]) if (k >= k0 && Math.abs(c - laneAt(j, k)) < SIDE + 0.02) s += 2 * DT;
      }
      return s;
    };
    H.forEach((x, i) => {
      const st = x.plan.st.filter(p => p.lane != null);
      if (st.length === 1 && st[0].ph === "entry") return;                      // turning in only: the straight is planned
      const p = planTarget(x.plan, 200, true, e[i]);
      if (p && p.w >= 1 && p.src) line[i] = { a: p.lane, b: p.lane, k: Infinity };
    });
    const kL = Math.min(...kIn);
    const order = [...Array(N).keys()].sort((p, q) => XD[q][kL] - XD[p][kL]);
    // the horse a held-up horse runs up behind: the nearest one ahead of it turning in (within 30 m and two and a half
    // lanes) that it gets to within a length of in the straight
    const blocker = i => {
      let best = null, bd = Infinity;
      for (let j = 0; j < N; j++) {
        if (j === i || !line[j]) continue;
        const dx = XD[j][kIn[i]] - XD[i][kIn[i]];
        if (dx <= 0.5 || dx > 30 || Math.abs(Y[j][kIn[i]] - e[i]) > 2.5) continue;
        let close = Infinity;                                                // does it get to within a length of it?
        for (let k = kIn[i]; k <= Math.min(kEnd[i], kEnd[j]); k += 2) close = Math.min(close, X[j][k] - X[i][k]);
        if (close > BODY + 2.4) continue;
        if (dx < bd) { bd = dx; best = j; }
      }
      return best;
    };
    for (const i of order) {
      if (line[i]) continue;
      const run = R0.runs[i], going = run.mover ? run.how : null, w = ST_WIN[going || "none"];
      if (run.held) {                                    // said to be held up: in behind the horse in front, then the first gap
        const j = blocker(i);
        if (j != null) {
          const a = line[j].a;
          let kGap = kIn[i] + 20;                         // at least two seconds after turning in
          for (const k of near[i][j] || []) if (Math.abs(XD[j][k] - XD[i][k]) < BODY + 0.3) { kGap = Math.max(kGap, k + 15); break; }
          const lo = Math.max(1, e[i] - 1.2), hi = Math.min(LANE_MAX, e[i] + 2.8);
          for (let kS = kGap; kS < kEnd[i] - 10 && !line[i]; kS += 4) {
            let best = null, bc = Infinity;
            for (let c = lo; c <= hi + 1e-9; c += 0.05) { if (clash(i, c, kS)) continue; const v = Math.abs(c - a) < SIDE ? 9 : Math.abs(c - e[i]); if (v < bc) { bc = v; best = c; } }
            if (best != null) line[i] = { a, b: best, k: kS };
          }
          if (!line[i]) line[i] = { a, b: a, k: Infinity };
          line[i].wait = true;
          continue;
        }
      }
      const lo = Math.max(1, e[i] - (going === "round" ? 0.2 : ST_IN)), hi = Math.min(LANE_MAX, e[i] + w);
      let pref = e[i] + (going ? ST_OFF[going] : (H[i].plan.fan || 0));
      if (going === "round") for (let j = 0; j < N; j++) if (passes[i][j] && line[j]) pref = Math.max(pref, Math.min(hi, line[j].b + SIDE + 0.15));
      const want = c => Math.abs(c - pref) + (going === "through" && c > e[i] + 0.3 ? 0.15 * (c - e[i]) : 0) + (going === "round" && c < pref ? 1 : 0);
      const pick = k0 => { let best = null, bc = Infinity; for (let c = lo; c <= hi + 1e-9; c += 0.05) { if (clash(i, c, k0)) continue; const v = want(c); if (v < bc) { bc = v; best = c; } } return best; };
      const one = pick(0);
      if (one != null) { line[i] = { a: one, b: one, k: Infinity }; continue; }
      let a0 = null, ac = Infinity;                          // the least crowded line
      for (let c = lo; c <= hi + 1e-9; c += 0.05) { const v = want(c) + 3 * clash(i, c, 0); if (v < ac) { ac = v; a0 = c; } }
      const first = going === "through" ? Math.max(lo, Math.min(hi, e[i])) : a0;
      for (let kS = kIn[i] + 10; kS < kEnd[i] - 20 && !line[i]; kS += 4) {       // the gap: from a second in, every 0.4 s
        const b2 = pick(kS);
        if (b2 != null) line[i] = { a: first, b: b2, k: kS };
      }
      if (!line[i]) line[i] = { a: a0, b: a0, k: Infinity };
    }
    return line.map((l, i) => ({ lane: l.a, lane2: l.k < Infinity ? l.b : null, at: l.k < Infinity ? D - XD[i][Math.min(K - 1, l.k)] : null, entry: e[i], wait: !!l.wait }));
  }

  /** Build a replay: a first pass to the top of the straight, then the straight planned from every horse's curve.
   *  opts.said false: leave out what the comments say about traffic (to test the engine's own traffic against them). */
  function build(race, corrections, opts) {
    const R0 = simulate(race, corrections, null, opts);
    if (R0.geo.straight) return R0;
    const plan = straightPlan(R0);
    const R = simulate(race, corrections, plan, opts);
    R.straightPlan = plan;
    return R;
  }

  /** "lead", "cover" or "none" for horse i, given positions (m) and lanes. */
  function coverOf(xs, ls, i) {
    let ahead = false, covered = false;
    for (let j = 0; j < xs.length; j++) {
      if (j === i) continue;
      const dx = xs[j] - xs[i];
      if (dx > 0.3) {
        ahead = true;
        if (dx <= COVER_M && Math.abs(ls[j] - ls[i]) < COVER_DY) covered = true;
      }
    }
    if (!ahead) return "lead";
    return covered ? "cover" : "none";
  }

  /** Field state at time t: position (m from the start), lane, cover, gap to the leader (lengths), flags. */
  function frame(R, t) {
    const { H, Y, FL } = R;
    const kf = Math.max(0, Math.min(R.K - 1.001, t / DT)), k = Math.floor(kf), f = kf - k;
    const XD = R.XD;
    const xs = H.map((x, i) => (t <= 0 ? 0 : XD[i][k] + (XD[i][k + 1] - XD[i][k]) * f));
    const ls = H.map((x, i) => Y[i][k] + (Y[i][k + 1] - Y[i][k]) * f);
    const lead = Math.max(...xs), kk = Math.min(R.K - 1, Math.round(kf));
    let fl = H.map((x, i) => 0);
    for (let q = Math.max(0, kk - 4); q <= Math.min(R.K - 1, kk + 4); q++) fl = fl.map((a, i) => a | FL[i][q]);   // flags held for ~1 s
    return H.map((x, i) => ({ i, no: x.h.no, x: xs[i], lane: ls[i], cover: coverOf(xs, ls, i), gapL: (lead - xs[i]) / LEN,
      vy: (Y[i][Math.min(R.K - 1, k + 1)] - Y[i][Math.max(0, k - 1)]) / (2 * DT), held: !!(fl[i] & F_HELD), squeezed: !!(fl[i] & F_SQUEEZED),
      contact: !!(fl[i] & F_CONTACT), switching: !!(fl[i] & F_SWITCH), round: !!(fl[i] & F_ROUND), waiting: !!(fl[i] & F_WAIT) }));
  }

  /** Which lane source applies to horse i at distance to go d (for display): "you", "cor", "inc", "led" or null. */
  function sourceAt(R, i, d) {
    const pl = R.H[i].plan, inStraight = !R.geo.straight && d < R.geo.Lh;
    const list = (inStraight ? pl.st : pl.pre).filter(a => a.lane != null && a.ph !== "gate" && a.w >= 0.5);
    let above = null, below = null;                 // list runs from the start towards the post
    for (const a of list) { if (a.d >= d) above = a; else if (!below) below = a; }
    if (above) return above.src;                    // a stated lane holds until the next one
    if (below && (inStraight || d - below.d < 220)) return below.src;
    return null;
  }

  const api = { LEN, DT, BODY, SIDE, COVER_M, PHASES, F_HELD, F_SQUEEZED, F_CONTACT, F_SWITCH, F_ROUND, F_WAIT, geometry, bends, onBend, anchors, phaseAt, timeline, posAt, timeAt,
    build, frame, coverOf, sourceAt, planTarget };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ReplayCore = api;
})(typeof window !== "undefined" ? window : globalThis);

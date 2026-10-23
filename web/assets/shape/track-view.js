/* Track view (20 Oct 2026): the field on the course's own shape, as seen from the stands, with a camera that follows it.
   Shared by Race Shape Replays and the Race Shape Simulator.

   The course is the engine's own geometry (ReplayCore.geometry): two straights and two half-circle bends, the home
   straight along the bottom running right to left to the post at the bottom left, so the field goes round clockwise:
   up the left side after the post, left to right along the back straight at the top, down the right side round the
   home bend, then right to left up the straight. At Sha Tin the 1400, 1600 and 1800 start in a chute off the back
   straight; the 1000 is the straight course.

   Along the track every distance is to scale (metres). Across it everything is drawn 1.6 times as wide as it is, so that
   horses side by side read as side by side: the engine's one-metre lanes 1.6 m apart, and the course at its own width
   for the rail position (HKJC's figures: Sha Tin A 30.5 m down to C+3 18.3 m, the all-weather 22.8 m, Happy Valley A
   30.5 m down to C+3 19.5 m), so a course with the rail out looks as tight as it is.
   The camera pans and zooms with the field and never turns: north stays up, the stands stay at the bottom.
*/
(function (root) {
  "use strict";
  const WIDEN = 1.6;               // across the track, everything is drawn this many times as wide as it is
  const LANE_VIS = 1.0 * WIDEN;    // metres between lane centres, as drawn (the engine's lanes are a metre apart)
  const DOT_M = 0.78;              // a horse's radius, metres as drawn
  // the course's width by rail position (HKJC, Course Information), metres
  const WIDTH = { "ST Turf": { A: 30.5, "A+2": 28.5, "A+3": 27.5, B: 26, "B+2": 24, C: 21.3, "C+3": 18.3 }, "ST AWT": { AWT: 22.8 },
    "HV Turf": { A: 30.5, "A+2": 28.5, B: 26.5, "B+2": 24.5, "B+3": 23.5, C: 22.5, "C+3": 19.5 } };
  function widthOf(race) {
    const t = WIDTH[`${race.venue} ${race.surface}`] || {}, w = t[String(race.course || "").toUpperCase()] || t.A || 26;
    return w * WIDEN;
  }
  let TRACK_W = 30.5 * WIDEN;      // set per race in draw()

  /** World point (metres, y down) d metres from the post, on the inner rail, with the outward normal. Same shape as the
   *  mini-map: the plan turned half round, so the home straight runs along the bottom, right to left. */
  function railPoint(geo, d, ovalOnly) {
    if (geo.straight) return { x: d, y: 0, nx: 0, ny: 1 };       // the straight course: run right to left to the post
    const { Lh, B, Lb } = geo, r = B / Math.PI, xL = 0, xR = Lh, yC = 0;
    let p;
    if (!ovalOnly && geo.chute && d > geo.chute.from) p = { x: xL + (d - Lh - B), y: yC + r, nx: 0, ny: 1 };
    else {
      const u = ((d % geo.C) + geo.C) % geo.C;
      if (u < Lh) p = { x: xR - u, y: yC - r, nx: 0, ny: -1 };
      else if (u < Lh + B) { const a = ((u - Lh) / B) * Math.PI; p = { x: xL - r * Math.sin(a), y: yC - r * Math.cos(a), nx: -Math.sin(a), ny: -Math.cos(a) }; }
      else if (u < Lh + B + Lb) p = { x: xL + (u - Lh - B), y: yC + r, nx: 0, ny: 1 };
      else { const a = ((u - Lh - B - Lb) / B) * Math.PI; p = { x: xR + r * Math.sin(a), y: yC + r * Math.cos(a), nx: Math.sin(a), ny: Math.cos(a) }; }
    }
    // turned half round (as seen from the stands): x -> -x, y -> -y; the normal still points away from the infield
    return { x: -p.x, y: -p.y, nx: -p.nx, ny: -p.ny };
  }

  /** Where a horse is: d metres to go, in lane l (1 = on the rail). */
  function horsePoint(geo, d, lane) {
    const p = railPoint(geo, d, false), o = (Math.max(1, lane) - 0.5) * LANE_VIS;
    return { x: p.x + p.nx * o, y: p.y + p.ny * o, p };
  }

  /** The direction of running at d (unit vector towards the post), from two nearby points. */
  function heading(geo, d) {
    const a = railPoint(geo, d + 1, false), b = railPoint(geo, Math.max(0, d - 1), false);
    const dx = b.x - a.x, dy = b.y - a.y, m = Math.hypot(dx, dy) || 1;
    return { x: dx / m, y: dy / m };
  }

  /** Camera state: create once per canvas, pass to draw() every frame. */
  function camera() { return { x: 0, y: 0, s: 1, ready: false }; }

  /** Draw one frame. o: { R, F (ReplayCore.frame), t, sel, col (colours), clothOf, font, held(f), awt, snap, cam, wide } */
  function draw(cv, o) {
    const dpr = root.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { R, F, col } = o, geo = R.geo, D = R.race.distance, cam = o.cam;
    TRACK_W = Math.max(widthOf(R.race), 15 * LANE_VIS);             // a full gate always fits
    // where the horses are
    const P = F.map(f => { const d = D - f.x; return { f, d, ...horsePoint(geo, Math.max(-30, d), f.lane) }; });
    // the camera: the box round the field, plus room ahead of the leaders; the whole course when asked
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const box = pts => pts.forEach(p => { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); });
    if (o.wide) {
      const pts = [];
      for (let d = 0; d <= (geo.straight ? D : geo.C); d += 20) { const p = railPoint(geo, d, true); pts.push(p, { x: p.x + p.nx * TRACK_W, y: p.y + p.ny * TRACK_W }); }
      if (geo.chute) for (let d = geo.chute.from; d <= geo.chute.to; d += 20) { const p = railPoint(geo, d, false); pts.push(p, { x: p.x + p.nx * TRACK_W, y: p.y + p.ny * TRACK_W }); }
      if (geo.straight) pts.push({ x: 0, y: -20 }, { x: D, y: TRACK_W + 20 });
      box(pts);
    } else {
      box(P);
      const lead = P.reduce((a, p) => (p.d < a.d ? p : a), P[0]);
      const h = heading(geo, Math.max(0, lead.d));
      box([{ x: lead.x + h.x * 25, y: lead.y + h.y * 25 }]);          // a little room for where the leaders are going
      const tr = railPoint(geo, Math.max(0, lead.d), false);
      box([tr]);                                                    // and the rail beside them
    }
    const pad = o.wide ? 14 : 24;
    // close enough to read the numbers (about 9 px a metre on a desktop), far enough to see the bend coming
    const bw = Math.max(o.wide ? 50 : W < 520 ? 60 : 100, x1 - x0), bh = Math.max(o.wide ? 50 : 42, y1 - y0);
    const sT = Math.min(o.wide ? 12 : 11, (W - 2 * pad) / bw, (H - 2 * pad) / bh);
    const cxT = (x0 + x1) / 2, cyT = (y0 + y1) / 2;
    if (o.snap || !cam.ready) { cam.x = cxT; cam.y = cyT; cam.s = sT; cam.ready = true; }
    else { const k = 0.08; cam.x += (cxT - cam.x) * k; cam.y += (cyT - cam.y) * k; cam.s += (sT - cam.s) * k; }
    const s = cam.s, X = x => W / 2 + (x - cam.x) * s, Y = y => H / 2 + (y - cam.y) * s;

    // ground: infield, then the track as a band, then the rails
    g.fillStyle = col.infield; g.fillRect(0, 0, W, H);
    const path = (from, to, off, ovalOnly) => {
      g.beginPath(); let first = true;
      const step = Math.max(2, 3 / s);
      for (let d = from; d <= to + 1e-6; d += step) {
        const p = railPoint(geo, Math.min(d, to), ovalOnly);
        const px = X(p.x + p.nx * off), py = Y(p.y + p.ny * off);
        if (first) { g.moveTo(px, py); first = false; } else g.lineTo(px, py);
      }
    };
    const band = (from, to, ovalOnly) => {
      g.strokeStyle = o.awt ? col.awt : col.turf; g.lineWidth = TRACK_W * s; g.lineCap = "butt"; g.lineJoin = "round";
      path(from, to, TRACK_W / 2, ovalOnly); g.stroke();
    };
    if (geo.straight) band(0, D + 60, false);
    else { band(0, geo.C + 0.5, true); if (geo.chute) band(geo.chute.from, geo.chute.to + 40, false); }
    // lane guides, faint, on the stretch the field is on
    g.lineWidth = 1; g.strokeStyle = o.awt ? col.awtLine : col.turfLine;
    const span = 0.75 * Math.hypot(W, H) / s;                     // as far as the view reaches, either way
    const near0 = Math.max(0, Math.min(...P.map(p => p.d)) - span), near1 = Math.min(D + 40, Math.max(...P.map(p => p.d)) + span);
    if (s * LANE_VIS >= 6) for (let l = 1; l < 14; l++) { path(near0, near1, l * LANE_VIS, false); g.stroke(); }
    // rails: the inner (white) and the outer (dim)
    g.strokeStyle = col.rail; g.lineWidth = Math.max(1.5, 0.35 * s);
    if (geo.straight) { path(0, D + 60, 0, false); g.stroke(); }
    else { path(0, geo.C + 0.5, 0, true); g.stroke(); if (geo.chute) { path(geo.chute.from, geo.chute.to + 40, 0, false); g.stroke(); } }
    g.strokeStyle = col.railOuter; g.lineWidth = Math.max(1, 0.25 * s);
    if (geo.straight) { path(0, D + 60, TRACK_W, false); g.stroke(); }
    else { path(0, geo.C + 0.5, TRACK_W, true); g.stroke(); if (geo.chute) { path(geo.chute.from, geo.chute.to + 40, TRACK_W, false); g.stroke(); } }
    // distance markers on the inner rail every 100 m, labelled every 200 m (and the 100 m inside the last 400)
    g.font = `500 ${Math.max(9.5, Math.min(12, 3.2 * s))}px ${o.font}`; g.textAlign = "center"; g.textBaseline = "middle";
    for (let m = 100; m < D; m += 100) {
      const p = railPoint(geo, m, false), a = { x: X(p.x - p.nx * 1.5), y: Y(p.y - p.ny * 1.5) }, b = { x: X(p.x + p.nx * 4), y: Y(p.y + p.ny * 4) };
      if (a.x < -40 || a.x > W + 40 || a.y < -40 || a.y > H + 40) continue;
      g.strokeStyle = col.faint; g.lineWidth = 1.5; g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      if (m % 200 === 0 || m < 400 || o.wide) {
        if (o.wide && m % 400 !== 0 && m > 400) continue;
        const l = { x: X(p.x - p.nx * (o.wide ? 14 : 7)), y: Y(p.y - p.ny * (o.wide ? 14 : 7)) };
        g.fillStyle = col.muted; g.fillText(String(m), l.x, l.y);
      }
    }
    // the winning post and the start
    const across = (d, c, w) => {
      const p = railPoint(geo, d, false);
      g.strokeStyle = c; g.lineWidth = w; g.beginPath();
      g.moveTo(X(p.x - p.nx * 2), Y(p.y - p.ny * 2)); g.lineTo(X(p.x + p.nx * (TRACK_W + 2)), Y(p.y + p.ny * (TRACK_W + 2))); g.stroke();
      return p;
    };
    const post = across(0, col.post, Math.max(2, 0.5 * s));
    across(D, col.muted, Math.max(1.5, 0.3 * s));
    g.fillStyle = col.post; g.textAlign = "left";
    g.fillText("post", X(post.x - post.nx * 7) + 4, Y(post.y - post.ny * 7));

    // the selected horse's last second and a half
    const r = Math.max(o.wide ? 2.6 : 4.5, DOT_M * s);
    if (o.sel != null && o.trail) {
      const pts = o.trail();
      if (pts && pts.length > 1) {
        g.strokeStyle = o.clothOf(o.sel); g.globalAlpha = 0.45; g.lineWidth = Math.max(1.5, 0.5 * r); g.beginPath();
        pts.forEach((q, k) => { const w = horsePoint(geo, Math.max(-30, q.d), q.lane); k ? g.lineTo(X(w.x), Y(w.y)) : g.moveTo(X(w.x), Y(w.y)); });
        g.stroke(); g.globalAlpha = 1;
      }
    }
    // horses, back to front so the leaders sit on top
    const label = r >= 5.6;
    g.font = `600 ${Math.max(8.5, Math.min(12, r * 1.15))}px ${o.font}`; g.textAlign = "center"; g.textBaseline = "middle";
    for (const p of [...P].sort((a, b) => b.d - a.d)) {
      const f = p.f, sx = X(p.x), sy = Y(p.y);
      f.sx = sx; f.sy = sy;
      if (sx < -20 || sx > W + 20 || sy < -20 || sy > H + 20) continue;
      if (f.no === o.sel) { g.strokeStyle = col.edge; g.lineWidth = 2.5; g.beginPath(); g.arc(sx, sy, r + 3.5, 0, Math.PI * 2); g.stroke(); }
      g.beginPath(); g.arc(sx, sy, r + 1.1, 0, Math.PI * 2); g.fillStyle = o.awt ? col.awt : col.turf; g.fill();
      g.fillStyle = o.clothOf(f.no); g.beginPath(); g.arc(sx, sy, r, 0, Math.PI * 2); g.fill();
      if (label) { g.fillStyle = col.dotText; g.fillText(String(f.no), sx, sy + 0.5); }
      if (f.contact && o.t > 1.5) { g.strokeStyle = col.hit; g.lineWidth = 2; g.beginPath(); g.arc(sx, sy, r + 5, 0, Math.PI * 2); g.stroke(); }
      if (o.held && o.held(f)) {
        const h = heading(geo, Math.max(0, p.d)), nx = -h.y, ny = h.x;
        g.strokeStyle = col.hit; g.lineWidth = 3; g.beginPath();
        g.moveTo(sx + h.x * (r + 3) + nx * r, sy + h.y * (r + 3) + ny * r); g.lineTo(sx + h.x * (r + 3) - nx * r, sy + h.y * (r + 3) - ny * r); g.stroke();
      }
    }
    // when the dots are too small to number, the selected horse gets a tag
    if (!label && o.sel != null) {
      const p = P.find(q => q.f.no === o.sel);
      if (p) {
        const sx = X(p.x), sy = Y(p.y), txt = String(o.sel);
        g.font = `600 11px ${o.font}`;
        const w = g.measureText(txt).width + 8;
        g.fillStyle = o.clothOf(o.sel); g.fillRect(sx + r + 4, sy - 17, w, 15);
        g.fillStyle = col.dotText; g.textAlign = "center"; g.fillText(txt, sx + r + 4 + w / 2, sy - 9);
      }
    }
    g.textBaseline = "alphabetic";
    return { scale: s };
  }

  const api = { LANE_VIS, WIDEN, widthOf, railPoint, horsePoint, heading, camera, draw };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TrackView = api;
})(typeof window !== "undefined" ? window : globalThis);

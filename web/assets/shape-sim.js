/* Race shape — the simulation panel on the Speed Map (racesim-SPEC section 9.2).
 *
 * Each race still to run is simulated 20,000 times by jobs/sim_card, one race
 * every fifteen minutes from cron, anchored so every horse wins exactly as
 * often as v2 says. This panel reads that record; it never simulates the
 * 20,000 itself. What it adds to the ladder above is HOW the race may be run:
 * who leads, where each horse settles, who is caught wide or held up, what the
 * trip costs.
 *
 * SIM WIN EQUALS v2 WIN BY CONSTRUCTION. Both are shown, in the same ink and
 * in v2's order, and nothing here sorts, colours or flags the difference: it
 * is not information (gbm-SPEC section 10's GAP rule).
 *
 * WATCH A RUN draws one race in the browser with the same vendored engine the
 * job ran (web/assets/shape/), loaded on first use: the simulator builds a
 * race from the stored anchor, the engine steers it, and the trip it takes
 * decides its finish. "Another race" draws the next seed.
 *
 * The behaviour follows the lab's Simulator page (claude/tools/race-sim/
 * sim_app.js); the look is the Speed Map's.
 */
import { api } from './api.js';
import { el, DASH } from './vocab.js';

const PACE = ['Very Fast', 'Fast', 'Neutral', 'Slow', 'Very Slow'];
const PACE_TOKEN = { 'Very Fast': '--pace-5', Fast: '--pace-4', Neutral: '--pace-3',
                     Slow: '--pace-2', 'Very Slow': '--pace-1' };
const PH_LABEL = { settle: 'Settled', mid: 'Mid-race', turn: 'Home turn', straight: 'Straight' };
const TIED = 'The simulation is tied to v2’s chances; it shows how the race may be run, not who wins.';

const pct = (v, d = 0) => (v == null ? DASH : `${(100 * v).toFixed(d)}%`);
const ord = (n) => { const v = n % 100; return n + (v >= 11 && v <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th')); };
const title = (s) => String(s || '').split(/\s+/).map((w) => (w ? w[0] + w.slice(1).toLowerCase() : w)).join(' ');
const lens = (v) => `${Math.abs(v) < 0.05 ? '0.0' : Math.abs(v).toFixed(1)} L`;
const laneName = (l) => { const r = Math.max(1, Math.round(l)); return r === 1 ? 'rail' : r === 2 ? 'one out' : `${r} wide`; };
const clsTxt = (c) => (/^G/.test(String(c)) ? String(c) : `Class ${c}`);
const fmtDay = (d) => new Date(`${d}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const fmtAt = (iso) => new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit',
                                                              minute: '2-digit', timeZone: 'Asia/Hong_Kong' });

function cloth(no) {
  const s = el('span', 'sc', String(no));
  s.style.setProperty('--c', `var(--sc-${((no - 1) % 14) + 1})`);
  return s;
}

/** Load the vendored engine once, for its side effect (window.ReplayCore and friends). */
let engineLoad = null;
function loadEngine() {
  engineLoad ??= Promise.all([import('./shape/engine.js'), import('./shape/sim-core.js'),
                              import('./shape/track-view.js'), import('./shape/sectionals.js')]);
  return engineLoad;
}

export function createShapePanel(host) {
  let data = null;            // the race's simulation, as /api/shape/sim/{date}/{race} gives it
  let record = null;          // the season's record, /api/shape/record
  let sel = null;             // the horse picked in the table or on the course
  let token = 0;              // the newest show() call; an older answer is dropped
  const viewer = { open: false, run: null, R: null, sr: null, ctx: null, t: 0, playing: false, speed: 2,
                   seed: 1, wide: false, lastTs: null, cam: null, frame: null, raf: null };

  async function show(date, raceNo) {
    const mine = ++token;
    stop();
    viewer.open = false;
    host.replaceChildren(el('p', 'shape-empty', 'Loading the simulation…'));
    try {
      const [d, rec] = await Promise.all([api.shapeSim(date, raceNo),
                                          record ? Promise.resolve(record) : api.shapeRecord().catch(() => null)]);
      if (mine !== token) return;
      data = d;
      record = rec;
    } catch (e) {
      if (mine !== token) return;
      data = null;
      // Say what went wrong: "not simulated yet" and "the request failed" need different fixes.
      host.replaceChildren(el('p', 'shape-empty', e.message.startsWith('404')
        ? `Not simulated yet: ${e.message.replace(/^404\s*/, '')}.`
        : `Could not load the simulation: ${e.message}`));
      return;
    }
    const top = [...data.runners].sort((a, b) => b.v2_win - a.v2_win)[0];
    sel = top ? top.no : null;
    render();
  }

  /* ── the panel ──────────────────────────────────────────────────────── */
  function render() {
    host.replaceChildren(metaLine(), ...onDay(), paceMix(), table(), trust(), watch());
  }

  function metaLine() {
    const m = data.meta;
    const p = el('p', 'shape-line');
    const n = data.summary.sims.toLocaleString();
    p.append(`${n} races of R${data.race.no} · simulated `, el('b', null, fmtAt(m.simulated_at)),
             ` · anchored to v2 ${m.v2_version} · ${m.borrowed} borrowed clocks`);
    if (m.gone) p.append(' · as it stood at the off: the record it is scored on');
    if (m.changed_since.length) {
      p.append(' · ', el('span', 'warn', `Since then ${m.changed_since.join('; ')}: the next run of the job re-simulates it.`));
    }
    if (m.tables_version.startsWith('lab')) p.append(' · the lab’s simulation, frozen before the meeting');
    return p;
  }

  function onDay() {
    const A = data.actual;
    if (!A) return [];
    const name = (h) => `${title(h.name)} (${h.no})`;
    const p = el('p', 'shape-line');
    p.append(el('b', null, 'On the day: '),
             `${A.band ? A.band.toLowerCase() : 'unknown'} pace`,
             A.leader ? `; ${name(A.leader)} led, which the simulation gave ${pct(A.leader.p_lead)} to lead` : '',
             A.result.length ? `; ${A.result.map(name).join(', ')} the first three` : '',
             A.missing.length ? `. ${A.missing.map(name).join(', ')} did not run as simulated.` : '.');
    return [p];
  }

  function paceMix() {
    const box = el('div', 'shape-pace');
    box.append(el('span', 'label', 'PACE OVER THE SIMULATED RACES'));
    const pace = data.summary.pace, day = data.actual?.band;
    for (const k of PACE) {
      if (!pace[k]) continue;
      const chip = el('span', `pp${k === day ? ' day' : ''}`, `${k.toLowerCase()} ${pct(pace[k])}${k === day ? ' · on the day' : ''}`);
      chip.style.setProperty('--c', `var(${PACE_TOKEN[k]})`);
      chip.title = 'Share of the simulated races whose borrowed clock was run at this pace';
      box.append(chip);
    }
    box.append(el('span', 'small', `borrowed from ${data.meta.borrowed} real races of this course and trip, `
      + 'a class either side — not a forecast: pace was barely predictable in the study (R² 0.03)'));
    return box;
  }

  function table() {
    const cols = [['', 'l'], ['HORSE', 'l'], ['v2 WIN', 'v2'], ['v2 PLACE', ''], ['SIM WIN', ''], ['SIM TOP 3', 'sim'],
                  ['LEADS', ''], ['EARLY POSITION', ''], ['NO COVER', ''], ['3+ WIDE', ''], ['HELD UP', ''],
                  ['GROUND LOST', ''], ['WINS IF LED', '']];
    const t = el('table', 'shape-grid');
    const head = el('tr');
    for (const [label, cls] of cols) head.append(el('th', cls, label));
    t.append(el('thead'));
    t.tHead.append(head);
    const body = el('tbody');
    const sm = new Map(data.summary.runners.map((s) => [s.no, s]));
    const eng = new Map(data.engine.map((e) => [e.no, e]));
    for (const r of [...data.runners].sort((a, b) => b.v2_win - a.v2_win)) {
      const s = sm.get(r.no), e = eng.get(r.no);
      const tr = el('tr');
      tr.dataset.no = r.no;
      tr.setAttribute('aria-selected', String(r.no === sel));
      const c0 = el('td', 'l'); c0.append(cloth(r.no));
      const who = el('td', 'l');
      who.append(el('div', 'nm', title(r.name)), el('div', 'sub', `gate ${r.draw} · ${r.jockey ?? DASH}`));
      const v2 = el('td', 'v2', pct(r.v2_win, 1));
      if (r.v2_win_now != null && Math.abs(r.v2_win_now - r.v2_win) >= 0.005) {
        v2.append(el('div', 'now', `now ${pct(r.v2_win_now, 1)}`));
      }
      const fmb = el('div', 'fmb');
      fmb.title = `At the first call: front third ${pct(s.front)}, middle ${pct(s.mid)}, back third ${pct(s.back)}`;
      for (const [cls, v] of [['f', s.front], ['m', s.mid], ['b', s.back]]) {
        const seg = el('span', cls); seg.style.width = `${(100 * v).toFixed(1)}%`; fmb.append(seg);
      }
      const fmbCell = el('td'); fmbCell.append(fmb);
      tr.append(c0, who, v2, el('td', null, pct(r.v2_place, 1)), el('td', null, pct(r.sim_win, 1)),
                el('td', 'sim', pct(r.sim_top3, 1)), el('td', null, pct(s.lead)), fmbCell,
                el('td', null, pct(s.nocover)), el('td', null, pct(s.wide3)), el('td', null, pct(s.held)),
                el('td', null, `${e.ground_lengths.toFixed(1)} L`),
                el('td', s.led_n < 100 ? 'dim' : null, s.led_n < 100 ? DASH : pct(s.win_if_led)));
      tr.addEventListener('click', () => select(r.no));
      body.append(tr);
    }
    t.append(body);
    const wrap = el('div', 'shape-scroll');
    wrap.append(t);
    return wrap;
  }

  /** What the simulation is and is not good at, read from the result and the record, never typed in. */
  function trust() {
    const box = el('div', 'shape-trust');
    const ul = el('ul');
    const li = (tag, strong, text, bad) => {
      const x = el('li');
      x.append(el('span', `tag${bad ? ' no' : ''}`, tag), el('b', null, strong), ` ${text}`);
      ul.append(x);
    };
    li('TIED', TIED, `Over the ${data.summary.sims.toLocaleString()} races every horse wins within `
      + `${(100 * data.anchor.max_err).toFixed(2)} points of v2.`);
    const c = data.coupled_check;
    const failed = c.mean_win_gap_to_v2 > 2 * c.mc_sd_mean;
    li(failed ? 'CHECK FAILED' : 'CHECKED', 'When the animated trip decides the finish,',
       `each horse’s win share over ${c.runs} runs is ${(100 * c.mean_win_gap_to_v2).toFixed(1)} points from v2’s on `
       + `average against ${(100 * c.mc_sd_mean).toFixed(1)} that chance alone gives`
       + (failed ? ' — over twice it, so this race’s runs drift from v2.' : '.'), failed);
    const S = record?.season;
    if (S && S.horses) {
      li('RECORD', `${S.races} races scored since the record began:`,
         `the horse that led had been given a log loss of ${S.p_lead.log_loss} (${S.p_lead.uniform} for a guess); `
         + `first-call position missed by ${pct(S.first_call.mean_miss, 1)} of the field `
         + `(the Speed Map ${pct(S.first_call.speed_map, 1)}, mid-field for everyone ${pct(S.first_call.mid_field, 1)}).`);
      const skill = (k, label) => {
        const b = S[k];
        if (!b) return;
        li(b.skill ? 'RECORD' : 'NO SKILL YET', `${label}:`, `Brier ${b.brier} against ${b.base_rate_brier} for the `
           + `season-before rate, ${b.horses} horses${b.skill ? '.' : ' — not yet clear of the rate by twice its error.'}`,
           !b.skill);
      };
      skill('nocover', 'No cover');
      skill('wide3', 'Caught 3+ wide');
      skill('held', 'Held up in the straight');
    } else {
      li('RECORD', 'No race has been scored yet:', 'the record starts once a simulated race has been run.');
    }
    box.append(ul);
    return box;
  }

  /* ── watch a run ────────────────────────────────────────────────────── */
  function watch() {
    const box = el('div', 'shape-watch');
    if (!data.templates.length) {
      box.append(el('p', 'shape-run', 'No run to watch: the borrowed clocks this simulation used are not stored here.'));
      return box;
    }
    const btn = el('button', 'shape-btn', 'WATCH A RUN');
    btn.type = 'button';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'LOADING THE ENGINE…';
      await loadEngine();
      viewer.open = true;
      box.replaceChildren(...viewerDom());
      newRun(1);
    });
    box.append(btn);
    return box;
  }

  function viewerDom() {
    const tools = el('div', 'shape-tools');
    const play = button('PLAY', () => togglePlay());
    play.id = 'shape-play';
    const again = button('ANOTHER RACE', () => { newRun(viewer.seed + 1); setPlaying(true); });
    const speeds = seg([['½×', 0.5], ['1×', 1], ['2×', 2], ['4×', 4]], viewer.speed, (v) => { viewer.speed = v; });
    const views = seg([['CAMERA · FOLLOWS THE FIELD', false], ['WHOLE COURSE', true]], viewer.wide,
                      (v) => { viewer.wide = v; viewer.snap = true; });
    const jumps = el('div', 'shape-seg');
    jumps.id = 'shape-jumps';
    tools.append(play, again, speeds, el('span', 'spacer'), views);
    const stage = el('div', 'shape-stage');
    const cv = el('canvas');
    cv.id = 'shape-canvas';
    cv.tabIndex = 0;
    cv.setAttribute('role', 'img');
    cv.setAttribute('aria-label', 'One simulated run of the race on the course, seen from the stands: the home '
      + 'straight along the bottom, run right to left to the post');
    cv.addEventListener('click', (e) => pick(cv, e));
    cv.addEventListener('keydown', (e) => {
      if (e.key === ' ') { e.preventDefault(); togglePlay(); }
      else if (e.key === 'ArrowRight') { viewer.t = Math.min(end(), viewer.t + 0.5); setPlaying(false); }
      else if (e.key === 'ArrowLeft') { viewer.t = Math.max(0, viewer.t - 0.5); setPlaying(false); }
    });
    const hud = el('div', 'shape-hud');
    hud.id = 'shape-hud';
    stage.append(cv, hud);
    const scrub = el('div', 'shape-scrub');
    const range = el('input');
    range.type = 'range'; range.min = '0'; range.step = '0.05'; range.id = 'shape-scrub';
    range.addEventListener('input', () => { viewer.t = Number(range.value); setPlaying(false); });
    scrub.append(range);
    const info = el('p', 'shape-run'); info.id = 'shape-runinfo';
    const result = el('p', 'shape-run'); result.id = 'shape-result';
    const cols = el('div', 'shape-cols');
    const trip = el('div'); trip.id = 'shape-trip';
    const pace = el('div'); pace.id = 'shape-runpace';
    cols.append(trip, pace);
    const secs = el('div'); secs.id = 'shape-secs';
    return [tools, jumps, stage, scrub, info, result, cols, secs];
  }

  function button(label, fn) {
    const b = el('button', 'shape-btn', label);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }

  function seg(items, current, fn) {
    const box = el('div', 'shape-seg');
    for (const [label, v] of items) {
      const b = el('button', null, label);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(v === current));
      b.addEventListener('click', () => {
        fn(v);
        for (const x of box.children) x.setAttribute('aria-pressed', String(x === b));
      });
      box.append(b);
    }
    return box;
  }

  const q = (id) => host.querySelector(`#${id}`);
  const end = () => (viewer.R ? viewer.R.lastTime + 3 : 0);

  function newRun(seed) {
    const S = window.SimCore, C = window.ReplayCore;
    viewer.seed = seed;
    viewer.ctx ??= S.context(data.race, data.tables, 0, data.settle_sd, data.templates);
    viewer.run = S.coupled(viewer.ctx, data.anchor.a, seed, C, data.coupling);
    viewer.sr = viewer.run.sr;
    viewer.R = viewer.run.R;
    viewer.cam = window.TrackView.camera();
    viewer.t = 0;
    viewer.snap = true;
    setPlaying(false);
    q('shape-scrub').max = String(end());
    const tp = data.templates.find((x) => x.id === viewer.sr.sim.template);
    q('shape-runinfo').replaceChildren(`Run ${seed} · the clock of `,
      el('b', null, tp ? `${fmtDay(tp.id.slice(0, 10))} R${Number(tp.id.slice(11))}` : DASH),
      tp ? ` (${clsTxt(tp.cls)}, ${String(viewer.sr.sim.pace || 'unknown').toLowerCase()} pace), on this race’s standard` : '');
    q('shape-result').textContent = '';
    jumps();
    renderTrip();
    renderSectionals();
    viewer.raf ??= requestAnimationFrame(loop);
  }

  function jumps() {
    const C = window.ReplayCore, R = viewer.R, A = R.anchors;
    const leaderAt = (d) => Math.min(...R.H.map((x) => C.timeAt(x.tl, R.race.distance - d)));
    const items = [['GATES', 0]];
    for (const ph of C.PHASES) if (A[ph] != null) items.push([PH_LABEL[ph].toUpperCase(), leaderAt(A[ph])]);
    if (A.entry != null) items.push(['STRAIGHTENING', leaderAt(A.entry)]);
    items.sort((a, b) => a[1] - b[1]);
    items.push(['FINISH', R.winnerTime]);
    q('shape-jumps').replaceChildren(...items.map(([label, at]) => button(label, () => { viewer.t = at; setPlaying(false); })));
  }

  function renderTrip() {
    const box = q('shape-trip');
    const run = viewer.run, sr = viewer.sr, K = data.coupling;
    if (!run.coupled) { box.replaceChildren(el('span', 'label', 'WHAT THE TRIP DID')); return; }
    const w0 = run.sr1.runners.find((h) => h.place === 1), w1 = sr.runners.find((h) => h.place === 1);
    const line = w0.no === w1.no
      ? `The trip didn’t change the winner: ${title(w1.name)} (${w1.no}) wins with or without it.`
      : `The trip changed the winner: with its usual trip for every horse ${title(w0.name)} (${w0.no}) would have won; ${title(w1.name)} (${w1.no}) did.`;
    const t = el('table', 'tripcost');
    const head = el('tr');
    for (const h of ['FINISH', 'NO.', 'HORSE', 'GROUND (USUAL)', 'HELD UP', 'TRIP', 'USUAL TRIP']) head.append(el('th', null, h));
    t.append(el('thead'));
    t.tHead.append(head);
    const body = el('tbody');
    const rows = sr.runners.map((h, i) => ({ h, i, tr: run.trip[i] })).sort((a, b) => a.h.place - b.h.place);
    for (const { h, i, tr: tc0 } of rows) {
      const tc = h.sim.tripCost, p0 = run.sr1.runners[i].place;
      const tr = el('tr');
      tr.dataset.no = h.no;
      tr.setAttribute('aria-selected', String(h.no === sel));
      const c = el('td'); c.append(cloth(h.no));
      const ground = el('td', null, `${tc0.ground.toFixed(1)} `);
      ground.append(el('span', 'dim', `(${K.mg[i].toFixed(1)})`));
      tr.append(el('td', null, ord(h.place)), c, el('td', 'nm', title(h.name)), ground,
                el('td', null, tc0.heldL >= 0.05 ? `${tc0.heldL.toFixed(1)} L` : tc0.heldS >= 0.5 ? `waited ${tc0.heldS.toFixed(1)} s` : DASH),
                el('td', tc >= 0.5 ? 'down' : tc <= -0.5 ? 'up' : null,
                   Math.abs(tc) < 0.05 ? 'usual' : `${lens(tc)} ${tc > 0 ? 'worse' : 'better'}`),
                el('td', p0 === h.place ? 'dim' : null, p0 === h.place ? 'same' : ord(p0)));
      tr.addEventListener('click', () => select(h.no));
      body.append(tr);
    }
    t.append(body);
    box.replaceChildren(el('span', 'label', 'WHAT THE TRIP DID'), el('p', 'small', `${line} Ground lost on the bends and `
      + `any hold-up in the straight, in lengths, against each horse’s usual trip (its average over the job’s `
      + `${data.meta.settings.N_ENGINE} engine runs). The last column is where it finishes with its usual trip.`), t);
  }

  function renderSectionals() {
    const SX = window.Sectionals, rc = data.race, sr = viewer.sr;
    const lead = SX.leaderSplits(sr.runners.map((h) => h.secs));
    const rd = rc.par ? SX.reading(rc.distance, lead, rc.par, 0) : null;
    q('shape-runpace').innerHTML = SX.paceHTML(rd, rc.distance, rc.sections, {
      title: 'THIS RUN’S PACE', cls: /^G/.test(String(rc.cls)) ? 'Group race' : `Class ${rc.cls}`, leaderLabel: 'Leader, this run',
      note: 'The leader’s time at each mark in this run, read as the dashboard reads a real race (tempo-1.0) '
        + 'against HKJC’s standard for the class on a standard day. The pace is borrowed from a real race.' });
    q('shape-secs').innerHTML = SX.tableHTML(sr.runners, sr.distance, sr.sections, { sel, note: 'Simulated: one run.' });
  }

  function select(no) {
    sel = no;
    for (const tr of host.querySelectorAll('.shape-grid tbody tr, table.tripcost tbody tr')) {
      tr.setAttribute('aria-selected', String(Number(tr.dataset.no) === no));
    }
    if (viewer.open && viewer.R) renderSectionals();
  }

  function pick(cv, e) {
    if (!viewer.frame) return;
    const rect = cv.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
    let best = null, bd = 18;
    for (const f of viewer.frame) {
      const d = Math.hypot(f.sx - x, f.sy - y);
      if (d < bd) { bd = d; best = f; }
    }
    if (best) select(best.no);
  }

  function togglePlay() {
    if (!viewer.playing && viewer.t >= end() - 0.1) viewer.t = 0;
    setPlaying(!viewer.playing);
  }

  function setPlaying(on) {
    viewer.playing = on;
    const b = q('shape-play');
    if (b) b.textContent = on ? 'PAUSE' : viewer.t >= end() - 0.1 ? 'REPLAY' : 'PLAY';
  }

  /* ── drawing ────────────────────────────────────────────────────────── */
  let COL = null;
  function colours() {
    const css = getComputedStyle(document.documentElement);
    const tok = (n) => css.getPropertyValue(n).trim();
    return {
      surface: tok('--surface'), faint: tok('--text-faint'), dim: tok('--text-dim'), muted: tok('--text-muted'),
      edge: tok('--edge'), nocover: tok('--nocover'), hit: tok('--hit'), rail: tok('--rail'), post: tok('--post'),
      dot: tok('--dot'), dotText: tok('--dot-text'), railOuter: tok('--rail-outer'), infield: tok('--infield'),
      turf: tok('--turf'), turfBend: tok('--turf-bend'), turfLine: tok('--turf-line'),
      awt: tok('--awt'), awtBend: tok('--awt-bend'), awtLine: tok('--awt-line'),
      cloth: Array.from({ length: 14 }, (_, k) => tok(`--sc-${k + 1}`)),
      font: tok('--font-mono'),
    };
  }

  function draw() {
    const C = window.ReplayCore, R = viewer.R, sr = viewer.sr, t = viewer.t;
    const cv = q('shape-canvas');
    if (!cv || !R) return;
    COL ??= colours();
    const F = C.frame(R, t);
    const i = R.H.findIndex((x) => x.h.no === sel);
    window.TrackView.draw(cv, {
      // the camera snaps whenever the run is paused (a jump, the scrub) and follows smoothly while it plays,
      // as the lab's page does
      R, F, t, sel, col: COL, font: COL.font, awt: sr.surface === 'AWT', snap: viewer.snap || !viewer.playing,
      cam: viewer.cam,
      wide: viewer.wide, clothOf: (no) => COL.cloth[(no - 1) % 14] || COL.dot,
      held: (f) => sr.runners[f.i].sim.held && f.held && sr.distance - f.x < R.geo.Lh + 50,
      trail: () => {
        if (i < 0) return null;
        const pts = [];
        for (let k = 0; k <= 12; k++) {
          const g = C.frame(R, Math.max(0, t - 1.5 + k / 8))[i];
          pts.push({ d: sr.distance - g.x, lane: g.lane });
        }
        return pts;
      },
    });
    viewer.snap = false;
    viewer.frame = F;
    const lead = Math.max(...F.map((f) => f.x)), D = sr.distance, d = Math.max(0, D - lead);
    const ph = R.geo.straight ? (d > 650 ? 'early' : d >= 350 ? 'middle' : 'final 350 m')
      : PH_LABEL[C.phaseAt(D, d)].toLowerCase();
    q('shape-hud').textContent = `${t.toFixed(1)} s · ${t < 0.05 ? 'at the gates' : lead >= D ? 'past the post' : `${Math.round(d)} m to go · ${ph}`}`;
    q('shape-scrub').value = String(t);
    if (t >= R.winnerTime + 0.05 && !q('shape-result').textContent) {
      const order = [...sr.runners].sort((a, b) => a.place - b.place).slice(0, 4);
      q('shape-result').textContent = `This run: ${order.map((h) => `${ord(h.place)} #${h.no} ${title(h.name)}`
        + (h.lbw ? ` (${h.lbw.toFixed(1)} L)` : '')).join(' · ')}. One draw of ${data.summary.sims.toLocaleString()}; `
        + 'the table is the many.';
    }
  }

  function loop(ts) {
    viewer.raf = null;
    if (!viewer.open || !host.isConnected) return;
    if (viewer.playing && viewer.lastTs != null) {
      viewer.t += ((ts - viewer.lastTs) / 1000) * viewer.speed;
      if (viewer.t >= end()) { viewer.t = end(); setPlaying(false); }
    }
    viewer.lastTs = ts;
    draw();
    viewer.raf = requestAnimationFrame(loop);
  }

  function stop() {
    if (viewer.raf) cancelAnimationFrame(viewer.raf);
    Object.assign(viewer, { raf: null, open: false, run: null, R: null, sr: null, ctx: null, playing: false, lastTs: null });
  }

  return { show };
}

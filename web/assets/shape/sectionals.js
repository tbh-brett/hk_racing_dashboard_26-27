/* Sectionals and pace, shared by Race Shape Replays and the Race Shape Simulator (20 Oct 2026).

   The pace reading is the dashboard's own (hkrd/derive/tempo.py, tempo-1.0), so a simulated race and a real one are
   read the same way:
   - the leader's time for each section is the fastest cumulative time any runner recorded at that mark, differenced;
   - the early reading runs to the 800 m mark (to the 400 m at 1000 m, where the first section is the 200 m jump);
   - it is read against HKJC's standard sectionals for the course, distance and class, scaled by the day's track speed;
   - the band comes from seconds per 400 m against that standard: Very Fast < -0.37 <= Fast < -0.15 <= Neutral < 0.17
     <= Slow < 0.42 <= Very Slow. Negative is faster.
*/
(function (root) {
  "use strict";
  const CUTS = [-0.37, -0.15, 0.17, 0.42];
  const BANDS = ["Very Fast", "Fast", "Neutral", "Slow", "Very Slow"];
  const VERSION = "tempo-1.0";

  function band(dev400) {
    if (dev400 == null || !isFinite(dev400)) return null;
    for (let k = 0; k < CUTS.length; k++) if (dev400 < CUTS[k]) return BANDS[k];
    return BANDS[BANDS.length - 1];
  }

  /** The leader's time for each section: the fastest cumulative time at each mark, differenced. Only runners with
   *  every section count. */
  function leaderSplits(rows) {
    const full0 = rows.filter(s => s && s.length);
    if (!full0.length) return null;
    const n = Math.max(...full0.map(s => s.length));
    const full = full0.filter(s => s.length === n && s.every(x => x > 0));
    if (!full.length) return null;
    const lead = [];
    for (let k = 0; k < n; k++) lead.push(Math.min(...full.map(s => s.slice(0, k + 1).reduce((a, b) => a + b, 0))));
    return lead.map((c, k) => k ? c - lead[k - 1] : c);
  }

  /** Metres in each section: the first takes whatever is left over (the dashboard's own rule). */
  function lengths(distance, n) { const first = distance - 400 * (n - 1); return [first, ...Array(n - 1).fill(400)]; }
  const earlySections = (distance, n) => (distance === 1000 ? 2 : Math.max(1, n - 2));

  /** One race's reading. lead: the leader's sections; std: the standard's sections (unscaled); variant: the day's track
   *  speed (0 = a standard day). */
  function reading(distance, lead, std, variant) {
    if (!lead || !std || lead.length !== std.length) return null;
    const n = lead.length, k = earlySections(distance, n), L = lengths(distance, n);
    const scale = 1 + (variant || 0), early = L.slice(0, k).reduce((a, b) => a + b, 0);
    const sum = a => a.reduce((x, y) => x + y, 0);
    const earlyTime = sum(lead.slice(0, k)), earlyStd = sum(std.slice(0, k)) * scale;
    const dev = earlyTime - earlyStd, dev400 = dev * 400 / early;
    return { earlyTo: distance === 1000 ? 400 : 800, earlyTime, earlyStd, dev, dev400, lateTime: lead[n - 1],
      lateStd: std[n - 1] * scale, lateDev: lead[n - 1] - std[n - 1] * scale, band: band(dev400),
      std: std.map(x => x * scale), lead, total: sum(lead), stdTotal: sum(std) * scale, variant: variant || 0 };
  }

  /** "1:47.63", or "56.95" under a minute. */
  function fmt(t) {
    if (t == null || !isFinite(t)) return "–";
    const m = Math.floor(t / 60), s = t - 60 * m;
    return m ? `${m}:${s.toFixed(2).padStart(5, "0")}` : s.toFixed(2);
  }
  const signed = (x, d = 2) => (x == null || !isFinite(x) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "±") + Math.abs(x).toFixed(d));

  /** The mark each section ends at, as metres to go. */
  function marks(distance, sections) {
    const out = []; let cum = 0;
    for (const L of sections) { cum += L; out.push(distance - cum); }
    return out;
  }
  const markLabel = m => (m === 0 ? "post" : `${m}`);

  const BAND_CLASS = { "Very Fast": "vf", Fast: "f", Neutral: "n", Slow: "s", "Very Slow": "vs" };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** The pace strip: the band, a scale with the reading marked, and the leader's sections against the standard.
   *  rd: reading(); opts: { title, cls (class label), note, compare: { label, rd } } */
  function paceHTML(rd, distance, sections, opts = {}) {
    if (!rd) return `<div class="pace"><span class="label">${esc(opts.title || "Pace")}</span> <span class="small">No standard to read this race against.</span></div>`;
    const lo = -0.8, hi = 0.8, pos = v => Math.max(0, Math.min(100, 100 * (v - lo) / (hi - lo)));
    const seg = [lo, ...CUTS, hi];
    const scale = BANDS.map((b, k) => `<span class="bs ${BAND_CLASS[b]}" style="left:${pos(seg[k]).toFixed(2)}%;width:${(pos(seg[k + 1]) - pos(seg[k])).toFixed(2)}%"></span>`).join("");
    const pin = (v, cls, lab) => `<span class="pin ${cls}" style="left:${pos(v).toFixed(2)}%" title="${esc(lab)}"></span>`;
    const cmp = opts.compare && opts.compare.rd ? opts.compare : null;
    const mk = marks(distance, sections);
    const quick = rd.dev < 0, cls = opts.cls ? `${esc(opts.cls)} standard` : "the standard";
    const day = rd.variant ? " for the day" : "";
    const head = `<b>${fmt(rd.earlyTime)}</b> to the ${rd.earlyTo} m, ${Math.abs(rd.dev).toFixed(2)} s ${quick ? "quicker" : "slower"} than the ${cls}${day}`
      + ` (${signed(rd.dev400)} s per 400 m) · last 400 m <b>${rd.lateTime.toFixed(2)}</b> (standard ${rd.lateStd.toFixed(2)})`
      + ` · time <b>${fmt(rd.total)}</b> (standard ${fmt(rd.stdTotal)})`;
    const row = (lab, vals, f, c) => `<tr><th scope="row">${lab}</th>${vals.map((v, k) => `<td class="${c ? c(v, k) : ""}">${f(v, k)}</td>`).join("")}</tr>`;
    const diffCls = v => (v < -0.05 ? "q" : v > 0.05 ? "sl" : "");
    const table = `<div class="sect-scroll"><table class="sect"><thead><tr><th scope="col">Section to</th>${mk.map((m, k) => `<th scope="col">${markLabel(m)}<span>${sections[k]} m</span></th>`).join("")}</tr></thead><tbody>
      ${row(esc(opts.leaderLabel || "Leader"), rd.lead, v => v.toFixed(2))}
      ${row("Standard", rd.std, v => v.toFixed(2))}
      ${row("Leader − standard", rd.lead.map((v, k) => v - rd.std[k]), v => signed(v), diffCls)}
      ${cmp ? row(esc(cmp.label), cmp.rd.lead, v => v.toFixed(2)) : ""}
      </tbody></table></div>`;
    return `<div class="pace">
      <div class="pace-head"><span class="label">${esc(opts.title || "Pace")}</span><span class="band ${BAND_CLASS[rd.band]}">${esc(rd.band)}</span>
        ${cmp ? `<span class="band ghost ${BAND_CLASS[cmp.rd.band]}" title="${esc(cmp.label)}">${esc(cmp.label)}: ${esc(cmp.rd.band)}</span>` : ""}</div>
      <p class="small">${head}.</p>
      <div class="bandscale" aria-hidden="true">${scale}${cmp ? pin(cmp.rd.dev400, "ghost", cmp.label) : ""}${pin(rd.dev400, "", opts.title || "this race")}
        <span class="bl" style="left:0">quicker</span><span class="bl" style="right:0">slower</span></div>
      ${table}
      ${opts.note ? `<p class="small dim">${opts.note}</p>` : ""}
    </div>`;
  }

  /** Every horse's sectionals, as HKJC prints them: position and time at each section, then the finish.
   *  runners: [{ no, name, place, code, secs, pos, finish, lbw }] */
  function tableHTML(runners, distance, sections, opts = {}) {
    const mk = marks(distance, sections);
    const rows = [...runners].sort((a, b) => (a.place || 99) - (b.place || 99));
    const lead = leaderSplits(rows.map(r => r.secs));
    const title = s => String(s || "").split(/\s+/).map(w => w ? w[0] + w.slice(1).toLowerCase() : w).join(" ");
    const sc = no => `<span class="sc" style="--c:var(--sc-${(no - 1) % 14 + 1})">${no}</span>`;
    const body = rows.map(r => {
      const cells = sections.map((L, k) => {
        const t = r.secs && r.secs[k], p = r.pos && r.pos[k];
        const best = lead && t != null && Math.abs(t - Math.min(...rows.map(x => (x.secs && x.secs[k]) || Infinity))) < 0.005;
        return `<td><span class="p">${p || "–"}</span> <span class="${best ? "best" : ""}">${t != null ? t.toFixed(2) : "–"}</span></td>`;
      }).join("");
      const fin = r.place ? (r.place === 1 ? "1st" : `${r.place}<span class="small"> · ${r.lbw != null ? r.lbw.toFixed(2).replace(/\.00$/, "") + " L" : ""}</span>`) : esc(r.code || "–");
      return `<tr${opts.sel === r.no ? ' aria-selected="true"' : ""}><td class="fin">${fin}</td><td>${sc(r.no)}</td><td class="nm">${esc(title(r.name))}</td>${cells}<td class="mono">${fmt(r.finish)}</td></tr>`;
    }).join("");
    return `<div class="sect-scroll"><table class="sect all"><thead><tr><th scope="col">Pl</th><th scope="col">No.</th><th scope="col">Horse</th>
      ${mk.map((m, k) => `<th scope="col">to ${markLabel(m)}<span>${sections[k]} m</span></th>`).join("")}<th scope="col">Time</th></tr></thead><tbody>${body}</tbody></table></div>
      <p class="small dim">Position at each mark, then the horse's own time for the section; the fastest time for each section is in bold.${opts.note ? " " + opts.note : ""}</p>`;
  }

  const api = { CUTS, BANDS, VERSION, band, leaderSplits, lengths, reading, fmt, signed, marks, paceHTML, tableHTML };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Sectionals = api;
})(typeof window !== "undefined" ? window : globalThis);

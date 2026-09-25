/* briefing-model.js — the Briefing's reading of the data, and no DOM.
 *
 * Everything here turns what the endpoints return into what one race row, one
 * backed horse or one source says: the marks that keep a pick, a featured
 * segment and an interview apart, the dashboard's own signal chips, and the
 * price comparison. Kept apart from the rendering so the desktop row and the
 * phone card read the same facts from one place and cannot drift.
 *
 * A PRICE ALWAYS SITS BESIDE SUPPORT. Ordering by how many sources agree
 * points at short prices (the tips handover says so); the price table is the
 * counterweight, never a footnote. And nothing here ranks, recolours or
 * re-orders the Race Day card — this page orders by support, the card keeps
 * its own order.
 */
import { DASH, MINUS, classLabel, isLiveBooking } from './vocab.js';

/* The sources the page expects, as the Sources panel and a race's SOURCES
 * cell list them. `usual` is the designed empty state for one not yet in. */
export const SRC = {
  racing_sports: { ab: 'R&S', name: 'Racing & Sports',
    usual: 'Racing & Sports usually arrives with the bookmakers’ prices' },
  rtw_preview: { ab: 'RTW', name: 'Racing To Win preview',
    usual: 'Racing To Win preview usually posts about 16:00 the day before' },
  rtw_interview: { ab: 'INT', name: 'Racing To Win interview',
    usual: 'Racing To Win interviews usually post with the preview, about '
      + '16:00 the day before' },
  factcheck: { ab: 'FC', name: '賽馬Fact Check',
    usual: 'Fact Check usually posts at 20:00 two days before' },
  threads: { ab: 'HD', name: '神探賽馬 Horse Detective',
    usual: 'Horse Detective posts on race-day morning, when it posts at all' },
  bryan: { ab: 'BRY', name: '全方位Bryan', usual: '' },
};
export const SRC_ORDER = ['racing_sports', 'rtw_preview', 'rtw_interview',
                          'factcheck', 'threads'];

/* The markets, tote first. A bookmaker the meeting has no prices from is left
 * out of the table rather than shown as a column of dashes. */
export const MARKET = {
  tote: { label: 'TOTE', short: 'T' },
  ladbrokes: { label: 'LADBROKES', short: 'LB' },
  sportsbet: { label: 'SPORTSBET', short: 'SB' },
  unibet: { label: 'UNIBET', short: 'UB' },
};

export const EDGE_AT = 5;          // value worth marking: +5% or better
export const FOLD_AT = 90;         // characters before a quote folds
const STALE_AFTER = 10;            // minutes, for a tote on race day

/* ── formatting ─────────────────────────────────────────────────────────── */

/** A price as the tote board writes one: 15, 4.4, 2.35. */
export function px(v) {
  if (v === null || v === undefined) return DASH;
  if (v >= 10 && v % 1 === 0) return String(v);
  const s = String(+Number(v).toFixed(2));
  return s.includes('.') ? s : `${s}.0`;
}

/** A signed percentage with a true minus sign. */
export function sgn(v) {
  if (v === null || v === undefined) return DASH;
  return `${v > 0 ? '+' : v < 0 ? MINUS : '±'}${Math.abs(v).toFixed(1)}%`;
}

export const pct = (v) => (v === null || v === undefined ? DASH
  : `${Number(v).toFixed(1)}%`);

/** Seconds into a video as m:ss. */
export const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

export function secondsIn(w) {
  if (w.t !== null && w.t !== undefined) return w.t;
  const m = /[?&]t=(\d+)s/.exec(w.url || '');
  return m ? Number(m[1]) : null;
}

export const money = (v) => (v === null || v === undefined ? DASH
  : `$${Math.round(v).toLocaleString('en-US')}`);

export const hhmm = (stamp) => (stamp ? stamp.slice(11, 16) : null);

/* ── time, in Hong Kong whatever the reader's clock says ─────────────────── */

export function hkNow() {
  const d = new Date(Date.now() + 8 * 3600e3);
  const iso = d.toISOString();
  return { date: iso.slice(0, 10), minute: d.getUTCHours() * 60 + d.getUTCMinutes(),
           stamp: iso.slice(0, 16) };
}

const toMin = (hm) => (hm ? Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5)) : null);

/** Minutes from `stamp` (HKT, YYYY-MM-DDTHH:MM) to now. */
export function ageMinutes(stamp, now) {
  if (!stamp) return null;
  const then = Date.parse(`${stamp}:00Z`);
  const at = Date.parse(`${now.stamp}:00Z`);
  return Math.round((at - then) / 60000);
}

/** Has this race run? Past meetings have; today's by its off time. */
export function raceState(date, off, now) {
  if (date < now.date) return { run: true, minsTo: null };
  if (date > now.date) return { run: false, minsTo: null };
  const m = toMin(off);
  if (m === null) return { run: false, minsTo: null };
  return { run: m <= now.minute, minsTo: m - now.minute };
}

/* ── support ────────────────────────────────────────────────────────────── */

/** How a source's support is marked. Three kinds, and they must not look
 *  alike: a pick with its rank, a featured segment (dashed), and the jockey
 *  or trainer interviewed (the voice colour). `heard` travels as a quiet ~. */
export function mark(b) {
  const ab = SRC[b.source]?.ab ?? b.source.toUpperCase();
  if (b.kind === 'connections') {
    return { t: `${ab} ${b.role === 'trainer' ? 'T' : 'J'}`, kind: 'voice', heard: b.heard };
  }
  if (b.kind === 'featured') return { t: `${ab} FEAT`, kind: 'feat', heard: b.heard };
  return { t: ab + (b.rank ? ` #${b.rank}` : ''), kind: 'pick', heard: b.heard };
}

export const countLabel = (n) => `${n} source${n === 1 ? '' : 's'}`;

/** A horse's supporters, in the page's order: most distinct sources, then
 *  best rank, then shortest tote price. Sources not yet published are not
 *  support yet. */
export function orderPicks(picks) {
  return picks.map((p) => ({
    p, who: new Set(p.backed_by.map((b) => `${b.source}|${b.who}`)).size,
    best: Math.min(...p.backed_by.map((b) => b.rank || 9)),
  })).sort((a, b) => b.who - a.who || a.best - b.best
    || (a.p.odds.tote.win ?? 999) - (b.p.odds.tote.win ?? 999));
}

/* ── the dashboard's own signals, from the Race Day card ─────────────────── */

/** The signal chips on one runner. Each colour is the one it has everywhere
 *  else in the app: teal blackbook, amber model, red firming, the gear and
 *  violet trainer hues. `k` is the flag name a race row aggregates by. */
export function chips(r) {
  if (!r) return [];
  const out = [];
  if (isLiveBooking(r.blackbook)) out.push({ t: 'BB', k: 'BB', tone: 'book' });
  if (r.rank_delta !== null && r.rank_delta !== undefined && r.rank_delta <= -2) {
    out.push({ t: `MODEL +${-r.rank_delta}`, k: 'MODEL', tone: 'edge' });
  }
  const m = r.movement;
  if (m && m.rush_direction === 'shortened' && Math.abs(m.rush_pct ?? 0) >= 10) {
    out.push({ t: `LATE $ ${Math.round(Math.abs(m.rush_pct))}%`, k: 'LATE $', tone: 'alert' });
  }
  const first = (r.gear_change?.pieces ?? []).filter((p) => p.state === 'first')
    .map((p) => p.code);
  if (first.length) out.push({ t: `1ST ${first.join(' ')}`, k: '1ST GEAR', tone: 'gear' });
  const vet = (r.vet_form ?? []).length
    || (r.vet ?? []).some((v) => v.grade === 'significant');
  if (vet) out.push({ t: 'VET', k: 'VET', tone: 'vet' });
  if (r.trainer_changed) out.push({ t: 'NEW TR', k: 'NEW TR', tone: 'violet' });
  return out;
}

/** One race's flags: each chip kind once, with how many runners carry it. */
export function raceFlags(runners) {
  const agg = new Map();
  runners.forEach((r) => chips(r).forEach((c) => {
    const had = agg.get(c.k);
    agg.set(c.k, had ? { ...had, n: had.n + 1 } : { ...c, n: 1 });
  }));
  return [...agg.values()].map((c) => ({ ...c, t: c.k + (c.n > 1 ? ` ${c.n}` : '') }));
}

/* ── the markets ────────────────────────────────────────────────────────── */

/** The markets on screen, and what each one's capture time says. */
export function markets(summary, date, now, lastOff) {
  const tote = summary.captured?.tote ?? null;
  const age = ageMinutes(tote, now);
  const stale = tote && date === now.date && lastOff !== null
    && now.minute < lastOff && age > STALE_AFTER;
  const list = [{ k: 'tote', ...MARKET.tote, on: !!tote, at: tote, stale,
                  sub: tote ? `${hhmm(tote)}${stale ? ` STALE ${age}m` : ''}` : 'NOT OPEN' }];
  (summary.books ?? []).forEach((k) => {
    const at = summary.captured?.[k] ?? null;
    list.push({ k, ...(MARKET[k] ?? { label: k.toUpperCase(), short: k.slice(0, 2).toUpperCase() }),
                on: !!at, at, stale: false, sub: at ? hhmm(at) : 'NOT YET' });
  });
  return list;
}

/** The race's largest disagreement between the markets: the listed edges
 *  first, then every backed horse's own, best value first. */
export function biggestGap(raceNo, edges, picks) {
  const seen = new Set();
  const out = [];
  edges.filter((e) => e.race_no === raceNo).forEach((e) => {
    seen.add(`${e.horse_no}|${e.bet_at}`);
    out.push({ no: e.horse_no, name: e.horse_name, at: e.bet_at, price: e.price,
               against: e.against, fair: e.fair_pct, ev: e.ev_pct, sup: e.supporters });
  });
  picks.forEach(({ p, who }) => {
    Object.entries(p.odds).forEach(([at, o]) => {
      if (!o || typeof o !== 'object' || o.value_pct === null) return;
      if (seen.has(`${p.horse_no}|${at}`)) return;
      out.push({ no: p.horse_no, name: p.horse_name, at, price: o.win,
                 against: at === 'tote' ? 'books' : 'tote',
                 fair: at === 'tote' ? p.odds.books_fair_pct : p.odds.tote.fair_pct,
                 ev: o.value_pct, sup: who });
    });
  });
  return out.sort((a, b) => b.ev - a.ev);
}

export const atShort = (k) => MARKET[k]?.short === 'T' ? 'TOTE' : (MARKET[k]?.short ?? k);
export const againstShort = (k) => (k === 'books' ? 'BOOKS' : 'TOTE');

/** "at TOTE 15 · BOOKS fair 9.6%" — a value never goes out without both. */
export function gapLine(g) {
  return `at ${atShort(g.at)} ${px(g.price)} · ${againstShort(g.against)} fair ${pct(g.fair)}`;
}

/* ── the race's own line ─────────────────────────────────────────────────── */

export function raceLabel(rd) {
  return { dist: rd?.distance ? `${rd.distance}m` : DASH,
           cls: classLabel(rd?.race_class) ?? DASH };
}

export const BAND_LEVEL = { strong: 3, moderate: 2, weak: 1 };

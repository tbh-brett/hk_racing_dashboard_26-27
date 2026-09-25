/* briefing-model.js — the Briefing's reading of /api/briefing, and no DOM.
 *
 * Ported from the logic of web/design-source/Briefing.dc.html (Claude Design,
 * 25 Sep 2026). Everything here turns the server's one answer per race into
 * what a race row, an opened race and a runner say, so the desktop list and
 * the phone list read the same facts from one place.
 *
 * Three kinds of evidence, never added together: SCREEN (computed from the
 * card), SAID (a source said it) and PRICE (a market priced it). The order
 * of races is the server's — a count of things to read, not a chance of
 * anything — and nothing here re-ranks the Race Day card.
 */
import { DASH, MINUS } from './vocab.js';

export const STAGE_NAME = {
  cold: 'TWO DAYS OUT', voices: 'NIGHT BEFORE', priced: 'DAY BEFORE · TOTE OPEN',
  race_day: 'RACE DAY', settled: 'SETTLED',
};
export const AB = {
  factcheck: 'FC', rtw_preview: 'RTW', rtw_interview: 'INT', tote: 'TOTE',
  racing_sports: 'R&S', ladbrokes: 'LB', sportsbet: 'SB', threads: 'HD', bryan: 'BRY',
};
/* The voices counted as support. Anything else a source said is shown with
 * "shown, not counted" and a dotted mark. */
export const COUNTED = new Set(['factcheck', 'rtw_preview', 'rtw_interview',
                                'racing_sports', 'threads']);
export const BOOKS = ['tote', 'ladbrokes', 'sportsbet'];
export const BK = { tote: 'TOTE', ladbrokes: 'LB', sportsbet: 'SB' };
export const STYLE_SHORT = { Leader: 'LD', 'On-Pace': 'OP', Midfield: 'MF', Closer: 'CL' };
export const STYLES = ['Leader', 'On-Pace', 'Midfield', 'Closer'];
/* Which reason leads a race: an interview first, then a disagreement between
 * the kinds of evidence, then the rest. */
const PRI = { interview: 0, screen_alone: 1, talked_up: 2, price_gap: 3,
              late_money: 3, market_apart: 3, lone_leader: 4, book: 5,
              consensus: 6, case: 7, trial: 8 };
export const FOLD_AT = 110;
const STALE_AFTER = 15;          // minutes, for a race-day price
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
             'Oct', 'Nov', 'Dec'];

/* ── formatting ─────────────────────────────────────────────────────────── */

const pd = (s) => new Date(s.length === 10 ? `${s}T00:00` : s);
export const hm = (s) => (s ? s.slice(11, 16) : '');
const dayDiff = (a, b) => Math.round((pd(a.slice(0, 10)) - pd(b.slice(0, 10))) / 864e5);
const minutesBetween = (a, b) => Math.round((pd(a) - pd(b)) / 6e4);

/** "tonight 20:00", "tomorrow 16:00", "Sat 09:00" — against the as-of time. */
export function when(iso, asOf) {
  const dd = dayDiff(iso, asOf);
  const t = hm(iso);
  if (dd === 0) return `${Number(t.slice(0, 2)) >= 18 ? 'tonight' : 'today'} ${t}`;
  if (dd === 1) return `tomorrow ${t}`;
  if (dd === -1) return `yesterday ${t}`;
  return `${DAYS[pd(iso).getDay()]} ${t}`;
}
const atShort = (iso, asOf) => (dayDiff(iso, asOf) === 0 ? hm(iso)
  : `${DAYS[pd(iso).getDay()]} ${hm(iso)}`);

export function px(v) {
  if (v === null || v === undefined) return DASH;
  if (v >= 10) return String(Math.round(v * 10) / 10);
  const s = String(+Number(v).toFixed(2));
  return s.includes('.') ? s : `${s}.0`;
}
export const sgn = (v) => `${v > 0 ? '+' : v < 0 ? MINUS : '±'}${Math.abs(v).toFixed(1)}%`;
export const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const tFromUrl = (u) => { const m = /[?&]t=(\d+)s/.exec(u || ''); return m ? Number(m[1]) : null; };
export const money = (v) => (v === null || v === undefined ? DASH
  : `$${Math.round(v).toLocaleString('en-US')}`);
export const moneyS = (v) => (v === null || v === undefined ? DASH
  : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${Math.round(v / 1e3)}K`);
export const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const shortDate = (iso) => `${Number(iso.slice(8, 10))} ${MON[Number(iso.slice(5, 7)) - 1]}`;

/** Quotes, as the renderer folds them: verbatim, with the second they were
 *  said (or "page" for a written source). */
export function words(list) {
  return (list || []).filter((w) => w && w.text).map((w) => {
    const t = w.t !== null && w.t !== undefined ? w.t : tFromUrl(w.url);
    return { text: w.text, en: w.en || null, url: w.url || null,
             tl: t !== null ? clock(t) : 'page', long: w.text.length > FOLD_AT };
  });
}

/* ── the three kinds, and the marks ─────────────────────────────────────── */

export function kindTag(x) {
  if (x.kind === 'computed') return { tag: 'SCREEN', tone: 'screen' };
  if (x.kind === 'said') return { tag: 'SAID', tone: x.key === 'interview' ? 'voice' : 'tip' };
  return { tag: 'PRICE', tone: 'price' };
}

/** A pick with its rank, a featured segment, an interview — three kinds of
 *  support that must not look alike — or a voice shown and not counted. */
export function mark(b) {
  const ab = AB[b.source] || b.source.toUpperCase();
  if (b.kind === 'connections') {
    return { t: `INT ${b.role === 'trainer' ? 'T' : 'J'}`, tone: 'voice', heard: !!b.heard };
  }
  if (!COUNTED.has(b.source)) {
    return { t: ab + (b.rank ? ` #${b.rank}` : ''), tone: 'shown', heard: !!b.heard };
  }
  if (b.kind === 'featured') return { t: `${ab} FEAT`, tone: 'feat', heard: !!b.heard };
  return { t: ab + (b.rank ? ` #${b.rank}` : ''), tone: 'pick', heard: !!b.heard };
}

/* ── the meeting ────────────────────────────────────────────────────────── */

function clockItem(c, asOf, status, toteEarly) {
  let txt;
  let tone;
  let mk;
  if (c.state === 'in') {
    mk = '✓';
    txt = atShort(c.at, asOf) + (c.key === 'tote' && toteEarly ? ' · thin' : '');
    tone = 'in';
  } else if (c.state === 'due') {
    mk = '·'; txt = `due ${when(c.due, asOf)}`; tone = 'due';
  } else if (c.state === 'overdue') {
    mk = '!'; txt = `overdue · was due ${when(c.due, asOf)}`; tone = 'overdue';
  } else {
    mk = '?'; txt = 'irregular'; tone = 'irregular';
  }
  const ab = c.state !== 'in' ? (c.state === 'overdue' ? 'overdue' : 'off')
    : c.kind === 'priced' ? 'price' : c.key === 'rtw_interview' ? 'voice'
      : COUNTED.has(c.key) ? 'tip' : 'shown';
  const st = status[c.key];
  let detail = c.usual;
  if (c.state === 'in' && st) {
    const bits = [];
    if (st.picks) bits.push(plural(st.picks, 'pick'));
    if (st.featured) bits.push(`${st.featured} featured`);
    if (st.interviews) bits.push(plural(st.interviews, 'interview'));
    if (st.quotes) bits.push(plural(st.quotes, 'quote'));
    if (st.runner_lines) bits.push(`${st.runner_lines} form lines`);
    if (st.heard) bits.push(`${st.heard} heard`);
    if (st.races) bits.push(plural(st.races, 'race'));
    if (st.held) bits.push(`${st.held} held for review`);
    detail = bits.join(' · ') || c.usual;
  } else if (c.state === 'in' && c.kind === 'priced') {
    detail = `captured ${atShort(c.at, asOf)}`
      + (toteEarly && c.key === 'tote' ? ' · one bet moves it' : '');
  }
  return { key: c.key, ab: AB[c.key] || c.key, abTone: ab, label: c.label, txt,
           tone, mark: mk, detail,
           counted: c.kind === 'said' && !COUNTED.has(c.key) ? 'shown, not counted' : '' };
}

/** The whole page's reading of one /api/briefing answer. `ui` carries which
 *  race and runner are open; undefined means "the page's default". */
export function buildView(d, ui) {
  const asOf = d.as_of;
  const isRD = d.stage === 'race_day' || d.stage === 'settled';
  const status = Object.fromEntries((d.source_status || []).map((s) => [s.source, s]));
  const clockBy = Object.fromEntries(d.clock.map((c) => [c.key, c]));
  const nextSaid = d.clock.filter((c) => c.kind === 'said' && c.state === 'due' && c.due)
    .sort((a, b) => (a.due < b.due ? -1 : 1))[0];
  const cap = d.captured || {};
  const toteEarly = !!cap.tote && dayDiff(cap.tote, d.race_date) < 0;
  const priceAge = cap.tote ? minutesBetween(asOf, cap.tote) : null;
  const stale = isRD && priceAge !== null && priceAge > STALE_AFTER
    && d.races.some((r) => !r.run);
  const clockItems = d.clock.map((c) => clockItem(c, asOf, status, toteEarly));

  const booked = new Set();
  d.races.forEach((r) => r.runners.forEach((x) => {
    if (x.blackbook && x.blackbook.live) booked.add(`${r.race_no}-${x.horse_no}`);
  }));
  const isBooked = (rn, no) => booked.has(`${rn}-${no}`);

  const nextRace = isRD ? d.races.find((r) => !r.run && r.minutes_to_off !== null
    && r.minutes_to_off > 0) : null;
  const featNos = d.order.filter((n) => {
    const r = d.races.find((z) => z.race_no === n);
    return r && !r.run && r.reasons.length;
  }).slice(0, 3);
  // A race in the address that has since run is not a race to open: the
  // shared header writes the first race of the card there on every load.
  const hasRun = (no) => d.races.some((r) => r.race_no === no && r.run);
  const pick = (v, dflt) => (v === undefined || (v !== null && hasRun(v)) ? dflt : v);
  const openD = pick(ui.openD, featNos[0] ?? null);
  const openM = pick(ui.openM, nextRace ? nextRace.race_no : (featNos[0] ?? null));

  const ctx = { d, ui, asOf, isRD, cap, toteEarly, stale, nextRace, openD, openM,
                isBooked, nextSaid };
  const races = d.races.map((r) => raceView(r, ctx));
  const byNo = Object.fromEntries(races.map((r) => [r.no, r]));
  const nRun = races.filter((r) => r.run).length;
  const list = [{ head: true, l: 'WORTH STUDYING FIRST',
                  r: 'most different things to read first · a count of reasons, not a chance of anything',
                  rs: 'a count of reasons to read, not a chance of anything' }]
    .concat(featNos.map((n) => ({ ...byNo[n], mode: 'feat' })))
    .concat([{ head: true, l: 'THE REST · IN RACE ORDER',
               r: nRun ? `${nRun} run, collapsed to their result` : 'quieter: fewer things to read',
               rs: nRun ? `${nRun} run` : 'in race order' }])
    .concat(races.filter((r) => !featNos.includes(r.no))
      .map((r) => ({ ...r, mode: r.run ? 'run' : 'quiet' })));

  const nowItems = [];
  if (isRD) {
    const running = d.races.find((r) => !r.run && r.minutes_to_off !== null && r.minutes_to_off <= 0);
    if (running) {
      nowItems.push({ l: 'RUNNING', v: `R${running.race_no}`,
                      sub: `${running.off_time} off · no result yet`, subS: running.off_time });
    }
    if (nextRace) {
      nowItems.push({ l: 'NEXT', v: `R${nextRace.race_no}`, go: nextRace.race_no,
                      sub: `${nextRace.off_time} · in ${nextRace.minutes_to_off}m`,
                      subS: `in ${nextRace.minutes_to_off}m` });
    }
    nowItems.push({ l: 'CARD', v: `${nRun} of ${d.races.length} run`, sub: '', subS: '', dim: true });
    if (priceAge !== null) {
      nowItems.push({ l: 'PRICES', v: hm(cap.tote), stale,
                      sub: stale ? `${priceAge}m old · last capture` : `${priceAge}m old`,
                      subS: stale ? `${priceAge}m old` : '' });
    }
  }

  const capLine = [];
  BOOKS.forEach((k) => {
    if (cap[k]) {
      capLine.push({ l: BK[k], v: hm(cap[k]) + (k === 'tote' && toteEarly ? ' thin' : ''),
                     tone: stale ? 'stale' : '' });
    }
  });
  if (d.meeting_total) capLine.push({ l: 'ALL POOLS', v: moneyS(d.meeting_total), tone: '' });
  const tc = clockBy.tote;
  if (!capLine.length) {
    capLine.push(tc && tc.state === 'overdue'
      ? { l: 'PRICES', v: 'tote overdue · none captured', tone: 'overdue' }
      : { l: 'PRICES', v: tc && tc.due ? `tote opens ${when(tc.due, asOf)}` : 'none yet', tone: 'dim' });
  }

  const ran = new Set(races.filter((r) => r.run).map((r) => r.no));
  const edges = isRD ? (d.edges || []).slice().sort((a, b) => b.ev_pct - a.ev_pct)
    .slice(0, 10).map((e) => ({
      race: e.race_no, no: e.horse_no, name: e.horse_name,
      booked: isBooked(e.race_no, e.horse_no),
      at: BK[e.bet_at] || e.bet_at, price: px(e.price),
      against: e.against === 'books' ? 'BOOKS FAIR' : 'TOTE FAIR',
      fair: `${Number(e.fair_pct).toFixed(1)}%`, ev: sgn(e.ev_pct),
      src: ran.has(e.race_no) ? 'RAN' : e.supporters ? plural(e.supporters, 'source') : 'no source',
      tip: !!e.supporters, ran: ran.has(e.race_no),
    })) : [];
  const lb = clockBy.ladbrokes;
  const noEdgesMsg = isRD ? 'No runner is priced 5% or more above another market’s chance.'
    : `Race day only: needs the bookmakers beside a deep tote${
      lb && lb.due ? ` · bookmakers due ${when(lb.due, asOf)}` : ''}.`;

  const f = d.fit || {};
  const fitLine = f.top4_has_winner
    ? `The Screen’s four held the winner in ${Math.round(f.top4_has_winner.screen * 100)}% of `
      + `${(f.test_races || 0).toLocaleString('en-US')} test races (the market’s four: `
      + `${Math.round(f.top4_has_winner.market * 100)}%) · the order is a reading order, and `
      + 'nothing here re-ranks the race card' : '';
  const dt = pd(d.race_date);
  const at = pd(asOf);
  const asOfTxt = `${DAYS[at.getDay()]} ${at.getDate()} ${MON[at.getMonth()]} ${hm(asOf)}`;
  const overdue = d.clock.filter((c) => c.state === 'overdue').map((c) => `${c.label} overdue`);
  return {
    stage: d.stage,
    stageLabel: `${STAGE_NAME[d.stage] || d.stage.toUpperCase()} · AS OF ${asOfTxt.toUpperCase()}`,
    venue: d.venue, dateShort: `${DAYS[dt.getDay()].toUpperCase()} ${dt.getDate()} ${MON[dt.getMonth()].toUpperCase()}`,
    asOfLine: `as of ${asOfTxt}`,
    asOfShort: hm(asOf) + (dayDiff(asOf, d.race_date) ? ` · ${DAYS[at.getDay()]}` : ''),
    clock: clockItems, capLine, nowItems, list, openD, openM,
    nextDue: [nextSaid ? `Next: ${nextSaid.label} due ${when(nextSaid.due, asOf)}` : '', ...overdue]
      .filter(Boolean).join(' · '),
    edges, noEdgesMsg, fitLine,
  };
}

/* ── one race ───────────────────────────────────────────────────────────── */

function raceView(r, ctx) {
  const { isRD, nextRace, cap, toteEarly, stale, openD, openM, ui, isBooked, asOf } = ctx;
  const no = r.race_no;
  const running = !r.run && r.minutes_to_off !== null && r.minutes_to_off <= 0;
  const isNext = !!nextRace && nextRace.race_no === no;
  const m = r.minutes_to_off;
  const minsL = running ? '● RUNNING' : (isRD && m !== null && m > 0
    ? (m >= 90 ? `in ${Math.floor(m / 60)}h ${m % 60}m` : `in ${m}m`) : '');
  const books = BOOKS.filter((k) => r.runners.some((x) => x.price && x.price[k] && x.price[k].win));
  const srcIn = (r.voices && r.voices.sources ? r.voices.sources : [])
    .filter((s) => COUNTED.has(s)).length;

  const sorted = r.reasons.slice().sort((a, b) => (PRI[a.key] ?? 9) - (PRI[b.key] ?? 9));
  const lead = [];
  const seen = new Set();
  sorted.forEach((x) => { if (!seen.has(x.kind)) { seen.add(x.kind); lead.push(x); } });
  const reasons = lead.concat(sorted.filter((x) => !lead.includes(x)))
    .map((x) => ({ text: x.text, ...kindTag(x) }));
  const kc = { computed: 0, said: 0, priced: 0 };
  r.reasons.forEach((x) => { kc[x.kind] += 1; });
  const kindCounts = [kc.computed && `${kc.computed} screen`, kc.said && `${kc.said} said`,
                      kc.priced && `${kc.priced} price`].filter(Boolean).join(' · ')
    || 'nothing to read yet';

  const L = r.pace.leaders || [];
  const cn = r.pace.counts || {};
  const paceHead = L.length === 0 ? 'No habitual leader'
    : L.length === 1 ? `1 leader · #${L[0].horse_no}${r.pace.leader_x ? ` (×${r.pace.leader_x})` : ''}`
      : `${L.length} leaders · ${L.map((z) => `#${z.horse_no}`).join(' ')}`;
  const pace = { head: paceHead,
                 segs: STYLES.filter((k) => cn[k]).map((k) => ({ style: k, n: cn[k],
                                                                t: `${STYLE_SHORT[k]} ${cn[k]}` })) };

  const saidOf = (x) => {
    const n = x.support ? x.support.sources : 0;
    const intv = !!x.support && x.support.backed_by.some((b) => b.kind === 'connections');
    if (n > 0) return { said: plural(n, 'source'), saidS: `${n}/${srcIn}`, tip: true, int: intv };
    if (srcIn > 0) return { said: `0 of ${srcIn}`, saidS: `0/${srcIn}`, tip: false, int: false };
    return { said: '', saidS: '', tip: false, int: false };
  };

  const bySc = r.runners.slice().sort((a, b) => a.screen.rank - b.screen.rank);
  const shortlist = bySc.filter((x) => x.screen.tier === 'SHORTLIST').slice(0, 4).map((x) => ({
    rank: x.screen.rank, no: x.horse_no, name: x.horse_name, booked: isBooked(no, x.horse_no),
    bar: Math.min(100, x.screen.place_pct), place: `${Math.round(x.screen.place_pct)}%`,
    price: x.price && x.price.tote && x.price.tote.win ? px(x.price.tote.win) : '',
    mrank: x.market_rank ? `M${x.market_rank}` : '', ...saidOf(x),
  }));

  const ivs = (r.voices && r.voices.interviews) || [];
  const openRunner = no in ui.openR ? ui.openR[no]
    : (no === openD ? ((ivs[0] && ivs[0].horse_no) || (bySc[0] && bySc[0].horse_no)) : null);
  const openRunnerM = ui.openRM[no] ?? null;
  const runners = bySc.map((x) => runnerView(x, r, { books, srcIn, saidOf, isBooked, cap,
                                                     toteEarly, ctx }));

  const mk = r.market;
  const winner = r.runners.find((x) => x.result === 1);
  const poolBits = [];
  if (mk) {
    if (mk.win_pool) poolBits.push({ l: 'WIN POOL', v: money(mk.win_pool) });
    if (mk.concentration !== null && mk.concentration !== undefined) {
      poolBits.push({ l: 'CONCENTRATION', v: `${Math.round(mk.concentration * 100)}% ${mk.band || ''}` });
    }
    const ov = Object.entries(mk.overround || {}).filter(([, v]) => v !== null && v !== undefined);
    if (ov.length) poolBits.push({ l: 'MARGIN', v: ov.map(([k, v]) => `${BK[k] || k} ${v}%`).join(' · ') });
  }
  return {
    no, label: `R${no}`, off: r.off_time || DASH, mins: minsL, running, isNext,
    minsStrong: running || isNext, run: r.run,
    dist: `${r.distance}m`, raceClass: r.race_class, restricted: !!r.restricted,
    course: r.course || DASH, going: r.going || DASH, field: `${r.field_size} run`,
    fieldN: r.field_size,
    market: mk && mk.favourite ? {
      favNo: mk.favourite.horse_no, favWin: px(mk.favourite.win),
      favTag: toteEarly ? 'THIN' : (cap.tote ? hm(cap.tote) : ''),
      conc: mk.concentration !== null && mk.concentration !== undefined
        ? `${Math.round(mk.concentration * 100)}% ${mk.band || ''}` : '',
      pool: mk.win_pool ? moneyS(mk.win_pool) : DASH } : null,
    poolBits, reasons, kindCounts, firstReason: reasons[0] ? reasons[0].text : '',
    pace, shortlist, slCompact: shortlist.map((x) => `#${x.no} ${x.place}`).join(' · '),
    interviews: ivs.map((iv) => {
      const rr = r.runners.find((z) => z.horse_no === iv.horse_no);
      return { role: (iv.role || '').toUpperCase(), speaker: iv.speaker,
               horse: `#${iv.horse_no} ${iv.horse_name}`, booked: isBooked(no, iv.horse_no),
               screen: rr ? `SCREEN ${rr.screen.rank} of ${r.field_size}`
                 + (rr.price && rr.price.tote && rr.price.tote.win ? ` · TOTE ${px(rr.price.tote.win)}` : '') : '',
               words: words(iv.words) };
    }),
    comments: ((r.voices && r.voices.comments) || []).map((c) => ({
      who: AB[c.source] || c.who, w: words([{ text: c.text, url: c.url }])[0] })).filter((c) => c.w),
    books, hasPrices: books.length > 0,
    priceHead: books.map((k) => ({ s: BK[k],
                                   sub: cap[k] ? hm(cap[k]) + (k === 'tote' && toteEarly ? ' thin' : '') : '',
                                   stale })),
    runners, openRunner, openRunnerM,
    winner: winner ? `1st #${winner.horse_no} ${winner.horse_name}`
      + (winner.price && winner.price.tote && winner.price.tote.win ? ` · tote ${px(winner.price.tote.win)}` : '')
      : 'result pending',
    open: no === openD && !r.run, mOpen: no === openM && !r.run,
    edge: r.run ? '' : no === openD ? 'open' : isNext ? 'next' : running ? 'running' : '',
    mEdge: no === openM ? 'open' : isNext ? 'next' : '',
    asOf,
  };
}

/* ── one runner ─────────────────────────────────────────────────────────── */

function runnerView(x, r, { books, srcIn, saidOf, isBooked, cap, toteEarly, ctx }) {
  const s = x.screen;
  const bb = x.blackbook;
  const p = x.price;
  const sup = x.support;
  const keys = new Set([...(s.for || []), ...(s.against || [])].map((f) => f.key));
  const cells = books.map((k) => {
    const q = p && p[k];
    return { v: q && q.win ? px(q.win) : DASH, on: !!(q && q.win),
             most: !!p && p.pays_most === k && books.length > 1 };
  });
  let move = null;
  if (x.move) {
    if (x.move.rush_pct && x.move.rush_direction === 'shortened') {
      move = { t: `LATE $ ${Math.abs(x.move.rush_pct).toFixed(0)}%`, tone: 'alert' };
    } else if (x.move.change_pct !== null && x.move.change_pct <= -5) {
      move = { t: `▼ ${Math.abs(x.move.change_pct).toFixed(0)}%`, tone: 'alert' };
    } else if (x.move.change_pct !== null && x.move.change_pct >= 5) {
      move = { t: `▲ ${x.move.change_pct.toFixed(0)}%`, tone: 'dim' };
    }
  }
  const chips = [];
  (x.gear_first || []).forEach((g) => chips.push({ t: `1ST ${g}`, tone: 'gear' }));
  if (keys.has('prev_vet')) chips.push({ t: 'VET', tone: 'vet' });
  if (keys.has('trainer_change')) chips.push({ t: 'NEW TR', tone: 'violet' });

  const srcs = (sup ? sup.backed_by : []).map((b) => {
    const conn = b.kind === 'connections';
    const t = tFromUrl(b.url);
    return { m: mark(b), voice: conn,
             who: b.who + (b.role && conn ? ` · ${b.role}` : '')
               + (!COUNTED.has(b.source) && !conn ? ' · shown, not counted' : ''),
             words: words(b.words), url: b.url, tl: t !== null ? clock(t) : 'page',
             noWordsMsg: b.heard ? 'named aloud, no commentary captured' : 'pick only, no commentary' };
  });
  const ls = s.last_start;
  const last = ls ? {
    head: `${ls.place ?? DASH} of ${ls.field_size ?? DASH} · ${ls.venue} ${ls.distance}m · `
      + `${shortDate(ls.race_date)} · dr ${ls.draw ?? DASH} · ${ls.jockey ?? DASH}`,
    raceClass: ls.race_class,
    run: ls.running_comment || '',
    inc: ls.incident_comment ? words([{ text: ls.incident_comment }])[0] : null,
    tags: (ls.tags || []).filter((t) => !/routine|^no_report$|^sampling$/.test(t))
      .map((t) => t.replace(/_/g, ' ').toUpperCase()),
  } : null;
  const tr = s.trial;
  const trial = tr ? {
    head: `${tr.band} · ${tr.place ?? DASH} of ${tr.field_size ?? DASH} · ${shortDate(tr.trial_date)}`,
    comment: tr.comment || '', good: /STANDOUT|POSITIVE/.test(tr.band || '') } : null;
  const pt = p ? priceTable(p, books) : null;
  const booked = isBooked(r.race_no, x.horse_no);
  return {
    rk: s.rank, no: x.horse_no, name: x.horse_name, zh: x.name_zh || '', booked,
    line2: `dr ${x.draw ?? DASH} · ${x.jockey ?? DASH} · ${x.trainer ?? DASH}`
      + (x.weight ? ` · ${x.weight}lb` : ''),
    style: x.style, styleShort: STYLE_SHORT[x.style] || DASH,
    bar: Math.min(100, s.place_pct), top: s.tier === 'SHORTLIST',
    place: `${Math.round(s.place_pct)}%`, win: `W ${Number(s.win_pct).toFixed(1)}%`,
    tier: s.tier === 'SHORTLIST' ? 'TOP 4' : s.tier === 'CASE' ? 'A CASE' : '',
    hasBB: !!bb, setup: bb ? s.setup : '', setupAgainst: s.setup === 'AGAINST',
    bbHead: bb ? `${s.setup} ×${s.setup_x} · ${bb.confidence || DASH} confidence · booked `
      + `${bb.added_date}${bb.conditions_text ? ` · ${bb.conditions_text}` : ''}` : '',
    bbWhy: bb ? bb.reasoning || '' : '',
    ...saidOf(x),
    marks: (sup ? sup.backed_by : []).map(mark),
    cells, move, chips,
    mPrice: p && p.tote && p.tote.win ? px(p.tote.win) : '',
    mPriceMost: !!p && p.pays_most === 'tote' && books.length > 1,
    unplaced: x.result !== null && x.result !== undefined && x.result > 3,
    setupLine: `SET-UP ${s.setup} ×${s.setup_x} · PLACE ${s.place_pct}% · WIN ${s.win_pct}% · `
      + `RANK ${s.rank} OF ${r.field_size}`,
    forList: (s.for || []).map((f) => ({ why: f.why || f.label, x: `×${f.x}`, title: f.label })),
    againstList: (s.against || []).map((f) => ({ why: f.why || f.label, x: `×${f.x}`, title: f.label })),
    last, trial,
    notes: (s.notes || []).map((n) => (typeof n === 'string' ? n : n.note || n.text || '')).filter(Boolean),
    reversals: (s.reversals || []).map((v) => ({ no: v.vs_no, name: v.vs_name, note: v.note,
                                                moved: (v.moved || []).join(', ') })),
    saidHead: sup ? `${plural(sup.sources, 'source')} back it`
      : srcIn ? `0 of ${srcIn} sources that tipped this race` : 'nothing in yet',
    srcs,
    noSaidMsg: srcIn ? `None of the ${srcIn} sources that tipped this race named it.`
      : `No source has published on this race yet${ctx.nextSaid
        ? ` · ${ctx.nextSaid.label} due ${when(ctx.nextSaid.due, ctx.asOf)}` : ''}.`,
    form: x.form_line || null,
    pt,
    priceHead: books.map((k) => `${BK[k]} ${cap[k] ? hm(cap[k]) : ''}`).join(' · ')
      + (x.market_rank ? ` · MARKET RANK ${x.market_rank}` : ''),
    ptNote: (toteEarly ? 'Tote is the day-before pool: thin, one bet moves it. ' : '')
      + (books.length > 1 ? 'Value = this price × the other market’s fair chance − 1.'
        : 'Value needs a second market.'),
  };
}

/** Win, place, fair chance and value in every market, one column each. */
function priceTable(p, books) {
  const rows = [['WIN', 'WIN', 'win'], ['PLACE', 'PLACE', 'place'],
                ['FAIR CHANCE', 'FAIR', 'fair_pct'], ['VALUE IF OTHER RIGHT', 'VALUE', 'value_pct']];
  return {
    head: books.map((k) => BK[k]),
    rows: rows.map(([long, short, f]) => ({
      long, short,
      cells: books.map((k) => {
        const q = p[k];
        if (!q || q[f] === null || q[f] === undefined) return { v: DASH, tone: 'off' };
        if (f === 'win') return { v: px(q.win), tone: p.pays_most === k && books.length > 1 ? 'win most' : 'win' };
        if (f === 'place') return { v: px(q.place), tone: '' };
        if (f === 'fair_pct') return { v: `${q.fair_pct.toFixed(1)}%`, tone: '' };
        if (books.length < 2) return { v: DASH, tone: 'off' };
        return { v: sgn(q.value_pct), tone: q.value_pct >= 5 ? 'good' : q.value_pct < 0 ? 'neg' : '' };
      }),
    })),
  };
}

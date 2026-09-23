/* briefing.js — the meeting Briefing: what the sources back, what was said,
 * and where the markets disagree, one row per race.
 *
 * Ported from web/design-source/Briefing.dc.html. Reads five endpoints and
 * writes nothing: /api/tips/summary (support, words, every market's price),
 * /api/raceday (the meeting strip), /api/raceday/{race} (the dashboard's own
 * signals), /api/money (pool sizes) and the shared freshness strip.
 *
 * It is a briefing, not a bet slip. Support is a COUNT of sources, never a
 * star or a verdict; a price sits beside every backed horse; and nothing here
 * re-ranks or recolours the Race Day card.
 */
import { api } from './api.js';
import { context } from './context.js';
import { $, el, DASH, renderNav } from './vocab.js';
import {
  SRC, SRC_ORDER, EDGE_AT, px, sgn, pct, hhmm, hkNow, markets as marketList,
  countLabel, gapLine, atShort,
} from './briefing-model.js';
import { buildRace, raceDetail, chipEl, markEl, noTipsMsg } from './briefing-race.js';

const state = { ctx: null, open: null, error: null };
const firstRace = Number(new URLSearchParams(window.location.search).get('race')) || null;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
                'Oct', 'Nov', 'Dec'];
const dayTime = (s) => (s ? `${Number(s.slice(8, 10))} ${MONTHS[Number(s.slice(5, 7)) - 1]} ${s.slice(11, 16)}` : null);

async function load() {
  const date = context.date;
  const strip = context.summary?.races ?? [];
  state.error = null;
  if (!date) { state.ctx = null; render(); return; }
  let summary;
  let money = null;
  let cards = [];
  try {
    [summary, money, cards] = await Promise.all([
      api.tipsSummary(date),
      api.money(date).catch(() => null),
      Promise.all(strip.map((r) => api.raceCard(date, r.race_no).catch(() => null))),
    ]);
  } catch (e) {
    // Never an empty page that looks like a quiet meeting: say what failed.
    state.error = `The briefing could not be read — ${e.message}`;
    state.ctx = null;
    render();
    return;
  }
  const now = hkNow();
  const offTimes = {};
  const byRace = {};
  cards.forEach((c, i) => {
    const no = strip[i].race_no;
    byRace[no] = c?.runners ?? [];
    offTimes[no] = c?.off_time ?? null;
  });
  summary.races.forEach((r) => { if (r.off_time) offTimes[r.race_no] = r.off_time; });
  const offs = Object.values(offTimes).filter(Boolean)
    .map((t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5)));
  const lastOff = offs.length ? Math.max(...offs) : null;
  const status = summary.source_status ?? [];
  state.ctx = {
    date, now, summary, strip, cards: byRace, offTimes, lastOff,
    pools: Object.fromEntries((money?.races ?? []).map((r) => [r.race_no, r.win_pool])),
    meetingTotal: money?.meeting_total ?? null,
    markets: marketList(summary, date, now, lastOff),
    published: new Set(status.filter((s) => s.published).map((s) => s.source)),
    sourceKeys: SRC_ORDER,
  };
  if (state.open === null) {
    const races = strip.map((rd) => buildRace(state.ctx, rd));
    state.open = firstRace ?? races.find((r) => !r.run && r.picks.length)?.no ?? null;
  }
  render();
}

function setOpen(no, { scroll = false } = {}) {
  state.open = state.open === no && !scroll ? null : no;
  if (state.open !== null) context.setRace(state.open);
  render();
  if (scroll && state.open !== null) {
    document.querySelector(`.bf-race[data-race="${no}"]`)?.scrollIntoView({ block: 'start' });
  }
}

/* ── the page bar ───────────────────────────────────────────────────────── */

function renderBar(ctx) {
  const bar = $('bf-bar');
  bar.replaceChildren(el('div', 'bf-title', 'BRIEFING'));
  if (!ctx) return;
  const prices = el('div', 'bf-prices');
  ctx.markets.forEach((m, i) => {
    const s = el('span', 'mkt');
    if (i === 0) s.append(el('span', 'dim', 'PRICES · '));
    s.append(document.createTextNode(`${m.label} `));
    s.append(el('b', m.stale ? 'stale' : m.on ? '' : 'dim',
      m.on ? `${hhmm(m.at)} HKT${m.stale ? ` · ${m.sub.split(' ').slice(1).join(' ')}` : ''}`
        : m.k === 'tote' ? 'NOT OPEN' : 'NO FIXED ODDS YET'));
    prices.append(s);
  });
  if (ctx.markets.length === 1) {
    prices.append(el('span', 'mkt dim', 'NO FIXED ODDS YET'));
  }
  const n = SRC_ORDER.filter((k) => ctx.published.has(k)).length;
  const src = el('span', 'mkt');
  src.append(el('span', 'dim', 'SOURCES IN '), el('b', null, `${n} / ${SRC_ORDER.length}`));
  const pool = el('span', 'mkt');
  pool.append(el('span', 'dim', 'ALL POOLS · MEETING '),
              el('b', null, ctx.meetingTotal ? `$${(ctx.meetingTotal / 1e6).toFixed(1)}M` : DASH));
  prices.append(src, pool);
  bar.append(prices, el('div', 'bf-rule',
    'ORDERED BY NUMBER OF SOURCES · NOTHING HERE RE-RANKS THE RACE DAY CARD'));
}

/* ── one race ───────────────────────────────────────────────────────────── */

function topPrices(v, ctx) {
  const odds = v.top?.p.odds;
  return ctx.markets.map((m) => {
    const on = m.on && odds?.[m.k]?.win;
    return { l: m.short === 'T' ? 'TOTE' : m.short, v: on ? px(odds[m.k].win) : m.on ? DASH : m.sub,
             on: !!on, most: on && ctx.markets.filter((x) => x.on).length > 1
               && odds.pays_most === m.k };
  });
}

function marks(v) {
  const box = el('div', 'marks');
  v.top.p.backed_by.forEach((b) => box.append(markEl(b)));
  return box;
}

function bars(level, cls) {
  const b = el('span', `bf-bars ${cls}`);
  [1, 2, 3].forEach((i) => b.append(el('span', i <= level ? 'on' : '')));
  return b;
}

function raceRow(v, ctx) {
  const open = state.open === v.no;
  const row = el('div', `bf-row${open ? ' open' : ''}`);
  row.addEventListener('click', () => setOpen(v.no));
  const cell = (cls, ...kids) => { const c = el('div', cls); c.append(...kids); return c; };

  row.append(cell('c-r', v.no ? `R${v.no}` : ''));
  row.append(cell('c-off', el('div', 'off', v.off ?? DASH),
    el('div', 'sub', v.minsTo !== null && v.minsTo > 0 && v.minsTo <= 90 ? `in ${v.minsTo}m` : '')));
  row.append(cell('c-race', el('div', null, v.dist), el('div', 'dim', v.cls)));
  row.append(cell('c-fld', String(v.field)));
  row.append(cell('c-mkt', bars(v.level, 'lg'), el('div', 'band', (v.band ?? DASH).toUpperCase())));

  const fav = cell('c-fav');
  if (v.fav) {
    fav.append(el('div', `nm${v.fav.booked ? ' booked' : ''}`, `${v.fav.no} ${v.fav.name}`));
    const p = el('div', 'dim', 'TOTE ');
    p.append(el('b', null, v.fav.price));
    fav.append(p);
  } else fav.append(el('div', 'dim', 'TOTE NOT OPEN'));
  row.append(fav);

  const top = cell('c-top');
  if (v.top) {
    const line = el('div', 'line');
    line.append(el('span', `nm${v.booked(v.top.p.horse_no) ? ' booked' : ''}`,
                   `${v.top.p.horse_no} ${v.top.p.horse_name}`),
                el('span', 'count', countLabel(v.top.who)),
                el('span', 'tie', v.tie ? `+${v.tie} level` : ''));
    top.append(line, marks(v));
  } else top.append(el('div', 'empty', noTipsMsg(ctx)));
  row.append(top);

  const price = cell('c-price');
  if (v.top) {
    topPrices(v, ctx).forEach((x) => {
      const r = el('div', 'px-row');
      r.append(el('span', 'l', x.l), el('span', `v${x.on ? '' : ' off'}${x.most ? ' most' : ''}`, x.v));
      price.append(r);
    });
  }
  row.append(price);

  const gap = cell('c-gap');
  if (v.gap) {
    gap.append(el('div', 'nm', `${v.gap.no} ${v.gap.name}`));
    const line = el('div', 'dim');
    line.append(el('b', v.gap.ev >= EDGE_AT ? 'good' : 'neg', sgn(v.gap.ev)),
                document.createTextNode(` ${gapLine(v.gap)}`));
    gap.append(line);
  } else {
    gap.append(el('div', 'empty', !ctx.markets[0].on ? 'needs both markets open'
      : ctx.markets.length < 2 ? 'needs bookmaker prices' : 'no priced runner'));
  }
  row.append(gap);

  const sig = cell('c-sig');
  v.flags.forEach((c) => sig.append(chipEl(c)));
  row.append(sig);
  const src = cell('c-src');
  v.srcIn.forEach((s) => src.append(el('span', `s-${s.tone}`, s.ab)));
  row.append(src);
  return row;
}

/** The same race as a phone card: the overview row, stacked. */
function raceCard(v, ctx) {
  const open = state.open === v.no;
  const c = el('div', `bf-card${open ? ' open' : ''}`);
  c.addEventListener('click', () => setOpen(v.no));
  const head = el('div', 'head');
  head.append(el('span', 'r', `R${v.no}`), el('span', 'off', v.off ?? DASH),
              el('span', 'sub', v.minsTo !== null && v.minsTo > 0 && v.minsTo <= 90 ? `in ${v.minsTo}m` : ''),
              el('span', 'dim', `${v.dist} ${v.cls}`), bars(v.level, 'sm'));
  const fav = el('span', 'fav', 'FAV ');
  fav.append(el('b', null, v.fav ? String(v.fav.no) : DASH), document.createTextNode(' '),
             el('b', 'big', v.fav?.price ?? DASH));
  head.append(fav);
  c.append(head);
  if (v.top) {
    const line = el('div', 'top');
    line.append(el('span', `nm${v.booked(v.top.p.horse_no) ? ' booked' : ''}`,
                   `${v.top.p.horse_no} ${v.top.p.horse_name}`));
    topPrices(v, ctx).forEach((x) => {
      const s = el('span', 'px', `${x.l} `);
      s.append(el('b', `${x.on ? '' : 'off'}${x.most ? ' most' : ''}`, x.v));
      line.append(s);
    });
    const who = el('div', 'who');
    who.append(el('span', 'count', countLabel(v.top.who)));
    v.top.p.backed_by.forEach((b) => who.append(markEl(b)));
    c.append(line, who);
  } else c.append(el('div', 'empty', noTipsMsg(ctx)));
  const strong = v.gap && v.gap.ev >= EDGE_AT;
  if (v.flags.length || strong) {
    const x = el('div', 'extras');
    v.flags.forEach((f) => x.append(chipEl(f)));
    if (strong) {
      const g = el('span', 'gap', 'GAP ');
      g.append(el('b', null, sgn(v.gap.ev)),
               document.createTextNode(` #${v.gap.no} at ${atShort(v.gap.at)}`));
      x.append(g);
    }
    c.append(x);
  }
  return c;
}

function ranRow(v, ctx) {
  const row = el('div', 'bf-ran');
  row.append(el('span', 'r', `R${v.no}`), el('span', 'off', v.off ?? DASH),
             el('span', 'dc', `${v.dist} ${v.cls}`),
             el('span', 'what', 'RAN · BRIEFING CLOSED'));
  const res = el('a', 'res', 'RESULT ▸');
  res.href = `results.html?date=${ctx.date}&race=${v.no}`;
  res.addEventListener('click', (e) => e.stopPropagation());
  row.append(res);
  // Closed, but not locked: what the sources said is still worth reading
  // against what happened.
  row.addEventListener('click', () => setOpen(v.no));
  return row;
}

/* ── the side rails ─────────────────────────────────────────────────────── */

function renderEdges(ctx, races) {
  const host = $('bf-edges');
  host.replaceChildren();
  const run = new Set(races.filter((r) => r.run).map((r) => r.no));
  const best = new Map();
  (ctx?.summary.edges ?? []).forEach((e) => {
    const k = `${e.race_no}|${e.horse_no}`;
    if (!best.has(k) || best.get(k).ev_pct < e.ev_pct) best.set(k, e);
  });
  const edges = [...best.values()].sort((a, b) => b.ev_pct - a.ev_pct).slice(0, 8);
  $('bf-edges-head').hidden = !edges.length;
  if (!edges.length) {
    const tote = ctx?.markets[0].on;
    const books = ctx && ctx.markets.slice(1).some((m) => m.on);
    host.append(el('div', 'bf-empty pad', !tote && !books
      ? 'Needs both markets. The tote has not opened and no bookmaker has priced this meeting yet.'
      : !books ? 'Needs bookmaker fixed odds to compare against the tote.'
        : !tote ? 'Needs the tote open to compare against the bookmakers.'
          : `No runner is +${EDGE_AT}% or better at one market against the other’s fair chance.`));
    return;
  }
  edges.forEach((e) => {
    const row = el('div', `bf-edge${run.has(e.race_no) ? ' ran' : ''}`);
    row.addEventListener('click', () => setOpen(e.race_no, { scroll: true }));
    const booked = races.find((r) => r.no === e.race_no)?.booked(e.horse_no);
    const horse = el('span', 'horse');
    horse.append(el('span', `nm${booked ? ' booked' : ''}`, `${e.horse_no} ${e.horse_name}`),
                 el('span', `src${e.supporters ? ' tip' : ''}`, run.has(e.race_no) ? 'RAN'
                   : e.supporters ? countLabel(e.supporters) : 'no tips'));
    const at = el('span', 'at');
    at.append(el('span', 'dim', `${atShort(e.bet_at)} `), document.createTextNode(px(e.price)));
    row.append(el('span', 'r', `R${e.race_no}`), horse, at,
               el('span', 'fair', pct(e.fair_pct)), el('span', 'ev', sgn(e.ev_pct)));
    host.append(row);
  });
}

function sourceLine(s) {
  if (s.source === 'bryan') return `${s.quotes} quote${s.quotes === 1 ? '' : 's'} · not counted as support`;
  const bits = [];
  if (s.picks) bits.push(`${s.picks} picks`);
  if (s.heard) bits.push(`${s.heard} heard`);
  if (s.featured) bits.push(`${s.featured} featured`);
  if (s.interviews) bits.push(`${s.interviews} interviews`);
  if (s.source === 'racing_sports') {
    if (s.quotes) bits.push(`${s.quotes} race comments`);
    if (s.runner_lines) bits.push(`${s.runner_lines} runner lines`);
  } else if (s.quotes) bits.push(`${s.quotes} quotes`);
  bits.push(`${s.races} races`);
  return bits.join(' · ');
}

function renderSources(ctx) {
  const host = $('bf-sources');
  host.replaceChildren();
  (ctx?.summary.source_status ?? []).forEach((s) => {
    const on = s.published;
    const box = el('div', 'bf-source');
    const head = el('div', 'head');
    const tone = !on ? 'off' : s.source === 'rtw_interview' ? 'voice'
      : s.source === 'bryan' ? 'muted' : 'tip';
    head.append(el('span', `ab t-${tone}`, SRC[s.source]?.ab ?? s.source.toUpperCase()),
                el('span', `name${on ? '' : ' off'}`, SRC[s.source]?.name ?? s.label),
                el('span', `time${on ? '' : ' off'}`, on ? dayTime(s.fetched_at) : 'NOT YET'));
    box.append(head, el('div', 'line', on ? sourceLine(s) : (SRC[s.source]?.usual ?? '')));
    if (s.held) {
      const why = Object.entries(s.held_reasons).map(([r, n]) => `${n} ${r.replace(/_/g, ' ')}`);
      box.append(el('div', 'held', `HELD FOR REVIEW · ${why.join(' · ')}`));
    }
    host.append(box);
  });
}

/* ── the page ──────────────────────────────────────────────────────────── */

function render() {
  const ctx = state.ctx;
  renderBar(ctx);
  const host = $('bf-races');
  host.replaceChildren();
  if (state.error) host.append(el('div', 'bf-error', state.error));
  if (!ctx) { renderEdges(null, []); renderSources(null); return; }
  if (!ctx.strip.length) host.append(el('div', 'bf-empty pad', 'NO CARD STORED FOR THIS MEETING'));
  const races = ctx.strip.map((rd) => buildRace(ctx, rd));
  races.forEach((v) => {
    const wrap = el('div', 'bf-race');
    wrap.dataset.race = String(v.no);
    if (v.run) wrap.append(ranRow(v, ctx));
    else wrap.append(raceRow(v, ctx), raceCard(v, ctx));
    if (state.open === v.no) wrap.append(raceDetail(v, ctx));
    host.append(wrap);
  });
  renderEdges(ctx, races);
  renderSources(ctx);
}

async function main() {
  await context.init();
  renderNav($('nav'), 'briefing.html');
  context.onChange((_c, what) => {
    if (what === 'date') { state.ctx = null; state.open = null; render(); }
    if (what === 'meeting') load();
  });
  await load();
}

main();

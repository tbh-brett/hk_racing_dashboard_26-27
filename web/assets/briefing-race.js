/* briefing-race.js — one race of the Briefing, opened.
 *
 * In the artboard's order: the race's numbers, the jockey and trainer
 * interviews (the most important words on the page, so first and in the
 * voice colour), what Racing & Sports wrote about the race, the horses the
 * sources back — most sources first, each with its price in every market —
 * and the rest of the field, only where a runner carries a signal or a price
 * gap. One DOM serves the desktop row and the phone card; the stylesheet
 * re-flows it.
 *
 * QUOTES ARE VERBATIM. A long one is folded to a line, never shortened, and
 * every one links to the second it was said.
 */
import { DASH, el, drawText, isLiveBooking } from './vocab.js';
import {
  EDGE_AT, FOLD_AT, SRC, BAND_LEVEL, px, sgn, pct, clock, secondsIn, money,
  mark, countLabel, chips, raceFlags, biggestGap, gapLine, atShort, raceLabel,
  orderPicks, raceState,
} from './briefing-model.js';

/* ── building one race's view ────────────────────────────────────────────── */

/** Everything one race row and its detail show, from the four endpoints. */
export function buildRace(ctx, rd) {
  const no = rd.race_no;
  const sr = ctx.summary.races.find((r) => r.race_no === no)
    ?? { race_no: no, picks: [], interviews: [], comments: [], form: [],
         overround: {}, off_time: null, tote_win_pool: null };
  const card = ctx.cards[no] ?? [];
  const byNo = Object.fromEntries(card.map((r) => [r.horse_no, r]));
  const booked = (h) => isLiveBooking(byNo[h]?.blackbook);
  const off = sr.off_time ?? ctx.offTimes[no] ?? null;
  const state = raceState(ctx.date, off, ctx.now);

  const picks = orderPicks(sr.picks);
  const pickNos = new Set(picks.map((x) => x.p.horse_no));
  const both = ctx.markets.filter((m) => m.on).length > 1;
  const gaps = both ? biggestGap(no, ctx.summary.edges, picks) : [];

  const priced = card.filter((r) => r.win_odds);
  const fav = priced.length
    ? priced.reduce((a, b) => (b.win_odds < a.win_odds ? b : a)) : null;

  const rest = new Map();
  card.forEach((r) => {
    if (!pickNos.has(r.horse_no) && chips(r).length) rest.set(r.horse_no, { r });
  });
  gaps.filter((g) => !pickNos.has(g.no) && g.ev >= EDGE_AT).forEach((g) => {
    const had = rest.get(g.no) ?? { r: byNo[g.no] };
    if (!had.gap) rest.set(g.no, { ...had, gap: g });
  });
  sr.interviews.forEach((iv) => {
    if (!pickNos.has(iv.horse_no)) {
      rest.set(iv.horse_no, { ...(rest.get(iv.horse_no) ?? { r: byNo[iv.horse_no] }), iv });
    }
  });

  const lit = new Set(picks.flatMap((x) => x.p.backed_by.map((b) => b.source)));
  if (sr.interviews.length) lit.add('rtw_interview');
  const { dist, cls } = raceLabel(rd);
  return {
    no, off, run: state.run, minsTo: state.minsTo, dist, cls,
    field: rd.field_size ?? card.length ?? DASH, band: rd.band ?? null,
    level: BAND_LEVEL[rd.band] ?? 0,
    conc: rd.concentration != null ? `${Math.round(rd.concentration * 100)}%` : DASH,
    course: `GOING ${rd.going ?? DASH} · COURSE ${rd.course ?? DASH} · FIELD ${rd.field_size ?? DASH}`,
    fav: fav && { no: fav.horse_no, name: fav.horse_name, price: px(fav.win_odds),
                  booked: booked(fav.horse_no) },
    picks, top: picks[0] ?? null,
    tie: picks.length ? picks.filter((x) => x.who === picks[0].who).length - 1 : 0,
    gap: gaps[0] ?? null, both, flags: raceFlags(card),
    srcIn: ctx.sourceKeys.map((k) => ({
      ab: SRC[k]?.ab ?? k, tone: lit.has(k) ? (k === 'rtw_interview' ? 'voice' : 'tip')
        : ctx.published.has(k) ? 'pub' : 'pending' })),
    interviews: sr.interviews, comments: sr.comments ?? [],
    rest: [...rest.entries()].sort((a, b) => a[0] - b[0]).map(([h, x]) => ({ no: h, ...x })),
    pool: sr.tote_win_pool ?? ctx.pools[no] ?? null,
    margins: sr.overround ?? {}, booked, byNo,
  };
}

/* ── small pieces ───────────────────────────────────────────────────────── */

export function chipEl(c, extra = '') {
  return el('span', `bf-chip t-${c.tone}${extra}`, c.t);
}

export function markEl(b) {
  const m = mark(b);
  const s = el('span', `bf-mark k-${m.kind}`, m.t);
  if (m.heard) {
    const h = el('span', 'heard', '~');
    h.title = 'heard: the number was said aloud and the name came through '
      + 'speech-to-text';
    s.append(h);
  }
  return s;
}

function timeLink(w, cls = 't') {
  const t = secondsIn(w);
  const a = el('a', cls, `▸ ${t !== null ? clock(t) : 'page'}`);
  a.href = w.url;
  a.target = '_blank';
  a.rel = 'noopener';
  return a;
}

/** Quoted words, folded past FOLD_AT characters. The original first; an
 *  English line, where there is one, underneath and never instead. */
export function wordRows(list, { voice = false } = {}) {
  const out = [];
  list.forEach((w) => {
    const long = (w.text ?? '').length > FOLD_AT;
    const row = el('div', `bf-w${long ? ' long' : ''}`);
    const caret = el('span', 'caret', long ? '▸' : '');
    const q = el('span', 'q', `“${w.text}”`);
    const flip = (e) => {
      if (!long) return;
      e.stopPropagation();
      const open = row.classList.toggle('open');
      caret.textContent = open ? '▾' : '▸';
    };
    caret.addEventListener('click', flip);
    q.addEventListener('click', flip);
    row.append(caret, q, timeLink(w, voice ? 't voice' : 't'));
    out.push(row);
    if (w.en) out.push(el('div', 'bf-en', w.en));
  });
  return out;
}

/* ── the opened race ───────────────────────────────────────────────────── */

export function raceDetail(v, ctx) {
  const box = el('div', 'bf-detail');

  const stats = el('div', 'bf-stats');
  const stat = (label, value) => {
    const s = el('span', null, `${label} `);
    s.append(el('b', null, value));
    return s;
  };
  stats.append(stat('WIN POOL', money(v.pool)));
  const margin = el('span', null, 'MARGIN');
  ctx.markets.forEach((m) => {
    margin.append(document.createTextNode(` · ${m.label} `));
    margin.append(el('b', null, m.on && v.margins[m.k] != null ? `${v.margins[m.k]}%` : DASH));
  });
  stats.append(margin);
  const conc = stat('CONCENTRATION', v.conc);
  if (v.band) conc.append(document.createTextNode(` ${v.band.toUpperCase()}`));
  stats.append(conc, el('span', 'course', v.course));
  const card = el('a', 'to-card', 'RACE DAY CARD ▸');
  card.href = `raceday.html?date=${ctx.date}&race=${v.no}`;
  stats.append(card);
  box.append(stats);

  v.interviews.forEach((iv) => box.append(interviewBox(iv, v)));
  if (!v.interviews.length) {
    box.append(el('div', 'bf-empty bf-no-iv', ctx.published.has('rtw_interview')
      ? 'NO JOCKEY OR TRAINER INTERVIEW FOR THIS RACE'
      : `INTERVIEWS NOT IN YET · ${SRC.rtw_interview.usual}`));
  }
  v.comments.forEach((c) => box.append(commentBlock(c)));

  box.append(el('div', 'bf-cap', 'BACKED · MOST SOURCES FIRST'));
  if (!v.picks.length) box.append(el('div', 'bf-empty ruled', noTipsMsg(ctx)));
  v.picks.forEach((x) => box.append(pickRow(x, v, ctx)));

  box.append(el('div', 'bf-cap', 'REST OF FIELD · ONLY RUNNERS WITH A SIGNAL OR A PRICE GAP'));
  if (!v.rest.length) {
    box.append(el('div', 'bf-empty ruled',
      `NO OTHER RUNNER CARRIES A SIGNAL OR A PRICE GAP OF +${EDGE_AT}% OR MORE`));
  }
  v.rest.forEach((x) => box.append(restRow(x, v, ctx)));
  return box;
}

export function noTipsMsg(ctx) {
  const pending = ctx.sourceKeys.filter((k) => !ctx.published.has(k));
  return pending.length ? `NO TIPS YET · ${SRC[pending[0]]?.usual ?? ''}`
    : 'NO SOURCE HAS TIPPED THIS RACE';
}

function interviewBox(iv, v) {
  const box = el('div', 'bf-iv');
  const head = el('div', 'bf-iv-head');
  head.append(el('span', 'role', (iv.role ?? '').toUpperCase()),
              el('span', 'spk', iv.speaker ?? DASH), el('span', 'on', 'on'),
              el('span', `horse${v.booked(iv.horse_no) ? ' booked' : ''}`,
                 `${iv.horse_no} ${iv.horse_name}`),
              el('span', 'src', 'RACING TO WIN INTERVIEW'));
  box.append(head, ...wordRows(iv.words, { voice: true }));
  return box;
}

function commentBlock(c) {
  const box = el('div', 'bf-comment');
  const head = el('div', 'bf-comment-head');
  head.append(el('span', 'lab', 'RACE COMMENT'), el('span', 'who', c.who ?? c.source_label));
  box.append(head, ...wordRows([{ text: c.text, url: c.url, t: null, en: null }]));
  return box;
}

function pickRow(x, v, ctx) {
  const { p, who } = x;
  const row = el('div', 'bf-pick');

  const id = el('div', 'bf-pick-id');
  const name = el('div', 'nm-line');
  name.append(el('span', 'no', String(p.horse_no)),
              el('span', `nm${v.booked(p.horse_no) ? ' booked' : ''}`, p.horse_name),
              el('span', 'zh', p.name_zh ?? ''));
  const jt = el('div', 'jt', `${p.jockey ?? DASH} · ${p.trainer ?? DASH} · DR ${drawText(p.draw)}`);
  const tags = el('div', 'tags');
  tags.append(el('span', 'count', countLabel(who)));
  chips(v.byNo[p.horse_no]).forEach((c) => tags.append(chipEl(c)));
  id.append(name, jt, tags);

  const srcs = el('div', 'bf-srcs');
  p.backed_by.forEach((b) => {
    const line = el('div', 'bf-src');
    const m = el('span', 'm');
    m.append(markEl(b));
    line.append(m, el('span', 'who',
      b.who + (b.role && b.kind !== 'featured' ? ` · ${b.role}` : '')));
    const words = el('div', 'words');
    if (b.kind !== 'connections' && b.words.length) {
      words.append(...wordRows(b.words));
    } else {
      const none = el('div', 'bf-w none');
      none.append(el('span', 'q', b.kind === 'connections' ? 'quoted in the interview above'
        : b.heard ? 'named aloud, no commentary captured' : 'pick only, no commentary captured'));
      if (b.url) none.append(timeLink({ url: b.url, t: null }));
      words.append(none);
    }
    line.append(words);
    srcs.append(line);
  });
  // Racing & Sports' line on the horse: form, not support, so it wears no
  // support mark and is not counted.
  if (p.form) {
    const line = el('div', 'bf-src form');
    const words = el('div', 'words');
    words.append(...wordRows([{ text: p.form.text, url: p.form.url, t: null, en: null }]));
    const m = el('span', 'm');
    m.append(el('span', 'bf-mark k-form', 'FORM'));
    line.append(m, el('span', 'who', 'Racing & Sports'), words);
    srcs.append(line);
  }

  row.append(id, priceTable(p.odds, ctx), srcs);
  return row;
}

const ROWS = [['WIN', 'WIN', 'win'], ['PLACE', 'PLACE', 'place'],
              ['FAIR CHANCE', 'FAIR %', 'fair_pct'],
              ['VALUE IF THE OTHER IS RIGHT', 'VALUE', 'value_pct']];

/** Every market's price for one horse: tote first, then each bookmaker. */
export function priceTable(odds, ctx) {
  const t = el('div', 'bf-pt');
  t.style.setProperty('--pt-n', String(ctx.markets.length));
  t.append(el('div', 'h'));
  ctx.markets.forEach((m) => {
    const h = el('div', 'h');
    const l = el('div', 'l');
    l.append(el('span', 'long', m.label), el('span', 'short', m.short === 'T' ? 'TOTE' : m.short));
    h.append(l, el('div', `s${m.stale ? ' stale' : ''}`, m.sub));
    t.append(h);
  });
  const live = ctx.markets.filter((m) => m.on).length;
  ROWS.forEach(([long, short, key]) => {
    const lab = el('div', 'lab');
    lab.append(el('span', 'long', long), el('span', 'short', short));
    t.append(lab);
    ctx.markets.forEach((m) => {
      const o = odds[m.k] ?? {};
      const val = o[key];
      let text = DASH;
      let cls = 'c off';
      if (m.on && val !== null && val !== undefined) {
        if (key === 'win') {
          text = px(val);
          cls = `c win${live > 1 && odds.pays_most === m.k ? ' most' : ''}`;
        } else if (key === 'place') { text = px(val); cls = 'c'; }
        else if (key === 'fair_pct') { text = pct(val); cls = 'c'; }
        else {
          text = sgn(val);
          cls = `c ${val >= EDGE_AT ? 'good' : val < 0 ? 'neg' : ''}`;
        }
      }
      t.append(el('div', cls, text));
    });
  });
  return t;
}

function restRow(x, v, ctx) {
  const row = el('div', 'bf-rest');
  const r = x.r;
  const tote = ctx.markets[0].on;
  const price = tote && r?.win_odds ? `TOTE ${px(r.win_odds)}`
    : x.gap ? `${atShort(x.gap.at)} ${px(x.gap.price)}`
      : tote ? `TOTE ${DASH}` : 'TOTE NOT OPEN';
  row.append(el('span', 'no', String(x.no)),
             el('span', `nm${v.booked(x.no) ? ' booked' : ''}`, r?.horse_name ?? x.gap?.name ?? ''),
             el('span', 'px', price));
  const cs = el('span', 'chips');
  chips(r).forEach((c) => cs.append(chipEl(c)));
  row.append(cs);
  const gap = el('span', 'gap');
  if (x.gap) {
    gap.append(el('b', null, sgn(x.gap.ev)), document.createTextNode(` value ${gapLine(x.gap)}`));
  }
  row.append(gap, el('span', 'src', x.iv ? 'INTERVIEW ONLY · NO PICK'
    : x.gap?.sup ? countLabel(x.gap.sup) : 'NO TIPS'));
  return row;
}

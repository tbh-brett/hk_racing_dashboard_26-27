/* Race Day — ported from web/design-source/Race Day.dc.html.
 *
 * The artboard's own structure, in order: race strip, blackbook band,
 * head-to-head band, race context bar, the 16-column card, a detail aside,
 * and a footer of standing facts. Keyboard: arrows move the selected runner,
 * digits switch race.
 *
 * The market price leads because it ranks horses better than every model here
 * (AUC .785 against .727), which the footer states outright.
 */
import { api, num } from './api.js';
import { el, $, DASH, MINUS, renderNav, styleBadge, styleOrdinal,
         compactDate, ordinal, tagLabel, tripTagChips, drawText,
         isVetTag } from './vocab.js';
import { context } from './context.js';
import { Live } from './live.js';
import { anchoredPanel } from './overlay.js';
import { install as installPalette } from './palette.js';

const svg = (tag, attrs) => {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
  return n;
};

const state = {
  date: null, race: 1, summary: null, card: null,
  selected: 0, sort: null, sortDir: 1, bbOpen: false, h2hOpen: true,
  seenAt: null,
  blackbook: null, blackbookError: null,
};

const COLS = [
  ['no', 'NO', 'c-num'], ['name', 'HORSE', ''], ['style', 'STYLE', ''],
  ['draw', 'DR', 'c-num'], ['jockey', 'JOCKEY', ''], ['trainer', 'TRAINER', ''],
  ['wt', 'WT', 'c-right'], ['odds', 'WIN / PLACE', 'c-right'],
  ['move', 'MOVE · MONEY', 'c-right'], ['win', 'WIN%', 'c-right'],
  ['mkt', 'MKT', 'c-num'], ['sarr', 'SARR', 'c-num'],
  ['edge', 'EDGE', 'c-num'], ['fig', 'LAST-RUN FIGURE', ''],
  ['last', 'LAST RUN', ''], ['bb', 'BB', 'c-num'],
];

/* ── chrome ──────────────────────────────────────────────────────────────── */

/* ── race strip ──────────────────────────────────────────────────────────── */

function renderStrip() {
  const races = state.summary?.races ?? [];
  $('race-chips').replaceChildren(...races.map((r) => {
    const b = el('button', 'race-chip', `R${r.race_no}`);
    b.setAttribute('aria-pressed', String(r.race_no === state.race));
    b.title = `${r.distance ?? DASH}m · ${r.field_size} runners`
      + (r.band ? ` · ${r.band}` : '');
    b.addEventListener('click', () => selectRace(r.race_no));
    return b;
  }));
  renderChanges();
}

/* ── what changed since I last looked ────────────────────────────────────────
 * Brief 01 lists this as one of the four questions the page exists to answer
 * twenty minutes before a race, and says the current dashboard does not show
 * it at all. The favourite changes between morning and post time in 44% of
 * races, which makes a fav swap the single most informative thing on screen.
 *
 * Brief 08 §1 puts it HERE rather than in the global chrome: it is Race Day
 * content, and a page-level fact rendering globally was the "button alternates
 * across pages" fault that brief was written to fix.
 *
 * "Since I last looked" is per-person, so the baseline is this browser's own
 * last visit to this meeting. With no earlier visit there is nothing to diff,
 * and the strip says so rather than inventing a baseline — a change count
 * measured from an arbitrary moment looks informative and is not.
 */
const SEEN_KEY = 'hkrd:last-seen';

function lastSeen(date) {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}')[date] ?? null;
  } catch {
    // A browser with site data blocked is not an error worth surfacing; it
    // just means there is no baseline, which the strip already handles.
    return null;
  }
}

function markSeen(date) {
  try {
    const all = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}');
    all[date] = new Date().toISOString().slice(0, 19);
    localStorage.setItem(SEEN_KEY, JSON.stringify(all));
  } catch { /* nothing to remember on is not a failure */ }
}

async function renderChanges() {
  const host = $('strip-right');
  const races = state.summary?.races ?? [];
  const withOdds = races.filter((r) => r.concentration !== null).length;
  host.replaceChildren();

  const since = state.seenAt;
  if (!since) {
    host.append(el('span', 'ch-none',
      `${races.length} RACES · ${withOdds} PRICED`));
    return;
  }
  let ch;
  try {
    ch = await api.changes(state.date, since);
  } catch (err) {
    host.append(el('span', 'ch-none', `CHANGES UNAVAILABLE — ${err.message}`));
    return;
  }
  if (!ch.observed || !ch.runners_compared) {
    host.append(el('span', 'ch-none',
      `${races.length} RACES · ${withOdds} PRICED`));
    return;
  }

  host.append(el('span', 'ch-k', 'CHANGES'));
  host.append(el('span', 'ch-since', `SINCE ${since.slice(11, 16)}`));
  if (ch.drifts) host.append(el('span', 'ch-drift', `${ch.drifts} DRIFTS`));
  if (ch.firmers) host.append(el('span', 'ch-firm', `${ch.firmers} FIRMERS`));
  if (ch.fav_swaps.length) {
    const swap = el('span', 'ch-swap',
      `${ch.fav_swaps.length} FAV SWAP${ch.fav_swaps.length > 1 ? 'S' : ''}`);
    swap.title = ch.fav_swaps
      .map((f) => `R${f.race_no}: ${f.from} → ${f.to}`).join('\n');
    host.append(swap);
  }
  if (ch.scratched.length) {
    const scr = el('span', 'ch-scr',
      `${ch.scratched.map((x) => `R${x.race_no}`).join(' ')} · ${ch.scratched.length} SCR`);
    scr.title = 'priced when you last looked, unpriced now';
    host.append(scr);
  }
  if (!ch.drifts && !ch.firmers && !ch.fav_swaps.length && !ch.scratched.length) {
    host.append(el('span', 'ch-none', 'nothing moved'));
  }
}

/* ── bands ───────────────────────────────────────────────────────────────── */

/** Movement as the band renders it: an arrow, a percentage, and a colour.
 *  A runner with one captured price gets a dash — never a 0% that would read
 *  as a market holding steady. */
function bbMove(e) {
  if (e.change_pct === null || e.change_pct === undefined || !e.observed) {
    return { text: DASH, cls: 'mv-none' };
  }
  const up = e.change_pct > 0;
  return {
    text: `${up ? '▲ +' : '▼ −'}${Math.abs(e.change_pct).toFixed(0)}%`,
    cls: up ? 'mv-drifted' : 'mv-shortened',
  };
}

/** The reason the horse is booked, shortened to fit one line. The tags are the
 *  fallback when no prose was written — a booking with neither says so. */
function bbNote(e) {
  if (e.reasoning) return e.reasoning;
  if (e.tags?.length) return e.tags.join(' · ').replace(/_/g, ' ');
  return 'no reason recorded';
}

function renderBlackbookBand() {
  const host = $('band-bb');
  const all = state.blackbook?.entries ?? [];
  const here = all.filter((e) => e.race_no === state.race);
  const rest = all.filter((e) => e.race_no !== state.race);

  const row = el('div', 'band-row');
  const tag = el('div', 'band-tag');
  tag.append(el('span', 'dot'));
  tag.append(document.createTextNode('BLACKBOOK'));
  tag.append(el('span', 'n', String(all.length)));
  // How many are in the race on screen, when that is more than one. The body
  // scrolls, and a row that scrolls with no count beside it looks like it is
  // showing everything there is.
  tag.append(el('span', 'sub', here.length > 1 ? `${here.length} HERE` : 'TODAY'));
  row.append(tag);

  const body = el('div', 'band-body');
  if (state.blackbookError) {
    body.append(el('div', 'band-empty',
      `BLACKBOOK UNAVAILABLE — ${state.blackbookError}`));
  } else if (!here.length) {
    body.append(el('div', 'band-empty',
      all.length ? 'NONE IN THIS RACE' : 'NONE BOOKED TODAY'));
  } else {
    // The reason for the booking is the first thing to go when the race is
    // crowded. Five booked horses each carrying a sentence is five items the
    // band can only show one of at a time; the name, the price and the move
    // are what you act on, and the reason is a click away in the expanded
    // grid and on the card row itself.
    const roomy = here.length <= 2;
    here.forEach((e) => {
      const item = el('div', 'band-item');
      if (!e.booked_before_race) item.classList.add('bb-stale');
      if (!roomy) item.classList.add('tight');
      item.append(el('span', 'name', `${e.horse_no} ${e.horse_name}`));
      item.append(el('span', 'od', num(e.win_odds)));
      const mv = bbMove(e);
      item.append(el('span', `pct ${mv.cls}`, mv.text));
      if (roomy) item.append(el('span', 'note', bbNote(e)));
      else item.title = bbNote(e);
      body.append(item);
    });
  }
  row.append(body);

  if (rest.length) {
    const strip = el('div', 'band-rest');
    rest.forEach((e) => {
      const chip = el('button', 'band-chip');
      if (!e.booked_before_race) chip.classList.add('bb-stale');
      chip.append(el('span', 'r', `R${e.race_no}`));
      chip.append(el('span', 'name', `${e.horse_no} ${e.horse_name}`));
      const mv = bbMove(e);
      chip.append(el('span', `pct ${mv.cls}`, mv.text));
      chip.addEventListener('click', () => selectRace(e.race_no));
      strip.append(chip);
    });
    row.append(strip);
  }

  const toggle = el('button', 'band-toggle',
    state.bbOpen ? 'COLLAPSE ▴' : `ALL ${all.length} ▾`);
  toggle.addEventListener('click', () => { state.bbOpen = !state.bbOpen; render(); });
  row.append(toggle);
  host.replaceChildren(row);

  if (!state.bbOpen || !all.length) return;
  const grid = el('div', 'bb-grid');
  all.forEach((e) => {
    const cell = el('button', 'bb-cell');
    if (e.race_no === state.race) cell.classList.add('here');
    if (!e.booked_before_race) cell.classList.add('bb-stale');
    const line = el('div', 'line');
    line.append(el('span', 'r', `R${e.race_no}`));
    line.append(el('span', 'off', e.off_time ?? DASH));
    line.append(el('span', 'name', `${e.horse_no} ${e.horse_name}`));
    line.append(el('span', 'odds', num(e.win_odds)));
    const mv = bbMove(e);
    line.append(el('span', `mv pct ${mv.cls}`, mv.text));
    cell.append(line);
    cell.append(el('div', 'note', bbNote(e)));
    cell.addEventListener('click', () => selectRace(e.race_no));
    grid.append(cell);
  });
  host.append(grid);
}

function swingLabel(p) {
  if (p.swing === null || p.swing === undefined) return 'NO WT DATA';
  // The dots are the tier, at 4, 6 and 8lb. A number alone makes the reader do
  // the threshold arithmetic on every card; the dots say "this one" at a
  // glance, which is the whole job of a band you scan rather than read.
  const dots = p.swing_tier ? ` ${'●'.repeat(p.swing_tier)}` : '';
  return `SWING ${p.swing}lb${dots}`;
}

/** Where a horse jumps from today against where it jumped from last time.
 *
 *  Two bare numbers used to sit here and they were not a pair of draws — they
 *  were each horse's draw at the LAST meeting, with today's never shown. So
 *  the line answered a question nobody asked and looked like it answered the
 *  one they did. */
function gateNote(then, now) {
  if (then === null || then === undefined
      || now === null || now === undefined) return String(now ?? then ?? DASH);
  const d = now - then;
  if (d === 0) return `${then}→${now} =`;
  // Wider or inside, named — a signed number alone leaves the reader working
  // out which direction is which on a track they may not have in mind.
  return `${then}→${now} ${d > 0 ? `+${d} W` : `${MINUS}${-d} IN`}`;
}

function renderH2HBand() {
  const host = $('band-h2h');
  const pairs = state.card?.head_to_head ?? [];
  const row = el('div', 'band-row');
  const tag = el('div', 'band-tag');
  tag.append(el('span', 'dot'));
  tag.append(document.createTextNode('HEAD TO HEAD'));
  tag.append(el('span', 'n', String(pairs.length)));
  tag.append(el('span', 'sub', 'PAIRS MEET AGAIN'));
  row.append(tag);
  row.append(el('div', 'band-body'));

  const toggle = el('button', 'band-toggle',
    state.h2hOpen ? 'HIDE' : 'SHOW');
  toggle.addEventListener('click', () => { state.h2hOpen = !state.h2hOpen; render(); });
  row.append(toggle);
  host.replaceChildren(row);

  if (!state.h2hOpen || !pairs.length) return;
  // EVERY pair, scrolled. Four of twenty-two were shown and the rest were
  // simply gone, with nothing on screen to say so — the band is sorted by
  // weight swing, so the ones dropped were the smallest swings, which is the
  // least bad truncation and still a silent one.
  const grid = el('div', 'h2h-grid');
  pairs.forEach((p) => {
    const c = el('div', 'h2h-cell');
    const l1 = el('div', 'h2h-line');
    // Full names. Taking the first word turned TO INFINITY into "TO" and
    // OUR LUCKY GLORY into "OUR" — two horses in the same field can share a
    // first word, so the short form was ambiguous as well as unreadable.
    l1.append(el('span', 'who', `${p.a_no} ${p.a_name}`));
    l1.append(el('span', 'v', 'v'));
    l1.append(el('span', 'who', `${p.b_no} ${p.b_name}`));
    // The record alone. "1-0 · 1 MEET" spent sixty pixels restating what
    // "1-0" already sums to, and those sixty pixels were coming out of the
    // horses' names.
    const rec = el('span', 'rec', p.record);
    if (p.meetings) {
      rec.title = `met ${p.meetings} time${p.meetings === 1 ? '' : 's'}`
        + ` — ${p.a_name} ${p.record.split('-')[0]}, ${p.b_name} ${p.record.split('-')[1]}`;
    }
    l1.append(rec);
    c.append(l1);

    const l2 = el('div', 'h2h-meta');
    l2.append(el('span', 'k', 'LAST'));
    l2.append(el('span', 'v2', compactDate(p.last_date)));
    l2.append(el('span', null, p.last_cond));
    // How each finished and what each carried — the pair the weight swing is
    // about. "6 v 8" was two finishing positions with the weights dropped, so
    // the line above it had nothing to be a change FROM.
    l2.append(el('span', 'v2',
      `${ordinal(p.a_place)}${p.a_weight_then ? ` (${p.a_weight_then})` : ''}`
      + ` · ${ordinal(p.b_place)}${p.b_weight_then ? ` (${p.b_weight_then})` : ''}`));
    c.append(l2);

    // WHO THE SWING FAVOURS, in words, above the arithmetic that produced it.
    // "-9 → 0" is the evidence; it is not the answer, and working the answer
    // out from it is the step that was going wrong.
    const l3 = el('div', 'h2h-verdict');
    if (p.favours_no) {
      const mine = p.favours_no === p.a_no;
      l3.append(el('span', `won-wt ${mine ? 'a' : 'b'}`,
        `${p.favours_no} ${p.favours_name}`));
      l3.append(el('span', 'by', `${p.favours_lb}lb better off`));
    } else if (p.favours_lb === 0) {
      l3.append(el('span', 'level', 'SAME WEIGHTS AS LAST TIME'));
    } else {
      l3.append(el('span', 'level', 'NO WEIGHT ON RECORD'));
    }
    l3.append(el('span', `swing swing-${p.swing_tier}`, swingLabel(p)));
    c.append(l3);

    const l4 = el('div', 'h2h-meta');
    l4.append(el('span', 'k', 'WT GAP'));
    l4.append(el('span', 'v2', `${p.gap_then ?? DASH} → ${p.gap_now ?? DASH}`));
    c.append(l4);

    // The gate move, one horse per line and labelled with the horse. Two
    // unlabelled notes side by side gave no way to tell whose gate was whose.
    [[p.a_no, p.a_gate_then, p.a_gate_now],
     [p.b_no, p.b_gate_then, p.b_gate_now]].forEach(([no, then, now]) => {
      const line = el('div', 'h2h-gate');
      line.append(el('span', 'no', String(no)));
      line.append(el('span', 'k', 'GATE'));
      const move = (then != null && now != null) ? now - then : null;
      line.append(el('span', `mv ${move === null ? '' : move > 0 ? 'wide'
        : move < 0 ? 'in' : 'same'}`.trim(), gateNote(then, now)));
      c.append(line);
    });
    grid.append(c);
  });
  host.append(grid);
}

/** The vet record in full, in the shared anchored panel.
 *
 * Brief 09 §1: every hover/click panel in the app goes through overlay.js, so
 * the collision handling and the fixed positioning are solved once. A panel
 * positioned in the row's own flow is what made the page vibrate on scroll.
 */
function openVet(trigger, runner, records) {
  // render() takes no arguments and RETURNS the element; anchoredPanel then
  // adds its own class to it. Passing a space-separated className would throw
  // in classList.add, so the panel's own class goes on the element here.
  anchoredPanel(trigger, () => {
    const host = el('div', 'vet-panel');
    host.append(el('h6', null, `${runner.horse_no} ${runner.horse_name} · VET RECORD`));
    records.forEach((v) => {
      const row = el('div', `vet-rec vet-${v.grade}`);
      const head = el('div', 'vr-head');
      head.append(el('span', 'd', compactDate(v.record_date)));
      head.append(el('span', 'c', v.category ?? 'UNKNOWN'));
      if (v.age_days != null) {
        head.append(el('span', 'age', `${v.age_days}d ago`));
      }
      row.append(head);
      row.append(el('div', 'vr-detail', v.detail));
      // Cleared to race is the difference between "was lame" and "is lame".
      row.append(el('div', v.cleared ? 'vr-cleared' : 'vr-open',
        v.cleared ? `cleared to race ${compactDate(v.passed_date)}`
                  : 'no clearance recorded'));
      host.append(row);
    });
    return host;
  });
}

/* ── race context bar ────────────────────────────────────────────────────── */

function renderRaceBar() {
  const c = state.card;
  const bar = $('race-bar');
  if (!c) { bar.replaceChildren(); return; }
  const nt = el('div', 'no-time');
  nt.append(el('span', 'rno', `R${c.race_no}`));
  // The venue is Layer 1 chrome and is already on screen once. This slot is
  // named rtime because the design puts the off time here: "R3 16:45".
  nt.append(el('span', 'rtime', c.off_time ?? ''));
  bar.replaceChildren(nt);

  const conds = el('div', 'conds');
  [['DIST', c.distance ? `${c.distance}m` : DASH],
   ['CLASS', c.race_class ?? DASH],
   ['GOING', c.going ?? DASH],
   ['COURSE', c.course ?? DASH],
   ['FIELD', c.field_size]].forEach(([k, v]) => {
    const d = el('div');
    d.append(el('span', 'k', `${k} `));
    d.append(document.createTextNode(String(v)));
    conds.append(d);
  });
  bar.append(conds);

  const conc = c.concentration ?? {};
  const box = el('div', 'conc-box');
  box.append(el('span', 'k', 'MKT CONCENTRATION'));
  if (conc.value === null || conc.value === undefined) {
    box.append(el('span', 'conc-badge conc-weak', 'NO ODDS'));
  } else {
    const bars = el('div', 'conc-bars');
    [7, 11, 16].forEach((h) => {
      const i = el('i');
      i.style.height = `${h}px`;
      bars.append(i);
    });
    box.append(bars);
    box.append(el('span', 'v', `${(conc.value * 100).toFixed(0)}%`));
    box.append(el('span', `conc-badge conc-${conc.band}`, conc.band.toUpperCase()));
    // A price captured hours before the off is not the price the coverage
    // rule was measured on, and must not read as though it were.
    if (conc.stale) {
      const s = el('span', 'conc-stale', `⚠ ${Math.round(conc.age_hours)}h EARLY`);
      s.title = conc.note ?? '';
      box.append(s);
    }
  }

  // When the price on screen was true. The page repaints itself as captures
  // land, so without this there is no way to tell a market that has not moved
  // from a page that has stopped listening — and those need different
  // reactions twenty minutes before the off.
  const at = conc.captured_at ?? c.captured_at;
  if (state.liveError) {
    const warn = el('span', 'conc-stale', '⚠ NOT UPDATING');
    warn.title = `the last attempt to refresh failed — ${state.liveError}. `
               + 'The price shown is the last one that was true.';
    box.append(warn);
  } else if (at) {
    const t = el('span', 'priced-at', `PRICED ${String(at).slice(11, 16)}`);
    t.title = `captured ${at}; refreshes on its own as new prices land`;
    box.append(t);
  }
  bar.append(box);
  poolBox(bar, c);
}

/* WHAT EACH POOL HOLDS.
 *
 * Concentration says how the money is DIVIDED and this says how much of it
 * there is, and the two are independent: a strongly concentrated race can be
 * a $90,000 market and a weak one a $4,000,000 market.
 *
 * The pair pools are here and not hidden behind the win pool because they are
 * usually the bigger market — 2026-09-09 HV race 1 held $251k in quinella
 * place and $202k in quinella against $175k in win — and because they are the
 * pools a QQP ticket is actually struck into.
 *
 * NOT AN ORDERING OF RACES BY INTEREST. The biggest win pool on a card is
 * usually an odds-on favourite in a small field: 2026-09-06 race 3 held
 * $4,288,122 because KA YING RISING was 1.0 in a field of six. Following that
 * money means backing an odds-on shot into a 17.5% takeout. */
function poolBox(bar, c) {
  const money = c.pool_money;
  const pools = money?.pools ?? {};
  const named = ['WIN', 'PLA', 'QIN', 'QPL'].filter((p) => pools[p] != null);
  if (!named.length) return;

  const box = el('div', 'pool-box');
  box.append(el('span', 'k', 'POOLS'));
  named.forEach((p) => {
    const cell = el('span', 'pool');
    cell.append(el('span', 'p', p === 'PLA' ? 'PLACE' : p));
    cell.append(el('span', 'v', dollars(pools[p])));
    cell.title = `${p} pool holds $${Math.round(pools[p]).toLocaleString()}`;
    box.append(cell);
  });
  // The pair pools added, because that is the market a QQP competes in and
  // neither half of it alone is that number.
  if (pools.QIN != null && pools.QPL != null) {
    const qqp = el('span', 'pool qqp');
    qqp.append(el('span', 'p', 'QQP'));
    qqp.append(el('span', 'v', dollars(pools.QIN + pools.QPL)));
    qqp.title = 'quinella + quinella place — the two pools one QQP ticket is '
      + 'struck into, added';
    box.append(qqp);
  }
  if (money?.captured_at) {
    const t = el('span', 'priced-at',
      `AT ${String(money.captured_at).slice(11, 16)}`);
    t.title = `pool turnover captured ${money.captured_at}`;
    box.append(t);
  }
  bar.append(box);
}

/* ── card ────────────────────────────────────────────────────────────────── */

function renderHead() {
  $('card-head').replaceChildren(...COLS.map(([key, label, cls]) => {
    const th = el('th', cls);
    th.append(document.createTextNode(label));
    if (state.sort === key) {
      th.append(el('span', 'ind', state.sortDir > 0 ? '▲' : '▼'));
    }
    th.addEventListener('click', () => {
      if (state.sort !== key) { state.sort = key; state.sortDir = 1; }
      else if (state.sortDir > 0) { state.sortDir = -1; }
      else { state.sort = null; }          // asc → desc → number order
      render();
    });
    return th;
  }));
}

const STYLE_ORDER = ['Leader', 'On-Pace', 'Midfield', 'Closer'];

function sortRunners(runners) {
  const rows = [...runners];
  if (!state.sort) return rows.sort((a, b) => (a.horse_no ?? 0) - (b.horse_no ?? 0));
  const key = state.sort;
  const val = (r) => {
    switch (key) {
      case 'no': return r.horse_no ?? 0;
      case 'name': return r.horse_name ?? '';
      // Never alphabetical: Closer, Leader, Midfield, On-Pace is meaningless.
      case 'style': return STYLE_ORDER.indexOf(r.last_run?.pace_style ?? '') + 1 || 99;
      case 'draw': return r.draw ?? 99;
      case 'jockey': return r.jockey ?? '';
      case 'trainer': return r.trainer ?? '';
      case 'wt': return r.actual_weight ?? 0;
      case 'odds': return r.win_odds ?? 9e9;
      case 'move': return r.movement?.change_pct ?? 0;
      case 'win': return -(r.win_pct ?? 0);
      case 'mkt': return r.market_rank ?? 99;
      case 'sarr': return r.sarr_rank ?? 99;
      case 'edge': return r.rank_delta ?? 0;
      case 'fig': return -(r.last_run?.figure ?? 0);
      case 'last': return r.last_run?.days_ago ?? 9e9;
      case 'bb': return r.blackbook ? 0 : 1;
      default: return 0;
    }
  };
  return rows.sort((a, b) => {
    const x = val(a); const y = val(b);
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * state.sortDir;
  });
}

/* GEAR, and what is happening to it.
 *
 * HKJC suffixes the code with the change — B1 is blinkers first time, B- is
 * blinkers off, B2 is second time — and the dashboard rendered the literal
 * text "B1" in a column on another page and nowhere said what the 1 meant.
 * The archive has 584 B1, 651 B- and 366 B2 in it.
 *
 * Two things the suffix cannot say are added by the server: RE-INSTATED (worn
 * before, off last start, back on — HKJC writes a plain B, identical to a
 * horse that has worn them for two seasons), and the BARRIER-TRIAL step. A
 * horse schooled in blinkers and not wearing them today is a stable preparing
 * something it has not committed to; that is a reason to keep watching, never
 * a reason to back, and the chip says so rather than scoring it.
 *
 * Only the notable states get a chip. A tongue tie worn every start for two
 * years is on the card and in the flyout; a chip that fires on everyone stops
 * being read, which is the same rule the vet badge follows. */
function gearChips(r) {
  const g = r.gear_change;
  if (!g) return [];
  const out = [];
  (g.pieces ?? []).filter((p) => p.notable).forEach((p) => {
    const label = p.state === 'first' ? `${p.code} 1ST`
      : p.state === 'second' ? `${p.code} 2ND`
        : p.state === 'off' ? `${p.code} OFF`
          : `${p.code} BACK`;
    const chip = el('span', `gear-chip g-${p.reinstated ? 'back' : p.state}`,
      label);
    chip.title = p.detail;
    out.push(chip);
  });
  // The schooling step, and only where it says something. `carried` is a horse
  // that trialled in gear it already wears, which is not news.
  (g.trial_gear ?? []).forEach((t) => {
    if (t.signal === 'carried') return;
    const chip = el('span', `gear-chip trial-${t.signal}`,
      t.signal === 'applied' ? `${t.code} AFTER TRIAL` : `TRIALLED ${t.code}`);
    chip.title = t.detail;
    out.push(chip);
  });
  // A month of the archive has no gear column at all — the results write
  // erased it for April to July 2026, and July has 0 of 641 runs on record —
  // so "nothing changed" and "we cannot tell what changed" are different
  // answers. That caveat is stated ONCE in the card footer rather than as a
  // chip per runner: it currently applies to most of the field, and a chip
  // that fires on everyone stops being read. See `gearCaveat`.
  return out;
}

/** How many runners have no gear on their previous run.
 *
 *  Everything above reads HKJC's own suffix, which is a fact about TODAY'S
 *  card and is unaffected. Only the two derived states — re-instated, and gear
 *  quietly dropped without a `-` — need the previous run, and for these
 *  runners there is nothing to compare against. */
function gearCaveat(runners) {
  const blind = runners.filter(
    (r) => r.gear_change && r.gear_change.declared && !r.gear_change.comparable);
  if (!blind.length) return null;
  return `${blind.length} OF ${runners.length} HAVE NO GEAR ON THEIR PREVIOUS `
    + 'RUN — APR–JUL 2026 WAS NOT RECORDED, SO A CHANGE CANNOT BE SHOWN FOR THEM';
}

function movementCell(r) {
  const m = r.movement;
  const td = el('td', 'c-right');
  const box = el('div', 'mv');

  if (!m) {
    box.append(el('span', 'mv-none', DASH));
  } else if (m.observed === false) {
    // Two captures a minute apart observed nothing. Reporting 0% from them
    // would claim the market held steady, which is unsupported.
    const n = el('span', 'mv-none', 'no window');
    n.title = `captures only ${m.window_minutes} min apart`;
    box.append(n);
  } else {
    const cls = `mv-${m.direction}`;
    box.append(el('span', 'prev', num(m.early, 1)));
    box.append(el('span', `arrow ${cls}`,
      m.direction === 'shortened' ? '▼' : m.direction === 'drifted' ? '▲' : '·'));
    box.append(el('span', `pct ${cls}`, `${Math.abs(m.change_pct).toFixed(0)}%`));
  }

  // THE LAST TEN MINUTES, on their own. A single first-to-last percentage
  // averages the window the money actually arrives in together with twenty
  // hours of nothing: on 2026-09-06 R9, #4 moved +1.6% over the whole day and
  // +21.6% inside the final ten minutes, so the one number on this row said
  // FLAT about a horse that was being let go. Settlement is tote, so this is
  // never a timing edge — it is what to look at, and how hard to back it.
  if (m && m.rush_pct !== null && m.rush_pct !== undefined
      && m.rush_direction !== 'flat') {
    const cls = `mv-${m.rush_direction}`;
    const rush = el('span', `rush ${cls}`,
      `${m.rush_pct > 0 ? '+' : '−'}${Math.abs(m.rush_pct).toFixed(0)}% late`);
    rush.title = `${num(m.rush_from, 1)} → ${num(m.late, 1)} inside the last `
      + `${m.rush_minutes} minutes`;
    box.append(rush);
  }

  // The shape of the money, in the row -- the artboard draws it inside this
  // cell rather than as a column of its own.
  const spark = sparkline(r);
  if (spark) box.append(spark);

  // AND HOW MUCH OF IT. This column has said MOVE · MONEY since the first
  // build and the money half was a sparkline of the PRICE, which is a ratio
  // and says nothing about scale: a tenth of a $9,000 pool and a tenth of a
  // $430,000 pool are the same number describing amounts fifty times apart.
  const win = r.money?.WIN;
  if (win?.dollars != null) {
    const cash = el('span', 'cash', dollars(win.dollars));
    cash.title = `${win.share_pct}% of the win pool`
      + (r.money.PLA?.dollars != null
        ? `
${dollars(r.money.PLA.dollars)} of the place pool `
          + `(${r.money.PLA.share_pct}%)` : '');
    box.append(cash);
  }
  td.append(box);
  return td;
}

/** $12.9k, $1.2M — a pool figure is read at a glance, not counted. */
function dollars(v) {
  if (v == null) return DASH;
  const n = Number(v);
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e4) return `$${Math.round(n / 1e3)}k`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}k`;
  return `$${Math.round(n)}`;
}

function sparkline(r) {
  if (!r.spark || r.spark_points_n < 2) return null;
  const s = svg('svg', { viewBox: '0 0 66 18', width: 50, height: 16,
                         preserveAspectRatio: 'none' });
  const colour = r.movement?.observed === false ? 'var(--text-faint)'
    : r.movement?.direction === 'shortened' ? 'var(--win)'
    : r.movement?.direction === 'drifted' ? 'var(--loss)' : 'var(--text-faint)';
  s.append(svg('polyline', { points: r.spark, fill: 'none',
                             stroke: colour, 'stroke-width': 1.6 }));
  s.append(svg('circle', { cx: r.spark_dot[0], cy: r.spark_dot[1],
                           r: 2.2, fill: colour }));
  s.setAttribute('aria-hidden', 'true');
  return s;
}

function edgeCell(r) {
  const td = el('td', 'c-num');
  const box = el('div', 'edge-cell');
  const d = r.rank_delta;
  if (d === null || d === undefined) { box.append(el('span', 'v mv-none', DASH)); td.append(box); return td; }
  // Negative means the model likes it more than the market does.
  const colour = d <= -3 ? 'var(--edge)' : d >= 3 ? 'var(--text-faint)' : 'var(--text-dim)';
  const v = el('span', 'v', d > 0 ? `+${d}` : String(d));
  v.style.color = colour;
  box.append(v);
  const track = el('div', 'edge-track');
  track.style.justifyContent = d < 0 ? 'flex-start' : 'flex-end';
  const fill = el('i');
  fill.style.width = `${Math.min(100, Math.abs(d) * 12)}%`;
  fill.style.background = colour;
  track.append(fill);
  box.append(track);
  td.append(box);
  return td;
}

function cardRow(r, index) {
  const tr = el('tr');
  tr.setAttribute('aria-selected', String(index === state.selected));
  tr.addEventListener('mouseenter', () => { state.selected = index; renderDetail(); });
  tr.addEventListener('click', () => { state.selected = index; renderDetail(); });

  const no = el('td', 'td-no');
  no.append(el('span', 'n', String(r.horse_no ?? DASH)));
  tr.append(no);

  const name = el('td');
  const box = el('div', 'horse');
  const nm = el('span', 'nm', r.horse_name);
  if (r.blackbook) nm.classList.add('booked');
  box.append(nm);
  // The FIRST tag in the array was an arbitrary pick — the order the deriver
  // happened to write them in — so a horse that bled last start showed
  // "contact" and the finding never reached the page at all. `tripTagChips`
  // is the shared rule: routine tags dropped, veterinary findings sorted to
  // the front and given the alert colour, the whole list on the tooltip.
  // A veterinary finding over the last six starts comes FIRST and by name.
  // The trip chip beside it reads the last run only, so a horse that bled
  // three starts back showed nothing here at all — and that is the one fact
  // on this row that decides whether you back it. Same server call as the
  // Form Guide, so the two pages cannot call the same horse sound and unsound.
  const found = r.vet_form ?? [];
  if (found.length) {
    const worst = found[0];   // newest first, from the server
    const chip = el('span', 'trip vet',
      `${tagLabel(worst.tag).toUpperCase()}${found.length > 1 ? ` +${found.length - 1}` : ''}`);
    chip.title = `${tagLabel(worst.tag)} — `
      + `${worst.runs_ago === 1 ? 'last start' : `${worst.runs_ago} runs ago`}`
      + `, ${compactDate(worst.race_date)}`
      + (worst.comment ? `\n\n${worst.comment}` : '');
    box.append(chip);
  }
  // Trouble only. The findings are on the chip above; showing them twice made
  // the row say "LAME HIND lame hind" and pushed the traffic note — a
  // different fact about the same run — off the end.
  const trip = tripTagChips((r.last_run?.tags ?? []).filter((t) => !isVetTag(t)), {
    comment: r.last_run?.comment ?? null,
    limit: 1, cls: 'trip',
  });
  if (trip) box.append(trip);
  // Vet records have been scraped into the database since the first build and
  // were never read back — brief 07 §2 puts a compact badge here, expanding on
  // click. Significant and routine read differently on purpose: a passed
  // examination is on nearly every runner, and a badge that fires on everyone
  // stops being read.
  const vet = r.vet ?? [];
  if (vet.length) {
    const worst = vet.find((v) => v.grade === 'significant') ?? vet[0];
    // A tag, in the same shape and the same colour family as every other tag
    // on this row. It used to read "VET !" in a style of its own, which made
    // the one chip about the horse's soundness look like a different kind of
    // object from the chip beside it saying what went wrong in the race.
    const chip = el('span',
      `trip vet vet-${worst.grade}${worst.cleared ? ' cleared' : ''}`,
      `VET${vet.length > 1 ? ` +${vet.length - 1}` : ''}`);
    // NO `title`. The panel below is the reading of these records — grouped,
    // dated, coloured by category, with what cleared them. The browser's own
    // tooltip drew the same text again in a white box on top of it, so every
    // hover produced two answers to one question.
    // Bound once, at construction. anchoredPanel attaches mouseenter/mouseleave
    // to the trigger, so calling it from a click handler would stack a fresh
    // pair of listeners on every click.
    openVet(chip, r, vet);
    box.append(chip);
  }
  gearChips(r).forEach((c) => box.append(c));
  name.append(box);
  tr.append(name);

  const st = el('td');
  st.append(styleBadge(r.last_run?.pace_style, { chip: false }));
  tr.append(st);

  tr.append(el('td', 'c-num', drawText(r.draw)));
  tr.append(el('td', null, r.jockey ?? DASH));

  const trn = el('td', r.trainer_changed ? 'trainer-changed' : null);
  trn.append(el('span', 'tn', r.trainer ?? DASH));
  if (r.trainer_changed) trn.title = `was ${r.trainer_prev}`;
  tr.append(trn);

  tr.append(el('td', 'c-right', String(r.actual_weight ?? DASH)));

  const od = el('td', 'c-right');
  od.append(el('div', 'odds-win', r.win_odds ? num(r.win_odds, 1) : DASH));
  // Place odds are scraped, never derived: there is no fixed ratio to win.
  od.append(el('div', 'odds-place', r.place_odds ? num(r.place_odds, 1) : DASH));
  tr.append(od);

  tr.append(movementCell(r));
  tr.append(el('td', 'c-right', r.win_pct !== null && r.win_pct !== undefined
    ? `${r.win_pct}%` : DASH));
  tr.append(el('td', 'c-num', String(r.market_rank ?? DASH)));

  const sr = el('td', 'c-num');
  const sv = el('span', null, String(r.sarr_rank ?? DASH));
  if (r.sarr_rank === 1) sv.style.color = 'var(--edge)';
  sr.append(sv);
  tr.append(sr);

  tr.append(edgeCell(r));

  const fg = el('td');
  const f = el('div', 'fig-cell');
  const lr = r.last_run;
  if (lr?.figure !== null && lr?.figure !== undefined) {
    const colour = lr.figure >= 100 ? 'var(--win)' : 'var(--loss)';
    const v = el('span', 'v', num(lr.figure, 0));
    v.style.color = colour;
    f.append(v);
    const m = lr.figure_display?.match(/([+-][\d.]+L)/);
    if (m) { const l = el('span', 'len', m[1]); l.style.color = colour; f.append(l); }
    const c = lr.figure_display?.match(/(low|medium|high)/);
    if (c) f.append(el('span', 'conf', c[1].toUpperCase()));
  } else {
    f.append(el('span', 'mv-none', DASH));
  }
  fg.append(f);
  tr.append(fg);

  tr.append(el('td', null, lr
    // "10th, 45d" — a finishing position is spoken as an ordinal, and the
    // design writes it that way. A bare 10 reads as a count.
    ? `${ordinal(lr.place)}, ${lr.days_ago}d` : DASH));

  const bb = el('td', 'c-num');
  if (r.blackbook) {
    const dot = el('span', 'bb-dot');
    // A booking made after this race was never a live thesis over it. The dot
    // is hollow in that case rather than absent, so an archived card does not
    // silently under-report what is in the book.
    if (r.blackbook.booked_before_race === false) dot.classList.add('later');
    dot.title = [r.blackbook.reasoning,
                 r.blackbook.tags?.join(' · ').replace(/_/g, ' '),
                 `booked ${r.blackbook.added_date} · ${r.blackbook.status}`]
      .filter(Boolean).join('\n');
    bb.append(dot);
  }
  tr.append(bb);
  return tr;
}

/* ── detail aside ────────────────────────────────────────────────────────── */

function renderDetail() {
  const host = $('detail');
  const rows = sortRunners(state.card?.runners ?? []);
  const r = rows[state.selected];
  if (!r) { host.replaceChildren(el('div', 'empty', 'no runner selected')); return; }

  host.replaceChildren();
  const dh = el('div', 'dh');
  dh.append(el('span', 'no', String(r.horse_no ?? DASH)));
  dh.append(el('span', 'nm', r.horse_name));
  dh.append(el('span', 'od', r.win_odds ? num(r.win_odds, 1) : DASH));
  host.append(dh);

  const tr = el('section', r.trainer_changed ? 'trainer-changed' : null);
  const th = el('div', 'h2h-meta');
  th.append(el('span', 'k', 'TRAINER'));
  th.append(el('span', 'v2', r.trainer ?? DASH));
  if (r.trainer_changed) th.append(el('span', 'k', `since ${r.trainer_prev}`));
  tr.append(th);
  host.append(tr);

  // The note that put this horse in the book. It was on the runner the whole
  // time and the panel never showed it — which is the one thing on this panel
  // the owner wrote themselves, and the reason the horse is worth a second
  // look at all.
  if (r.blackbook?.reasoning) {
    const bb = el('section', 'bb-note');
    bb.append(el('h6', null, 'BLACKBOOK NOTE'));
    bb.append(el('p', null, r.blackbook.reasoning));
    const meta = el('div', 'bb-meta');
    meta.append(el('span', 'k', r.blackbook.status?.toUpperCase() ?? ''));
    if (r.blackbook.added_date) {
      meta.append(el('span', null, `since ${compactDate(r.blackbook.added_date)}`));
    }
    (r.blackbook.tags ?? []).forEach(
      (t) => meta.append(el('span', 'tag', tagLabel(t))));
    bb.append(meta);
    host.append(bb);
  }

  // Who else in today's field this horse has already run against. The band
  // across the top has every pair; this is the same fact narrowed to the
  // runner being looked at, which is the question you have while looking at it.
  const met = (state.card?.head_to_head ?? []).filter(
    (p) => p.a_no === r.horse_no || p.b_no === r.horse_no);
  if (met.length) {
    const sec = el('section');
    sec.append(el('h6', null, "MET TODAY'S FIELD BEFORE"));
    met.forEach((p) => {
      const mine = p.a_no === r.horse_no;
      const row = el('div', 'met-row');
      row.append(el('span', 'v2',
        `v ${mine ? p.b_no : p.a_no} ${mine ? p.b_name : p.a_name}`));
      // The record read from THIS horse's side. Printed as stored it would
      // say 2-1 to a horse that has lost twice.
      const rec = String(p.record ?? '').split('-');
      row.append(el('span', 'rec',
        mine ? p.record : `${rec[1] ?? ''}-${rec[0] ?? ''}`));
      // WHICH WAY THE WEIGHT WENT, from this horse's side. The swing is the
      // gap BETWEEN the pair, so the same "5lb" is a help to one of them and a
      // hindrance to the other, and printing it unsigned left the reader doing
      // that arithmetic under time pressure on the one figure the pair is
      // sorted by. Green is better off than when they last met, red is worse.
      if (p.swing != null) {
        const helped = p.favours_no === r.horse_no;
        const level = p.favours_no == null || !p.favours_lb ? 'level'
          : helped ? 'better' : 'worse';
        const k = el('span', `k swing-${level}`,
          level === 'level' ? 'level'
            : `${helped ? '−' : '+'}${p.favours_lb}lb`);
        k.title = level === 'level'
          ? 'the weight gap between them is unchanged since they last met'
          : helped
            ? `${p.favours_lb}lb better off with ${mine ? p.b_name : p.a_name} `
              + 'than when they last met'
            : `${p.favours_lb}lb worse off with ${mine ? p.b_name : p.a_name} `
              + 'than when they last met';
        row.append(k);
      }
      sec.append(row);
    });
    host.append(sec);
  }

  moneySection(host, r);

  const form = el('section');
  form.append(el('h6', null, 'LAST SIX'));
  const tbl = el('table');
  form.append(tbl);
  host.append(form);
  if (r.form) {
    // Straight from cache. The table used to be filled ONLY by the fetch
    // callback, so the second time you hovered a horse it stayed empty
    // forever — the data was there and nothing drew it.
    tbl.replaceChildren(...r.form.map(formRow));
  } else {
    form.append(el('div', 'empty', 'loading…'));
    loadForm(r, form);
  }

  /* MARKET SHAPE — the de-vigged win probability of every runner, so the whole
   * race's money is one picture rather than fourteen numbers.
   *
   * Three things about it are deliberate.
   *
   * It is SORTED, shortest price first. Unsorted, in saddlecloth order, it is a
   * list of bars and not a shape at all — you cannot see whether the money is
   * on one horse or spread across six, which is the only question it answers.
   *
   * The bar runs with PROBABILITY, so the favourite is longest and the outsider
   * shortest. That has always been true — the bar is win %, not the price — but
   * it did not read that way, so the price now sits beside the percentage and
   * the heading says which way round it is.
   *
   * The brightness runs with market standing, in the palette's existing neutral
   * steps. It used to be one amber bar on whichever horse was being hovered,
   * which meant a 26.0 outsider got the loudest bar on screen — and amber means
   * "model edge" everywhere else on this page, so it was also saying something
   * about the model that nothing had computed. The horse being looked at is now
   * marked by an outline, which is what "you are here" means elsewhere.
   */
  const shape = el('section');
  // One line, like every other heading here. The direction needs no words now:
  // the rows descend by probability and the price column ascends beside them,
  // which says "longer bar, shorter odds" more plainly than a label could.
  const shapeHead = el('h6', null, 'MARKET SHAPE · WIN %');
  shapeHead.title = 'Every runner’s de-vigged win probability, shortest '
    + 'price first. A longer bar is a shorter price.';
  shape.append(shapeHead);
  const priced = rows
    .filter((x) => x.win_pct !== null && x.win_pct !== undefined)
    .sort((a, b) => b.win_pct - a.win_pct);
  if (!priced.length) {
    shape.append(el('div', 'empty', 'no market yet'));
  }
  const maxPct = Math.max(...priced.map((x) => x.win_pct), 1);
  // Brightest at the head of the market, faintest at the tail. Four bands
  // because the palette has four neutral steps and inventing a fifth would
  // mean a colour with no name.
  const weight = (i) => (i === 0 ? 'lead' : i < 3 ? 'strong' : i < 6 ? 'mid' : 'tail');
  priced.forEach((x, i) => {
    const row = el('div', 'shape-row');
    if (x.horse_no === r.horse_no) row.classList.add('you');
    row.append(el('span', 'no', String(x.horse_no ?? DASH)));
    const track = el('div', 'shape-track');
    const bar = el('i', weight(i));
    bar.style.width = `${(100 * x.win_pct) / maxPct}%`;
    track.append(bar);
    row.append(track);
    row.append(el('span', 'pct', `${x.win_pct}`));
    // The price beside the share, because "longer bar = shorter odds" is the
    // one thing about this panel that has to be unambiguous.
    row.append(el('span', 'od', x.win_odds ? num(x.win_odds, 1) : DASH));
    shape.append(row);
  });
  host.append(shape);

  const dis = el('section');
  dis.append(el('h6', null, `MODEL vs MARKET · R${state.card.race_no}`));
  const disagreements = rows
    .filter((x) => x.rank_delta !== null && x.rank_delta !== undefined)
    .sort((a, b) => a.rank_delta - b.rank_delta).slice(0, 3);
  // Edge is the MODEL's rank minus the MARKET's, so it needs both. Before the
  // market opens there is no second opinion to disagree with — and saying "no
  // model ranks" then blamed the model for the market's absence, on a card
  // whose SARR column was fully populated.
  if (!disagreements.length) {
    const rated = rows.some((x) => x.sarr_rank != null);
    const priced = rows.some((x) => x.market_rank != null);
    dis.append(el('div', 'empty',
      !rated ? 'no model ranks — run the analysis'
        : !priced ? 'no market yet — edge needs odds to disagree with'
          : 'model and market agree'));
  }
  disagreements.forEach((x) => {
    const row = el('div', 'dis-row');
    const e = el('span', 'e', x.rank_delta > 0 ? `+${x.rank_delta}` : String(x.rank_delta));
    e.classList.add(x.rank_delta <= -3 ? 'strong' : 'quiet');
    row.append(e);
    row.append(el('span', null, `${x.horse_no} ${x.horse_name}`));
    row.append(el('span', 'r', `SARR ${x.sarr_rank ?? DASH} · MKT ${x.market_rank ?? DASH}`));
    dis.append(row);
  });
  host.append(dis);
}

/** One past run, as the panel shows it. Split out because the panel draws it
 *  from cache and the fetch draws it on arrival, and two copies would drift. */
/* WHERE THE MONEY IS, for this runner.
 *
 * Two readings the win price cannot give on its own. First, the same horse's
 * share of the WIN pool against its share of the PLACE pool: they are separate
 * markets with separate money, and a horse holding a tenth of one and a
 * twentieth of the other is being backed to do two different things. On
 * 2026-09-09 HV race 1, runner 6 held 9.5% of the win pool and 11.9% of the
 * place pool — the biggest place share on the race, fifth-favourite to win.
 *
 * Second, the PAIRS. A QQP ticket is struck into the quinella and the quinella
 * place together, and those two pools held $454,000 on that race against the
 * win pool's $175,000 — so a win-only reading of "where the money is" misses
 * more than half of it. Runner 6 was in four of the six best-backed pairs and
 * appears nowhere near the top of the win market.
 *
 * NOT A TIP. The pool is a denominator. Following it means backing whatever is
 * shortest into a 17.5% takeout, and nothing here ranks a runner by the money
 * on it — the card is still sorted by price. */
function moneySection(host, r) {
  const money = r.money;
  const pool = state.card?.pool_money;
  if (!money && !pool?.pairs?.length) return;

  const sec = el('section');
  sec.append(el('h6', null, 'MONEY ON THIS RUNNER'));

  if (money) {
    const grid = el('div', 'money-grid');
    [['WIN', money.WIN], ['PLACE', money.PLA]].forEach(([label, m]) => {
      if (!m) return;
      const row = el('div', 'money-row');
      row.append(el('span', 'p', label));
      row.append(el('span', 'o', m.odds == null ? DASH : num(m.odds, 1)));
      row.append(el('span', 'pc', `${m.share_pct}%`));
      row.append(el('span', 'v', m.dollars == null ? DASH : dollars(m.dollars)));
      grid.append(row);
    });
    if (grid.childElementCount) sec.append(grid);
    // The gap between the two shares, said rather than left to be read off two
    // percentages: it is the whole point of showing them together.
    const w = money.WIN?.share_pct;
    const p = money.PLA?.share_pct;
    if (w != null && p != null && Math.abs(p - w) >= 1.5) {
      sec.append(el('div', 'money-note', p > w
        ? `Backed to PLACE more than to win — ${(p - w).toFixed(1)} points more `
          + 'of the place pool than of the win pool.'
        : `Backed to WIN more than to place — ${(w - p).toFixed(1)} points more `
          + 'of the win pool than of the place pool.'));
    }
  }

  // The pairs, filtered to the ones this runner is in. Ranked by the money on
  // them across both pair pools, which is what a QQP ticket competes with.
  const mine = (pool?.pairs ?? []).filter(
    (x) => x.horse_nos.includes(r.horse_no));
  if (pool?.pairs?.length) {
    sec.append(el('div', 'money-sub',
      `BEST-BACKED PAIRS · TOP ${pool.pairs.length} OF ${pool.pairs_priced}`));
    if (!mine.length) {
      sec.append(el('div', 'money-note',
        'Not in any of them — the pair money is elsewhere in this race.'));
    }
    mine.slice(0, 5).forEach((x) => {
      const other = x.horse_nos.find((n) => n !== r.horse_no) ?? r.horse_no;
      const row = el('div', 'money-row pair');
      row.append(el('span', 'p', `with ${other}`));
      const qin = x.pools?.QIN;
      const qpl = x.pools?.QPL;
      row.append(el('span', 'o', qin?.odds == null ? DASH : num(qin.odds, 1)));
      row.append(el('span', 'o', qpl?.odds == null ? DASH : num(qpl.odds, 1)));
      row.append(el('span', 'v',
        x.combined == null ? DASH : dollars(x.combined)));
      row.title = `QIN ${qin?.odds ?? '—'} · QPL ${qpl?.odds ?? '—'} — `
        + 'combined is the two pools added, which is what one QQP ticket is '
        + 'struck into';
      sec.append(row);
    });
  }
  host.append(sec);
}


function formRow(f) {
  const tr = el('tr');
  tr.append(el('td', null, f.race_date?.slice(5) ?? DASH));
  tr.append(el('td', null, `${f.distance ?? DASH} ${f.going ?? ''}`));
  // Classes, not inline styles. A colour written into the element cannot be
  // restyled from the stylesheet, does not follow the tokens when they move,
  // and is the one place on this page a rule could not reach.
  const pos = el('td', `c-right${f.place === 1 ? ' won' : ''}`,
    String(f.place ?? f.place_code ?? DASH));
  tr.append(pos);
  // Presence, not truth. `f.et_figure ?` reads a figure of 0 as no figure;
  // par is 100 so it cannot happen today, and it is the same falsy test that
  // hid the missing draw for a week.
  const hasFig = f.et_figure !== null && f.et_figure !== undefined;
  const fig = el('td', `c-right${hasFig ? (f.et_figure >= 100 ? ' above' : ' below') : ''}`,
    hasFig ? num(f.et_figure, 0) : DASH);
  tr.append(fig);
  tr.append(el('td', 'c-right', f.win_odds ? num(f.win_odds, 1) : DASH));
  return tr;
}

async function loadForm(runner, section) {
  try {
    const data = await api.horse(runner.horse_name, 6);
    runner.form = data.runs;
    // Moving the cursor down a card re-renders the panel and throws this
    // section away mid-flight. Writing into it then puts the rows in a node
    // nobody can see, and the horse now on screen keeps saying "loading…".
    if (!section.isConnected) return;
    section.querySelector('.empty')?.remove();
    section.querySelector('table').replaceChildren(...data.runs.map(formRow));
  } catch {
    if (!section.isConnected) return;
    section.querySelector('.empty')
      ?.replaceChildren(document.createTextNode('unavailable'));
  }
}

/* ── footer: standing facts, not per-race values ─────────────────────────── */

function renderFoot() {
  const c = state.card;
  const foot = $('card-foot');
  const bits = [];
  if (c?.overround !== null && c?.overround !== undefined) {
    bits.push(`OVERROUND ${c.overround}%`);
  }
  // The 3× rule of thumb used to be named here, because it was the thing the
  // ratio disproved. It is gone from the whole dashboard now, so what the
  // range says is simply that no single multiple works.
  bits.push(c?.place_ratio_range
    ? `PLACE ODDS ARE SCRAPED — WIN/PLACE RATIO RUNS ${c.place_ratio_range} ON THIS CARD`
    : 'PLACE ODDS ARE SCRAPED, NEVER DERIVED FROM WIN');
  const blind = gearCaveat(state.card?.runners ?? []);
  if (blind) bits.push(blind);
  bits.push('STYLE SORTS LEADER → ON-PACE → MIDFIELD → CLOSER');
  bits.push('MODEL AUC .727 · MARKET AUC .785');
  foot.replaceChildren(...bits.map((b) => el('span', null, b)));
  foot.append(el('span', 'keys', '↑↓ runner · 1–9 race · click header to sort'));
}

/* ── render / load ───────────────────────────────────────────────────────── */

function render() {
  renderStrip();
  renderBlackbookBand();
  renderH2HBand();
  renderRaceBar();
  renderHead();
  const rows = sortRunners(state.card?.runners ?? []);
  $('card-body').replaceChildren(...rows.map(cardRow));
  renderFoot();
  renderDetail();
}

function selectRace(no) {
  // Routed through context so the URL and every other page-level reader stay
  // in step; context calls back into onContext.
  context.setRace(no);
}

/* The card follows the market rather than the page load.
 *
 * Odds are captured every minute through the last ten before a race, so a card
 * read once at load is showing the price from whenever it was opened — in the
 * exact window brief 01 says this page is for. Every re-render is state-driven,
 * so a fresh payload repaints without losing the selected row, an open detail
 * panel or either band's expanded state.
 *
 * The freshness strip is refreshed with it: a new price landing and the header
 * still claiming the odds are an hour old is a page disagreeing with itself.
 */
const liveCard = new Live((card) => {
  state.card = card;
  state.liveError = null;
  render();
  context.refreshFreshness();
}, (message) => {
  // Kept, not thrown away, and NOT allowed to blank the card: the price on
  // screen is still the last one that was true. renderRaceBar says it is not
  // being updated any more.
  state.liveError = message;
  renderRaceBar();
});

async function loadRace() {
  try {
    const { body, etag, pollAfter } = await api.raceCardLive(state.date, state.race);
    state.card = body;
    state.liveError = null;
    state.selected = 0;
    liveCard.watch(`${state.date}/${state.race}`,
                   `/raceday/${state.date}/${state.race}`);
    liveCard.seed(etag, pollAfter);
  } catch (e) {
    state.card = null;
    liveCard.stop();
    $('card-body').replaceChildren(el('tr', null, `failed to load: ${e.message}`));
    return;
  }
  render();
}

/* The page no longer owns the meeting. It reads context and re-renders when
   context changes — brief 01: "chosen once. Every part of the page obeys it." */
async function onContext(_ctx, what) {
  const changedMeeting = state.date !== context.date;
  state.date = context.date;
  state.summary = context.summary;
  state.race = context.race;
  if (changedMeeting && context.date) {
    // Read the baseline BEFORE stamping this visit, or the diff would always
    // be against a moment two milliseconds ago and always read "nothing moved".
    state.seenAt = lastSeen(context.date);
    markSeen(context.date);
  }
  if (what === 'date') {                    // a new meeting is loading
    state.card = null;
    state.blackbook = null;
    render();
    return;
  }
  if (what === 'meeting') {
    try {
      state.blackbook = await api.meetingBlackbook(state.date);
      state.blackbookError = null;
    } catch (e) {
      state.blackbook = null;
      state.blackbookError = e.message;
    }
  }
  await loadRace();
}

function onKey(e) {
  // The palette is an input; a digit typed into it is a search, not a race.
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
  const rows = sortRunners(state.card?.runners ?? []);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = state.selected + (e.key === 'ArrowDown' ? 1 : -1);
    state.selected = Math.max(0, Math.min(rows.length - 1, next));
    render();
  } else if (/^[1-9]$/.test(e.key)) {
    const no = Number(e.key);
    if ((state.summary?.races ?? []).some((r) => r.race_no === no)) {
      selectRace(no);
    }
  }
}

async function init() {
  renderNav($('nav'), 'raceday.html');
  installPalette();
  document.addEventListener('keydown', onKey);
  context.onChange(onContext);
  await context.init();
  await onContext(context, 'meeting');
}

init();

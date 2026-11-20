/* desk.js — the Bets page's DESK view: choose a banker, one race at a time.
 *
 * Rebuilt on 19 Nov 2026. The first version put every race and every candidate
 * on one long page, each a click to open, and its owner did not use it: "not
 * at all intuitive (infinite scroll)", purpose unclear. He decides race by
 * race and compares his horses with each other, so that is the page:
 *
 *   the race chips   one per race, with how many of your horses run and a ✓
 *                    once you have decided; the next race to run opens first.
 *   the table        your candidates side by side: how strong your evidence is
 *                    and when you wrote it, the place pool's chance, the pace
 *                    model's chance of leading, the model's rank, and the facts
 *                    your own record has done better (▲) or worse (▼) with.
 *   one row opened   your words, your record, the ticket, TAKE or PASS
 *                    (desk-detail.js).
 *   the multi        how often the bankers you have taken would ALL place —
 *                    one miss loses the whole ticket — beside the likeliest of
 *                    your own horses in the same races.
 *
 * A race that has gone off stays: its result, its dividends, and where each of
 * your horses finished, for reviewing the night race by race.
 *
 * Moved to a page of its own on 20 Nov 2026 (desk-page.js), where a race that
 * has gone off also carries the review of your bankers in it: the page hands
 * in `afterRace` (drawn under the result) and `chipNote` (a race chip's "to
 * judge" count), and `firstRace` picks the race to open once every race has
 * run. The facts column left the table that day: measured on 20 Nov its ▲/▼
 * marks could not be told from noise. The facts stay in the opened row.
 */
import { el, DASH } from './vocab.js';
import { api } from './api.js';
import { detailEl, evidenceCell, odds, ordinal, pct } from './desk-detail.js';
import { isBoth } from './book-origin.js';

const S = {
  host: null, date: null, data: null, error: null, account: 'brett', hooks: {},
  race: null,            // the race on screen
  open: null,            // the candidate opened, 'race-banker'
  all: new Set(),        // candidates showing every fact
  tickets: new Map(),    // a re-priced, decided or nominated ticket, by key
  nominated: new Map(),  // race -> [banker], runners he chose to look at
  taps: new Map(), notes: new Map(), sure: new Map(), busy: new Set(), msg: new Map(),
};
const key = (n, b) => `${n}-${b}`;

export function renderDesk(host, date, { account, afterRace, chipNote, firstRace } = {}) {
  S.host = host;
  S.account = account || 'brett';
  S.hooks = { afterRace, chipNote, firstRace };
  if (date !== S.date) {
    S.date = date;
    S.data = null;
    S.error = null;
    S.race = null;
    S.open = null;
    [S.all, S.tickets, S.nominated, S.taps, S.notes, S.sure, S.msg].forEach((m) => m.clear());
    load();
  }
  draw();
}

async function load() {
  if (!S.date) return;
  try {
    S.data = await api.desk(S.date);
    S.error = null;
    const d = S.data;
    const next = d.races.find((r) => !r.gone && r.candidates.length) ?? d.races.find((r) => !r.gone);
    // Once every race has run the night is for reviewing, from the first race
    // with something of yours still to judge.
    S.race = (next?.race_no ?? S.hooks?.firstRace?.(d.races) ?? d.races[0]?.race_no) ?? null;
  } catch (err) {
    S.error = err.message;
  }
  draw();
}

function candidates(race) {
  const list = race.candidates.map((c) => S.tickets.get(key(race.race_no, c.banker.horse_no)) ?? c);
  (S.nominated.get(race.race_no) ?? []).forEach((b) => {
    const t = S.tickets.get(key(race.race_no, b));
    if (t && !list.some((c) => c.banker.horse_no === b)) list.push(t);
  });
  return list;
}

const taken = (t) => t.decision && !t.decision.late && t.decision.decision !== 'rejected';

/* ── the page ───────────────────────────────────────────────────────────── */

function draw() {
  const host = S.host;
  if (!host) return;
  host.replaceChildren();
  const head = el('div', 'section-head');
  head.append(el('span', 'title', `THE DESK · ${S.date ?? DASH}`));
  head.append(el('span', 'sub', 'BEFORE THE OFF: CHOOSE · AFTER IT: JUDGE'));
  host.append(head);
  host.append(el('div', 'dk-purpose',
    'Before a race: your horses side by side — your evidence, how likely each is to place and to '
    + 'lead. Open one, say in a line what has to happen, how sure you are, and TAKE or PASS. '
    + 'After it: one tap for what decided each of your bankers.'));
  if (S.error) { host.append(el('div', 'no-match', `DESK FAILED TO LOAD — ${S.error}`)); return; }
  if (!S.data) { host.append(el('div', 'empty-line', 'LOADING')); return; }
  if (!S.data.races.length) { host.append(el('div', 'empty-line', 'NO CARD FOR THIS MEETING')); return; }
  host.append(chipsEl());
  // The multi is a question asked before racing; once every race has run, the
  // review under each race has taken its place.
  if (S.data.races.some((r) => !r.gone)) host.append(multiEl());
  const race = S.data.races.find((r) => r.race_no === S.race);
  if (race) host.append(raceEl(race));
}

function chipsEl() {
  const box = el('div', 'race-chips dk-chips');
  S.data.races.forEach((r) => {
    const list = candidates(r);
    const yours = list.filter((c) => c.evidence?.own).length;
    const decided = list.some((c) => c.decision && !c.decision.late);
    const b = el('button', `race-chip${r.gone ? ' gone' : ''}`, `R${r.race_no}`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(r.race_no === S.race));
    if (yours) b.append(el('span', 'bb', String(yours)));
    // Once the race has run, the chip says what is left to judge instead.
    const note = r.gone ? S.hooks?.chipNote?.(r) : null;
    if (decided && !note) b.append(el('span', 'ok', '✓'));
    if (note) b.append(el('span', note.cls, note.text));
    b.title = `${r.off_time ?? ''} · ${yours} of your horses${r.gone ? ' · gone off' : ''}`
      + (note?.title ? ` · ${note.title}` : '');
    b.addEventListener('click', () => { S.race = r.race_no; S.open = null; draw(); });
    box.append(b);
  });
  return box;
}

/* ── the multi ──────────────────────────────────────────────────────────── */

function multiEl() {
  const box = el('div', 'dk-multi');
  box.append(el('span', 'k', 'YOUR MULTI'));
  const legs = S.data.races.map((r) => ({ race: r, t: candidates(r).find(taken) })).filter((x) => x.t);
  if (legs.length < 2) {
    box.append(el('span', 'v', 'Take a banker in two or more races and this shows how often they '
      + 'would all place. One miss loses a multi; it does not lose a single.'));
    return box;
  }
  const chance = (list) => (list.every((p) => p !== null && p !== undefined)
    ? list.reduce((a, p) => a * (p / 100), 1) : null);
  const mine = legs.map(({ race, t }) => ({ race, b: t.banker }));
  const best = legs.map(({ race }) => {
    const own = candidates(race).filter((c) => c.evidence?.own && c.banker.place_pct !== null);
    const top = own.sort((a, b) => b.banker.place_pct - a.banker.place_pct)[0];
    return { race, b: top ? top.banker : null };
  });
  const line = (label, list) => {
    const row = el('div', 'dk-multi-row');
    row.append(el('span', 'l', label));
    list.forEach(({ race, b }) => row.append(el('span', 'leg',
      b ? `R${race.race_no} #${b.horse_no} ${b.horse_name} ${pct(b.place_pct)}` : `R${race.race_no} —`)));
    const all = chance(list.map(({ b }) => (b ? b.place_pct : null)));
    row.append(el('b', null, all === null ? 'no price yet'
      : `all ${list.length} place: ${(all * 100).toFixed(1)}%`));
    return row;
  };
  box.append(line('your bankers', mine));
  if (best.some((x, i) => x.b && x.b.horse_no !== mine[i].b.horse_no)) {
    box.append(line('likeliest of your horses', best));
  }
  box.title = 'Each banker’s chance of placing from the place pool, multiplied: how often every leg’s '
    + 'banker would be in the frame. Partners have to land as well, so the ticket pays less often.';
  return box;
}

/* ── one race ───────────────────────────────────────────────────────────── */

function raceEl(race) {
  const box = el('section', 'dk-race');
  const hd = el('div', 'dk-race-hd');
  hd.append(el('span', 'no', `R${race.race_no}`));
  hd.append(el('span', 'meta', [race.off_time, race.venue, race.distance ? `${race.distance}m` : null,
    race.race_class ? `CLASS ${race.race_class}` : null, `${race.field_size} RUNNERS`]
    .filter(Boolean).join(' · ')));
  hd.append(el('span', 'at', race.gone ? 'GONE OFF'
    : race.captured_at ? `PRICES AT ${race.captured_at.slice(11, 16)}` : 'NO PRICE YET'));
  box.append(hd);
  if (race.result) box.append(resultEl(race.result));
  const after = race.gone ? S.hooks?.afterRace?.(race) : null;
  if (after) {
    box.append(after);
    box.append(el('div', 'dk-line', 'YOUR HORSES HERE AS THEY STOOD BEFORE THE OFF'));
  }
  const leaders = race.runners.filter((r) => r.lead_pct !== null && r.lead_pct !== undefined)
    .sort((a, b) => b.lead_pct - a.lead_pct).slice(0, 3);
  if (leaders.length) {
    box.append(el('div', 'dk-line', 'LIKELIEST TO LEAD (PACE MODEL): ' + leaders.map((r) =>
      `#${r.horse_no} ${r.horse_name} ${pct(r.lead_pct)}`).join(' · ')));
  }
  const list = candidates(race);
  if (!list.length) box.append(el('div', 'dk-quiet', 'None of your horses in this race.'));
  else box.append(tableEl(race, list, leaders[0]?.lead_pct ?? null));
  const open = list.find((t) => key(race.race_no, t.banker.horse_no) === S.open);
  if (open) box.append(openEl(race, open));
  if (!race.gone) box.append(nominateEl(race, list));
  return box;
}

function resultEl(res) {
  const box = el('div', 'dk-line dk-result');
  const order = res.order.slice(0, 3).map((x) => `${ordinal(x.place)} #${x.horse_no} ${x.horse_name} ${odds(x.win_odds)}`);
  const div = (pool) => (res.dividends[pool] ?? []).map((d) =>
    `${pool} ${d.combination.replace(',', '-')} $${Number(d.dividend).toFixed(2)}`);
  box.textContent = ['RESULT', ...order].join(' · ')
    + (res.dividends.QIN ? `   ·   ${[...div('QIN'), ...div('QPL')].join(' · ')} per $10` : '');
  return box;
}

function tableEl(race, list, topLead) {
  const tab = el('table', 'grid dk-compare');
  const tr0 = el('tr');
  ['', 'YOUR HORSE', 'YOUR EVIDENCE', 'PLACES', 'LEADS', 'MODEL',
    race.gone ? 'FINISHED' : 'YOU'].forEach((x, i) => tr0.append(el('th', [3, 4, 5].includes(i) ? 'num' : null, x)));
  const thead = el('thead');
  thead.append(tr0);
  tab.append(thead);
  const tbody = el('tbody');
  list.forEach((t) => {
    const b = t.banker;
    const k = key(race.race_no, b.horse_no);
    const tr = el('tr', `dk-row${S.open === k ? ' open' : ''}`);
    tr.append(el('td', 'caret', S.open === k ? '▾' : '▸'));
    const name = el('td', 'name');
    name.append(el('span', null, `#${b.horse_no} ${b.horse_name ?? ''}`));
    const ev = t.evidence;
    const both = isBoth(ev?.booking);
    name.append(el('span', `dk-who ${both ? 'both' : ev?.own ? 'own' : ev ? 'sys' : 'none'}`,
      both ? 'YOURS + SYSTEM' : ev?.own ? 'YOURS' : ev ? 'SYSTEM' : 'NOMINATED'));
    tr.append(name);
    const e = evidenceCell(t, S.date);
    const evTd = el('td', `dk-ev s-${e.strength}`);
    evTd.append(el('span', null, e.text));
    if (e.when) evTd.append(el('span', 'w', e.when));
    evTd.title = e.title;
    tr.append(evTd);
    const pl = el('td', 'num');
    pl.append(el('b', null, pct(b.place_pct)));
    pl.append(el('span', 'n', b.market_rank ? ` ${ordinal(b.market_rank)} · ${odds(b.win_odds)}` : ' no price'));
    pl.title = b.place_source === 'place pool' ? 'the place pool’s own chance' : 'the model’s chance (no place pool yet)';
    tr.append(pl);
    tr.append(el('td', `num${b.lead_pct !== null && b.lead_pct === topLead ? ' top' : ''}`, pct(b.lead_pct)));
    tr.append(el('td', 'num', b.model_rank ? `#${b.model_rank}` : DASH));
    tr.append(lastTd(race, t));
    tr.addEventListener('click', () => { S.open = S.open === k ? null : k; draw(); });
    tbody.append(tr);
  });
  tab.append(tbody);
  const wrap = el('div', 'table-box');
  wrap.append(tab);
  return wrap;
}

function lastTd(race, t) {
  const td = el('td', 'dk-last');
  const d = t.decision;
  if (race.gone) {
    const f = t.banker.finish;
    td.append(el('b', f && f <= 3 ? 'placed' : null, f ? ordinal(f) : DASH));
  }
  if (d) td.append(el('span', `dk-state ${d.decision}${d.late ? ' late' : ''}`,
    d.decision === 'rejected' ? 'PASSED' : 'TAKEN'));
  else if (!race.gone) td.append(el('span', 'dk-quiet-in', DASH));
  return td;
}

function openEl(race, t) {
  const k = key(race.race_no, t.banker.horse_no);
  if (!S.taps.has(k)) S.taps.set(k, new Set(t.decision?.reasons ?? []));
  if (!S.notes.has(k)) S.notes.set(k, t.decision?.reason ?? '');
  if (!S.sure.has(k)) S.sure.set(k, t.decision?.confidence ?? null);
  return detailEl(t, {
    race, raceDate: S.date, gone: race.gone, all: S.all.has(k), runners: race.runners,
    reasons: S.data.reasons, taps: S.taps.get(k), note: S.notes.get(k), busy: S.busy.has(k),
    sure: S.sure.get(k), onSure: (v) => { S.sure.set(k, v); draw(); },
    msg: S.msg.get(k),
    onAll: () => { if (S.all.has(k)) S.all.delete(k); else S.all.add(k); draw(); },
    onNote: (v) => S.notes.set(k, v),
    onReprice: (partners) => act(k, () => api.deskTicket(S.date, race.race_no, t.banker.horse_no, partners)),
    onDecide: (kind) => act(k, async () => {
      const body = { decision: kind, account: S.account, reasons: [...S.taps.get(k)],
                     reason: S.notes.get(k) || null, confidence: S.sure.get(k) || null };
      if (kind === 'accept' && t.edited) body.partners = t.partners.map((p) => p.horse_no);
      return (await api.deskDecide(S.date, race.race_no, t.banker.horse_no, body)).ticket;
    }),
  });
}

async function act(k, fn) {
  S.busy.add(k);
  draw();
  try {
    S.tickets.set(k, await fn());
    S.msg.delete(k);
  } catch (err) {
    S.msg.set(k, err.message);
  }
  S.busy.delete(k);
  draw();
}

function nominateEl(race, list) {
  const have = new Set(list.map((c) => c.banker.horse_no));
  const rest = race.runners.filter((r) => !have.has(r.horse_no));
  const box = el('div', 'dk-nominate');
  if (!rest.length) return box;
  const sel = el('select');
  sel.append(el('option', null, 'Compare another runner…'));
  rest.forEach((r) => {
    const o = el('option', null, `#${r.horse_no} ${r.horse_name ?? ''}`
      + (r.win_odds ? ` · ${odds(r.win_odds)}` : ''));
    o.value = String(r.horse_no);
    sel.append(o);
  });
  sel.addEventListener('change', async () => {
    const b = Number(sel.value);
    if (!b) return;
    const k = key(race.race_no, b);
    try {
      S.tickets.set(k, await api.deskTicket(S.date, race.race_no, b));
      S.nominated.set(race.race_no, [...(S.nominated.get(race.race_no) ?? []), b]);
      S.open = k;
    } catch (err) {
      S.msg.set(k, err.message);
    }
    draw();
  });
  box.append(sel);
  return box;
}


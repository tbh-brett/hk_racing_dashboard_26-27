/* desk.js — the Bets page's DESK view: your banker, the engine's partners,
 * and your own record beside every fact about the horse.
 *
 * docs/ticket-desk.md and docs/banker-card.md. The candidates are the horses
 * you have a book tier on; any other runner can be nominated from the race's
 * list. Each opens to three things, in the order a decision is made:
 *
 *   the brief    each fact knowable before the off, beside how YOUR bankers
 *                with that fact have placed against the price, on the same
 *                side of your evidence. The mark (▲ ▼) is against your USUAL
 *                on that side -- every horse you have written about starts
 *                from your evidence's record, so a mark against break-even
 *                would read the same on all of them. The number's colour is
 *                against the money: break-even or past it, or below the market.
 *   the ticket   the engine's partners, by the pool that pays them, what each
 *                line would return alone, and the flags. Tap runners to change
 *                the partners; the ticket is re-priced and the change is kept
 *                as an edit, because "your partners or the engine's" is a
 *                question the record is still answering.
 *   the decision TAKE or PASS, with the taps for what the database cannot see
 *                and a line of your own. Nothing here places a bet.
 */
import { el, DASH } from './vocab.js';
import { api } from './api.js';

const MARK = { stronger: '▲ STRONGER', weaker: '▼ WEAKER', usual: 'AS USUAL', thin: 'THIN',
               unknown: DASH };
const DECIDED = { accepted: 'TAKEN', edited: 'TAKEN · YOUR PARTNERS', rejected: 'PASSED' };

const S = {
  host: null, date: null, data: null, error: null, account: 'brett',
  open: new Set(),        // candidate keys opened
  all: new Set(),         // candidate keys showing every fact, not just the marked
  tickets: new Map(),     // key -> a re-priced or nominated ticket
  nominated: new Map(),   // race_no -> [banker_no]
  taps: new Map(),        // key -> Set of reason keys being tapped
  notes: new Map(),       // key -> the line being typed
  busy: new Set(), msg: new Map(),
};
const key = (n, b) => `${n}-${b}`;

export function renderDesk(host, date, { account } = {}) {
  S.host = host;
  S.account = account || 'brett';
  if (date !== S.date) {
    S.date = date;
    S.data = null;
    S.error = null;
    [S.open, S.all, S.tickets, S.nominated, S.taps, S.notes, S.msg].forEach((m) => m.clear());
    load();
  }
  draw();
}

async function load() {
  if (!S.date) return;
  try {
    S.data = await api.desk(S.date);
    S.error = null;
  } catch (err) {
    S.error = err.message;
  }
  draw();
}

/* ── formatting ─────────────────────────────────────────────────────────── */

const ae = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(2));
const odds = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(1));
const pctx = (v) => (v === null || v === undefined ? DASH : `${Number(v).toFixed(0)}%`);
const money = (v) => (v === null || v === undefined ? DASH : `$${Math.round(v).toLocaleString()}`);
const time = (iso) => (iso ? iso.slice(11, 16) : DASH);

function lineText(ln) {
  if (!ln) return DASH;
  return `${ae(ln.ae)} · ${ln.placed}/${Number(ln.expected ?? 0).toFixed(1)} · n ${ln.bankers}`;
}

/* ── drawing ────────────────────────────────────────────────────────────── */

function draw() {
  const host = S.host;
  if (!host) return;
  host.replaceChildren();
  const head = el('div', 'section-head');
  head.append(el('span', 'title', `THE DESK · ${S.date ?? DASH}`));
  head.append(el('span', 'sub', 'YOU PICK THE BANKER · THE ENGINE PICKS THE PARTNERS · '
    + 'A CANDIDATE IS NOT A TIP'));
  host.append(head);
  if (S.error) { host.append(el('div', 'no-match', `DESK FAILED TO LOAD — ${S.error}`)); return; }
  if (!S.data) { host.append(el('div', 'empty-line', 'LOADING')); return; }
  host.append(el('div', 'caveat',
    'Beside each fact: your bankers since April with that fact, placed against the price’s own '
    + 'place chance, on the same side of your evidence where there are enough. ▲ and ▼ say the '
    + 'fact has gone better or worse than your usual on that side; the number is green at '
    + `${S.data.breakeven.toFixed(2)}, roughly where a banker’s tickets start to cover the `
    + 'takeout, and red below the market. Your record, not a forecast.'));
  S.data.races.forEach((race) => host.append(raceEl(race)));
}

function raceEl(race) {
  const box = el('section', `dk-race${race.gone ? ' gone' : ''}`);
  const hd = el('div', 'dk-race-hd');
  hd.append(el('span', 'no', `R${race.race_no}`));
  hd.append(el('span', 'meta', [race.off_time, race.venue, race.distance ? `${race.distance}m` : null,
    race.race_class ? `CLASS ${race.race_class}` : null, `${race.field_size} RUNNERS`]
    .filter(Boolean).join(' · ')));
  hd.append(el('span', 'at', race.gone ? 'GONE OFF'
    : race.captured_at ? `PRICES AT ${time(race.captured_at)}` : 'NO PRICE YET'));
  box.append(hd);
  if (race.gone) {
    box.append(el('div', 'dk-quiet', 'Scored on the BANKERS view once its result is in.'));
    return box;
  }
  const shown = [...race.candidates];
  (S.nominated.get(race.race_no) ?? []).forEach((b) => {
    const t = S.tickets.get(key(race.race_no, b));
    if (t && !shown.some((c) => c.banker.horse_no === b)) shown.push(t);
  });
  if (!shown.length) box.append(el('div', 'dk-quiet', 'Nothing of yours in this race.'));
  shown.forEach((c) => box.append(candidateEl(race, S.tickets.get(key(race.race_no, c.banker.horse_no)) ?? c)));
  box.append(nominateEl(race, shown));
  return box;
}

function candidateEl(race, t) {
  const b = t.banker;
  const k = key(race.race_no, b.horse_no);
  const open = S.open.has(k);
  const box = el('div', `dk-cand${open ? ' open' : ''}`);
  const hd = el('button', 'dk-cand-hd');
  hd.type = 'button';
  hd.append(el('span', 'caret', open ? '▾' : '▸'));
  hd.append(el('span', 'name', `#${b.horse_no} ${b.horse_name ?? ''}`));
  const ev = t.evidence;
  hd.append(el('span', `dk-who ${ev?.own ? 'own' : ev ? 'sys' : 'none'}`,
    ev?.own ? `YOURS · ${ev.tier}` : ev ? `SYSTEM · ${ev.tier}` : 'NOMINATED'));
  hd.append(el('span', 'px', b.market_rank ? `${ordinalRank(b.market_rank)} · ${odds(b.win_odds)}` : 'UNPRICED'));
  hd.append(el('span', 'pl', b.place_pct === null ? DASH
    : `PLACES ${pctx(b.place_pct)} ${b.place_source === 'place pool' ? 'POOL' : 'MODEL'}`));
  if (t.brief) {
    hd.append(el('span', 'tally', `${t.brief.stronger} ▲ · ${t.brief.weaker} ▼`));
  }
  if (t.decision) {
    hd.append(el('span', `dk-state ${t.decision.decision}${t.decision.late ? ' late' : ''}`,
      `${DECIDED[t.decision.decision]}${t.decision.late ? ' · LATE' : ''}`));
  }
  hd.addEventListener('click', () => {
    if (open) S.open.delete(k); else S.open.add(k);
    draw();
  });
  box.append(hd);
  if (!open) return box;
  const body = el('div', 'dk-cand-body');
  if (t.brief) body.append(briefEl(t, k));
  body.append(ticketEl(race, t, k));
  body.append(decideEl(race, t, k));
  box.append(body);
  return box;
}

function ordinalRank(n) {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th');
  return `${n}${s} IN MARKET`;
}

/* ── the brief ──────────────────────────────────────────────────────────── */

function briefEl(t, k) {
  const b = t.brief;
  const every = S.all.has(k);
  const order = { stronger: 0, weaker: 1, usual: 2, thin: 3, unknown: 4 };
  const rows = [...b.rows]
    .filter((r) => every || r.mark === 'stronger' || r.mark === 'weaker')
    .sort((x, y) => (every ? 0 : order[x.mark] - order[y.mark]));
  const box = el('div', 'dk-brief');
  const hd = el('div', 'dk-sub');
  hd.append(el('span', 't', 'YOUR RECORD BESIDE EACH FACT'));
  hd.append(el('span', 'n', b.own ? 'READ WITH YOUR EVIDENCE ON IT' : 'READ WITHOUT YOUR EVIDENCE ON IT'));
  box.append(hd);
  if (b.usual) {
    const u = el('div', `dk-usual p-${b.usual_paid}`);
    u.append(el('span', 'k', `Your usual ${b.own ? 'with' : 'without'} your evidence`));
    u.append(el('span', 'rec', lineText(b.usual)));
    u.append(el('span', 'mark', b.usual_paid === 'breakeven' ? 'PAYS'
      : b.usual_paid === 'market' ? 'ABOVE THE PRICE' : 'BELOW THE PRICE'));
    box.append(u);
  }
  if (!rows.length) box.append(el('div', 'dk-quiet', 'No fact about this horse has gone better or worse than your usual.'));
  rows.forEach((r) => {
    const row = el('div', `dk-fact m-${r.mark} p-${r.paid}`);
    row.append(el('span', 'k', r.label));
    row.append(el('span', 'v', r.value ?? 'not known'));
    row.append(el('span', 'rec', lineText(r.line)));
    row.append(el('span', 'mark', MARK[r.mark]));
    row.title = [
      r.basis === 'same side'
        ? `Your bankers like this ${b.own ? 'with' : 'without'} your evidence on them, against your usual on that side (${lineText(r.usual)}).`
        : r.basis === 'all your bankers'
          ? `All your bankers like this (too few on this side of your evidence), against all your bankers (${lineText(r.usual)}).`
          : `Against the other side of your evidence (${lineText(r.usual)}).`,
      r.yours ? `All of them: ${lineText(r.yours)}.` : null,
      r.lean ? `You pick this ${Number(r.lean).toFixed(1)}x as often as it turns up.` : null,
      r.field_ae !== null && r.field_ae !== undefined
        ? `The runners like this you did not bank: ${ae(r.field_ae)}.` : null,
    ].filter(Boolean).join('\n');
    box.append(row);
  });
  const more = el('button', 'act ghost dk-more', every ? 'SHOW MARKED FACTS ONLY'
    : `SHOW ALL ${b.rows.length} FACTS`);
  more.type = 'button';
  more.addEventListener('click', () => {
    if (every) S.all.delete(k); else S.all.add(k);
    draw();
  });
  box.append(more);
  return box;
}

/* ── the ticket ─────────────────────────────────────────────────────────── */

function ticketEl(race, t, k) {
  const box = el('div', 'dk-ticket');
  const hd = el('div', 'dk-sub');
  hd.append(el('span', 't', `THE TICKET · ${t.pools.join(' + ')} · ${t.lines} LINES · ${money(t.cost)}`));
  hd.append(el('span', 'n', t.partner_source ? `PARTNERS BY THE ${t.partner_source.toUpperCase()}`
    : 'NO PRICE TO RANK PARTNERS'));
  box.append(hd);
  if (t.partners.length) {
    const tab = el('table', 'grid dk-lines');
    const tr0 = el('tr');
    ['', 'PARTNER', 'WIN', 'QPL', 'QIN', 'LINE ALONE PAYS'].forEach((h, i) =>
      tr0.append(el('th', i >= 2 ? 'num' : null, h)));
    const thead = el('thead');
    thead.append(tr0);
    tab.append(thead);
    const tbody = el('tbody');
    t.partners.forEach((p) => {
      const tr = el('tr');
      tr.append(el('td', 'num', String(p.rank)));
      tr.append(el('td', 'name', `#${p.horse_no} ${p.horse_name ?? ''}`
        + (p.engine_rank && p.engine_rank !== p.rank ? ` (engine’s ${p.engine_rank})` : '')));
      tr.append(el('td', 'num', odds(p.win_odds)));
      tr.append(el('td', 'num', odds(p.qpl_odds)));
      tr.append(el('td', 'num', odds(p.qin_odds)));
      const pays = el('td', 'num');
      ['QPL', 'QIN'].forEach((pool) => {
        const v = p.line_returns[pool];
        if (v === null || v === undefined) return;
        pays.append(el('span', p.covers_cost[pool] === false ? 'dk-under' : null, `${pool} ${money(v)} `));
      });
      tr.append(pays);
      tbody.append(tr);
    });
    tab.append(tbody);
    box.append(tab);
  }
  const hit = [];
  if (t.hit.qin_pct !== null) hit.push(`THE QUINELLA LANDS ${pctx(t.hit.qin_pct)} OF THE TIME`);
  if (t.hit.qpl_lines !== null) hit.push(`${Number(t.hit.qpl_lines).toFixed(2)} QPL LINES LAND ON AVERAGE`);
  if (hit.length) box.append(el('div', 'dk-quiet', `${hit.join(' · ')} — as the pools price it.`));
  t.flags.forEach((f) => {
    const row = el('div', 'dk-flag', `${f.code.replace(/_/g, ' ').toUpperCase()} · ${f.detail}`);
    row.title = f.basis;
    box.append(row);
  });
  box.append(partnerPicker(race, t, k));
  return box;
}

function partnerPicker(race, t, k) {
  const box = el('div', 'dk-pick');
  box.append(el('span', 'chip-label', 'PARTNERS'));
  const mine = new Set(t.partners.map((p) => p.horse_no));
  race.runners.filter((r) => r.horse_no !== t.banker.horse_no).forEach((r) => {
    const c = el('button', `chip${mine.has(r.horse_no) ? ' on' : ''}`, `#${r.horse_no}`);
    c.type = 'button';
    c.title = `${r.horse_name ?? ''} · ${r.win_odds ? odds(r.win_odds) : 'no price'}`;
    c.addEventListener('click', () => {
      const next = new Set(mine);
      if (next.has(r.horse_no)) next.delete(r.horse_no); else next.add(r.horse_no);
      if (next.size) reprice(race, t, k, [...next].sort((a, b) => a - b));
    });
    box.append(c);
  });
  if (t.edited) {
    const back = el('button', 'act ghost', 'ENGINE’S PARTNERS');
    back.type = 'button';
    back.addEventListener('click', () => reprice(race, t, k, null));
    box.append(back);
  }
  return box;
}

async function reprice(race, t, k, partners) {
  try {
    const got = await api.deskTicket(S.date, race.race_no, t.banker.horse_no, partners);
    S.tickets.set(k, got);
    S.msg.delete(k);
  } catch (err) {
    S.msg.set(k, err.message);
  }
  draw();
}

/* ── the decision ───────────────────────────────────────────────────────── */

function decideEl(race, t, k) {
  const box = el('div', 'dk-decide');
  const hd = el('div', 'dk-sub');
  hd.append(el('span', 't', 'WHY — WHAT THE DATABASE CANNOT SEE'));
  hd.append(el('span', 'n', 'OPTIONAL · SCORED ON THE BANKERS VIEW'));
  box.append(hd);
  const was = t.decision;
  if (!S.taps.has(k)) S.taps.set(k, new Set(was?.reasons ?? []));
  if (!S.notes.has(k)) S.notes.set(k, was?.reason ?? '');
  const taps = S.taps.get(k);
  const chips = el('div', 'chips');
  S.data.reasons.forEach((r) => {
    const c = el('button', `chip${taps.has(r.key) ? ' on' : ''}`, r.label);
    c.type = 'button';
    c.addEventListener('click', () => {
      if (taps.has(r.key)) taps.delete(r.key); else taps.add(r.key);
      c.classList.toggle('on');
    });
    chips.append(c);
  });
  box.append(chips);
  const note = el('input', 'dk-note');
  note.placeholder = 'a line of your own (optional)';
  note.value = S.notes.get(k);
  note.addEventListener('input', () => S.notes.set(k, note.value));
  box.append(note);

  const acts = el('div', 'dk-acts');
  const take = el('button', 'act primary', t.edited ? 'TAKE WITH YOUR PARTNERS' : 'TAKE');
  const pass = el('button', 'act', 'PASS');
  [take, pass].forEach((b) => { b.type = 'button'; b.disabled = S.busy.has(k); });
  take.addEventListener('click', () => decide(race, t, k, 'accept'));
  pass.addEventListener('click', () => decide(race, t, k, 'reject'));
  acts.append(take, pass);
  if (was) {
    acts.append(el('span', `dk-state ${was.decision}${was.late ? ' late' : ''}`,
      `${DECIDED[was.decision]} ${time(was.decided_at && toHk(was.decided_at))}`
      + (was.partners?.length ? ` · ${was.partners.map((h) => `#${h}`).join(' ')}` : '')
      + (was.late ? ' · AFTER THE OFF, IN NO FIGURE' : '')));
  }
  box.append(acts);
  if (S.msg.has(k)) box.append(el('div', 'no-match', S.msg.get(k)));
  box.append(el('div', 'caveat', 'Nothing here places a bet. Strike it with HKJC; the statement '
    + 'import brings it into the ledger and the desk matches it there.'));
  return box;
}

function toHk(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null
    : new Date(d.getTime() + 8 * 3600 * 1000).toISOString();
}

async function decide(race, t, k, decision) {
  S.busy.add(k);
  draw();
  const body = { decision, account: S.account, reasons: [...(S.taps.get(k) ?? [])],
                 reason: S.notes.get(k) || null };
  if (decision === 'accept' && t.edited) body.partners = t.partners.map((p) => p.horse_no);
  try {
    const res = await api.deskDecide(S.date, race.race_no, t.banker.horse_no, body);
    S.tickets.set(k, res.ticket);
    S.msg.delete(k);
  } catch (err) {
    S.msg.set(k, err.message);
  }
  S.busy.delete(k);
  draw();
}

/* ── nominating any runner ─────────────────────────────────────────────── */

function nominateEl(race, shown) {
  const have = new Set(shown.map((c) => c.banker.horse_no));
  const rest = race.runners.filter((r) => !have.has(r.horse_no));
  const box = el('div', 'dk-nominate');
  if (!rest.length) return box;
  const sel = el('select');
  sel.append(el('option', null, 'Bank another runner…'));
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
      S.open.add(k);
    } catch (err) {
      S.msg.set(k, err.message);
    }
    draw();
  });
  box.append(sel);
  return box;
}

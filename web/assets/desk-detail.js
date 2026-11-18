/* desk-detail.js — one candidate banker, opened on the DESK view.
 *
 * desk.js draws the race and the comparison table; this draws what a row opens
 * to, in the order a decision is made:
 *
 *   your evidence  the note or book entry itself, in your words, and when
 *   your record    your usual on this side of your evidence, then every fact
 *                  marked ▲ (gone better than your usual) or ▼ (worse); the
 *                  number is green at break-even, red below the market. All
 *                  fourteen facts fold out.
 *   the ticket     the engine's partners by the pool that pays them, what each
 *                  line returns alone, the flags; tap runners to change them.
 *   the decision   TAKE or PASS with taps for what the database cannot see.
 *                  None once the race has gone off: that would be hindsight.
 *
 * Also the shared vocabulary both modules use: the short name of every fact
 * and what a candidate's evidence amounts to in a table cell.
 */
import { el, DASH } from './vocab.js';

export const MARK = { stronger: '▲', weaker: '▼', usual: 'AS USUAL', thin: 'THIN', unknown: DASH };
export const DECIDED = { accepted: 'TAKEN', edited: 'TAKEN · YOUR PARTNERS', rejected: 'PASSED' };

/* The facts in a table cell: a few words each, the scorecard's own values. */
const SHORT = {
  own: { yours: 'your evidence', "system's only": "system's book", none: 'nothing of yours' },
  model: { 'top 3': 'model top 3', '4th or worse': 'model 4th+' },
  course_trip: { yes: 'placed here before', no: 'not placed here before' },
  market: { favourite: 'favourite', '2nd-3rd': '2nd–3rd in market', '4th-6th': '4th–6th in market',
            '7th or longer': '7th+ in market' },
  last_start: { won: 'won last start', '2nd-3rd': 'placed last start', '4th-6th': '4th–6th last start',
                '7th or worse': '7th+ last start', debut: 'debut' },
  close_last: { 'within 2 lengths': 'beaten ≤2L last start', 'further back': 'beaten >2L last start',
                won: 'won last start', debut: 'debut' },
  since_last: { '21 days or less': 'back within 21 days', '22-59 days': '22–59 days since',
                'first-up (60 days+)': 'first-up', debut: 'debut' },
  class: { down: 'down in class', same: 'same class', up: 'up in class' },
  draw: { '1-3': 'drawn 1–3', middle: 'middle draw', 'outside 30%': 'wide draw' },
  style: { 'on the pace': 'on-pace type', midfield: 'midfield type', back: 'back-marker' },
  rider: { '15%+ winners': '15%+ rider', '8-15%': '8–15% rider', 'under 8% or few rides': 'rider under 8%' },
  trial: { yes: 'trialled since', no: 'no trial since' },
  gear: { yes: 'first-time gear', no: 'no gear change' },
  tips: { '2+ sources': 'tipped by 2+', '1 source': 'tipped by 1', none: 'not tipped' },
};
export const short = (row) => SHORT[row.key]?.[row.value] ?? `${row.label}: ${row.value}`;

/* ── formatting ─────────────────────────────────────────────────────────── */

export const ae = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(2));
export const odds = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(1));
export const pct = (v) => (v === null || v === undefined ? DASH : `${Math.round(Number(v))}%`);
export const money = (v) => (v === null || v === undefined ? DASH : `$${Math.round(v).toLocaleString()}`);
export const ordinal = (n) => (n === null || n === undefined ? DASH
  : `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th')}`);

export function lineText(ln) {
  if (!ln) return DASH;
  return `${ae(ln.ae)} · ${ln.placed}/${Number(ln.expected ?? 0).toFixed(1)} · n ${ln.bankers}`;
}

function daysBefore(iso, raceDate) {
  if (!iso || !raceDate) return null;
  return Math.round((Date.parse(raceDate) - Date.parse(iso.slice(0, 10))) / 86400000);
}

function age(days) {
  if (days === null) return '';
  if (days <= 0) return 'today';
  if (days === 1) return '1 day before';
  return days < 60 ? `${days} days before` : `${Math.round(days / 30)} months before`;
}

const EXCUSE_WORD = { traffic: 'traffic', bad_draw: 'draw', bad_run: 'run', slow_start: 'start',
                      jockey_error: 'riding' };

/** What a candidate's evidence amounts to, for a table cell: what kind, how
 *  strong you rated it, and how long before the race you wrote it. The words
 *  themselves are the title, and the opened row. */
export function evidenceCell(t, raceDate) {
  const ev = t.evidence;
  if (!ev) return { text: 'nothing of yours', strength: 'none', title: 'Not in your book, and no note of yours on it.' };
  if (ev.tier === 'NOTE' && ev.note) {
    const r = ev.note.response;
    return { text: `trial note · ${r ? r : 'not rated'}${ev.note.asked === 1 ? ' · asked' : ''}`,
             when: age(daysBefore(ev.note.written_at, raceDate)), strength: r || 'unrated',
             title: `“${ev.note.note}” — trial of ${ev.note.trial_date}` };
  }
  if (ev.tier === 'RUN_NOTE' && ev.run_note) {
    return { text: 'run note', when: age(daysBefore(ev.run_note.written_at, raceDate)),
             strength: 'unrated', title: `“${ev.run_note.note}”` };
  }
  const b = ev.booking ?? {};
  const kind = ev.tier === 'EXCUSE' ? `excuse: ${(ev.excuse ?? []).map((x) => EXCUSE_WORD[x] ?? x).join(', ')}`
    : ev.tier === 'STALE' ? `stale · ${ev.starts} starts since` : ev.tier.toLowerCase();
  return { text: `${ev.own ? 'book' : "system's book"} · ${kind}`,
           when: b.added_date ? `booked ${age(daysBefore(b.added_date, raceDate))}` : '',
           strength: ev.own ? 'book' : 'system', title: b.reasoning ? `“${b.reasoning}”` : '' };
}

/* ── the opened row ─────────────────────────────────────────────────────── */

function sub(title, note) {
  const hd = el('div', 'dk-sub');
  hd.append(el('span', 't', title));
  if (note) hd.append(el('span', 'n', note));
  return hd;
}

/** h: {race, raceDate, gone, all, onAll, taps, note, onNote, busy, msg,
 *       reasons, runners, onReprice(partners|null), onDecide(kind)} */
export function detailEl(t, h) {
  const box = el('div', 'dk-detail');
  const left = el('div', 'dk-col');
  left.append(evidenceEl(t, h), recordEl(t, h));
  const right = el('div', 'dk-col');
  right.append(ticketEl(t, h));
  right.append(h.gone ? goneEl(t) : decideEl(t, h));
  box.append(left, right);
  return box;
}

function evidenceEl(t, h) {
  const box = el('div', 'dk-evidence');
  box.append(sub('YOUR EVIDENCE', evidenceCell(t, h.raceDate).when));
  const ev = t.evidence;
  const say = (label, text, when) => {
    if (!text) return;
    const row = el('div', 'dk-say');
    row.append(el('span', 'k', label), el('span', 'q', `“${text}”`));
    if (when) row.append(el('span', 'w', when));
    box.append(row);
  };
  if (!ev) box.append(el('div', 'dk-quiet', 'Nothing of yours on this horse.'));
  if (ev?.note) say(`trial ${ev.note.trial_date}${ev.note.response ? ` · ${ev.note.response}` : ''}`,
                    ev.note.note, ev.note.written_at?.slice(0, 10));
  if (ev?.run_note) say('your run note', ev.run_note.note, ev.run_note.written_at?.slice(0, 10));
  if (ev?.booking?.reasoning) say(ev.own ? 'your book' : "system's book", ev.booking.reasoning,
                                  ev.booking.added_date);
  return box;
}

function recordEl(t, h) {
  const b = t.brief;
  const box = el('div', 'dk-brief');
  if (!b) return box;
  box.append(sub('YOUR RECORD ON ITS FACTS', b.own ? 'WITH YOUR EVIDENCE ON IT' : 'WITHOUT YOUR EVIDENCE ON IT'));
  if (b.usual) {
    const u = el('div', `dk-usual p-${b.usual_paid}`);
    u.append(el('span', 'k', `Your usual ${b.own ? 'with' : 'without'} your evidence`));
    u.append(el('span', 'rec', lineText(b.usual)));
    u.append(el('span', 'mark', b.usual_paid === 'breakeven' ? 'PAYS'
      : b.usual_paid === 'market' ? 'ABOVE THE PRICE' : 'BELOW THE PRICE'));
    box.append(u);
  }
  const order = { stronger: 0, weaker: 1, usual: 2, thin: 3, unknown: 4 };
  const rows = [...b.rows].filter((r) => h.all || r.mark === 'stronger' || r.mark === 'weaker')
    .sort((x, y) => (h.all ? 0 : order[x.mark] - order[y.mark]));
  if (!rows.length) box.append(el('div', 'dk-quiet', 'No fact about it has gone better or worse than your usual.'));
  rows.forEach((r) => {
    const row = el('div', `dk-fact m-${r.mark} p-${r.paid}`);
    row.append(el('span', 'k', r.label), el('span', 'v', r.value ?? 'not known'),
               el('span', 'rec', lineText(r.line)), el('span', 'mark', MARK[r.mark]));
    row.title = r.basis === 'same side'
      ? `Your bankers like this ${b.own ? 'with' : 'without'} your evidence, against your usual (${lineText(r.usual)}).`
      : r.basis === 'all your bankers'
        ? `All your bankers like this (too few on this side), against all of them (${lineText(r.usual)}).`
        : r.basis ? `Against the other side of your evidence (${lineText(r.usual)}).` : '';
    box.append(row);
  });
  const more = el('button', 'act ghost dk-more', h.all ? 'MARKED FACTS ONLY' : `ALL ${b.rows.length} FACTS`);
  more.type = 'button';
  more.addEventListener('click', h.onAll);
  box.append(more);
  return box;
}

function ticketEl(t, h) {
  const box = el('div', 'dk-ticket');
  box.append(sub(`THE TICKET · ${t.pools.join(' + ')} · ${t.lines} LINES · ${money(t.cost)}`,
                 t.partner_source ? `PARTNERS BY THE ${t.partner_source.toUpperCase()}` : 'NO PRICE TO RANK PARTNERS'));
  if (t.partners.length) {
    const tab = el('table', 'grid dk-lines');
    const tr0 = el('tr');
    ['', 'PARTNER', 'WIN', 'QPL', 'QIN', 'THE LINE ALONE PAYS'].forEach((x, i) =>
      tr0.append(el('th', i >= 2 ? 'num' : null, x)));
    const thead = el('thead');
    thead.append(tr0);
    tab.append(thead);
    const tbody = el('tbody');
    t.partners.forEach((p) => {
      const tr = el('tr');
      tr.append(el('td', 'num', String(p.rank)));
      tr.append(el('td', 'name', `#${p.horse_no} ${p.horse_name ?? ''}`
        + (p.engine_rank && p.engine_rank !== p.rank ? ` (engine’s ${p.engine_rank})` : '')));
      [p.win_odds, p.qpl_odds, p.qin_odds].forEach((v) => tr.append(el('td', 'num', odds(v))));
      const pays = el('td', 'num');
      ['QPL', 'QIN'].forEach((pool) => {
        const v = p.line_returns[pool];
        if (v !== null && v !== undefined) {
          pays.append(el('span', p.covers_cost[pool] === false ? 'dk-under' : null, `${pool} ${money(v)} `));
        }
      });
      tr.append(pays);
      tbody.append(tr);
    });
    tab.append(tbody);
    box.append(tab);
  }
  t.flags.filter((f) => f.code !== 'no_own_evidence').forEach((f) => {
    const row = el('div', 'dk-flag', `${f.code.replace(/_/g, ' ').toUpperCase()} · ${f.detail}`);
    row.title = f.basis;
    box.append(row);
  });
  if (!h.gone) {
    const pick = el('div', 'dk-pick');
    pick.append(el('span', 'chip-label', 'PARTNERS'));
    const mine = new Set(t.partners.map((p) => p.horse_no));
    h.runners.filter((r) => r.horse_no !== t.banker.horse_no).forEach((r) => {
      const c = el('button', `chip${mine.has(r.horse_no) ? ' on' : ''}`, `#${r.horse_no}`);
      c.type = 'button';
      c.title = `${r.horse_name ?? ''} · ${r.win_odds ? odds(r.win_odds) : 'no price'}`;
      c.addEventListener('click', () => {
        const next = new Set(mine);
        if (next.has(r.horse_no)) next.delete(r.horse_no); else next.add(r.horse_no);
        if (next.size) h.onReprice([...next].sort((a, b) => a - b));
      });
      pick.append(c);
    });
    if (t.edited) {
      const back = el('button', 'act ghost', 'ENGINE’S PARTNERS');
      back.type = 'button';
      back.addEventListener('click', () => h.onReprice(null));
      pick.append(back);
    }
    box.append(pick);
  }
  return box;
}

function goneEl(t) {
  const box = el('div', 'dk-decide');
  box.append(sub('GONE OFF', t.banker.finish ? `FINISHED ${ordinal(t.banker.finish).toUpperCase()}` : 'NO RESULT YET'));
  const d = t.decision;
  box.append(el('div', 'dk-quiet', d
    ? `You ${d.decision === 'rejected' ? 'passed it' : 'took it'}${d.late ? ' after the off — kept, in no figure' : ''}`
      + `${d.reasons?.length ? ` · ${d.reasons.join(', ')}` : ''}${d.reason ? ` · “${d.reason}”` : ''}.`
    : 'No decision on the desk. Your bets reach the scorecard from the statement import.'));
  return box;
}

function decideEl(t, h) {
  const box = el('div', 'dk-decide');
  box.append(sub('TAKE OR PASS — AND WHY', 'TAPS ARE OPTIONAL · SCORED ON BANKERS AFTER THE RACE'));
  const chips = el('div', 'chips');
  h.reasons.forEach((r) => {
    const c = el('button', `chip${h.taps.has(r.key) ? ' on' : ''}`, r.label);
    c.type = 'button';
    c.addEventListener('click', () => {
      if (h.taps.has(r.key)) h.taps.delete(r.key); else h.taps.add(r.key);
      c.classList.toggle('on');
    });
    chips.append(c);
  });
  box.append(chips);
  const note = el('input', 'dk-note');
  note.placeholder = 'a line of your own (optional)';
  note.value = h.note;
  note.addEventListener('input', () => h.onNote(note.value));
  box.append(note);
  const acts = el('div', 'dk-acts');
  const take = el('button', 'act primary', t.edited ? 'TAKE WITH YOUR PARTNERS' : 'TAKE');
  const pass = el('button', 'act', 'PASS');
  [take, pass].forEach((b) => { b.type = 'button'; b.disabled = h.busy; });
  take.addEventListener('click', () => h.onDecide('accept'));
  pass.addEventListener('click', () => h.onDecide('reject'));
  acts.append(take, pass);
  const d = t.decision;
  if (d) {
    acts.append(el('span', `dk-state ${d.decision}`, `${DECIDED[d.decision]}`
      + (d.partners?.length ? ` · ${d.partners.map((x) => `#${x}`).join(' ')}` : '')));
  }
  box.append(acts);
  if (h.msg) box.append(el('div', 'no-match', h.msg));
  box.append(el('div', 'caveat', 'Nothing here places a bet. Strike it with HKJC; the statement '
    + 'import brings it into the ledger, and the desk matches it there.'));
  return box;
}

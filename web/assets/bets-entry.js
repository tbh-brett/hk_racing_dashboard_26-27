/* Bet entry — ported from web/design-source/Bets.dc.html.
 *
 * The decision side of the page. The ledger says what was staked; this is where
 * a ticket is built, and it is the one screen where the analysis in this whole
 * project actually lands on a decision.
 *
 * Four figures sit in front of the confirm button, each because a measurement
 * said the intuitive version is wrong:
 *
 *   banker place probability   Harville-Henery, shown beside the 3x rule of
 *                              thumb it corrects — which overstates a short
 *                              banker by ~34 points. Both are on screen: a
 *                              wrong number the user can SEE failing is worth
 *                              more than one quietly corrected, because the
 *                              rule of thumb is what they would otherwise reach
 *                              for.
 *   market concentration       from the LATEST snapshot. The morning price
 *                              misclassifies the band in 60% of races, always
 *                              downward.
 *   combination count          live, because betlines multiply faster than
 *                              intuition tracks and it is the number that turns
 *                              an intended small bet into a large one.
 *   pair ranking               ranking pairs beats boxing a set at every
 *                              ticket size.
 *
 * And one rule the interface must not break: a guardrail FLAGS, it never
 * blocks. Going past one is a checkbox that gets recorded, not a wall.
 */
import { api } from './api.js';
import { el, DASH, drawText } from './vocab.js';

const money = (v) => (v == null ? DASH : `$${Number(v).toLocaleString('en-HK',
  { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`);
const pct = (v) => (v == null ? DASH : `${Number(v).toFixed(1)}%`);

const STAKE_PRESETS = [50, 100, 200, 500];

export const entry = {
  account: 'brett', accounts: [], date: null, meeting: null,
  betType: 'WIN', raceNo: null, card: null,
  picks: new Set(), banker: null, unit: 100,
  allUpRaces: [], legs: new Map(), legsRequired: null,
  ticket: null, acknowledged: new Set(), bbLinks: [], bbLink: null,
  placed: null, busy: false,
};

let onChange = () => {};

export function initEntry(notify) { onChange = notify; }

/* ── the account band ─────────────────────────────────────────────────────
 * Brief 07 §3.1: the band and the rule stay this account's colour everywhere.
 * The dropdown is quiet, the indicator is not. */
function accountClass() {
  return entry.account === 'kelvin' ? 'acct-2' : 'acct-1';
}

function renderAccount(host) {
  const bar = el('div', `entry-acct ${accountClass()}`);
  bar.append(el('span', 'lab', 'LOGGING TO'));

  const sel = el('select', 'acct-pick');
  sel.setAttribute('aria-label', 'Account');
  entry.accounts.forEach((a) => {
    const o = el('option', null,
      `${a.name} · ${a.bets} bet${a.bets === 1 ? '' : 's'}`);
    o.value = a.key;
    if (a.key === entry.account) o.selected = true;
    sel.append(o);
  });
  sel.addEventListener('change', async () => {
    entry.account = sel.value;
    await refreshDay();
    onChange();
  });
  bar.append(sel);

  const me = entry.accounts.find((a) => a.key === entry.account);
  if (me) {
    // Never a bare number: the sign carries the direction, the unit carries
    // what it is, and `bets` says how much record is behind it.
    const pl = el('span', `acct-pl ${me.pnl >= 0 ? 'up' : 'down'}`,
      `${me.pnl >= 0 ? '+' : '−'}${money(Math.abs(me.pnl))} over ${me.bets} bet${
        me.bets === 1 ? '' : 's'}`);
    bar.append(pl);
  }

  const day = entry.day;
  if (day) {
    const box = el('div', `entry-day${day.over ? ' over' : ''}`);
    box.append(el('span', 'lab', 'RACEDAY'));
    box.append(el('span', 'v', money(day.staked)));
    box.append(el('span', 'of', `/ ${money(day.ceiling)}`));
    // A ceiling warns. It is never a limit, so nothing here disables anything.
    box.append(el('span', 'note',
      day.over ? 'past the ceiling — flagged, not blocked'
        : `${money(day.remaining)} to the ceiling`));
    bar.append(box);
  }
  host.append(bar);
}

/* ── bet type and race ────────────────────────────────────────────────────── */

function chip(label, on, click, cls) {
  const b = el('button', `entry-chip${on ? ' on' : ''}${cls ? ` ${cls}` : ''}`, label);
  b.type = 'button';
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  b.addEventListener('click', click);
  return b;
}

function renderTypes(host) {
  const row = el('div', 'entry-row');
  row.append(el('div', 'entry-lab', 'BET TYPE'));
  const chips = el('div', 'entry-chips');
  ['WIN', 'PLACE', 'QIN', 'QPL', 'ALLUP'].forEach((t) => {
    chips.append(chip(t === 'ALLUP' ? 'ALL-UP' : t, entry.betType === t, () => {
      entry.betType = t;
      entry.picks.clear();
      entry.banker = null;
      entry.acknowledged.clear();
      entry.placed = null;
      priceTicket();
    }));
  });
  row.append(chips);
  host.append(row);
}

function renderRaces(host) {
  if (entry.betType === 'ALLUP') return;
  const row = el('div', 'entry-row');
  row.append(el('div', 'entry-lab', 'RACE'));
  const chips = el('div', 'entry-chips');
  (entry.meeting?.races ?? []).forEach((r) => {
    chips.append(chip(String(r.race_no), entry.raceNo === r.race_no, async () => {
      entry.raceNo = r.race_no;
      entry.picks.clear();
      entry.banker = null;
      entry.placed = null;
      await loadCard();
      priceTicket();
    }));
  });
  row.append(chips);
  host.append(row);
}

/* ── the selection table ──────────────────────────────────────────────────── */

const CARD_COLS = [
  ['no', 'NO'], ['bnk', 'BNK'], ['sel', 'SEL'], ['horse', 'HORSE'],
  ['style', 'STYLE'], ['dr', 'DR'], ['jockey', 'JOCKEY'],
  ['win', 'WIN'], ['place', 'PLACE'],
  ['winp', 'WIN %'], ['placep', 'PLACE % · HARVILLE-HENERY'],
  ['linear', 'v OLD 3× RULE'],
];

function styleCell(r) {
  const s = r.pace_style;
  if (!s) return el('span', 'dim', DASH);
  const key = s.toLowerCase().replace(/[^a-z]/g, '');
  return el('span', `style-badge s-${key}`, s.toUpperCase());
}

function cardRow(r) {
  const row = el('div', `entry-crow${r.scratched ? ' scr' : ''}`);
  row.append(el('span', 'no', String(r.horse_no)));

  // Banker sits LEFT of selection (brief 08 §2): it is the structural
  // decision, and reading left to right should follow the logic of the bet.
  const bnk = el('span', 'bnk');
  if (entry.betType === 'QIN' || entry.betType === 'QPL') {
    const b = el('button', `pick banker${entry.banker === r.horse_no ? ' on' : ''}`,
      entry.banker === r.horse_no ? '◆' : '');
    b.type = 'button';
    b.setAttribute('aria-label', `Banker ${r.horse_name}`);
    b.addEventListener('click', () => {
      entry.banker = entry.banker === r.horse_no ? null : r.horse_no;
      entry.picks.delete(r.horse_no);
      priceTicket();
    });
    bnk.append(b);
  }
  row.append(bnk);

  const sel = el('span', 'sel');
  const s = el('button', `pick${entry.picks.has(r.horse_no) ? ' on' : ''}`,
    entry.picks.has(r.horse_no) ? '✓' : '');
  s.type = 'button';
  s.setAttribute('aria-label', `Select ${r.horse_name}`);
  s.addEventListener('click', () => {
    if (entry.picks.has(r.horse_no)) entry.picks.delete(r.horse_no);
    else {
      entry.picks.add(r.horse_no);
      if (entry.banker === r.horse_no) entry.banker = null;
    }
    priceTicket();
  });
  sel.append(s);
  row.append(sel);

  const name = el('span', 'horse');
  name.append(el('span', 'n', r.horse_name));
  if (r.blackbook) name.append(el('span', 'bb', 'BB'));
  if (r.market_rank === 1) name.append(el('span', 'fav', 'FAV'));
  if (r.scratched) name.append(el('span', 'scr-tag', 'SCR'));
  row.append(name);

  row.append(styleCell(r));
  row.append(el('span', 'dr', drawText(r.draw)));
  row.append(el('span', 'jockey', r.jockey ?? DASH));
  row.append(el('span', 'win', r.win_odds == null ? DASH : r.win_odds.toFixed(1)));
  row.append(el('span', 'place', r.place_odds == null ? DASH : r.place_odds.toFixed(1)));
  row.append(el('span', 'winp', pct(r.win_pct)));
  row.append(el('span', 'placep', pct(r.place_pct)));

  // The rule of thumb, kept on screen next to the honest figure.
  const lin = el('span', 'linear');
  lin.append(el('span', 'v', pct(r.linear_pct)));
  if (r.gap_points != null && Math.abs(r.gap_points) >= 1) {
    lin.append(el('span', r.gap_points > 0 ? 'over' : 'under',
      `${r.gap_points > 0 ? '+' : '−'}${Math.abs(r.gap_points).toFixed(1)}`));
  }
  row.append(lin);
  return row;
}

function renderCard(host) {
  if (entry.betType === 'ALLUP') return;
  if (!entry.card) {
    host.append(el('div', 'entry-empty', 'Pick a race to see the card.'));
    return;
  }
  const isPair = entry.betType === 'QIN' || entry.betType === 'QPL';
  if (isPair) {
    const b = el('div', 'entry-row');
    b.append(el('div', 'entry-lab', 'BANKER'));
    const zone = el('div', 'entry-chips');
    zone.append(el('span', 'banker-note', entry.banker == null
      ? 'No banker — all selections combine with each other.'
      : `Banker: ${entry.card.runners.find((r) => r.horse_no === entry.banker)
        ?.horse_name ?? entry.banker} — appears in every combination.`));
    if (entry.banker != null) {
      zone.append(chip('CLEAR BANKER', false, () => {
        entry.banker = null;
        priceTicket();
      }, 'ghost'));
    }
    b.append(zone);
    host.append(b);
  }

  const table = el('div', 'entry-card');
  const head = el('div', 'entry-chead');
  CARD_COLS.forEach(([k, label]) => head.append(el('span', k, label)));
  table.append(head);
  entry.card.runners.forEach((r) => table.append(cardRow(r)));
  host.append(table);

  if (entry.card.place_ratio_range) {
    host.append(el('div', 'entry-foot',
      'Win and place are both scraped prices at equal weight — place is never '
      + `derived from win. The ratio on this card ranges ${entry.card.place_ratio_range}, `
      + 'which is why the 3× rule of thumb cannot work.'));
  }
}

/* ── all-up ───────────────────────────────────────────────────────────────── */

function renderAllUp(host) {
  if (entry.betType !== 'ALLUP') return;

  const step1 = el('div', 'entry-step');
  step1.append(el('span', 'n', 'STEP 1'));
  step1.append(el('span', 't', 'PICK RACES'));
  host.append(step1);

  const chips = el('div', 'entry-chips');
  (entry.meeting?.races ?? []).forEach((r) => {
    const on = entry.allUpRaces.includes(r.race_no);
    chips.append(chip(String(r.race_no), on, async () => {
      if (on) {
        entry.allUpRaces = entry.allUpRaces.filter((n) => n !== r.race_no);
        entry.legs.delete(r.race_no);
      } else {
        entry.allUpRaces = [...entry.allUpRaces, r.race_no].sort((a, b) => a - b);
        await loadLeg(r.race_no);
      }
      entry.legsRequired = null;
      priceTicket();
    }));
  });
  host.append(chips);
  host.append(el('div', 'entry-count',
    `${entry.allUpRaces.length} selected · only selected races appear below`));

  if (entry.allUpRaces.length < 2) {
    host.append(el('div', 'entry-empty',
      'Pick at least two races to build a chain. The workspace stays empty '
      + 'until then — no greyed-out placeholders.'));
    return;
  }

  // Side-by-side, not stacked: comparing selections across legs is the whole
  // task of building an all-up (brief 08 §3).
  const rail = el('div', 'entry-rail');
  entry.allUpRaces.forEach((no) => rail.append(legPanel(no)));
  host.append(rail);
  host.append(el('div', 'entry-foot',
    'Panels scroll sideways. Comparing selections across legs is the whole '
    + 'task, so the legs sit beside each other rather than stacked.'));

  renderFormulas(host);
}

function legPanel(no) {
  const leg = entry.legs.get(no);
  const panel = el('div', 'leg');
  const head = el('div', 'leg-head');
  head.append(el('span', 'r', `Race ${no}`));
  if (leg?.meta) head.append(el('span', 'meta', leg.meta));
  const rm = el('button', 'leg-rm', 'REMOVE');
  rm.type = 'button';
  rm.addEventListener('click', () => {
    entry.allUpRaces = entry.allUpRaces.filter((n) => n !== no);
    entry.legs.delete(no);
    entry.legsRequired = null;
    priceTicket();
  });
  head.append(rm);
  panel.append(head);

  const types = el('div', 'leg-types');
  ['WIN', 'PLACE', 'QIN', 'QPL'].forEach((t) => {
    types.append(chip(t, (leg?.betType ?? 'WIN') === t, () => {
      if (leg) leg.betType = t;
      priceTicket();
    }, 'sm'));
  });
  panel.append(types);

  const head2 = el('div', 'leg-chead');
  [['no', 'NO'], ['bnk', 'BNK'], ['sel', 'SEL'], ['horse', 'HORSE NAME'],
    ['win', 'WIN'], ['place', 'PLACE']].forEach(([k, l]) =>
    head2.append(el('span', k, l)));
  panel.append(head2);

  (leg?.runners ?? []).forEach((r) => {
    const row = el('div', `leg-row${r.scratched ? ' scr' : ''}`);
    row.append(el('span', 'no', String(r.horse_no)));

    const bnk = el('span', 'bnk');
    const bb = el('button', `pick banker${leg.banker === r.horse_no ? ' on' : ''}`,
      leg.banker === r.horse_no ? '◆' : '');
    bb.type = 'button';
    bb.setAttribute('aria-label', `Banker ${r.horse_name}`);
    bb.addEventListener('click', () => {
      leg.banker = leg.banker === r.horse_no ? null : r.horse_no;
      leg.picks.delete(r.horse_no);
      priceTicket();
    });
    bnk.append(bb);
    row.append(bnk);

    const sel = el('span', 'sel');
    const s = el('button', `pick${leg.picks.has(r.horse_no) ? ' on' : ''}`,
      leg.picks.has(r.horse_no) ? '✓' : '');
    s.type = 'button';
    s.setAttribute('aria-label', `Select ${r.horse_name}`);
    s.addEventListener('click', () => {
      if (leg.picks.has(r.horse_no)) leg.picks.delete(r.horse_no);
      else {
        leg.picks.add(r.horse_no);
        if (leg.banker === r.horse_no) leg.banker = null;
      }
      priceTicket();
    });
    sel.append(s);
    row.append(sel);

    const name = el('span', 'horse');
    name.append(el('span', 'n', r.horse_name));
    // Scratched runners stay visible and greyed. Their absence is information.
    if (r.scratched) name.append(el('span', 'scr-tag', 'SCR'));
    if (r.market_rank === 1) name.append(el('span', 'fav', 'FAV'));
    row.append(name);
    row.append(el('span', 'win', r.win_odds == null ? DASH : r.win_odds.toFixed(1)));
    row.append(el('span', 'place', r.place_odds == null ? DASH : r.place_odds.toFixed(1)));
    panel.append(row);
  });
  return panel;
}

function renderFormulas(host) {
  const options = entry.ticket?.formulas ?? [];
  if (!options.length) return;
  const step = el('div', 'entry-step');
  step.append(el('span', 'n', 'STEP 3'));
  step.append(el('span', 't',
    `HOW MANY OF MY ${entry.allUpRaces.length} LEGS MUST WIN`));
  host.append(step);
  host.append(el('div', 'entry-count',
    'Generated from the race count — an invalid formula cannot be picked'));

  const chips = el('div', 'entry-chips wrap');
  options.forEach((f) => {
    const on = (entry.ticket?.legs_required ?? null) === f.legs;
    const b = el('button', `entry-formula${on ? ' on' : ''}`);
    b.type = 'button';
    b.append(el('span', 'lab', f.label));
    b.append(el('span', 'legs', `${f.legs} of ${entry.allUpRaces.length} must win`));
    b.append(el('span', 'combos',
      `${f.combinations} combination${f.combinations === 1 ? '' : 's'}`));
    b.addEventListener('click', () => {
      entry.legsRequired = f.legs;
      priceTicket();
    });
    chips.append(b);
  });
  host.append(chips);
}

/* ── stake, and the HKJC-shaped total ─────────────────────────────────────── */

function renderStake(host) {
  const row = el('div', 'entry-row');
  row.append(el('div', 'entry-lab', 'STAKE PER COMBINATION'));
  const zone = el('div', 'entry-chips');

  const wrap = el('span', 'stake-field');
  wrap.append(el('span', 'cur', '$'));
  const input = el('input', 'stake-input');
  input.type = 'number';
  input.min = '0';
  input.step = '10';
  input.value = String(entry.unit);
  input.setAttribute('aria-label', 'Stake per combination');
  // Typing an arbitrary amount is the primary path; the presets are shortcuts.
  input.addEventListener('input', () => {
    entry.unit = Number(input.value) || 0;
    priceTicket();
  });
  wrap.append(input);
  zone.append(wrap);

  STAKE_PRESETS.forEach((v) => zone.append(
    chip(String(v), entry.unit === v, () => {
      entry.unit = v;
      priceTicket();
    }, 'sm')));
  row.append(zone);
  host.append(row);

  const t = entry.ticket;
  if (!t) return;
  // HKJC's own three-figure line, in HKJC's order.
  const calc = el('div', 'entry-calc');
  calc.append(el('span', 'k', 'No. of Bets:'));
  calc.append(el('span', 'v', String(t.combinations ?? 0)));
  calc.append(el('span', 'k', 'Unit Bet:'));
  calc.append(el('span', 'v', money(t.unit_stake)));
  calc.append(el('span', 'k', 'Bet Total:'));
  calc.append(el('span', 'v big', money(t.total_outlay)));
  if (t.combination_formula) calc.append(el('span', 'f', t.combination_formula));
  host.append(calc);
}

/* ── the pre-bet panel ────────────────────────────────────────────────────── */

function panel(title, note) {
  const p = el('div', 'pre-panel');
  p.append(el('div', 'pre-title', title));
  if (note) p.append(el('div', 'pre-note', note));
  return p;
}

function bankerPanel(t) {
  const b = t.banker_panel;
  if (!b) {
    return panel('NO BANKER',
      'All selections combine with each other. A banker is optional — this is '
      + 'a complete ticket, not an unfinished one.');
  }
  const p = panel('BANKER PLACE PROBABILITY · HARVILLE-HENERY');
  if (b.place_pct == null) {
    p.append(el('div', 'pre-thin', b.note ?? 'no priced snapshot'));
    return p;
  }
  const g = el('div', 'pre-grid');
  g.append(el('span', 'k', b.horse_name));
  g.append(el('span', 'v', pct(b.place_pct)));
  g.append(el('span', 'k', 'win'));
  g.append(el('span', 'v sm', pct(b.win_pct)));
  g.append(el('span', 'k', 'place odd'));
  g.append(el('span', 'v sm', b.place_odds == null ? DASH : b.place_odds.toFixed(1)));
  p.append(g);
  const cmp = el('div', 'pre-compare');
  cmp.append(el('span', 'old', `3× rule ${pct(b.linear_pct)}`));
  cmp.append(el('span', b.overstated ? 'over' : 'under',
    `${b.overstated ? 'overstates' : 'understates'} by ${Math.abs(b.gap_points).toFixed(1)} points`));
  p.append(cmp);
  p.append(el('div', 'pre-note', '3 × win% is not a place probability.'));
  return p;
}

function concentrationPanel(t) {
  const c = t.concentration;
  if (!c || c.value == null) {
    return panel('MARKET CONCENTRATION', c?.note ?? 'no priced snapshot');
  }
  const p = panel(`MARKET CONCENTRATION · LATEST SNAPSHOT${
    c.captured_at ? ` ${c.captured_at.slice(11, 16)}` : ''}`);
  const g = el('div', 'pre-grid');
  g.append(el('span', 'k', 'band'));
  g.append(el('span', `v band-${c.band}`, (c.band ?? DASH).toUpperCase()));
  g.append(el('span', 'k', 'top 3'));
  g.append(el('span', 'v', pct(100 * c.value)));
  g.append(el('span', 'k', 'priced'));
  g.append(el('span', 'v sm', `${c.runners} runners`));
  p.append(g);
  if (c.note) p.append(el('div', 'pre-thin', c.note));
  return p;
}

function pairsPanel(t) {
  if (!t.pairs?.length) return null;
  const p = panel('PAIR RANKING',
    'Ranking pairs beats boxing a set at every ticket size.');
  t.pairs.forEach((pr) => {
    const row = el('div', `pre-pair${pr.in_ticket ? ' in' : ''}`);
    row.append(el('span', 'r', String(pr.rank)));
    row.append(el('span', 'l', pr.horse_nos.join(' · ')));
    row.append(el('span', 'p', pct(pr.prob)));
    row.append(el('span', 'in', pr.in_ticket ? 'in ticket' : ''));
    p.append(row);
  });
  return p;
}

/* Guardrails. Each one is a warning with a checkbox, never a block — and the
 * checkbox is what gets written, because reviewing which flags were overridden
 * and how those bets performed is only possible if the override is logged. */
function flagsPanel(t) {
  if (!t.flags?.length) return null;
  const p = panel('FLAGS', 'A flag warns. It never blocks a bet.');
  t.flags.forEach((f) => {
    const row = el('div', 'pre-flag');
    row.append(el('span', 'ft', f.title));
    row.append(el('span', 'fd', f.detail));
    const lab = el('label', 'ov');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = entry.acknowledged.has(f.flag);
    cb.addEventListener('change', () => {
      if (cb.checked) entry.acknowledged.add(f.flag);
      else entry.acknowledged.delete(f.flag);
      onChange();
    });
    lab.append(cb);
    lab.append(el('span', null, entry.acknowledged.has(f.flag)
      ? 'override logged' : 'log an override'));
    row.append(lab);
    p.append(row);
  });
  return p;
}

function renderPreBet(host) {
  const t = entry.ticket;
  const wrap = el('div', 'pre-wrap');
  wrap.append(el('div', 'pre-head', 'PRE-BET'));
  if (!t) {
    wrap.append(el('div', 'entry-empty', 'Build a ticket to price it.'));
    host.append(wrap);
    return;
  }

  const outlay = panel('COMBINATIONS AND OUTLAY', t.combination_formula ?? '');
  const g = el('div', 'pre-grid');
  g.append(el('span', 'k', 'combinations'));
  g.append(el('span', 'v', String(t.combinations)));
  g.append(el('span', 'k', 'per combination'));
  g.append(el('span', 'v', money(t.unit_stake)));
  g.append(el('span', 'k', 'total outlay'));
  g.append(el('span', 'v big', money(t.total_outlay)));
  outlay.append(g);
  wrap.append(outlay);

  if (t.bet_type !== 'ALLUP') wrap.append(bankerPanel(t));
  if (t.concentration !== undefined) wrap.append(concentrationPanel(t));
  const pairs = pairsPanel(t);
  if (pairs) wrap.append(pairs);
  const flags = flagsPanel(t);
  if (flags) wrap.append(flags);

  const act = el('div', 'pre-act');
  const btn = el('button', 'pre-place',
    t.placeable ? `PLACE — ${money(t.total_outlay)}` : (t.reason ?? 'not placeable'));
  btn.type = 'button';
  btn.disabled = !t.placeable || entry.busy;
  btn.addEventListener('click', placeTicket);
  act.append(btn);

  const clear = el('button', 'pre-clear', 'CLEAR');
  clear.type = 'button';
  clear.addEventListener('click', () => {
    entry.picks.clear();
    entry.banker = null;
    entry.acknowledged.clear();
    entry.legs.forEach((l) => { l.picks.clear(); l.banker = null; });
    entry.placed = null;
    priceTicket();
  });
  act.append(clear);
  wrap.append(act);

  if (entry.placed) {
    const done = el('div', 'pre-placed');
    done.append(el('span', 'ok', `Logged ${entry.placed.bet_id}`));
    done.append(el('span', null,
      `${entry.placed.combinations} combinations · ${money(entry.placed.stake)}`));
    if (entry.placed.overrides_logged?.length) {
      done.append(el('span', 'ov',
        `override logged: ${entry.placed.overrides_logged.join(', ')}`));
    }
    if (entry.placed.flags_unacknowledged?.length) {
      done.append(el('span', 'ov',
        `flagged, not overridden: ${entry.placed.flags_unacknowledged.join(', ')}`));
    }
    wrap.append(done);
  }
  if (entry.error) wrap.append(el('div', 'pre-error', entry.error));
  host.append(wrap);
}

/* ── data ─────────────────────────────────────────────────────────────────── */

async function loadCard() {
  if (entry.raceNo == null) { entry.card = null; return; }
  entry.card = await api.betCard(entry.date, entry.raceNo);
}

async function loadLeg(no) {
  const card = await api.betCard(entry.date, no);
  entry.legs.set(no, {
    betType: 'WIN', picks: new Set(), banker: null,
    runners: card.runners,
    meta: [card.course, card.distance ? `${card.distance}m` : null, card.going]
      .filter(Boolean).join(' · '),
  });
}

async function refreshDay() {
  entry.day = await api.betRaceday(entry.date, entry.account);
}

function ticketBody() {
  if (entry.betType === 'ALLUP') {
    return {
      race_date: entry.date, bet_type: 'ALLUP', account: entry.account,
      unit_stake: entry.unit, legs_required: entry.legsRequired,
      legs: entry.allUpRaces.map((no) => {
        const l = entry.legs.get(no);
        return {
          race_no: no, bet_type: l?.betType ?? 'WIN',
          selections: [...(l?.picks ?? [])], banker: l?.banker ?? null,
        };
      }),
    };
  }
  return {
    race_date: entry.date, bet_type: entry.betType, race_no: entry.raceNo,
    account: entry.account, unit_stake: entry.unit,
    selections: [...entry.picks], banker: entry.banker,
  };
}

export async function priceTicket() {
  entry.error = null;
  if (entry.betType !== 'ALLUP' && entry.raceNo == null) {
    entry.ticket = null;
    onChange();
    return;
  }
  try {
    entry.ticket = await api.prebet(ticketBody());
  } catch (err) {
    // A failed price is shown, never swallowed into an empty panel.
    entry.ticket = null;
    entry.error = err.message;
  }
  onChange();
}

async function placeTicket() {
  entry.busy = true;
  entry.error = null;
  onChange();
  try {
    entry.placed = await api.placeBet({
      ...ticketBody(), acknowledged: [...entry.acknowledged],
      blackbook_entry_id: entry.bbLink,
    });
    entry.picks.clear();
    entry.banker = null;
    entry.acknowledged.clear();
    entry.accounts = await api.betAccounts().then((r) => r.accounts);
    await refreshDay();
    await priceTicket();
  } catch (err) {
    entry.error = err.message;
  } finally {
    entry.busy = false;
    onChange();
  }
}

export async function loadEntry(date, meeting) {
  entry.date = date;
  entry.meeting = meeting;
  entry.accounts = (await api.betAccounts()).accounts;
  if (!entry.accounts.some((a) => a.key === entry.account)) {
    entry.account = entry.accounts[0]?.key ?? 'brett';
  }
  await refreshDay();
  if (entry.raceNo == null && meeting?.races?.length) {
    entry.raceNo = meeting.races[0].race_no;
    await loadCard();
  }
  await priceTicket();
}

export function renderEntry(host) {
  host.replaceChildren();
  const left = el('div', 'entry-left');
  renderAccount(left);
  renderTypes(left);
  renderRaces(left);
  renderCard(left);
  renderAllUp(left);
  renderStake(left);
  host.append(left);

  const right = el('div', 'entry-right');
  renderPreBet(right);
  host.append(right);
}

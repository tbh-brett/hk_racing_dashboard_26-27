/* banker-card.js — the Bets page's BANKERS view: what you lean towards when
 * you pick a banker, and which of those leanings have paid.
 *
 * docs/banker-card.md. Every banker you have taken since April -- the ledger's
 * and the desk's -- read with the facts knowable before its race, beside the
 * rest of the same fields read the same way.
 *
 * It OPENS on the answer (rebuilt 19 Nov 2026, when the owner found the first
 * version's fifteen panels a scroll without a point): what has paid for you,
 * what has not, and your reasons' record. Every fact in full is folded below.
 * For each fact there:
 *
 *   LEAN       how much more often your banker had it than the field did
 *   YOURS      how your bankers with it placed, against the price's own chance
 *   SPLIT      the same, with and without your own evidence on the horse
 *   FIELD      the runners with it you did NOT bank, in the same races
 *
 * Coloured only past a line: at the break-even line (the takeout's) in the
 * win colour, below the market in the loss colour, under the thin count dim.
 * Fourteen facts are read, so a few lines will clear by luck; the point is
 * which ones keep clearing as the record grows.
 */
import { el, DASH } from './vocab.js';
import { api } from './api.js';

const S = { host: null, data: null, error: null, loading: false, loaded: false };

export function renderBankers(host) {
  S.host = host;
  if (!S.loaded && !S.loading) load();
  draw();
}

/** Forget the scorecard, so the next visit reads it again (after a decision). */
export function staleBankers() {
  S.loaded = false;
  S.data = null;
}

async function load() {
  S.loading = true;
  try {
    S.data = await api.bankerScorecard();
    S.error = null;
  } catch (err) {
    S.error = err.message;
  }
  S.loading = false;
  S.loaded = true;
  draw();
}

const ae = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(2));

function tone(ln, breakeven) {
  if (!ln || ln.ae === null || ln.ae === undefined) return 'nil';
  if (ln.thin) return 'thin';
  return ln.ae >= breakeven ? 'for' : ln.ae < 1 ? 'against' : 'level';
}

function cell(ln, breakeven, { detail = true } = {}) {
  const td = el('td', `num bc-${tone(ln, breakeven)}`);
  if (!ln || !ln.bankers) { td.textContent = DASH; return td; }
  td.append(el('b', null, ae(ln.ae)));
  // A/E and the count in the cell; placed against expected and the interval
  // in the tooltip, so seven columns fit two panels to a row.
  if (detail) td.append(el('span', 'n', ` · ${ln.bankers}`));
  td.title = `${ln.bankers} bankers placed ${ln.placed} where the price expected `
    + `${Number(ln.expected ?? 0).toFixed(1)} · 95% interval ${ae(ln.ae_lo)} to ${ae(ln.ae_hi)}`
    + (ln.thin ? ' · too few to say' : '');
  return td;
}

function draw() {
  const host = S.host;
  if (!host) return;
  host.replaceChildren();
  const d = S.data;
  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'YOUR BANKERS'));
  head.append(el('span', 'sub', d
    ? `PLACED AGAINST THE PRICE · BREAK-EVEN ${d.breakeven.toFixed(2)} · UNDER ${d.thin} BANKERS IS DIMMED`
    : 'PLACED AGAINST THE PRICE'));
  host.append(head);
  if (S.error) { host.append(el('div', 'no-match', `SCORECARD FAILED TO LOAD — ${S.error}`)); return; }
  if (!d) { host.append(el('div', 'empty-line', 'LOADING')); return; }

  const o = d.overall;
  const stats = el('div', 'stat-row');
  const stat = (v, k) => {
    const s = el('div', 'stat');
    s.append(el('span', 'v', v), el('span', 'k', k));
    return s;
  };
  stats.append(stat(String(o.bankers), `bankers · ${d.races} races`));
  stats.append(stat(`${o.placed} / ${Number(o.expected ?? 0).toFixed(1)}`, 'placed / the price expected'));
  stats.append(stat(`${ae(o.ae)} [${ae(o.ae_lo)}, ${ae(o.ae_hi)}]`, 'A/E, 95% interval'));
  stats.append(stat(ae(d.field.ae), 'the runners you did not bank'));
  stats.append(stat(`${d.since} → ${d.through ?? DASH}`, `${d.from_pool} read off the place pool`));
  host.append(stats);
  host.append(el('div', 'caveat',
    'Your books only (Kelvin’s sheet is his). The price’s chance is the place pool where it was '
    + 'captured, else the starting prices. A line needs ' + d.thin + ' bankers to be listed; '
    + 'fourteen facts are read, so a few will clear by luck — what matters is which keep clearing.'));

  const lists = el('div', 'bc-lists');
  const { pays, costs } = verdicts(d);
  lists.append(listEl('WHAT HAS PAID FOR YOU', `AT ${d.breakeven.toFixed(2)} OR BETTER`, 'pays', pays,
    'Nothing yet has placed often enough to cover the takeout.'));
  lists.append(listEl('WHAT HAS NOT', 'BELOW THE PRICE', 'costs', costs,
    'Nothing with enough bankers has placed less often than the price said.'));
  host.append(lists);
  const panels = el('div', 'panels');
  panels.append(reasonsPanel(d));
  host.append(panels);

  const fold = el('details', 'bc-fold');
  fold.append(el('summary', null, `EVERY FACT IN FULL (${d.facts.length}) ▸`));
  const all = el('div', 'panels');
  d.facts.forEach((f) => all.append(factPanel(f, d.breakeven)));
  fold.append(all);
  host.append(fold);
}

/* Every line with enough bankers behind it that has paid, or has placed less
 * often than the price -- the fact itself, and a side of your own evidence
 * only where it says something the fact alone does not (the fact is thin, or
 * lands on the other side of a line). Best and worst first. */
function verdicts(d) {
  const pays = [];
  const costs = [];
  const side = { with_own: 'with your evidence on it', without_own: 'without your evidence on it' };
  const verdict = (ln) => (!ln || ln.thin || ln.ae === null || ln.ae === undefined ? null
    : ln.ae >= d.breakeven ? 'pays' : ln.ae < 1 ? 'costs' : null);
  d.facts.forEach((f) => f.values.forEach((v) => {
    const whole = verdict(v);
    const lines = [[v, null]];
    if (f.key !== 'own') lines.push([v.with_own, 'with_own'], [v.without_own, 'without_own']);
    lines.forEach(([ln, s]) => {
      const got = verdict(ln);
      if (!got || (s && got === whole)) return;
      const item = { what: `${f.label}: ${v.value}${s ? ` — ${side[s]}` : ''}`, ln,
                     lean: s ? null : v.lean, field: s ? null : v.field?.ae };
      (got === 'pays' ? pays : costs).push(item);
    });
  }));
  pays.sort((a, b) => b.ln.ae - a.ln.ae);
  costs.sort((a, b) => a.ln.ae - b.ln.ae);
  return { pays: pays.slice(0, 8), costs: costs.slice(0, 8) };
}

function listEl(title, note, cls, items, empty) {
  const box = el('section', `bc-list ${cls}`);
  const hd = el('div', 'bc-hd', title);
  hd.append(el('span', null, note));
  box.append(hd);
  if (!items.length) box.append(el('div', 'empty-line', empty));
  items.forEach((it) => {
    const row = el('div', 'bc-item');
    row.append(el('span', 'what', it.what));
    const num = el('span', 'num');
    num.append(el('b', null, ae(it.ln.ae)));
    num.append(el('span', 'n', ` · ${it.ln.placed} of ${Number(it.ln.expected ?? 0).toFixed(1)} · ${it.ln.bankers} bankers`));
    row.append(num);
    const why = [];
    if (it.lean !== null && it.lean !== undefined) why.push(`you pick it ${Number(it.lean).toFixed(1)}× as often as it turns up`);
    if (it.field !== null && it.field !== undefined) why.push(`the runners like it you did not bank: ${ae(it.field)}`);
    why.push(`95% interval ${ae(it.ln.ae_lo)} to ${ae(it.ln.ae_hi)}`);
    row.append(el('span', 'why', why.join(' · ')));
    box.append(row);
  });
  return box;
}

function factPanel(f, breakeven) {
  const box = el('section', 'panel-box wide');
  const hd = el('div', 'panel-hd');
  hd.append(el('span', 't', f.label.toUpperCase()));
  if (f.unknown) hd.append(el('span', 'n', `NOT KEPT FOR ${f.unknown} OF YOUR BANKERS`));
  box.append(hd);
  const body = el('div', 'panel-body');
  const wrap = el('div', 'table-box');
  const tab = el('table', 'grid bc-tab');
  const tr0 = el('tr');
  ['', 'LEAN', 'YOURS', 'WITH YOUR EVIDENCE', 'WITHOUT', 'FIELD'].forEach((h, i) =>
    tr0.append(el('th', i ? 'num' : null, h)));
  const thead = el('thead');
  thead.append(tr0);
  tab.append(thead);
  const tbody = el('tbody');
  f.values.forEach((v) => {
    const tr = el('tr', v.thin ? 'thin' : null);
    tr.append(el('td', 'name', v.value));
    tr.append(el('td', 'num', v.lean === null ? DASH : `${Number(v.lean).toFixed(1)}×`));
    tr.append(cell(v, breakeven));
    tr.append(cell(v.with_own, breakeven));
    tr.append(cell(v.without_own, breakeven));
    tr.append(cell(v.field, breakeven, { detail: false }));
    tbody.append(tr);
  });
  tab.append(tbody);
  wrap.append(tab);
  body.append(wrap);
  box.append(body);
  return box;
}

function reasonsPanel(d) {
  const box = el('section', 'panel-box wide');
  const hd = el('div', 'panel-hd');
  hd.append(el('span', 't', 'YOUR REASONS'));
  hd.append(el('span', 'n', d.desk.with_reasons
    ? `${d.desk.with_reasons} DECISIONS CARRY A TAP` : 'NONE TAPPED YET — USE THE DESK'));
  box.append(hd);
  const body = el('div', 'panel-body');
  const wrap = el('div', 'table-box');
  const tab = el('table', 'grid bc-tab');
  const tr0 = el('tr');
  ['', 'TAKEN', 'PASSED'].forEach((h, i) => tr0.append(el('th', i ? 'num' : null, h)));
  const thead = el('thead');
  thead.append(tr0);
  tab.append(thead);
  const tbody = el('tbody');
  const row = (label, a, b) => {
    const tr = el('tr');
    tr.append(el('td', 'name', label), cell(a, d.breakeven), cell(b, d.breakeven));
    tbody.append(tr);
  };
  row('Every decision on the desk', d.desk.taken, d.desk.passed);
  d.reasons.forEach((r) => row(r.label, r.taken, r.passed));
  tab.append(tbody);
  wrap.append(tab);
  body.append(wrap);
  body.append(el('div', 'caveat', 'A passed banker that placed less often than the price '
    + 'expected was a good pass. Only decisions made before the off are counted.'));
  box.append(body);
  return box;
}

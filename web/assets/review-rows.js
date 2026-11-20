/* review-rows.js — a list of bankers to review, shared by the desk page's race
 * view (review-race.js) and its backlog (banker-review.js).
 *
 * One row per horse: the race, the horse, the tickets that carried it, where it
 * finished, what the stewards name, and the verdict. The next one still to
 * judge is open; a tap saves (banker-review-form.js) and, once a verdict is
 * given, the one after it opens, so a night is a run of single taps.
 */
import { el, DASH } from './vocab.js';
import { api } from './api.js';
import { draftOf, formEl, ordinal } from './banker-review-form.js';

export const keyOf = (h) => `${h.race_date}-${h.race_no}-${h.horse_no}`;
export const toJudge = (h) => h.race.result && !h.run.withdrawn && !h.review.verdict;

const TYPE = { QIN_BANKER: 'QIN', QPL_BANKER: 'QPL', QQP_BANKER: 'QQP', WIN: 'WIN', PLACE: 'PLACE',
               WP: 'W+P', SIXUP: 'SIX UP' };

export function ticketsText(h) {
  const by = new Map();
  h.tickets.forEach((t) => {
    const who = t.account === 'personal' ? 'brett' : t.account;
    const kind = t.bet_type.startsWith('ALLUP') ? `${(t.formula ?? t.bet_type.slice(6)).toLowerCase()} leg`
      : t.bet_type === 'SIXUP' ? 'six up leg' : (TYPE[t.bet_type] ?? t.bet_type);
    if (!by.has(who)) by.set(who, new Set());
    by.get(who).add(kind);
  });
  if (h.desk && !h.tickets.length) by.set(h.desk.account, new Set(['taken on the desk']));
  return [...by].map(([who, kinds]) => `${who.toUpperCase()} ${[...kinds].join(' + ')}`);
}

export class RowSet {
  /** horses: the rows; redraw(): draw the page again; changed(): a verdict moved. */
  constructor(horses, { redraw, changed, label = (h) => `R${h.race_no} #${h.horse_no} ${h.horse_name}` }) {
    this.horses = horses;
    this.redraw = redraw;
    this.changed = changed ?? (() => {});
    this.label = label;
    const first = horses.find(toJudge);
    this.open = first ? keyOf(first) : null;
    this.drafts = new Map();
    this.busy = new Set();
    this.msg = new Map();
  }

  els(vocab) {
    return this.horses.flatMap((h) => this.rowEls(h, vocab));
  }

  rowEls(h, vocab) {
    const k = keyOf(h);
    const open = this.open === k;
    const row = el('div', `rv-row${open ? ' open' : ''}${toJudge(h) ? ' todo' : ''}`);
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-expanded', String(open));
    row.append(el('span', 'caret', open ? '▾' : '▸'), el('span', 'name', this.label(h)));
    const tk = el('span', 'tk');
    ticketsText(h).forEach((t) => tk.append(el('span', `rv-who ${t.startsWith('KELVIN') ? 'kelvin' : 'brett'}`, t)));
    row.append(tk);
    const run = h.run;
    row.append(el('span', `fin${run.placed ? ' placed' : ''}`, run.withdrawn ? 'WITHDRAWN'
      : !h.race.result ? 'NOT RUN' : run.place ? ordinal(run.place) : (run.place_code ?? DASH)));
    row.append(el('span', 'rec', h.record.map((x) => x.key).join(' · ')));
    const r = h.review;
    row.append(el('span', `st${r.verdict ? ` v-${r.verdict}` : ''}`, r.verdict
      ? vocab.verdicts.find((v) => v.key === r.verdict)?.label ?? r.verdict
      : toJudge(h) ? 'TO JUDGE' : ''));
    row.append(el('span', `why ${r.claim_timing ?? 'none'}`, r.claim_timing === 'before' ? 'LINE BEFORE'
      : r.claim_timing === 'after' ? 'LINE AFTER' : 'NO LINE'));
    const toggle = () => { this.open = open ? null : k; this.redraw(); };
    row.addEventListener('click', toggle);
    row.addEventListener('keydown', (e) => {
      if (e.target === row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(); }
    });
    if (!open) return [row];
    if (!this.drafts.has(k)) this.drafts.set(k, draftOf(h));
    const i = this.horses.indexOf(h);
    const next = this.horses.slice(i + 1).find(toJudge) ?? this.horses[i + 1];
    return [row, formEl(h, {
      vocab, draft: this.drafts.get(k), busy: this.busy.has(k), msg: this.msg.get(k),
      redraw: this.redraw,
      onSave: (body, opts) => this.save(h, body, opts),
      onNext: next ? () => { this.open = keyOf(next); this.redraw(); } : null,
    })];
  }

  async save(h, body, { advance = false } = {}) {
    const k = keyOf(h);
    this.busy.add(k);
    this.msg.delete(k);
    this.redraw();
    try {
      const row = await api.reviewSave(h.race_date, h.race_no, h.horse_no, body);
      const i = this.horses.findIndex((x) => keyOf(x) === k);
      if (i >= 0 && row?.review) this.horses[i] = { ...this.horses[i], review: row.review };
      if (advance) {
        const next = this.horses.slice(i + 1).find(toJudge) ?? this.horses.find(toJudge);
        this.open = next ? keyOf(next) : null;
      }
      this.changed();
    } catch (err) {
      this.msg.set(k, err.message);
    }
    this.busy.delete(k);
    this.redraw();
  }
}

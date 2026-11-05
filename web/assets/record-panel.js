/* record-panel.js — a runner's dated records, in the shared anchored panel.
 *
 * The VET RECORD and the TRIAL NOTE are the same kind of reading: a few dated
 * entries about one horse, each saying what kind of entry it is, how long ago,
 * the words themselves, and a line on what settled it — a clearance to race,
 * or when the note was written. One component, so the two read alike and a
 * fix to one is a fix to both. Positioning is overlay.js's (brief 09 §1).
 */
import { el } from './vocab.js';
import { anchoredPanel } from './overlay.js';

/**
 * Bind a hover panel headed `title` to `trigger`. Each row is
 *   { date, label, tone, age, body, lines: [[text, tone], ...] }
 * and `tone` colours the label or line: 'incident' | 'win' | 'muted'.
 * Built on first open and reused; returns the teardown.
 */
export function recordPanel(trigger, title, rows) {
  // anchoredPanel adds its own class to what render() returns, so the
  // panel's own class goes on the element here rather than through it.
  return anchoredPanel(trigger, () => {
    const host = el('div', 'rec-panel');
    host.append(el('h6', null, title));
    rows.forEach((r) => {
      const row = el('div', 'rec');
      const head = el('div', 'rec-head');
      head.append(el('span', 'd', r.date));
      head.append(el('span', `c ${r.tone || 'incident'}`, r.label));
      if (r.age != null) head.append(el('span', 'age', `${r.age}d ago`));
      row.append(head);
      if (r.body) row.append(el('div', 'rec-body', r.body));
      (r.lines || []).forEach(([text, tone]) => {
        row.append(el('div', `rec-line ${tone || 'muted'}`, text));
      });
      host.append(row);
    });
    return host;
  });
}

/** Whole days from one ISO date to another (today in Hong Kong when `to` is
 *  missing). `null` when either will not parse. */
export function daysBetween(from, to) {
  const a = Date.parse(from ?? '');
  const b = Date.parse(to ?? hkNow().slice(0, 10));
  return Number.isNaN(a) || Number.isNaN(b) ? null : Math.round((b - a) / 864e5);
}

/** A stored UTC timestamp as Hong Kong `YYYY-MM-DDTHH:MM`. HK keeps no
 *  summer time, so the shift is a constant. */
export function hkTime(ts) {
  const t = Date.parse(ts ?? '');
  return Number.isNaN(t) ? null : new Date(t + 8 * 36e5).toISOString().slice(0, 16);
}

function hkNow() {
  return new Date(Date.now() + 8 * 36e5).toISOString();
}

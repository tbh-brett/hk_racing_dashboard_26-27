/* book-tier.js — which of the book's horses to back, drawn the same way on
 * every page that shows a card.
 *
 * The tier is decided on the server (hkrd/query/book_tier.py); this only
 * draws it. TRIAL NOTE is the one tier with a record clear of the price, so it
 * is the only one that reads as an action; the quiet tiers are drawn quiet so
 * a race with five book horses reads as the one or two that matter.
 */
import { el } from './vocab.js';

const SHORT = { NOTE: 'TRIAL NOTE', STANDOUT: 'STANDOUT', EXCUSE: 'EXCUSE',
                QUIET: 'QUIET', STALE: 'STALE', RUN_NOTE: 'RUN NOTE' };
const DOES = { NOTE: 'back it to win, flat', STANDOUT: 'consider it',
               EXCUSE: 'watch it, not a bet on its own', QUIET: 'nothing measured behind it',
               STALE: 'three starts since it was booked: renew or close it on the Blackbook page',
               RUN_NOTE: 'your note on its last start; not in the book' };

/** The tier's record as one sentence, with its sample — never a bare figure. */
export function recordText(t) {
  const r = t && t.record;
  if (!r || !r.runs) return 'no settled runs in this tier yet';
  return `since ${r.since}: ${r.won} won of ${r.runs}, the tote expected ${r.expected}`
    + (r.ae !== null && r.ae !== undefined ? ` (A/E ${r.ae.toFixed(2)})` : '');
}

/** The chip on a runner's row. `null` when the runner has no tier. */
export function tierChip(t) {
  if (!t) return null;
  const c = el('span', `bt bt-${t.tier.toLowerCase()}`, SHORT[t.tier] || t.tier);
  c.title = `${t.label}: ${DOES[t.tier] || ''}.\n${recordText(t)}`
    + (t.starts !== null && t.starts !== undefined && t.in_book
      ? `\n${t.starts} start${t.starts === 1 ? '' : 's'} since booked` : '');
  return c;
}

/** "Rider asked · response strong", from the note's two taps, or ''. */
export function tapsText(n) {
  if (!n) return '';
  const bits = [];
  if (n.asked === 1) bits.push('asked');
  if (n.asked === 0) bits.push('not asked');
  if (n.response) bits.push(`response ${n.response}`);
  return bits.join(' · ');
}

/** The owner's trial note behind a TRIAL NOTE tier, as a line under the
 *  runner — the reason to back it, in the owner's own words. */
export function noteLine(t, shortDate) {
  if (!t || !t.note) return null;
  const n = t.note;
  const line = el('div', 'bt-noteline');
  const taps = tapsText(n);
  line.append(el('span', 'cap', `YOUR TRIAL NOTE · ${shortDate ? shortDate(n.trial_date) : n.trial_date} `),
              document.createTextNode(`“${n.note}”`));
  if (taps) line.append(el('span', 'taps', ` · ${taps}`));
  return line;
}

/** The race's book in one line: what to back, what to watch, what is quiet. */
export function bookLineEl(line) {
  if (!line) return null;
  return el('div', `bt-line bt-line-${line.tone}`, line.text);
}

const ORDER = { NOTE: 0, STANDOUT: 1, EXCUSE: 2, QUIET: 3, STALE: 4, RUN_NOTE: 5 };

/** Sort key: TRIAL NOTE first, untiered last — the order a crowded race reads in. */
export function tierRank(t) {
  return t ? (ORDER[t.tier] ?? 6) : 7;
}

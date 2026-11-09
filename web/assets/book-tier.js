/* book-tier.js — which of the book's horses to back, drawn the same way on
 * every page that shows a card.
 *
 * The tier is decided on the server (hkrd/query/book_tier.py); this only
 * draws it. TRIAL NOTE is the one tier with a record clear of the price, so it
 * is the only one that reads as an action; the quiet tiers are drawn quiet so
 * a race with five book horses reads as the one or two that matter.
 */
import { el, compactDate, tagLabel } from './vocab.js';
import { recordPanel, daysBetween, hkTime } from './record-panel.js';

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

/** "5 starts since booked", or null when the horse is not in the book. */
function startsText(t) {
  if (t.starts === null || t.starts === undefined || !t.in_book) return null;
  return `${t.starts} start${t.starts === 1 ? '' : 's'} since booked`;
}

/** The chip on a runner's row. `null` when the runner has no tier.
 *  `who` ("5 LIGHTNING ACE") heads the chip's panel and `on` (the race date)
 *  is what its ages count to; without them it is headed by the tier alone and
 *  aged to today. Every chip opens on the horse's own case — the trial note,
 *  the booking and its reason, the standout trial, the run note — and the
 *  tier's record comes last, labelled as the tier's, not the horse's. */
export function tierChip(t, { who = null, on = null } = {}) {
  if (!t) return null;
  const c = el('span', `bt bt-${t.tier.toLowerCase()}`, SHORT[t.tier] || t.tier);
  if (t.tier === 'NOTE' && t.note) {
    notePanel(c, t, who, on);
    return c;
  }
  const rows = [t.booking ? bookingRow(t, on) : null,
                t.tier === 'STANDOUT' && t.standout ? standoutRow(t.standout, on) : null,
                t.tier === 'RUN_NOTE' && t.run_note ? runNoteRow(t.run_note, on) : null]
    .filter(Boolean);
  if (!rows.length) {
    const starts = startsText(t);
    c.title = `${t.label}: ${DOES[t.tier] || ''}.\n${recordText(t)}`
      + (starts ? `\n${starts}` : '');
    return c;
  }
  rows[rows.length - 1].lines.push(...tailLines(t));
  recordPanel(c, `${who ? `${who} · ` : ''}${SHORT[t.tier] || t.tier}`, rows);
  return c;
}

/** What the tier asks, the starts since booking, and the TIER's record — every
 *  runner in it since it began, so the A/E reads as the rule's, not the horse's. */
function tailLines(t) {
  const tier = t.record?.runs ? `every ${t.label.toLowerCase()} runner ${recordText(t)}`
    : recordText(t);
  return [[[DOES[t.tier], startsText(t)].filter(Boolean).join(' · '), 'muted'],
          [tier, 'muted']];
}

/** The book entry behind the tier: who booked it, off which run, and why. */
function bookingRow(t, on) {
  const b = t.booking;
  const sys = b.origin === 'system';
  const from = b.source_date || b.added_date;
  const who = sys ? (b.adopted_date ? 'SYSTEM BOOK · ADOPTED' : 'SYSTEM BOOK') : 'YOUR BOOK';
  const lines = [];
  const excuse = t.excuse ?? [];
  if (excuse.length) lines.push([`excuse: ${excuse.map(tagLabel).join(', ')}`, 'incident']);
  const other = (b.tags ?? []).filter((x) => !excuse.includes(x));
  if (other.length) lines.push([`${excuse.length ? 'also ' : ''}tagged ${other.map(tagLabel).join(', ')}`, 'muted']);
  if (b.added_date && b.added_date !== from) lines.push([`booked ${compactDate(b.added_date)}`, 'muted']);
  return {
    date: compactDate(from),
    label: b.source_date && b.source_race_no != null ? `${who} · OFF R${b.source_race_no}` : who,
    tone: t.tier === 'EXCUSE' ? 'incident' : 'muted',
    age: daysBetween(from, on),
    body: b.reasoning || 'no reason written on the entry',
    lines,
  };
}

/** The STANDOUT trial since its last run, in HKJC's words. */
function standoutRow(s, on) {
  return {
    date: compactDate(s.trial_date),
    label: `STANDOUT TRIAL${s.trial_no != null ? ` · BATCH ${s.trial_no}` : ''}`,
    tone: 'win',
    age: daysBetween(s.trial_date, on),
    body: s.comment || null,
    lines: [],
  };
}

/** The owner's note on the horse's last start. */
function runNoteRow(n, on) {
  return {
    date: compactDate(n.race_date),
    label: `YOUR RUN NOTE${n.race_no != null ? ` · R${n.race_no}` : ''}`,
    tone: 'muted',
    age: daysBetween(n.race_date, on),
    body: `“${n.note}”`,
    lines: [],
  };
}

/** The owner's trial note, in the panel the VET RECORD uses: the trial it is
 *  about, the note in the owner's own words, and when it was written. The
 *  writing time is the proof the note came before the race (`written_at`;
 *  book_tier only counts a note written before the off). No tote record here:
 *  the note is the reason to back the horse, not the tier's A/E. */
function notePanel(chip, t, who, on) {
  const n = t.note;
  const at = hkTime(n.written_at);
  const lines = [[at ? `written ${compactDate(at.slice(0, 10))} · ${at.slice(11)}`
                       + (on ? ', before the off' : '')
                     : 'no writing time recorded', at ? 'win' : 'incident']];
  const taps = tapsText(n);
  if (taps) lines.push([taps, 'muted']);
  const more = (t.notes ?? 1) - 1;
  const tail = [DOES.NOTE, startsText(t),
                more > 0 ? `${more} earlier note${more === 1 ? '' : 's'} since its last run` : null];
  lines.push([tail.filter(Boolean).join(' · '), 'muted']);
  recordPanel(chip, `${who ? `${who} · ` : ''}TRIAL NOTE`, [{
    date: compactDate(n.trial_date),
    label: n.trial_no != null ? `TRIAL · BATCH ${n.trial_no}` : 'TRIAL',
    tone: 'win',
    age: daysBetween(n.trial_date, on),
    body: `“${n.note}”`,
    lines,
  }]);
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

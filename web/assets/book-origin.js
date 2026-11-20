/* Who booked a blackbook entry — the owner, or the automatic pass.
 *
 * `jobs/auto_book` writes system entries into the same book the owner keeps,
 * so they reach every page an entry does: the Blackbook, the Briefing, the
 * Race Day band. Every one of those pages asks the same question of an entry
 * — is this the owner's thesis or the system's? — and they must not answer it
 * three different ways, so the answer lives here.
 *
 * A system entry the owner ADOPTED is the owner's: it reads in the book's own
 * teal everywhere. Its origin stays 'system' on the server so the system's
 * record keeps it, which is a fact about the record, not about the page.
 */
import { el } from './vocab.js';

/** Booked by the system and not taken on by the owner. */
export function isSystem(e) {
  return !!e && e.origin === 'system' && !e.adopted_date;
}

/** Picked by BOTH (owner, 20 Nov 2026): a system pick the owner adopted, or a
 *  horse with an entry in each book (`both` from the server). Adopting used to
 *  turn a system horse the owner's teal, and the page forgot the system had
 *  picked it too; it now reads in its own colour, --book-both. */
export function isBoth(e) {
  return !!e && ((e.origin === 'system' && !!e.adopted_date) || !!e.both);
}

/** The class a page paints a booked horse with: 'both', 'sys', or ''. */
export function bookClass(e) {
  return isBoth(e) ? 'both' : isSystem(e) ? 'sys' : '';
}

/** The chip's word: BB+SYS, SYS or BB. */
export function bookWord(e) {
  return isBoth(e) ? 'BB+SYS' : isSystem(e) ? 'SYS' : 'BB';
}

/** The three ways to read the book. */
export const BOOKS = [['all', 'ALL'], ['owner', 'YOURS'], ['system', 'SYSTEM']];

/** Is this entry in that book? The same rule `query/blackbook_origin` uses:
 *  the system's book keeps what the owner adopted, and so does the owner's. */
export function inBook(e, book) {
  if (book === 'system') return e.origin === 'system';
  if (book === 'owner') return e.origin !== 'system' || !!e.adopted_date;
  return true;
}

/** The reason and the day it was added, written out under a system entry's
 *  row. The owner knows why they booked their own horses; a system entry is
 *  only worth a look if its reason is on screen without a click. */
export function systemLine(e) {
  const line = el('div', 'sys-line');
  line.append(el('span', 'sys-tag', 'SYSTEM'));
  line.append(el('span', 'sys-date', `added ${e.added_date}`));
  line.append(el('span', 'sys-why', e.reasoning ?? ''));
  line.title = e.reasoning ?? '';
  return line;
}

/* book-renew.js — the Blackbook page's answer to a STALE entry.
 *
 * An entry with three starts since it was booked (or renewed) is muted on
 * race day (hkrd/query/book_tier.py) and never closed by anything but the
 * owner. RETIRE was already on the row; RENEW is the other answer: keep
 * following it, and count its starts again from today.
 */
import { api } from './api.js';
import { el } from './vocab.js';

/** The STALE mark for the status column, or null when the entry is not stale. */
export function staleBadge(e) {
  if (!e.stale_starts || e.status !== 'active') return null;
  const b = el('span', 'badge stale', 'STALE');
  b.title = `${e.stale_starts} starts since ${e.renewed_date ? `renewed ${e.renewed_date}` : 'booked'}`
    + ' — muted on race day. RENEW to keep following it, RETIRE to let it go.';
  return b;
}

/** RENEW, for a stale entry. `busy` is the page's set of entries mid-request;
 *  `render` redraws the page. */
export function renewButton(e, { busy, render }) {
  if (!e.stale_starts || e.status !== 'active') return null;
  const b = el('button', 'act-btn', 'RENEW');
  b.disabled = busy.has(e.id);
  b.title = 'keep following this horse: its starts are counted again from today';
  b.addEventListener('click', async (event) => {
    event.stopPropagation();
    busy.add(e.id);
    render();
    try {
      const out = await api.renewBlackbookEntry(e.id);
      e.renewed_date = out.renewed_date;
      e.stale_starts = null;
    } catch (err) {
      b.textContent = err.message;
    } finally {
      busy.delete(e.id);
      render();
    }
  });
  return b;
}

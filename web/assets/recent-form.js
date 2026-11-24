/* The Form Guide's LAST 3 column: each runner's last three figures weighted
 * 3-2-1, newest heaviest, and where that stands among today's runners.
 *
 * Computed once, in query/formguide.recent_form, and only drawn here: a
 * reading built in the browser is a second opinion that drifts from the first.
 * Its own module because form-guide.js is far past the file-size cap.
 */
import { el, DASH, ordinal } from './vocab.js';

/* Why this reading and not another, measured 24 Nov 2026 over 2021-26. */
export const RECENT_HINT =
  'the last three figures weighted 3-2-1, newest heaviest, and its rank among '
  + 'today’s runners. Of the ways tried it best picks the next winner over '
  + 'a beaten rival (69%, against 67% for the last run alone), and about half '
  + 'of a horse’s edge over 100 shows up again next time. A reading, not a bet: '
  + 'the odds already hold it.';

/** One cell. `summary` is recent_form's entry for the horse, or null. */
export function recentCell(summary) {
  const cell = el('div', 'recent');
  if (!summary) {
    cell.append(el('span', 'none', DASH));
    cell.title = 'no figure in its last three runs';
    return cell;
  }
  // Fewer than three figures is a thinner reading and is drawn as one.
  if (summary.figures < 3) cell.classList.add('thin');
  cell.append(el('span', 'v', summary.figure.toFixed(1)));
  cell.append(el('span', 'rk', `${ordinal(summary.rank)}/${summary.of}`));
  const read = summary.figures === summary.runs
    ? `${summary.figures} figure${summary.figures === 1 ? '' : 's'}`
    : `${summary.figures} figure${summary.figures === 1 ? '' : 's'} from the last `
      + `${summary.runs} runs (a run with no figure gives its weight to the others)`;
  cell.title = `${summary.figure.toFixed(1)}, weighted 3-2-1 over ${read}; `
    + `${ordinal(summary.rank)} of ${summary.of} runners with a reading`;
  return cell;
}

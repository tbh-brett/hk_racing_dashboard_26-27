/* LAST 3: each runner's last three figures weighted 3-2-1, newest heaviest,
 * and where that stands among today's runners. On the Form Guide card and in
 * Race Day's figure column, drawn by the one function below so the two pages
 * cannot shade the same horse differently.
 *
 * Computed once, in query/formguide.recent_form; this only draws it. Its own
 * module because form-guide.js and race-day.js are both past the size cap.
 */
import { el, DASH } from './vocab.js';

/* Why this reading and not another, measured 24 Nov 2026 over 2021-26. */
export const RECENT_HINT =
  'the last three figures weighted 3-2-1, newest heaviest; click to rank the '
  + 'field by it. Of the ways tried it best picks the next winner over '
  + 'a beaten rival (69%, against 67% for the last run alone), and about half '
  + 'of a horse’s edge over 100 shows up again next time. Green is better, '
  + 'red worse, deeper for further from the middle. A reading, not a bet: the '
  + 'odds already hold it.';

/* The shading, read off what each band did at its NEXT start over 2021-26:
   how often it placed, against 25% for all runners. The
   middle band is left unshaded because it is the average horse. Winners in
   every band matched what their odds said, so a colour is a description of
   form, never a price. Bands are on the reading as shown, to one decimal. */
const BANDS = [
  { min: 104, tone: 'g3', placed: 53.2 },
  { min: 102, tone: 'g2', placed: 40.5 },
  { min: 101, tone: 'g1', placed: 32.5 },
  { min: 99, tone: '', placed: 24.6 },
  { min: 97, tone: 'r1', placed: 16.4 },
  { min: 95, tone: 'r2', placed: 11.5 },
  { min: -Infinity, tone: 'r3', placed: 6.8 },
];

export function recentBand(figure) {
  return BANDS.find((b) => figure >= b.min);
}

/** One cell. `summary` is recent_form's entry for the horse, or null. */
export function recentCell(summary) {
  const cell = el('div', 'rf');
  if (!summary) {
    cell.append(el('span', 'none', DASH));
    cell.title = 'no figure in its last three runs';
    return cell;
  }
  // Fewer than three figures is a thinner reading and is drawn as one.
  if (summary.figures < 3) cell.classList.add('thin');
  const band = recentBand(summary.figure);
  // The number alone: where it stands in the field is a click on the column
  // header away, on both pages, so the cell does not spell it out.
  cell.append(el('span', `v${band.tone ? ` ${band.tone}` : ''}`, summary.figure.toFixed(1)));
  const read = summary.figures === summary.runs
    ? `${summary.figures} figure${summary.figures === 1 ? '' : 's'}`
    : `${summary.figures} figure${summary.figures === 1 ? '' : 's'} from the last `
      + `${summary.runs} runs (a run with no figure gives its weight to the others)`;
  cell.title = `${summary.figure.toFixed(1)}, weighted 3-2-1 over ${read}. `
    + `Horses reading like this placed next start ${band.placed}% of the time `
    + '(all runners 25%), and won about what their odds said.';
  return cell;
}

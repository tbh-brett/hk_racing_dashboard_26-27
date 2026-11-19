/* gear-chips.js — the gear facts on a Race Day runner's second line.
 *
 * Moved out of race-day.js (past the 600-line cap) when the horse's own
 * record in its gear was added, 19 Nov 2026.
 *
 * GEAR, and what is happening to it.
 *
 * HKJC suffixes the code with the change — B1 is blinkers first time, B- is
 * blinkers off, B2 is second time — and the dashboard rendered the literal
 * text "B1" in a column on another page and nowhere said what the 1 meant.
 * The archive has 584 B1, 651 B- and 366 B2 in it.
 *
 * Two things the suffix cannot say are added by the server: RE-INSTATED (worn
 * before, off last start, back on — HKJC writes a plain B, identical to a
 * horse that has worn them for two seasons), and the BARRIER-TRIAL step. A
 * horse schooled in blinkers and not wearing them today is a stable preparing
 * something it has not committed to; that is a reason to keep watching, never
 * a reason to back, and the chip says so rather than scoring it.
 *
 * THE HORSE'S OWN RECORD IN THE GEAR (`query/gear_history.py`). The owner's
 * case was PRIVATE WEALTH: keen in blinkers twice, a warning on its manners,
 * blinkers off, second at 19.0 — and the card said "B OFF" in the colour it
 * gives every removal. Three readings now change the chip:
 *
 *   B KEEN 1/1        wearing them again after a keen FIRST run in them — the
 *                     caution, in the loss colour (83 such runs returned 39-47
 *                     cents a dollar worse than their price)
 *   B KEEN 3/5        keen the last time, in gear it had worn before — the
 *                     horse's history, neutral: those runs placed 1.05x
 *   B OFF AFTER KEEN  taken off after a keen run in them
 *   HOOD OFF          the hood comes off
 *
 * The last two take the edge colour because the archive put them on the right
 * side of the price over two seasons; the tooltip says how far, on how many
 * runs, and that neither has been scored forward yet.
 *
 * Only the notable states get a chip. A tongue tie worn every start for two
 * years is on the card and in the flyout; a chip that fires on everyone stops
 * being read, which is the same rule the vet badge follows. */
import { el } from './vocab.js';

const STATE_LABEL = { first: '1ST', second: '2ND', off: 'OFF' };

function label(p) {
  if (p.signal === 'hood_off') return 'HOOD OFF';
  if (p.signal === 'off_after_keen') return `${p.code} OFF AFTER KEEN`;
  const base = p.reinstated ? `${p.code} BACK`
    : STATE_LABEL[p.state] ? `${p.code} ${STATE_LABEL[p.state]}` : p.code;
  if ((p.signal === 'keen_in' || p.signal === 'keen_first') && p.history) {
    return `${base} KEEN ${p.history.keen}/${p.history.read}`;
  }
  return base;
}

export function gearChips(r) {
  const g = r.gear_change;
  if (!g) return [];
  const out = [];
  (g.pieces ?? []).filter((p) => p.notable).forEach((p) => {
    const cls = `gear-chip g-${p.reinstated ? 'back' : p.state}`
      + (p.signal ? ` s-${p.signal.replace(/_/g, '-')}` : '');
    const chip = el('span', cls, label(p));
    chip.title = p.detail;
    out.push(chip);
  });
  // The schooling step, and only where it says something. `carried` is a horse
  // that trialled in gear it already wears, which is not news; `raced_since`
  // one that has raced in it since the trial, where the removal chip speaks.
  (g.trial_gear ?? []).forEach((t) => {
    if (t.signal !== 'applied' && t.signal !== 'withheld') return;
    const chip = el('span', `gear-chip trial-${t.signal}`,
      t.signal === 'applied' ? `${t.code} AFTER TRIAL` : `TRIALLED ${t.code}`);
    chip.title = t.detail;
    out.push(chip);
  });
  // A month of the archive has no gear column at all — the results write
  // erased it for April to July 2026, and July has 0 of 641 runs on record —
  // so "nothing changed" and "we cannot tell what changed" are different
  // answers. That caveat is stated ONCE in the card footer rather than as a
  // chip per runner: it currently applies to most of the field, and a chip
  // that fires on everyone stops being read. See `gearCaveat`.
  return out;
}

/** How many runners have no gear on their previous run.
 *
 *  Everything above reads HKJC's own suffix, which is a fact about TODAY'S
 *  card and is unaffected. Only the two derived states — re-instated, and gear
 *  quietly dropped without a `-` — need the previous run, and for these
 *  runners there is nothing to compare against. */
export function gearCaveat(runners) {
  const blind = runners.filter(
    (r) => r.gear_change && r.gear_change.declared && !r.gear_change.comparable);
  if (!blind.length) return null;
  return `${blind.length} OF ${runners.length} HAVE NO GEAR ON THEIR PREVIOUS `
    + 'RUN — APR–JUL 2026 WAS NOT RECORDED, SO A CHANGE CANNOT BE SHOWN FOR THEM';
}

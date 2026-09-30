/* What the model cannot see, under a runner on the Briefing.
 *
 * Two kinds of line, both from the API and neither typed here:
 *  - a fact the model does not read (query/screen `unseen`, model/gbm_unseen:
 *    a trial since the last run, a new stable), and
 *  - a flag, where five seasons say the model's number is weakest (`flags`).
 * Each says what it has been worth against the model's chance and against the
 * price's, and over how many runs; a line whose record has not been built yet
 * says so. They sit beside the number and are never added to it: mixed into
 * one number, the old Screen's facts gained nothing (docs/gbm.md). Colour is
 * the direction the record points, never the gap, and nothing is re-ranked.
 */
import { el, DASH } from './vocab.js';
import { shortDate } from './briefing-format.js';

const times = (v) => `${v.toFixed(2)}×`;
const known = (v) => v !== null && v !== undefined;

function worth(x) {
  if (!known(x.model_ae)) return 'not measured yet';
  const price = known(x.price_ae) ? ` · the price's ${times(x.price_ae)}` : '';
  const runs = x.runs ? ` · ${x.runs.toLocaleString('en-US')} runs` : '';
  // The range says how far the figure could be from the truth; one that
  // crosses 1.00 has not been shown to be anything.
  const range = known(x.model_lo) && known(x.model_hi)
    ? ` (${x.model_lo.toFixed(2)}–${x.model_hi.toFixed(2)})` : '';
  return `won ${times(x.model_ae)} the model's chance${range}${price}${runs}`;
}

const tone = (ae) => (!known(ae) ? '' : ae > 1 ? 'for' : ae < 1 ? 'against' : '');

/** The lines for one runner: its facts first, then its flags. */
export function unseenView(s) {
  const facts = (s.unseen || []).map((u) => ({
    kind: u.key === 'new_stable' ? 'STABLE' : 'TRIAL',
    head: u.label,
    what: u.trial
      ? `${shortDate(u.trial.trial_date)}, ${u.trial.place ?? DASH} of ${u.trial.field_size ?? DASH}`
      : u.from_trainer ? `from ${u.from_trainer}` : '',
    worth: worth(u), tone: tone(u.model_ae), note: u.note,
  }));
  const flags = (s.flags || []).map((f) => ({
    kind: 'FLAG', head: f.label, what: '', worth: worth(f), tone: tone(f.model_ae), note: f.note,
  }));
  return [...facts, ...flags];
}

/** The row's chips for the same facts, and the VET finding the model reads
 *  but the eye should not have to open a runner to find. */
export function unseenChips(s) {
  const chips = [];
  if (s.vet) chips.push({ t: 'VET', tone: 'vet' });
  const facts = s.unseen || [];
  if (facts.some((u) => u.key === 'new_stable')) chips.push({ t: 'NEW TR', tone: 'violet' });
  const trial = facts.find((u) => u.key !== 'new_stable');
  if (trial) {
    const bad = trial.key === 'trial_negative';
    chips.push({ t: bad ? 'TRIAL −' : 'TRIAL +', tone: bad ? 'trial-neg' : 'trial' });
  }
  return chips;
}

export function unseenBlock(lines, { compact = false } = {}) {
  const box = el('div', 'bf-unseen');
  box.append(el('div', 'cap', compact ? 'NOT IN THE MODEL'
    : 'NOT IN THE MODEL · said beside its number, never added to it'));
  lines.forEach((u) => {
    const row = el('div', `bf-unseen-row ${u.tone}`);
    row.title = u.note;
    row.append(el('span', 'k', u.kind), el('span', 'h', u.head));
    if (u.what) row.append(el('span', 'w', u.what));
    row.append(el('span', 'm', u.worth));
    box.append(row);
  });
  return box;
}

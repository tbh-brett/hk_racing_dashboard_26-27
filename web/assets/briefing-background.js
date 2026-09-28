/* briefing-background.js — the BACKGROUND RECORD, below the sources' record.
 *
 * From HKJC's "Intro to New Horses" (query/background): per agent, country,
 * import type, sale price and trial, how those imports ran against what the
 * market expected. Horses profiled from 2026/27 on are the test and are kept
 * apart from 2025/26, where the idea came from. A horse's own background is
 * drawn by background.js, shared with Race Day.
 */
import { DASH, el } from './vocab.js';

/* ── the background record ─────────────────────────────────────────────── */

const DIMS = { agent: 'AGENT', 'trained in': 'TRAINED BEFORE IMPORT IN', 'import type': 'IMPORT TYPE',
               'sale price': 'SALE PRICE (HIGHEST)', 'pre-import trial': 'PRE-IMPORT TRIAL (UNRACED IMPORTS)' };
const pct = (k, n) => (n ? `${Math.round((100 * k) / n)}%` : '');
const signed = (v) => (v === null || v === undefined ? DASH : `${v > 0 ? '+' : ''}${v}`);

let shown = null;          // which era the reader chose, kept across re-reads

export function renderBackgroundRecord(host, rec) {
  host.replaceChildren();
  if (!rec) return;
  if (rec.error) {
    host.append(el('div', 'bf-rec-note', rec.error));
    return;
  }
  // The test first once it has something to say; until then, where the idea
  // came from, with the test one click away.
  const test = rec.eras.find((e) => e.key === 'test');
  const runs = test ? test.lines.filter((x) => x.dimension === 'import type')
    .reduce((n, x) => n + x.runs, 0) : 0;
  const era = rec.eras.find((e) => e.key === (shown || (runs >= 60 ? 'test' : 'origin')));

  const cap = el('div', 'bf-rec-cap');
  cap.append(el('span', 'l', 'BACKGROUND RECORD'),
             el('span', 'r', `imports by where they came from · A/E against the closing tote · `
               + `rating from first run to last · rose = ${rec.rose_by}+ points`));
  host.append(cap);
  const tabs = el('div', 'bf-bgtabs');
  rec.eras.forEach((e) => {
    const b = el('button', `tab${e.key === era.key ? ' on' : ''}`, `${e.label} · ${e.horses} horses`);
    b.type = 'button';
    b.addEventListener('click', () => { shown = e.key; renderBackgroundRecord(host, rec); });
    tabs.append(b);
  });
  host.append(tabs);

  const head = el('div', 'bf-rec-row bf-bgrow bf-rec-head');
  [['src', 'GROUP'], ['hr', 'HORSES'], ['runs', 'RUNS'], ['won', 'WON'], ['ae', 'A/E'],
   ['rt', 'RATING'], ['rose', 'ROSE']].forEach(([k, h]) => head.append(el('span', `c c-${k}`, h)));
  host.append(head);
  let dim = null;
  era.lines.forEach((x) => {
    if (x.dimension !== dim) {
      dim = x.dimension;
      host.append(el('div', 'bf-rec-rule', DIMS[dim] || dim.toUpperCase()));
    }
    const row = el('div', 'bf-rec-row bf-bgrow');
    const src = el('span', 'c c-src');
    src.append(el('span', 'gn', x.group));
    if (x.ran < x.horses) src.append(el('span', 'aside', `${x.horses - x.ran} yet to run`));
    const won = el('span', 'c c-won', x.runs ? `${x.won}/${x.runs}` : DASH);
    if (x.runs) won.append(el('span', 'p', ` ${pct(x.won, x.runs)}`));
    const rose = el('span', 'c c-rose', x.rated ? `${x.rose}/${x.rated}` : DASH);
    const ae = el('span', `c c-ae${x.ae > 1.15 ? ' good' : x.ae !== null && x.ae < 0.85 ? ' neg' : ''}`,
                  x.ae === null || x.ae === undefined ? DASH : x.ae.toFixed(2));
    row.append(src, el('span', 'c c-hr', String(x.horses)), el('span', 'c c-runs', String(x.runs)),
               won, ae, el('span', 'c c-rt', signed(x.rating_change)), rose);
    host.append(row);
  });
  host.append(el('div', 'bf-rec-note',
                 `A record, not a verdict. ${era.key === 'origin'
                   ? 'These are the 2025/26 imports the idea came from: they were looked at to form it, '
                     + 'so they cannot also test it — the imports from 2026/27 on do that. '
                   : 'These horses were profiled after the idea was formed: they are its test. '}`
                 + 'A horse with two agents counts under each. The profile is written by HKJC’s '
                 + 'contractor and read by rule; a group is only as good as what the profiles say.'));
}

/* briefing-record.js — every source's record this season, below the Briefing.
 *
 * Read from GET /api/tips/record (query/tips_record): per source, how its top
 * pick (one a race) and every horse it named have run, beside the tote
 * favourite and the Screen scored the same way. A record, not a ranking: the
 * lines keep their order, and every figure says how many it was measured on,
 * because at the start of a season one winner moves a strike rate by several
 * points.
 */
import { DASH, MINUS, el } from './vocab.js';

const TONE = { pick: 'tip', featured: 'tip', connections: 'voice',
               computed: 'screen', market: 'price' };
const BENCH = new Set(['computed', 'market']);
/* What a phone has room for: three lines began "Racing …" when cut short. */
const SHORT = { racing_sports: 'R&S', rtw_preview: 'RTW picks', factcheck: 'FC picks',
                threads: 'Horse Det.', oncc: 'on.cc', rtw_interview: 'Interviews',
                factcheck_featured: 'FC feat.', screen: 'Screen', favourite: 'Favourite' };
/* The columns, left to right. The phone keeps SRC, RACES, the top pick's
 * WON, PLACED and $10, and every horse's PLACED (briefing-after.css). Every
 * count is written out of how many — "15/35", never a bare 15. */
const COLS = [['src', 'SOURCE'], ['mtg', 'MTG'], ['races', 'RACES'], ['tw', 'WON'],
              ['tp', 'PLACED'], ['ret', '$10'], ['ae', 'A/E'], ['fav', 'FAV SAME'],
              ['aw', 'WON'], ['ap', 'PLACED']];

const pct = (k, n) => (n ? `${Math.round((100 * k) / n)}%` : '');
const ret = (v) => (v === null || v === undefined ? DASH
  : `${v > 0 ? '+' : v < 0 ? MINUS : '±'}${Math.abs(v).toFixed(0)}%`);

/** One source's line, as the table writes it: [value, percentage, tone]
 *  per column after the source's name. */
export function recordLine(x) {
  const t = x.top;
  const a = x.all;
  const aside = [x.late && `${x.late} after the off, not counted`,
                 x.scratched && `${x.scratched} on a non-runner`].filter(Boolean);
  const has = (v) => v !== null && v !== undefined;
  return {
    key: x.key, label: x.label, short: SHORT[x.key] || x.label,
    tone: TONE[x.kind] || 'tip', bench: BENCH.has(x.kind),
    aside: aside.join(' · '), noTop: !t && !!a,
    cells: {
      mtg: [String(x.meetings)], races: [String(x.races)],
      tw: t ? [`${t.won}/${t.n}`, pct(t.won, t.n)] : [DASH],
      tp: t ? [`${t.placed}/${t.n}`, pct(t.placed, t.n)] : [DASH],
      ret: t ? [ret(t.return_pct), '', t.return_pct > 0 ? 'good' : t.return_pct < 0 ? 'neg' : '']
        : [DASH],
      ae: [t && has(t.ae) ? t.ae.toFixed(2) : DASH],
      fav: [t && has(t.fav_won) ? `${t.fav_won}/${t.n}` : DASH, '', 'dim'],
      aw: a ? [`${a.won}/${a.n}`, pct(a.won, a.n)] : [DASH],
      ap: a ? [`${a.placed}/${a.n}`, pct(a.placed, a.n)] : [DASH],
    },
  };
}

export function renderRecord(host, rec) {
  host.replaceChildren();
  if (!rec) return;
  if (rec.error) {
    host.append(el('div', 'bf-rec-note', rec.error));
    return;
  }
  const cap = el('div', 'bf-rec-cap');
  cap.append(el('span', 'l', 'SOURCES’ RECORD · THIS SEASON'),
             el('span', 'r', `${rec.meetings} meetings · ${rec.races} races with a result · `
               + `placed = ${rec.placed_is} · $${rec.stake} on each top pick at the final tote price`));
  host.append(cap);

  const groups = el('div', 'bf-rec-row bf-rec-groups');
  const g1 = el('span', 'g1');
  g1.append(el('span', 'gl', 'TOP PICK · ONE A RACE'), el('span', 'gs', 'TOP PICK'));
  const g2 = el('span', 'g2');
  g2.append(el('span', 'gl', 'EVERY HORSE NAMED'), el('span', 'gs', 'ALL'));
  groups.append(g1, g2);
  const head = el('div', 'bf-rec-row bf-rec-head');
  COLS.forEach(([k, h]) => head.append(el('span', `c c-${k}`, h)));
  host.append(groups, head);

  let benched = false;
  rec.lines.map(recordLine).forEach((x) => {
    if (x.bench && !benched) {
      benched = true;
      host.append(el('div', 'bf-rec-rule', 'BENCHMARKS · SCORED THE SAME WAY'));
    }
    const row = el('div', `bf-rec-row${x.bench ? ' bench' : ''}`);
    if (x.noTop) row.title = 'No top pick: a conversation about a horse is not a tip';
    const who = el('span', 'c c-src');
    who.append(el('span', `dot k-${x.tone}`), el('span', 'nm', x.label),
               el('span', 'nm-s', x.short));
    if (x.aside) who.append(el('span', 'aside', x.aside));
    row.append(who);
    COLS.slice(1).forEach(([k]) => {
      const [v, p, tone] = x.cells[k];
      const c = el('span', `c c-${k}${tone ? ` ${tone}` : ''}`, v);
      if (p) c.append(el('span', 'p', ` ${p}`));
      row.append(c);
    });
    host.append(row);
  });
  if (!rec.lines.some((x) => x.key === 'screen')) {
    host.append(el('div', 'bf-rec-note',
                   'The Screen joins once a meeting after its weights were fitted has a result '
                   + 'recorded — it is never scored on races it was fitted on.'));
  }
  host.append(el('div', 'bf-rec-note',
                 'A record, not a ranking. A/E: wins against what the closing tote expected of the '
                 + 'same horses — above 1, winners the market underrated. FAV SAME: how the tote '
                 + 'favourite did in the races that source tipped. Picks count only if made before '
                 + 'the off; once a race has run, its tips are kept as they stood.'));
}

/* vocab.js — the visual grammar every page shares.
 *
 * `RunnerLine` makes a run the SAME OBJECT wherever it appears. This is the
 * other half of that promise: the same object rendered the same way. The data
 * rule was being kept and the rendering rule was not — the navigation array was
 * copied into nine files, `el`/`$`/`DASH` into eight, and the running-style
 * badge existed three times with three different wrappers and a fourth page
 * showing it as plain text with no colour at all.
 *
 * That is the mechanism by which pages drift apart, and it is why a fix applied
 * to one page kept not reaching the others. Anything here is defined once and
 * imported; nothing here may import a page module.
 */

export const DASH = '—';
export const MINUS = '−';

export const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

export const $ = (id) => document.getElementById(id);

/* ── navigation ───────────────────────────────────────────────────────────
 * Design note 11 §4 gives the canonical order. Model Analysis is not in that
 * list because the note left the Lab page's home as an open question; it is
 * eighth and last here, which is the answer the owner gave.
 *
 * One array. Changing the order used to mean editing nine files and finding
 * out later which one was missed.
 */
export const NAV = [
  ['Race Day', 'raceday.html'],
  ['Form Guide', 'form-guide.html'],
  ['Bets', 'bets.html'],
  ['Blackbook', 'blackbook.html'],
  ['Results', 'results.html'],
  ['Lookup', 'lookup.html'],
  ['Trials', 'trials.html'],
  ['Model Analysis', 'model-analysis.html'],
];

/** Render the page nav, marking `here` (a filename) as current. */
export function renderNav(host, here) {
  host.replaceChildren(...NAV.map(([name, href]) => {
    const a = el('a', null, name);
    a.href = href;
    if (href === here) a.setAttribute('aria-current', 'page');
    return a;
  }));
}

/* ── running style ────────────────────────────────────────────────────────
 * Front of the field to the back. Sorting these alphabetically gives Closer,
 * Leader, Midfield, On-Pace, which is meaningless — so the order is an
 * ordinal, never a string comparison, and it is this ordinal everywhere.
 */
export const STYLE_ORDER = ['Leader', 'On-Pace', 'Midfield', 'Closer'];

/** Position in the field, 1-4. Unknown sorts last rather than first. */
export function styleOrdinal(style) {
  const i = STYLE_ORDER.indexOf(style ?? '');
  return i < 0 ? 99 : i + 1;
}

/** The class pair for a style. Four distinct hues, not four brightnesses:
 *  a brightness step reads as intensity of one thing, not four categories. */
export function styleClass(style, { chip = true } = {}) {
  const key = (style ?? 'unknown').toLowerCase().replace(/[^a-z]/g, '');
  return `${chip ? 'style-chip ' : ''}style-${key}`;
}

/** A style badge. The one implementation — pages used to have three. */
export function styleBadge(style, opts) {
  return el('span', styleClass(style, opts), style ?? DASH);
}

/* ── market movement ──────────────────────────────────────────────────────
 * Direction and magnitude together. Settlement is tote, so this is a sizing
 * input and an operational signal — never a timing edge, and nothing built on
 * it may read as a selection rule.
 */
export function movementArrow(direction) {
  if (direction === 'drifted') return '▲';
  if (direction === 'shortened') return '▼';
  return '·';
}

/** Class naming what the money did. Drift and firm are opposite meanings and
 *  must never share a colour with a result (win/loss) elsewhere. */
export function movementClass(direction) {
  if (direction === 'drifted') return 'mv-drifted';
  if (direction === 'shortened') return 'mv-shortened';
  return 'mv-flat';
}

/* ── trip trouble ─────────────────────────────────────────────────────────
 * Tags extracted from the stewards' commentary. Routine veterinary entries are
 * not trip trouble and must not render as though they were — a badge that
 * fires on every runner stops being read.
 */
export const ROUTINE_TAGS = new Set(['sampling', 'vet_routine', 'no_report',
                                     'jumped_fairly']);

export function tripTags(tags) {
  return (tags ?? []).filter((t) => !ROUTINE_TAGS.has(t));
}

/** Tag text as it reads on screen: underscores are a storage detail. */
export function tagLabel(tag) {
  return (tag ?? '').replace(/_/g, ' ');
}

/* ── figures ──────────────────────────────────────────────────────────────
 * `figure_display` is built in query/types.py so the figure, its length
 * equivalent and its confidence are assembled once, server side. This renders
 * what arrives; it never recomputes it. If a figure needs to read differently,
 * the change belongs in the query layer or it will disagree with itself.
 */
export function figureCell(run, { cls = 'fig' } = {}) {
  const box = el('span', cls);
  if (!run || run.et_figure == null) {
    box.append(el('span', 'dim', DASH));
    return box;
  }
  box.append(el('span', 'v', run.et_figure.toFixed(1)));
  if (run.et_len_vs_par != null) {
    const len = run.et_len_vs_par;
    box.append(el('span', len >= 0 ? 'up' : 'down',
      `${len >= 0 ? '+' : MINUS}${Math.abs(len).toFixed(1)}L`));
  }
  if (run.et_confidence) {
    box.append(el('span', `conf c-${run.et_confidence.toLowerCase()}`,
      run.et_confidence.toUpperCase()));
  }
  return box;
}

/* ── dates ────────────────────────────────────────────────────────────────
 * One format, defined once, used everywhere — design brief 08 §1. An ISO date
 * on screen is a storage format that escaped.
 */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `2026-07-15` → `15 Jul 2026`. */
export function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '');
  if (!m) return iso ?? DASH;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** `2026-07-15` → `15 Jul 26`, for a cell too narrow for the year in full. */
export function compactDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '');
  if (!m) return iso ?? DASH;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1].slice(2)}`;
}

/** A finishing position as it is spoken: 1st, 2nd, 3rd, 11th. */
export function ordinal(place) {
  if (place == null) return DASH;
  const n = Number(place);
  if (!Number.isFinite(n)) return String(place);
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${({ 1: 'st', 2: 'nd', 3: 'rd' })[n % 10] ?? 'th'}`;
}


/* ── HKJC deep links ──────────────────────────────────────────────────────
 * The replay URL pattern is the one the old dashboard used; design note 04 §4
 * carries it verbatim and asks for a play control per run. It needs no
 * scraping and no storage — the race identifies the video.
 */
export function replayUrl(raceDate, raceNo) {
  if (!raceDate || !raceNo) return null;
  const ymd = String(raceDate).replace(/-/g, '');
  const no = String(raceNo).padStart(2, '0');
  return 'https://racing.hkjc.com/contentAsset/videoplayer_v4/'
    + 'video-player-iframe_v4.html'
    + `?type=replay-full&date=${ymd}&no=${no}&lang=eng`
    + '&noPTbar=false&noLeading=false&videoParam=P';
}

/** The official result page for a race, for checking a figure against source. */
export function hkjcResultUrl(raceDate, raceNo, venue) {
  if (!raceDate || !raceNo) return null;
  const track = (venue ?? '').toUpperCase() === 'ST' ? 'ST' : 'HV';
  return 'https://racing.hkjc.com/racing/information/English/Racing/'
    + `LocalResults.aspx?RaceDate=${raceDate}&Racecourse=${track}`
    + `&RaceNo=${raceNo}`;
}

/** A small link that opens in a new tab, with the rel a new tab needs. */
export function externalLink(href, text, cls) {
  const a = el('a', cls, text);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}

/** Running positions as the sequence they are: `10 9 6 3 2`. */
export function positionsText(positions) {
  return (positions ?? []).length ? positions.join(' ') : DASH;
}

/** Race pace with its signed deviation — "Sl.Fast (-0.25)".
 *  Brief 08 §4: the number is what makes the label checkable. */
export const PACE_SHORT = {
  'Very Slow': 'V.Slow', Slow: 'Sl.Slow', Neutral: 'Neutral',
  Fast: 'Sl.Fast', 'Very Fast': 'V.Fast',
};

export function paceCell(pace, { cls = 'pace' } = {}) {
  const box = el('span', cls);
  if (!pace || !pace.band) {
    box.append(el('span', 'dim', DASH));
    return box;
  }
  const key = (pace.band || '').toLowerCase().replace(/[^a-z]/g, '');
  box.append(el('span', `pace-band p-${key}`, PACE_SHORT[pace.band] ?? pace.band));
  if (pace.z != null) {
    box.append(el('span', 'z', ` (${pace.z > 0 ? '+' : ''}${pace.z.toFixed(2)})`));
  }
  return box;
}

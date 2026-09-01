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

/* VETERINARY FINDINGS, which are not trip trouble and must not read as it.
 *
 * There is no vet scrape in the archive — `vet_records` holds zero rows — so
 * every piece of veterinary information the dashboard has comes from the
 * stewards' text. It used to arrive as one undifferentiated `vet_finding`;
 * `derive/tags.NAMED_VET` now names them, and a test holds these two lists
 * identical, because a horse that bled and a horse that was checked at the
 * 800m are different facts and a shared badge says they are the same one.
 */
export const VET_TAGS = new Set(['bled', 'roarer', 'lame_fore', 'lame_hind',
                                 'arrhythmia', 'mucus', 'barred',
                                 'vet_finding']);

export function tripTags(tags) {
  return (tags ?? []).filter((t) => !ROUTINE_TAGS.has(t));
}

/** A finding reads before trouble: it is about the horse rather than the run,
 *  and it is the one that changes whether you back it next time. */
export function isVetTag(tag) {
  return VET_TAGS.has(tag);
}

/** Tag text as it reads on screen: underscores are a storage detail. */
export function tagLabel(tag) {
  return (tag ?? '').replace(/_/g, ' ');
}

/** The trip tags as chips, with the commentary they came from on hover.
 *
 *  One renderer, because the filter is the part that was drifting: the Form
 *  Guide dropped `sampling` and `vet_routine` inline and kept `no_report` and
 *  `jumped_fairly`, so its TRIP flag fired on runs where the stewards said
 *  nothing happened. A rule written twice is a rule that disagrees with
 *  itself; `ROUTINE_TAGS` is the rule, and this is the only way to draw it.
 *
 *  `limit` is how many fit the cell — the rest stay on the hover rather than
 *  overflowing, which is what the caller's layout can actually promise.
 */
export function tripTagChips(tags, { comment = null, limit = 2,
                                     cls = 'trip-tag' } = {}) {
  // Findings first — a horse that bled is a different kind of fact from one
  // that met traffic, and it is the one that changes whether you back it next
  // time. Sorting them forward means the tag that survives a `limit` is the
  // one worth keeping.
  const trouble = tripTags(tags)
    .sort((a, b) => (isVetTag(b) ? 1 : 0) - (isVetTag(a) ? 1 : 0));
  if (!trouble.length) return null;
  const box = el('span', `${cls}s`);
  const title = comment || trouble.map(tagLabel).join(' · ');
  trouble.slice(0, limit).forEach((t) => {
    const chip = el('span', isVetTag(t) ? `${cls} vet` : cls, tagLabel(t));
    chip.title = title;
    box.append(chip);
  });
  if (trouble.length > limit) {
    const more = el('span', `${cls} more`, `+${trouble.length - limit}`);
    more.title = trouble.map(tagLabel).join(' · ');
    box.append(more);
  }
  return box;
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

/* A BARRIER TRIAL IS NOT A RACE, and its video is not a race replay.
 *
 * Every trial link in this dashboard was built by calling `replayUrl` with a
 * trial date and a batch number, which produces a perfectly well-formed URL for
 * a RACE that does not exist. Nothing failed loudly — the player just had
 * nothing to play.
 *
 *   race    ?type=replay-full&date=…&no=…&lang=eng&noPTbar=…&videoParam=P
 *   trial   ?type=brts&date=…&rc=…&no=…&lang=eng&rf=…&pageid=racing/local
 *
 * Two differences beyond the type. A trial needs `rc`, the racecourse, because
 * trials run at three (Sha Tin, Happy Valley, Conghua) and the date alone does
 * not say which — a race replay does not need it because a race meeting is at
 * one track. And `rf` is the page the player returns to, which HKJC moves into
 * an archive once a newer trial day is published.
 *
 * `jumpTime` is a seek offset to where the batch actually jumps, published on
 * the trials page per batch. It is a convenience, not part of addressing the
 * video: without it the clip starts from the beginning of the batch, which is
 * a slightly early start rather than a broken link. We do not store it yet.
 */
const TRIAL_COURSE = { ST: 'st', HV: 'hv', CH: 'ch' };

export function trialReplayUrl(trialDate, trialNo, venue,
                               { archived = true, jumpTime = null } = {}) {
  const rc = TRIAL_COURSE[String(venue ?? '').toUpperCase()];
  // No guessing a default course. A link to the wrong track's trial is worse
  // than no link: it plays, and it is a different set of horses.
  if (!trialDate || !trialNo || !rc) return null;
  const ymd = String(trialDate).replace(/-/g, '');
  const no = String(trialNo).padStart(2, '0');
  // The return link. Once a newer trial day is published HKJC moves the old
  // one behind the archive page, which carries the date; the live page does
  // not, because it only ever shows the newest day.
  const rf = archived
    ? 'http://racing.hkjc.com/en-us/local/information/archive/btresult'
      + `?Date=${String(trialDate).replace(/-/g, '/')}`
    : 'http://racing.hkjc.com/en-us/local/information/btresult';
  return 'https://racing.hkjc.com/contentAsset/videoplayer_v4/'
    + 'video-player-iframe_v4.html'
    + `?type=brts&date=${ymd}&rc=${rc}&no=${no}&lang=eng`
    + `&rf=${rf}&pageid=racing/local`
    + (jumpTime == null ? '' : `&jumpTime=${jumpTime}`);
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

/* ── race class ───────────────────────────────────────────────────────────
 *
 * Hong Kong grades its races on a ladder — Class 1 at the top down to Class 5
 * — and stores two values on it that are not rungs. Group races arrive as the
 * class string "0"; Griffin races arrive as the literal words "Griffin Race".
 * A page that prints `C${race_class}` therefore renders them "C0" and
 * "CGriffin Race": the first says nothing, the second is broken text. Both are
 * named here, once, so every page calls the same race the same thing.
 *
 * Colour follows the ladder rather than merely labelling it. Five steps of one
 * warm-to-cool ramp read as an ORDERING; five separate hues would read as five
 * unrelated categories, which is not what a class is. The two off-ladder
 * values carry the only strong marks, because they are the two a reader most
 * needs to catch: a Group race is the grade above the ladder, and a Griffin
 * race sits beside it — a series for horses that have no rating yet, so its
 * form cannot be read against a class at all.
 */
export const CLASS_NAME = {
  '0': 'GRP',
  'Griffin Race': 'GRIF',
};

const CLASS_TITLE = {
  GRP: 'Group race — above the class ladder',
  GRIF: 'Griffin race — for horses not yet rated, so not on the class ladder',
};

export function classLabel(raceClass) {
  if (raceClass === null || raceClass === undefined || raceClass === '') return null;
  const key = String(raceClass);
  return CLASS_NAME[key] ?? `C${key}`;
}

/** The class as a tinted chip, or null when the race has no class on record.
 *  Callers append it; a missing class prints nothing rather than a dash,
 *  because the surrounding cell already carries the track and the distance. */
export function classCell(raceClass, { cls = 'cl' } = {}) {
  const label = classLabel(raceClass);
  if (label === null) return null;
  const key = String(raceClass);
  // The tone name never comes from raw data — an unexpected class string would
  // otherwise become a CSS class name of its own and silently match nothing.
  const tone = key === '0' ? 'group'
    : CLASS_NAME[key] ? 'griffin'
      : /^[1-5]$/.test(key) ? `c${key}` : 'other';
  const chip = el('span', `${cls} ${cls}-${tone}`, label);
  chip.title = CLASS_TITLE[label] ?? `Class ${key}`;
  return chip;
}

/** A run's conditions as text, for tooltips, popover titles and any row too
 *  narrow for chips. Three files each had their own copy of this and each
 *  wrote `C${race_class}`, so a Group race read "C0" and a Griffin race read
 *  "CGriffin Race" in all three. One copy, calling `classLabel`. */
export function conditionLabel(run) {
  return [run.venue, run.course, run.distance ? `${run.distance}m` : null,
          run.going, classLabel(run.race_class)]
    .filter(Boolean).join(' ');
}

/* ── how fast away ────────────────────────────────────────────────────────
 *
 * ESZ: the runner's early sectional standardised inside its OWN race.
 * Positive is faster away — `early_pace` is a time, and query/pace.py flips
 * the sign there so no reader ever has to hold "smaller is quicker" in mind.
 *
 * Standardised within the race and not across the archive, because a field's
 * early sectional is dominated by distance and grade: compared across races
 * every sprinter reads as fast away and every stayer as slow, which says
 * nothing about how either began relative to what it was beaten away by.
 */
export function eszCell(z, { cls = 'esz' } = {}) {
  if (z === null || z === undefined) return el('span', `${cls} dim`, DASH);
  const sign = z > 0 ? '+' : z < 0 ? MINUS : '';
  const cell = el('span', `${cls} ${z >= 0 ? 'esz-fast' : 'esz-slow'}`,
    `${sign}${Math.abs(z).toFixed(2)}`);
  cell.title = z >= 0
    ? `jumped ${Math.abs(z).toFixed(2)} sd FASTER than this race\u2019s field`
    : `jumped ${Math.abs(z).toFixed(2)} sd SLOWER than this race\u2019s field`;
  return cell;
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


/* ── the window a figure is measured over ─────────────────────────────────
 *
 * Daily, weekly, monthly, seasonal, lifetime — the same five on every page
 * that shows a performance figure, resolved SERVER SIDE by query/period.py.
 * The browser never computes a boundary: the Hong Kong season runs September
 * to July, and a page doing its own date arithmetic would cut one season in
 * half and mix two together while looking entirely reasonable.
 *
 * `onPick` is handed the period name. The caller re-fetches; nothing here
 * touches data.
 */
export const PERIODS = [
  ['day', 'DAY'], ['week', 'WEEK'], ['month', 'MONTH'],
  ['season', 'SEASON'], ['lifetime', 'LIFETIME'],
];

export function periodPicker(current, onPick, { label = 'OVER', window: win = null,
                                                cls = '', seasons = null,
                                                season = null,
                                                onSeason = null } = {}) {
  const bar = el('div', `period-pick ${cls}`.trim());
  bar.append(el('span', 'lab', label));
  PERIODS.forEach(([key, text]) => {
    const on = (current ?? 'lifetime') === key;
    const b = el('button', `p-chip${on ? ' on' : ''}`, text);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(on));
    b.addEventListener('click', () => onPick(key));
    bar.append(b);
  });

  // WHICH season, once SEASON is the window. Without this, "season" can only
  // ever mean the one containing the meeting on screen — which is right while
  // reviewing a past meeting and exactly wrong in September, when the last
  // meeting on record is still last season's and the question is about the one
  // that just opened.
  if (current === 'season' && seasons?.length && onSeason) {
    const pick = el('span', 'seasons');
    seasons.forEach((s) => {
      const on = season === s.season;
      const b = el('button', `s-chip${on ? ' on' : ''}`, s.label);
      b.type = 'button';
      // Say what is in it. An empty season is a fresh slate, not a fault, and
      // the count is what tells the two apart before you click.
      b.title = s.bets
        ? `${s.bets} bets · ${s.meetings} meetings`
        : s.current ? 'the season now open — nothing bet in it yet'
          : 'no bets recorded in this season';
      if (!s.bets) b.classList.add('empty');
      if (s.current) b.classList.add('current');
      b.addEventListener('click', () => onSeason(s.season));
      pick.append(b);
    });
    bar.append(pick);
  }

  // The bounds, always. "SEASON" is a word; "SEASON 2025/26" is checkable, and
  // a figure copied off the page can be checked again later against the same
  // dates rather than against whatever the word means that month.
  if (win?.label) bar.append(el('span', 'bounds', win.label));
  return bar;
}

/* ── which book ───────────────────────────────────────────────────────────
 * One book, two ledgers. The blackbook is shared; the money is not.
 */
export const ACCOUNTS = [
  ['', 'BOTH'], ['brett', 'BRETT'], ['kelvin', 'KELVIN'],
];

export function accountPicker(current, onPick, { label = 'LEDGER' } = {}) {
  const bar = el('div', `acct-pick${current ? ` acct-${current}` : ''}`);
  bar.append(el('span', 'lab', label));
  ACCOUNTS.forEach(([key, text]) => {
    const on = (current ?? '') === key;
    const b = el('button', `a-chip${on ? ' on' : ''}`, text);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(on));
    b.addEventListener('click', () => onPick(key || null));
    bar.append(b);
  });
  return bar;
}

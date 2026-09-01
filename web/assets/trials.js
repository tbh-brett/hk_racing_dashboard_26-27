/* Trials — ported from web/design-source/Trials.dc.html.
 *
 * Three views: the recent batches, the standouts feed, and what the rating
 * actually predicts.
 *
 * The artboard states the design twice, and the second time as the reason the
 * page is worth having:
 *
 *     "One engine, two surfaces: the same finish + margin + comment rating
 *      used inline on a horse's own trial line, aggregated here as a live feed
 *      — not a separately curated list."
 *
 * So nothing here rates a trial. The mark comes from the API, which calls
 * derive/trial_quality — the same function the Form Guide's inline band reads
 * through. A second rating written in this file would drift from that one
 * within a season, and the two surfaces would quietly disagree about the same
 * horse.
 *
 * The CALIBRATION view exists because a rating nobody can check is a rating
 * nobody should act on. It recomputes, live, what each band went on to do at
 * the races: STANDOUT 21.0% next-start wins against a baseline of 8.2%,
 * NEGATIVE 3.2%, and UNTESTED on the baseline at 7.7% — which is the intent,
 * not a shortcoming, because a trial the horse was not asked to win says
 * nothing about it either way.
 */
import { api, num } from './api.js';
import { el, $, DASH, renderNav, tripTagChips,
         replayUrl, trialReplayUrl, externalLink, compactDate,
         ordinal } from './vocab.js';
import { context } from './context.js';
import { renderReview, trialSubject, loadTags } from './review.js';
import { install as installPalette } from './palette.js';


const VIEWS = [['batches', 'BATCHES'], ['standouts', 'STANDOUTS'],
               ['calibration', 'DOES IT HOLD']];

const BANDS = ['STANDOUT', 'POSITIVE', 'NEUTRAL', 'NEGATIVE', 'UNTESTED'];
const VENUES = [['all', 'ALL'], ['ST', 'ST'], ['HV', 'HV']];

const COLS = [
  ['DR', 'r'], ['', ''], ['P', 'r'], ['HORSE', ''], ['GEAR', ''],
  ['JOCKEY', ''], ['POSITIONS', ''], ['MGN', 'r'], ['TIME', 'r'],
  ['COMMENT', ''], ['NEXT ACTUAL START', ''],
];

const state = {
  view: 'batches', batches: [], standouts: null, calibration: null,
  search: '', band: null, venue: 'all',
  // The trial morning on screen. Null is the rolling feed of the most recent
  // batches, which is what the page opened on before there was any way to
  // choose — and remains the default, because the newest trials are what the
  // page is usually for.
  days: [], day: null,
  // Which trial rows are expanded, and each horse's recent form once fetched.
  // Keyed by horse + batch, because the same horse can appear in two mornings
  // and they are different rows with different notes.
  open: new Set(), form: {},
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderViewToggle() {
  $('view-toggle').replaceChildren(...VIEWS.map(([key, label]) => {
    const b = el('button', null, label);
    b.setAttribute('aria-pressed', String(state.view === key));
    b.addEventListener('click', () => { state.view = key; render(); });
    return b;
  }));
}

function pct(v, digits = 1) {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(digits)}%`;
}

function qMark(band, mark) {
  return el('span', `q q-${band.toLowerCase()}`, mark);
}

/** What the horse did at the RACES after this trial. Never another trial. */
function nextStart(nxt) {
  const box = el('div', 'next');
  if (!nxt) {
    box.append(el('span', 'none', 'no start since'));
    return box;
  }
  const placed = nxt.place <= (nxt.field_size >= 7 ? 3 : 2);
  const cls = nxt.place === 1 ? 'won' : placed ? 'plc' : null;
  // No padding spaces: the cell is a flex row and would collapse them, which
  // ran the date into the finishing position. The gap is the stylesheet's.
  // The date and the ordinal both come from vocab.js — the design reads
  // "28 May · 5th/11", and every other page already spells them that way.
  box.append(el('span', null, compactDate(nxt.race_date)));
  box.append(el('span', cls, `${ordinal(nxt.place)}/${nxt.field_size}`));
  box.append(el('span', null,
    `${nxt.venue} ${nxt.distance ?? ''}m${nxt.race_class ? ` Cl${nxt.race_class}` : ''}`));
  box.title = `${nxt.race_date} R${nxt.race_no} — finished ${nxt.place} of `
    + `${nxt.field_size}${nxt.win_odds ? ` at ${nxt.win_odds}` : ''}`;

  // "It trialled well and then ran 7th" is a different fact from "it trialled
  // well, ran 7th, and was checked at the 800m". The second is what keeps a
  // horse worth following, so the tags travel with the result rather than
  // being a click away on another page.
  // The tags are the part that gives way when the column is narrow, which is
  // why they live in their own shrinking box. Losing "wide no cover" to an
  // ellipsis costs a detail the hover still carries; losing the play control
  // costs the thing the column is for.
  const tags = tripTagChips(nxt.tags, { comment: nxt.comment });
  if (tags) box.append(tags);

  // And the footage of it, because a trial mark is a claim about what the
  // horse can do and the next start is where that claim gets tested. It is
  // pinned to the end of the row rather than queued behind the tags: a
  // replay that scrolls out of the cell is a replay nobody watches.
  const url = replayUrl(nxt.race_date, nxt.race_no);
  if (url) {
    const play = externalLink(url, '▶', 'ns-play');
    play.title = `replay — ${nxt.race_date} race ${nxt.race_no}`;
    play.addEventListener('click', (e) => e.stopPropagation());
    box.append(play);
  }
  return box;
}

/* ── filters ─────────────────────────────────────────────────────────────── */

function chip(label, on, onClick, count) {
  const b = el('button', `chip${on ? ' on' : ''}`);
  b.append(document.createTextNode(label));
  if (count !== undefined) b.append(el('span', 'n', ` ${count}`));
  b.addEventListener('click', onClick);
  return b;
}

/* ── the trial morning ────────────────────────────────────────────────────
 * A trial day is not a meeting: 159 of them in the archive, and most fall on
 * mornings with no race card, so Layer 1 cannot reach them. Brief 08 §1's ban
 * on per-page date pickers is about four pages disagreeing over which MEETING
 * is on screen; this is a different calendar, and having no control for it is
 * what made the whole archive unreachable from this page.
 */
function renderDayPicker() {
  const sel = $('day-select');
  sel.replaceChildren();
  const rolling = el('option', null, `MOST RECENT · ${state.batches.length} batches`);
  rolling.value = '';
  sel.append(rolling);
  state.days.forEach((d) => {
    const o = el('option', null,
      `${compactDate(d.trial_date)} · ${d.venues} · ${d.batches} batch`
      + `${d.batches === 1 ? '' : 'es'} · ${d.runners} runs`);
    o.value = d.trial_date;
    sel.append(o);
  });
  sel.value = state.day ?? '';

  const at = state.days.findIndex((d) => d.trial_date === state.day);
  // The list is newest first, so "earlier" is forward through it.
  $('day-prev').disabled = at >= state.days.length - 1;
  $('day-next').disabled = state.day === null || at <= 0;
  $('day-all').classList.toggle('on', state.day === null);
}

function wireDayPicker() {
  $('day-select').addEventListener('change', (e) => {
    state.day = e.target.value || null;
    load();
  });
  const step = (by) => {
    const at = state.days.findIndex((d) => d.trial_date === state.day);
    // From the rolling feed, stepping back lands on the newest morning.
    const next = state.day === null ? (by > 0 ? 0 : -1) : at + by;
    if (next < 0 || next >= state.days.length) return;
    state.day = state.days[next].trial_date;
    load();
  };
  $('day-prev').addEventListener('click', () => step(1));
  $('day-next').addEventListener('click', () => step(-1));
  $('day-all').addEventListener('click', () => { state.day = null; load(); });
}

function renderChips() {
  const counts = new Map();
  state.batches.forEach((b) => b.runners.forEach((r) => {
    counts.set(r.quality_band, (counts.get(r.quality_band) ?? 0) + 1);
  }));
  $('band-chips').replaceChildren(...BANDS.map((band) =>
    chip(band, state.band === band, () => {
      state.band = state.band === band ? null : band;
      render();
    }, counts.get(band) ?? 0)));
  $('venue-chips').replaceChildren(...VENUES.map(([key, label]) =>
    chip(label, state.venue === key, () => {
      state.venue = key;
      load();
    })));
}

function matches(r) {
  if (state.band && r.quality_band !== state.band) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  return `${r.horse_name} ${r.comment ?? ''}`.toLowerCase().includes(q);
}

/* ── batches ─────────────────────────────────────────────────────────────── */

function rowKey(r) {
  return `${r.trial_date}:${r.trial_no}:${r.horse_name}`;
}

/** The horse's recent RACES, under the trial row that prompted the question.
 *
 *  A trial mark is a claim about what a horse can do; its form is the record
 *  of what it has done, and putting them one above the other is the whole
 *  reason to expand a row. The columns are the Form Guide's, because it is the
 *  same fact and it should not read differently here.
 */
function runnerDetail(r) {
  const box = el('div', 'tr-detail');

  const head = el('div', 'd-head');
  head.append(el('span', 'k', 'LAST SIX'));
  if (r.blackbook) {
    const chip = el('span', 'booked', `IN THE BOOK · ${r.blackbook.status}`);
    chip.title = r.blackbook.reasoning || 'no reason recorded';
    head.append(chip);
  }
  head.append(el('span', 'right'));
  const pen = el('button', `icon${r.note ? ' has' : ''}`, '✎');
  pen.title = r.note ? r.note.note : 'note on this trial';
  pen.addEventListener('click', (e) => { e.stopPropagation(); showNote(e, r); });
  head.append(pen);
  box.append(head);

  if (r.note) box.append(el('div', 'd-note', r.note.note));

  const runs = state.form[rowKey(r)];
  if (!runs) {
    box.append(el('div', 'd-empty', 'loading…'));
    loadForm(r, box);
    return box;
  }
  box.append(formTable(runs));
  return box;
}

function formTable(runs) {
  if (!runs.length) return el('div', 'd-empty', 'NO RACES ON RECORD');
  const tbl = el('div', 'd-runs');
  const head = el('div', 'd-run d-run-head');
  ['DATE', 'TRK DIST GOING CL', 'FIN', 'FIGURE', 'SP', 'TRIP']
    .forEach((h, i) => head.append(el('span', i >= 2 ? 'r' : null, h)));
  tbl.append(head);
  runs.forEach((f) => {
    const line = el('div', 'd-run');
    line.append(el('span', null, compactDate(f.race_date)));
    line.append(el('span', 'cond', [
      f.venue, f.distance ? `${f.distance}m` : null, f.going,
      f.race_class ? `C${f.race_class}` : null,
    ].filter(Boolean).join(' ')));
    const fin = el('span', 'r fin', String(f.place ?? f.place_code ?? DASH));
    if (f.place === 1) fin.classList.add('won');
    line.append(fin);
    const fig = el('span', 'r fig', f.et_figure == null ? DASH
      : num(f.et_figure, 0));
    if (f.et_figure != null) {
      fig.classList.add(f.et_figure >= 100 ? 'above' : 'below');
      if (f.figure_display) fig.title = f.figure_display;
    }
    line.append(fig);
    line.append(el('span', 'r', f.win_odds ? num(f.win_odds, 1) : DASH));
    const trip = el('span', 'trip');
    const chips = tripTagChips(f.tags,
      { comment: f.incident_comment || f.running_comment, limit: 3 });
    trip.append(chips ?? el('span', 'dim', DASH));
    line.append(trip);
    // Watch it, from the row that made you ask.
    const url = replayUrl(f.race_date, f.race_no);
    if (url) {
      const play = externalLink(url, '▶', 'd-play');
      play.title = `replay — ${f.race_date} race ${f.race_no}`;
      play.addEventListener('click', (e) => e.stopPropagation());
      line.append(play);
    }
    tbl.append(line);
  });
  return tbl;
}

async function loadForm(r, box) {
  try {
    const data = await api.horse(r.horse_name, 6);
    state.form[rowKey(r)] = data.runs;
    // The row may have been collapsed, or another opened, while this was in
    // flight — writing into a detached node would leave the row on screen
    // saying "loading…" for ever.
    if (!box.isConnected) return;
    box.querySelector('.d-empty')?.remove();
    box.append(formTable(data.runs));
  } catch (e) {
    if (!box.isConnected) return;
    box.querySelector('.d-empty')?.replaceChildren(
      document.createTextNode(`unavailable — ${e.message}`));
  }
}

/** The note form — the same one the Form Guide and Results use. */
function showNote(event, r) {
  renderReview($('popover'), {
    horseName: r.horse_name,
    subject: trialSubject(r),
    existingNote: r.note,
    booked: r.blackbook,
    onSaved: (saved) => { r.note = saved; hidePopover(); render(); },
    onPromoted: (entry) => { r.blackbook = entry; hidePopover(); render(); },
    onClose: hidePopover,
  });
  const pop = $('popover');
  pop.hidden = false;
  const rect = event.currentTarget.getBoundingClientRect();
  const w = 344;
  pop.style.left = `${Math.min(rect.left, window.innerWidth - w - 12)}px`;
  pop.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 220)}px`;
}

function hidePopover() {
  $('popover').hidden = true;
}

function runnerRow(r) {
  const row = el('div', 'tr-row');
  const key = rowKey(r);
  if (state.open.has(key)) row.classList.add('open');
  row.addEventListener('click', () => {
    if (state.open.has(key)) state.open.delete(key);
    else state.open.add(key);
    render();
  });
  // Draw first, as the design has it: a trial is barrier practice as much as
  // it is a time, and the gate the horse came out of is the context for the
  // positions three columns along.
  row.append(el('div', 'r', r.draw === null || r.draw === undefined
    ? DASH : String(r.draw)));
  row.append(qMark(r.quality_band, r.quality_mark));
  row.append(el('div', 'r', r.place === null ? DASH : String(r.place)));
  const horse = el('div', 'horse');
  horse.append(document.createTextNode(r.horse_name));
  if (r.blackbook) {
    const chip = el('span', 'bb', 'BB');
    chip.title = `in the blackbook since ${r.blackbook.added_date}`;
    horse.append(chip);
  }
  if (r.note) {
    const pen = el('span', 'noted', '✎');
    pen.title = r.note.note;
    horse.append(pen);
  }
  horse.title = `${r.quality_band}${r.quality_reasons.length
    ? ` — ${r.quality_reasons.join('; ')}` : ''}`;
  row.append(horse);
  row.append(el('div', null, r.gear || DASH));
  const jockey = el('div', 'jockey', r.jockey || DASH);
  // Who rode a trial is how you read the intent behind it, and the trainer is
  // the other half of that; the design puts the rider in the column and the
  // stable on the hover rather than spending a column on it.
  if (r.trainer) jockey.title = `trained by ${r.trainer}`;
  row.append(jockey);
  row.append(el('div', null, r.running_positions.join(' ') || DASH));
  // Margin is shown because it is useful context on a row. It does not move
  // the band: measured, it carries no signal once the finish is held constant.
  // The winner reads WON rather than 0.0L — a zero margin is arithmetic, and
  // the design says the fact instead.
  row.append(el('div', r.place === 1 ? 'r won' : 'r',
    r.place === 1 ? 'WON'
      : r.margin === null || r.margin === undefined ? DASH
        : `${num(r.margin, 1)}L`));
  row.append(el('div', 'r',
    r.finish_time === null ? DASH : num(r.finish_time, 2)));
  const comment = el('div', 'comment', r.comment ?? DASH);
  comment.title = r.comment ?? '';
  row.append(comment);
  row.append(nextStart(r.next_start));
  return row;
}

function renderBatches() {
  renderChips();
  renderDayPicker();
  const host = $('batches');
  host.replaceChildren();
  let shown = 0;
  let total = 0;
  state.batches.forEach((b) => {
    const runners = b.runners.filter(matches);
    total += b.runners.length;
    if (!runners.length) return;
    shown += runners.length;
    const box = el('div', 'batch');
    const head = el('div', 'batch-head');
    // The date as it is spoken, through the shared formatter. An ISO date on
    // screen is a storage format that escaped — brief 08 §1, and this page was
    // the one still printing one.
    head.append(el('span', 'd', compactDate(b.trial_date)));
    head.append(el('span', 'no', `T${b.trial_no}`));
    head.append(el('span', null, `${b.venue} ${b.surface}`));
    // Going was stored on every trial row and never read. A trial time means
    // nothing without the surface it was run on.
    if (b.going) head.append(el('span', 'going', b.going));
    head.append(el('span', 'k', 'RUNNERS'));
    head.append(el('span', null, String(b.field_size)));
    head.append(el('span', 'k', 'WIN TIME'));
    head.append(el('span', null,
      b.winning_time === null ? DASH : num(b.winning_time, 2)));
    if (b.section_times.length) {
      head.append(el('span', 'k', 'SPLITS'));
      // One set of sectionals per trial, repeated on every row by HKJC. Shown
      // once, on the batch, rather than four times as if measured per runner.
      head.append(el('span', 'splits',
        b.section_times.map((t) => num(t, 1)).join(' · ')));
    }
    // The distance IS published, in the batch header — the legacy import
    // dropped it, which is why this line used to say there wasn't one. A
    // missing distance now means this batch's header was not captured, not
    // that HKJC withheld it, so it says which.
    if (b.distance) {
      head.append(el('span', 'dist', `${b.distance}m`));
      head.append(el('span', 'right'));
    } else {
      head.append(el('span', 'right', 'no distance captured for this batch'));
    }
    // The footage of the batch itself. A trial video is addressed differently
    // from a race replay — different type, and it needs the racecourse, since
    // trials run at three of them and the date does not say which.
    const turl = trialReplayUrl(b.trial_date, b.trial_no, b.venue,
                                { archived: b.archived });
    if (turl) {
      const play = externalLink(turl, '▶ REPLAY', 'batch-replay');
      play.title = `trial replay — ${b.trial_date} batch ${b.trial_no}`;
      head.append(play);
    }
    box.append(head);

    const cols = el('div', 'tr-head');
    COLS.forEach(([label, cls]) => cols.append(el('div', cls || null, label)));
    box.append(cols);
    runners.forEach((r) => {
      box.append(runnerRow(r));
      if (state.open.has(rowKey(r))) box.append(runnerDetail(r));
    });
    host.append(box);
  });
  if (!shown) {
    host.replaceChildren(el('div', 'no-match',
      state.search ? `No trial run matches "${state.search}".`
        : 'NO TRIAL CLEARS THIS QUALITY FILTER IN THE CURRENT SLICE.'));
  }
  $('match-count').textContent = `${shown} of ${total}`;
  const active = $('active-filters');
  active.replaceChildren(el('span', 'lab', 'SHOWING'));
  active.append(el('span', 'none',
    `${state.batches.length} most recent batches`
    + (state.venue === 'all' ? '' : ` at ${state.venue}`)));
}

/* ── standouts ───────────────────────────────────────────────────────────── */

function renderStandouts() {
  const s = state.standouts;
  const host = $('standouts');
  if (!s) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const note = el('div', 'so-note');
  note.append(el('b', null, `${s.shown} TRIALS SHOWN. `));
  note.append(document.createTextNode(
    'One engine, two surfaces: the same finish and comment rating used inline '
    + "on a horse's own trial line, aggregated here as a live feed — not a "
    + `separately curated list. ${s.considered} trials since ${s.since} were `
    + 'rated; these are the ones that came out '
    + `${s.bands.join(' or ').toLowerCase()}.`));
  host.append(note);

  if (!s.runs.length) {
    host.append(el('div', 'no-match',
      'NO TRIAL CLEARS THIS QUALITY FILTER IN THE CURRENT SLICE.'));
    return;
  }

  s.runs.filter(matches).forEach((r) => {
    const box = el('div', 'standout');
    const line = el('div', 'so-line');
    line.append(qMark(r.quality_band, r.quality_mark));
    line.append(el('span', 'name', r.horse_name));
    line.append(el('span', 'where',
      `${r.trial_date} T${r.trial_no} · ${r.venue} ${r.surface} · `
      + `finished ${r.place ?? DASH} of ${r.field_size}`
      + (r.margin ? ` · ${num(r.margin, 1)}L` : '')));
    if (r.blackbook) {
      const badge = el('span', 'so-badge',
        `IN THE BLACKBOOK · ${r.blackbook.status}`);
      badge.title = `added ${r.blackbook.added_date}`;
      line.append(badge);
    }
    line.append(el('span', 'right'));
    line.append(nextStart(r.next_start));
    box.append(line);
    // The reasons, because a mark nobody can check is a mark nobody should
    // act on.
    box.append(el('div', 'so-reasons', r.quality_reasons.join(' · ')));
    if (r.comment) box.append(el('div', 'so-comment', r.comment));

    // What the REST of the batch did next is the check on the rating: a
    // standout out of a batch whose other five all won next start says more
    // about the batch than about the horse.
    if (r.batch_next && r.batch_next.length) {
      const rest = el('div', 'so-batch');
      rest.append(el('span', 'k', 'WHAT THE REST OF THE BATCH DID NEXT'));
      r.batch_next.forEach((o) => {
        const row = el('div', 'row');
        row.append(el('span', null, o.place === null ? DASH : String(o.place)));
        row.append(el('span', 'n', o.horse_name));
        row.append(el('span', null, '→'));
        if (!o.next_start) {
          row.append(el('span', null, 'no start since'));
        } else {
          const placed = o.next_start.place
            <= (o.next_start.field_size >= 7 ? 3 : 2);
          row.append(el('span', o.next_start.place === 1 ? 'won' : null,
            `${o.next_start.place}/${o.next_start.field_size}`
            + `${placed && o.next_start.place !== 1 ? ' placed' : ''}`
            + ` ${o.next_start.race_date}`));
        }
        rest.append(row);
      });
      box.append(rest);
    }
    host.append(box);
  });
}

/* ── calibration ─────────────────────────────────────────────────────────── */

function renderCalibration() {
  const c = state.calibration;
  const host = $('calibration');
  if (!c) { host.replaceChildren(el('div', 'empty-line', 'LOADING')); return; }
  host.replaceChildren();

  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'DOES THE RATING HOLD'));
  head.append(el('span', 'sub',
    'WHAT EACH BAND WENT ON TO DO AT THE RACES · RECOMPUTED, NOT QUOTED'));
  host.append(head);

  const base = c.overall.next_win_rate ?? 0;
  const top = Math.max(base, ...Object.values(c.bands)
    .map((v) => v.next_win_rate ?? 0)) || 1;

  const table = el('table', 'cal-tab');
  const hr = el('tr');
  [['BAND', ''], ['TRIALS', 'r'], ['WITH A NEXT START', 'r'],
   ['NEXT-START WIN %', 'r'], ['', ''], ['NEXT-START PLACE %', 'r']]
    .forEach(([label, cls]) => hr.append(el('th', cls || null, label)));
  const thead = el('thead');
  thead.append(hr);
  table.append(thead);

  const body = el('tbody');
  const row = (label, v, cls) => {
    const tr = el('tr', cls ?? null);
    tr.append(el('td', 'label', label));
    tr.append(el('td', 'r', v.trials.toLocaleString()));
    tr.append(el('td', 'r', v.with_next.toLocaleString()));
    const win = el('td', 'r', pct(v.next_win_rate));
    if (v.next_win_ci && v.next_win_ci.length === 2) {
      win.title = `95% CI [${pct(v.next_win_ci[0])}, ${pct(v.next_win_ci[1])}]`
        + (v.clears_baseline ? ' — excludes the baseline'
          : ' — contains the baseline');
    }
    tr.append(win);
    const bar = el('td');
    const track = el('span', 'bar');
    // Colour is earned by an interval that excludes the baseline, not by
    // landing on one side of it: NEUTRAL at 7.9% against a baseline of 8.2%
    // is the same number.
    const fill = el('i', !v.clears_baseline ? 'flat'
      : v.next_win_rate > base ? 'up' : 'down');
    fill.style.width = `${((v.next_win_rate ?? 0) / top) * 100}%`;
    track.append(fill);
    bar.append(track);
    tr.append(bar);
    tr.append(el('td', 'r', pct(v.next_place_rate)));
    return tr;
  };
  c.order.forEach((band) => body.append(row(band, c.bands[band])));
  body.append(row('ALL TRIALS · BASELINE', c.overall, 'baseline'));
  table.append(body);
  const box = el('div', 'table-box');
  box.append(table);
  host.append(box);

  host.append(el('div', 'caveat',
    'The rating is only worth showing if the bands separate, so this is here '
    + 'rather than a claim that they do. UNTESTED landing on the baseline is '
    + 'the intent, not a shortcoming: a trial the horse was not asked to win '
    + 'says nothing about it either way, and every phrase in that family was '
    + 'measured on the baseline before being treated as one.'));
  host.append(el('div', 'caveat',
    'Margin is shown on every row and is not in the score. Holding the finish '
    + 'constant, mid-pack finishers go 7.2% next-start wins within two lengths '
    + 'of the winner and 5.7% at fourteen or more — a trial winner is often '
    + 'not extended, so the field’s margin measures how hard the winner '
    + 'was ridden more than how well the rest went.'));
}

/* ── render ──────────────────────────────────────────────────────────────── */

function renderSummary() {
  const host = $('tr-sum');
  host.replaceChildren();
  const c = state.calibration;
  if (!c) return;
  const stat = (value, label) => {
    const box = el('span');
    box.append(el('b', null, value));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  host.append(stat(c.overall.trials.toLocaleString(), 'TRIALS RATED'));
  const so = c.bands.STANDOUT;
  host.append(stat(pct(so.next_win_rate), 'STANDOUT NEXT-WIN'));
  host.append(stat(pct(c.overall.next_win_rate), 'BASELINE'));
}

function render() {
  renderViewToggle();
  renderSummary();
  VIEWS.forEach(([key]) => { $(`view-${key}`).hidden = state.view !== key; });
  const filtersApply = state.view !== 'calibration';
  document.querySelector('.filter-bar').hidden = !filtersApply;
  document.querySelector('.chip-bar').hidden = !filtersApply;
  if (state.view === 'batches') renderBatches();
  if (state.view === 'standouts') renderStandouts();
  if (state.view === 'calibration') renderCalibration();
}

function wireSearch() {
  const input = $('search');
  const clear = $('clear-search');
  input.addEventListener('input', () => {
    state.search = input.value;
    clear.hidden = !input.value;
    render();
  });
  clear.addEventListener('click', () => {
    input.value = '';
    state.search = '';
    clear.hidden = true;
    render();
  });
}

async function load() {
  const venue = state.venue === 'all' ? undefined : state.venue;
  // A named morning asks for ALL of that morning's batches; the rolling feed
  // asks for the newest twelve. Twelve on a nine-batch morning would silently
  // pull in the previous one and show two dates under one heading.
  const [batches, days, standouts, calibration] = await Promise.all([
    api.trials(state.day ? 60 : 12, venue, state.day),
    api.trialDays(venue),
    api.trialStandouts(),
    api.trialCalibration(),
  ]);
  state.batches = batches.batches;
  state.days = days.days;
  state.standouts = standouts;
  state.calibration = calibration;
  render();
}

async function boot() {
  renderNav($('nav'), 'trials.html');
  wireSearch();
  wireDayPicker();
  // The tag vocabulary the promote form offers, loaded once.
  loadTags();
  // A trial morning is addressable, like the meeting is: ?day=2026-08-21
  // restores the view, so a note can link back to the trial it came from.
  state.day = new URLSearchParams(window.location.search).get('day');
  installPalette();
  await context.init();
  render();
  await load();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});

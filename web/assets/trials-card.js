/* trials-card.js — the Trials page's DECLARED view: every declared runner's
 * recent trials, race by race, for the meeting in the header
 * (hkrd/query/trial_card.py).
 *
 * Asked for on 2026-10-08. The batch view is laid out by trial morning; the
 * question before a meeting is laid out by race — which of these runners
 * trialled since they last ran, have I watched those, and what did I make of
 * them. The owner's trial note is the one note in the book with a record clear
 * of the price (docs/book-tiers.md), so every trial line carries its replay
 * and the same note form the batch view opens, and TO WATCH narrows the view
 * to the trials since the last run that have no note yet.
 *
 * Nothing here rates a trial: the mark comes from the server, from the engine
 * every surface reads.
 */
import { api, num } from './api.js';
import { el, DASH, compactDate, ordinal, trialReplayUrl, externalLink,
         classCell } from './vocab.js';
import { renderReview, trialSubject } from './review.js';
import { isSystem } from './book-origin.js';
import { tierChip, tapsText } from './book-tier.js';

/** The view's own state. `scope` is 'since' (trials since the last run, the
 *  default: what the last run cannot have told anyone) or 'recent' (the last
 *  four, to read a preparation as a whole). */
export const card = {
  date: null, data: null, error: null, loading: false,
  race: 'all', scope: 'since', watch: false, open: null,
};

export async function loadCard(date, { force = false } = {}) {
  if (!date) return;
  if (!force && card.date === date && (card.data || card.error)) return;
  card.date = date;
  card.loading = true;
  try {
    card.data = await api.trialsForMeeting(date);
    card.error = null;
  } catch (err) {
    card.data = null;
    card.error = err.message;
  } finally {
    card.loading = false;
  }
}

/** The strip's counts for this view: what is on the card, not the archive. */
export function cardStats() {
  const c = card.data?.counts;
  if (!c) return [];
  return [[c.runners, 'DECLARED'], [c.trialled, 'TRIALLED SINCE LAST RUN'],
          [c.noted, 'NOTED'], [c.to_watch, 'TO WATCH'], [c.standout, 'STANDOUT']];
}

function chip(label, on, onClick, count) {
  const b = el('button', `chip${on ? ' on' : ''}`);
  b.append(document.createTextNode(label));
  if (count !== undefined) b.append(el('span', 'n', ` ${count}`));
  b.addEventListener('click', onClick);
  return b;
}

function shown(t) {
  if (card.scope === 'since' && !t.since_last) return false;
  if (card.watch && (!t.since_last || t.owner_note)) return false;
  return true;
}

function key(x, t) {
  return `${x.horse_name}|${t.trial_date}|${t.venue}|${t.trial_no}`;
}

function controls(races, redraw) {
  const bar = el('div', 'tc-controls');
  bar.append(el('span', 'chip-label', 'RACE'));
  bar.append(chip('ALL', card.race === 'all', () => { card.race = 'all'; redraw(); }));
  races.forEach((r) => bar.append(chip(`R${r.race_no}`, card.race === r.race_no,
    () => { card.race = r.race_no; redraw(); }, r.trialled)));
  bar.append(el('span', 'chip-label bordered', 'TRIALS'));
  bar.append(chip('SINCE LAST RUN', card.scope === 'since',
    () => { card.scope = 'since'; redraw(); }));
  bar.append(chip(`LAST ${card.data.recent}`, card.scope === 'recent',
    () => { card.scope = 'recent'; card.watch = false; redraw(); }));
  bar.append(el('span', 'chip-label bordered', 'SHOW'));
  const w = chip('TO WATCH', card.watch, () => {
    card.watch = !card.watch;
    if (card.watch) card.scope = 'since';
    redraw();
  }, card.data.counts.to_watch);
  w.title = 'trials since the last run that you have not written a note on yet';
  bar.append(w);
  return bar;
}

function lastRun(x) {
  const lr = x.last_run;
  if (!lr) return el('span', 'tc-debut', 'DEBUT');
  const s = el('span', 'tc-last',
    `last ${ordinal(lr.place)}/${lr.field_size} · ${compactDate(lr.race_date)} · ${lr.days_ago}d`);
  s.title = `${lr.starts} start${lr.starts === 1 ? '' : 's'} in Hong Kong; last `
    + `${lr.race_date} race ${lr.race_no}`;
  return s;
}

function trialLine(x, t, redraw) {
  const k = key(x, t);
  const row = el('div', `tc-trial${t.since_last ? '' : ' before'}${card.open === k ? ' open' : ''}`);
  row.append(el('span', 'd', compactDate(t.trial_date)));
  row.append(el('span', 'where', `${t.venue} T${t.trial_no}`));
  const url = trialReplayUrl(t.trial_date, t.trial_no, t.venue, { archived: t.archived });
  if (url) {
    const play = externalLink(url, '▶', 'tc-play');
    play.title = `trial replay — ${t.trial_date} batch ${t.trial_no} at ${t.venue}`;
    row.append(play);
  } else {
    row.append(el('span'));
  }
  const q = el('span', `q q-${t.quality_band.toLowerCase()}`, t.quality_mark);
  q.title = `${t.quality_band}${t.quality_reasons?.length ? ` — ${t.quality_reasons.join('; ')}` : ''}`;
  row.append(q);
  row.append(el('span', `fin${t.place === 1 ? ' won' : ''}`,
    t.place === null ? DASH : `${t.place}/${t.field_size}`));
  row.append(el('span', 'mgn', t.place === 1 ? 'WON'
    : t.margin === null || t.margin === undefined ? DASH : `${num(t.margin, 1)}L`));
  row.append(el('span', 'gear', t.gear || ''));
  row.append(el('span', 'jk', t.jockey || DASH));
  const cm = el('span', 'cm', t.comment || DASH);
  cm.title = t.comment || '';
  row.append(cm);
  const mine = t.owner_note;
  const b = el('button', `sc-btn${mine ? ' has' : ''}`, mine ? '✎ NOTE' : 'NOTE');
  b.title = mine ? `${t.note.note}${tapsText(t.note) ? ` (${tapsText(t.note)})` : ''}`
    : 'watch the replay, then write what you saw';
  b.addEventListener('click', () => { card.open = card.open === k ? null : k; redraw(); });
  row.append(b);
  return row;
}

function noteForm(x, t, redraw) {
  const host = el('div', 'tc-form');
  // The same form the batch view opens: a note, the two taps, and one
  // deliberate click to book. A save re-reads the view, because a note on a
  // trial since the last run changes the horse's tier on this card.
  const done = async () => {
    card.open = null;
    await loadCard(card.date, { force: true });
    redraw();
  };
  renderReview(host, {
    horseName: x.horse_name, subject: trialSubject(t),
    existingNote: t.owner_note ? t.note : null, booked: x.blackbook,
    onSaved: done, onPromoted: done,
    onClose: () => { card.open = null; redraw(); },
  });
  return host;
}

function runnerBlock(x, list, redraw) {
  const box = el('div', 'tc-runner');
  const head = el('div', 'tc-head');
  head.append(el('span', 'no', String(x.horse_no)));
  const sys = x.blackbook && isSystem(x.blackbook);
  head.append(el('span', `nm${x.blackbook ? ' booked' : ''}${sys ? ' sys' : ''}`, x.horse_name));
  const tier = tierChip(x.book_tier);
  if (tier) head.append(tier);
  head.append(el('span', 'meta', `dr ${x.draw ?? DASH} · ${x.jockey ?? DASH} · ${x.trainer ?? DASH}`));
  head.append(lastRun(x));
  head.append(el('span', 'cnt', x.since_last
    ? `${x.since_last} since last run` : 'none since last run'));
  box.append(head);
  list.forEach((t) => {
    box.append(trialLine(x, t, redraw));
    if (card.open === key(x, t)) box.append(noteForm(x, t, redraw));
  });
  return box;
}

function raceBlock(r, redraw) {
  const sec = el('section', 'tc-race');
  const head = el('div', 'tc-race-head');
  head.append(el('span', 'rn', `R${r.race_no}`), el('span', 'off', r.off_time ?? DASH),
              el('span', null, `${r.distance ?? DASH}m`));
  const cls = classCell(r.race_class);
  if (cls) head.append(cls);
  head.append(el('span', null, `${r.venue} ${r.course ?? ''}`.trim()),
              el('span', null, `${r.field_size} run`),
              el('span', 'k', `${r.trialled} trialled since last run`));
  const link = el('a', 'right', 'RACE DAY ▸');
  link.href = `raceday.html?date=${card.date}&race=${r.race_no}`;
  head.append(link);
  sec.append(head);
  const quiet = [];
  let any = false;
  r.runners.forEach((x) => {
    const list = x.trials.filter(shown);
    if (!list.length) { quiet.push(x); return; }
    any = true;
    sec.append(runnerBlock(x, list, redraw));
  });
  // The rest of the field in one line, so a runner with nothing to watch is
  // still accounted for rather than silently missing from its race.
  if (quiet.length) {
    const line = el('div', 'tc-quiet');
    line.append(el('span', 'k', card.watch ? 'NOTHING TO WATCH'
      : card.scope === 'since' ? 'NO TRIAL SINCE LAST RUN' : 'NO TRIALS'));
    line.append(document.createTextNode(
      quiet.map((x) => `${x.horse_no} ${x.horse_name}`).join(' · ')));
    sec.append(line);
  }
  return any || quiet.length ? sec : null;
}

/** Draw the view into `host`. `redraw` re-renders the whole page, so the
 *  strip's counts follow a saved note. */
export function renderCard(host, redraw) {
  host.replaceChildren();
  if (!card.date) {
    host.append(el('div', 'no-match', 'CHOOSE A MEETING IN THE HEADER.'));
    return;
  }
  if (card.loading && !card.data) {
    host.append(el('div', 'no-match', 'LOADING THE CARD’S TRIALS…'));
    return;
  }
  if (card.error) {
    host.append(el('div', 'no-match', `NO TRIALS FOR THIS MEETING — ${card.error}`));
    return;
  }
  const races = card.data.races;
  host.append(controls(races, redraw));
  races.filter((r) => card.race === 'all' || r.race_no === card.race)
    .map((r) => raceBlock(r, redraw)).filter(Boolean)
    .forEach((s) => host.append(s));
}

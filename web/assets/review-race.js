/* review-race.js — the desk page after a race has gone off: your bankers in
 * it, one tap each for what decided it, and your note on the race; under the
 * races, your note on the night.
 *
 * desk.js draws the race; this hands it three things (desk-page.js wires them):
 *   raceEl(race)      the review block drawn under the result
 *   chipNote(race)    "2 to judge" on the race chip, ✓ once all are judged
 *   firstRace(races)  the race to open once the whole card has run
 *
 * Your bankers are the horses a ticket could not pay without
 * (query/banker_review): the desk's TAKEs at once, and your statement's and
 * Kelvin's sheet's once they are imported, usually the next day. The notes are
 * free text and save when you leave the box — on 18 Nov 2026 the night's
 * reflection was a text file; this is where it lives now.
 */
import { el } from './vocab.js';
import { api } from './api.js';
import { RowSet, toJudge } from './review-rows.js';

const N = {
  date: null, account: undefined, data: null, error: null, fetchedAt: 0,
  sets: new Map(), notes: {}, drafts: new Map(), saving: new Set(), noteMsg: new Map(),
  redraw: () => {}, changed: () => {},
};
const REFRESH_MS = 30000;

export function load(date, account, { redraw, changed } = {}) {
  if (redraw) N.redraw = redraw;
  if (changed) N.changed = changed;
  const acct = account || null;
  if (date === N.date && acct === N.account) return;
  N.date = date; N.account = acct; N.data = null; N.error = null;
  N.sets = new Map(); N.notes = {}; N.drafts.clear(); N.noteMsg.clear();
  if (date) fetchNight();
}

async function fetchNight() {
  const { date, account } = N;
  N.fetchedAt = Date.now();
  try {
    const got = await api.reviewMeeting(date, account);
    if (date !== N.date || account !== N.account) return;     // a newer meeting is on screen
    N.data = got;
    N.notes = got.notes ?? {};
    const by = new Map();
    got.horses.forEach((h) => {
      if (!by.has(h.race_no)) by.set(h.race_no, []);
      by.get(h.race_no).push(h);
    });
    const old = N.sets;
    N.sets = new Map([...by].map(([no, list]) => {
      const set = new RowSet(list, { redraw: () => N.redraw(), changed: () => N.changed(),
                                     label: (h) => `#${h.horse_no} ${h.horse_name}` });
      if (old.has(no)) { set.open = old.get(no).open; set.drafts = old.get(no).drafts; }
      return [no, set];
    }));
    N.error = null;
  } catch (err) {
    if (date === N.date) N.error = err.message;
  }
  N.redraw();
}

/** A race the desk says has gone, whose result or bankers this page has not
 *  read yet: read the night again, at most every 30 seconds. */
function freshen(race) {
  if (!N.data || Date.now() - N.fetchedAt < REFRESH_MS) return;
  const set = N.sets.get(race.race_no);
  const unrun = set?.horses.some((h) => !h.race.result);
  const taken = race.candidates?.some((c) => c.decision && !c.decision.late && c.decision.decision !== 'rejected'
    && !set?.horses.some((h) => h.horse_no === c.banker.horse_no));
  if (unrun || taken) fetchNight();
}

export function chipNote(race) {
  const set = N.sets.get(race.race_no);
  if (!set) return null;
  const left = set.horses.filter(toJudge).length;
  if (left) return { cls: 'rv-todo', text: `${left} to judge`, title: `${left} of your bankers to judge` };
  if (set.horses.some((h) => h.review.verdict)) return { cls: 'ok', text: '✓', title: 'every banker judged' };
  return null;
}

export function firstRace(races) {
  const r = races.find((x) => N.sets.get(x.race_no)?.horses.some(toJudge));
  return r ? r.race_no : null;
}

export function raceEl(race) {
  freshen(race);
  const box = el('div', 'rv-race');
  const hd = el('div', 'rv-race-hd');
  hd.append(el('span', 't', 'AFTER THE RACE · YOUR BANKERS'));
  box.append(hd);
  if (N.error) box.append(el('div', 'no-match', `YOUR BANKERS FAILED TO LOAD — ${N.error}`));
  else if (!N.data) box.append(el('div', 'rv-quiet', 'Reading your bankers…'));
  else {
    const set = N.sets.get(race.race_no);
    if (set) box.append(...set.els(N.data.vocabulary));
    else {
      box.append(el('div', 'rv-quiet', 'No banker of yours on record in this race yet. A TAKE on the desk '
        + 'shows here at once; your statement and Kelvin’s sheet bring the rest when imported.'));
    }
  }
  box.append(noteEl(race.race_no, 'YOUR NOTE ON THIS RACE',
    'the horse you should have banked, why you left the race alone, what you would do differently'));
  return box;
}

export function nightEl() {
  const box = el('div', 'rv-night');
  if (!N.data) return box;
  box.append(noteEl(0, 'THE NIGHT',
    'how the night went: the multi, the structure, what to change next time'));
  return box;
}

function noteEl(no, label, placeholder) {
  const box = el('div', 'rv-notebox');
  const saved = N.notes[String(no)];
  const hd = el('div', 'rv-label');
  hd.append(document.createTextNode(label));
  hd.append(el('span', 'rv-quiet-in', N.saving.has(no) ? ' · saving…'
    : saved ? ` · saved ${new Date(saved.edited_at ?? saved.written_at).toLocaleString('en-GB', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Hong_Kong' })}` : ''));
  box.append(hd);
  const text = el('textarea', 'rv-text');
  text.rows = no ? 2 : 4;
  text.placeholder = placeholder;
  text.value = N.drafts.get(no) ?? saved?.note ?? '';
  text.addEventListener('input', () => N.drafts.set(no, text.value));
  text.addEventListener('change', () => saveNote(no, text.value));
  box.append(text);
  if (N.noteMsg.get(no)) box.append(el('div', 'no-match', N.noteMsg.get(no)));
  return box;
}

async function saveNote(no, value) {
  const date = N.date;
  N.saving.add(no);
  N.noteMsg.delete(no);
  N.redraw();
  try {
    const got = await api.reviewNote(date, { race_no: no, note: value.trim() || null });
    if (date === N.date) {
      if (got.note) N.notes[String(no)] = got.note; else delete N.notes[String(no)];
      N.drafts.delete(no);
    }
  } catch (err) {
    N.noteMsg.set(no, err.message);
  }
  N.saving.delete(no);
  N.redraw();
}

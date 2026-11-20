/* banker-review.js — the desk page's BACKLOG: every banker since April, meeting
 * by meeting, and what the review adds up to so far.
 *
 * Asked for on 20 Nov 2026 (docs/banker-review.md): grade the reasoning, keep
 * the times he picked wrong apart from the times circumstances decided it, and
 * be able to go back over bankers already run — his own and the legs of
 * Kelvin's tickets. Going back is OPTIONAL: the race night is done on the desk
 * itself (review-race.js), and the backlog is there for when he wants it. A
 * verdict on an old banker is one tap; a reason written now is kept apart.
 *
 *   the record      the verdicts so far, how often the stewards' report names
 *                   a cause for what he blamed, how his confidence has run
 *   meeting chips   newest first, each with how many of its horses are judged
 *   the horses      review-rows.js
 */
import { el } from './vocab.js';
import { api } from './api.js';
import { RowSet, toJudge } from './review-rows.js';

const S = {
  host: null, account: undefined, list: null, vocab: null, error: null, summary: null,
  date: null, meeting: null, rows: null, mError: null,
};

export function renderReview(host, { account } = {}) {
  S.host = host;
  const acct = account || null;
  if (acct !== S.account) setAccount(acct);
  else draw();
}

/** The record moved (a verdict elsewhere on the page): read it again next time. */
export function staleReview() {
  S.summary = null;
  if (S.account !== undefined) loadSummary();
}

function setAccount(acct) {
  S.account = acct;
  S.list = null; S.error = null; S.summary = null; S.date = null; S.meeting = null; S.rows = null;
  draw();
  load();
}

async function load() {
  try {
    const got = await api.review(S.account);
    S.list = got.meetings;
    S.vocab = got.vocabulary;
    const first = S.list.find((m) => m.run > m.judged) ?? S.list[0];
    pick(first?.race_date ?? null);
  } catch (err) {
    S.error = err.message;
    draw();
  }
  loadSummary();
}

async function loadSummary() {
  try {
    S.summary = await api.reviewSummary(S.account);
  } catch (err) {
    S.summary = { error: err.message };
  }
  draw();
}

async function pick(date) {
  S.date = date; S.meeting = null; S.rows = null; S.mError = null;
  draw();
  if (!date) return;
  try {
    S.meeting = await api.reviewMeeting(date, S.account);
    S.rows = new RowSet(S.meeting.horses, { redraw: draw, changed: recount });
  } catch (err) {
    S.mError = err.message;
  }
  draw();
}

function recount() {
  const m = S.list?.find((x) => x.race_date === S.meeting?.race_date);
  if (m) m.judged = S.meeting.horses.filter((x) => x.review.verdict).length;
  loadSummary();
}

function draw() {
  const host = S.host;
  if (!host) return;
  host.replaceChildren();
  const head = el('div', 'section-head');
  head.append(el('span', 'title', 'THE BACKLOG'), el('span', 'sub', 'OPTIONAL · ONE TAP A BANKER'));
  host.append(head);
  host.append(el('div', 'rv-purpose',
    'Every banker since April that a ticket could not pay without — yours and the legs of Kelvin’s. '
    + 'You do not have to go back over these: race nights are judged on TONIGHT. When you do, a '
    + 'verdict is one tap, the stewards’ report is beside it, and a reason written now is kept '
    + 'apart from one written before the off.'));
  if (S.error) { host.append(el('div', 'no-match', `BACKLOG FAILED TO LOAD — ${S.error}`)); return; }
  if (!S.list) { host.append(el('div', 'empty-line', 'LOADING')); return; }
  host.append(summaryEl(), chipsEl());
  if (S.mError) host.append(el('div', 'no-match', `MEETING FAILED TO LOAD — ${S.mError}`));
  else if (!S.meeting) host.append(el('div', 'empty-line', S.date ? 'LOADING' : 'NO MEETING'));
  else host.append(meetingEl());
}

/* ── what it adds up to ─────────────────────────────────────────────────── */

const aeText = (ln) => (ln.ae === null ? `n ${ln.n}`
  : `${ln.placed} placed v ${Number(ln.expected).toFixed(1)} the price expected · A/E ${ln.ae.toFixed(2)} `
    + `[${ln.ae_lo.toFixed(2)}, ${ln.ae_hi.toFixed(2)}] · n ${ln.n}${ln.thin ? ' · thin' : ''}`);

export function summaryEl() {
  const box = el('div', 'rv-record');
  const s = S.summary;
  if (!s) { box.append(el('div', 'rv-quiet', 'Reading the record…')); return box; }
  if (s.error) { box.append(el('div', 'no-match', `RECORD FAILED TO LOAD — ${s.error}`)); return box; }
  const hd = el('div', 'rv-record-hd');
  hd.append(el('span', 't', 'THE RECORD SO FAR'),
            el('span', 'n', `${s.judged} of ${s.run} judged · ${s.claimed} with a line, `
              + `${s.claimed_before} written before the off`));
  box.append(hd);
  const verdicts = el('div', 'rv-counts');
  s.verdicts.forEach((v) => {
    const c = el('span', v.n ? 'on' : null);
    c.append(el('b', null, String(v.n)), document.createTextNode(` ${v.label}`));
    verdicts.append(c);
  });
  box.append(verdicts);
  const sides = s.record.sides.filter((x) => x.n);
  if (sides.length) {
    const SIDE = { circumstances: 'you put down to circumstances', mistake: 'you called a misread',
                   right_beaten: 'you called beaten fairly', unsure: 'you could not call' };
    const rec = el('div', 'rv-line');
    rec.append(el('span', 'k', 'THE STEWARDS’ REPORT NAMES A CAUSE FOR'), el('span', 'v', sides.map((x) =>
      `${x.record_names_one} of ${x.n} ${SIDE[x.verdict] ?? x.label}`).join(' · ')));
    rec.title = 'Of the beaten bankers you judged, how often the stewards’ report or the comments on running '
      + 'name a cause. If it names one as often for the ones you called a misread, blaming '
      + 'circumstances is not telling them apart.';
    box.append(rec);
  }
  const more = el('details', 'rv-more');
  more.append(el('summary', null, 'HOW SURE YOU WERE · WHOSE PICK · BY BOOK'));
  s.confidence.filter((c) => c.n).forEach((c) => more.append(
    lineEl(`${c.label.toUpperCase()} · ${c.timing === 'before' ? 'BEFORE THE OFF' : 'AFTER'}`, c)));
  s.kelvin_picks.filter((p) => p.n).forEach((p) => more.append(lineEl(`KELVIN’S LEGS · PICKED BY ${p.label.toUpperCase()}`, p)));
  s.accounts.forEach((a) => more.append(lineEl(`${a.account.toUpperCase()} · EVERY KEY HORSE RUN`, a)));
  s.kinds.forEach((k) => more.append(lineEl(`KIND · ${k.kind.toUpperCase()}`, k)));
  box.append(more);
  return box;
}

function lineEl(label, ln) {
  const row = el('div', `rv-line${ln.thin ? ' thin' : ''}`);
  row.append(el('span', 'k', label), el('span', 'v', aeText(ln)));
  return row;
}

/* ── the meetings ───────────────────────────────────────────────────────── */

function chipsEl() {
  const box = el('div', 'race-chips rv-chips');
  S.list.forEach((m) => {
    const d = new Date(`${m.race_date}T12:00:00`);
    const b = el('button', 'race-chip', d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }).toUpperCase());
    b.type = 'button';
    b.setAttribute('aria-pressed', String(m.race_date === S.date));
    if (m.run) b.append(el('span', m.judged >= m.run ? 'ok' : 'bb', m.judged >= m.run ? '✓' : `${m.judged}/${m.run}`));
    b.title = `${m.race_date} · ${m.venue ?? ''} · ${m.horses} horses · ${m.judged} judged · ${m.claimed} with a line`
      + ` · ${m.accounts.join(', ').toUpperCase()}`;
    b.addEventListener('click', () => pick(m.race_date));
    box.append(b);
  });
  return box;
}

function meetingEl() {
  const m = S.meeting;
  const box = el('div', 'rv-meeting');
  const hd = el('div', 'rv-meeting-hd');
  const d = new Date(`${m.race_date}T12:00:00`);
  const n = m.horses.length;
  hd.append(el('span', 'no', d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })),
            el('span', 'meta', `${m.venue ?? ''} · ${n} horse${n === 1 ? '' : 's'}`
              + ` · ${m.horses.filter((h) => h.review.verdict).length} judged`
              + ` · ${m.horses.filter(toJudge).length} to judge`));
  box.append(hd);
  if (!n) box.append(el('div', 'rv-quiet', 'No banker on record for this meeting.'));
  else box.append(...S.rows.els(S.vocab));
  return box;
}

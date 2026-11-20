/* banker-review-form.js — one banker opened for review, on the desk page's race
 * view (review-race.js) and in its backlog (banker-review.js).
 *
 * Rebuilt on 20 Nov 2026 to cost as little as possible on a race night: he
 * means to do this every meeting, so every tap saves on the spot and there is
 * no SAVE button. After the race the whole job is ONE TAP — what decided it —
 * and a second only when the answer is circumstances:
 *
 *   before the race   what you had written on it before it ran, and your line
 *                     from the desk. A line written before the off is shown,
 *                     never edited once the race has gone. With none, "add the
 *                     reason now" opens a box, and what you write is kept
 *                     apart as written after the race.
 *   after the race    how it ran and what the price gave it, what the
 *                     stewards' report says, your note on the run if you wrote
 *                     one, then WHAT DECIDED IT. Circumstances opens the eight
 *                     causes, with the ones the report names marked.
 *
 * Text saves when you leave the box; a chip saves when you tap it.
 */
import { el, DASH } from './vocab.js';

const day = (iso) => (iso ? new Date(iso).toLocaleDateString('en-GB', {
  day: 'numeric', month: 'short', timeZone: 'Asia/Hong_Kong' }) : '');
const stamp = (iso) => (iso ? new Date(iso).toLocaleString('en-GB', {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  timeZone: 'Asia/Hong_Kong' }) : '');
export const ordinal = (n) => (n === null || n === undefined ? DASH
  : `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th')}`);

/** What a row keeps between redraws: the text in its boxes, and what is open. */
export function draftOf(h) {
  const r = h.review;
  return { reasoning: r.reasoning ?? '', verdict_note: r.verdict_note ?? '',
           why: false, causes: r.verdict === 'circumstances' || (r.circumstances ?? []).length > 0 };
}

function sub(title, note) {
  const s = el('div', 'rv-sub');
  s.append(el('span', 't', title));
  if (note) s.append(el('span', 'n', note));
  return s;
}

/** One of several; tapping the chosen one again clears it. Saves at once. */
function choices(items, current, onPick, cls = '') {
  const box = el('div', `rv-choices ${cls}`);
  items.forEach((it) => {
    const on = current === it.key;
    const b = el('button', `chip${on ? ' on' : ''}`, it.label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(on));
    b.addEventListener('click', () => onPick(on ? null : it.key));
    box.append(b);
  });
  return box;
}

export function runText(h) {
  const run = h.run;
  if (run.withdrawn) return 'Withdrawn.';
  if (!h.race.result) return 'No result yet.';
  const bits = [run.place ? `${ordinal(run.place)} of ${h.race.field_size}` : `did not finish (${run.place_code ?? DASH})`];
  if (run.place && run.place > 1 && run.lengths_behind !== null) bits.push(`beaten ${run.lengths_behind} L`);
  if (run.win_odds) bits.push(`SP ${run.win_odds}`);
  if (run.running_positions) bits.push(`ran ${run.running_positions}`);
  return bits.join(' · ');
}

/** The line that keeps a miss in proportion: how often the price said it places. */
function chanceText(h) {
  const c = h.run.chance;
  if (c === null || c === undefined) return null;
  const p = Math.round(100 * c);
  return `The price gave it ${p}% to place: read right, a horse like this still misses ${100 - p} times in 100.`;
}

function beforeEl(h, d, ctx) {
  const box = el('div', 'rv-col');
  box.append(sub('BEFORE THE RACE', h.review.claim_timing === 'before' ? 'YOUR LINE STANDS AS WRITTEN' : ''));
  const ev = h.evidence;
  const say = (label, text, when) => {
    if (!text) return;
    const row = el('div', 'rv-say');
    row.append(el('span', 'k', label), el('span', 'q', `“${text}”`));
    if (when) row.append(el('span', 'w', when));
    box.append(row);
  };
  if (ev?.note) say(`trial ${ev.note.trial_date}${ev.note.response ? ` · ${ev.note.response}` : ''}`,
                    ev.note.text, day(ev.note.written_at));
  if (ev?.run_note) say('your run note', ev.run_note.text, day(ev.run_note.written_at));
  // A book entry is often the trial note copied in: say it once.
  if (ev?.booking?.reasoning && ev.booking.reasoning.trim() !== (ev?.note?.text ?? '').trim()) {
    say(ev.booking.origin === 'owner' ? 'your book' : "system's book", ev.booking.reasoning,
        ev.booking.added_date);
  }

  const r = h.review;
  if (r.claim_timing === 'before') {
    say(r.claim_source === 'desk' ? 'your line, desk' : 'your line', r.reasoning, stamp(r.claim_at));
    box.append(el('div', 'rv-when before', r.confidence ? `How sure: ${r.confidence}.` : 'How sure: not given.'));
  } else if (r.claim_timing === 'after' || d.why) {
    const text = el('textarea', 'rv-text');
    text.rows = 2;
    text.placeholder = 'What had to happen for it to place — "leads from gate 2", "trial says it is back".';
    text.value = d.reasoning;
    text.addEventListener('input', () => { d.reasoning = text.value; });
    text.addEventListener('change', () => ctx.onSave({ reasoning: text.value.trim() || null }));
    box.append(text);
    box.append(el('div', 'rv-when after', r.claim_at
      ? `Written after the race, ${stamp(r.claim_at)}: kept apart from lines written before the off.`
      : 'Written after the race, this is kept apart from lines written before the off.'));
    box.append(el('div', 'rv-label', 'HOW SURE YOU WERE'));
    box.append(choices(ctx.vocab.confidence, r.confidence, (v) => ctx.onSave({ confidence: v })));
  } else {
    if (!ev?.note && !ev?.run_note && !ev?.booking?.reasoning) {
      box.append(el('div', 'rv-quiet', 'Nothing written on it before it ran.'));
    }
    const add = el('button', 'act ghost rv-add', '+ ADD THE REASON NOW (OPTIONAL)');
    add.type = 'button';
    add.addEventListener('click', () => { d.why = true; ctx.redraw(); });
    box.append(add);
  }
  if (h.accounts.includes('kelvin')) {
    box.append(el('div', 'rv-label', 'WHO PICKED IT FOR KELVIN’S TICKET'));
    box.append(choices(ctx.vocab.picked_by, r.picked_by, (v) => ctx.onSave({ picked_by: v })));
  }
  return box;
}

function afterEl(h, d, ctx) {
  const box = el('div', 'rv-col');
  const r = h.review;
  box.append(sub('AFTER THE RACE', h.run.placed === true ? 'IT PLACED' : h.run.placed === false ? 'IT DID NOT PLACE' : ''));
  box.append(el('div', 'rv-run', runText(h)));
  const odds = chanceText(h);
  if (odds && h.run.placed === false) box.append(el('div', 'rv-quiet', odds));
  h.record.forEach((x) => {
    const row = el('div', 'rv-say');
    row.append(el('span', 'k', x.label.toLowerCase()), el('span', 'q', x.quote));
    box.append(row);
  });
  if (!h.record.length && h.race.result) {
    box.append(el('div', 'rv-quiet', h.stewards ? 'The stewards name nothing for it.' : 'No stewards’ report stored yet.'));
  }
  if (h.run_note?.text) {
    const row = el('div', 'rv-say');
    row.append(el('span', 'k', 'your note on this run'), el('span', 'q', `“${h.run_note.text}”`),
               el('span', 'w', day(h.run_note.written_at)));
    box.append(row);
  }
  if (h.run.placed === null) {
    box.append(el('div', 'rv-quiet', h.run.withdrawn ? 'Withdrawn: nothing to judge.'
      : 'The verdict opens once the result is in.'));
    return box;
  }
  box.append(el('div', 'rv-label', 'WHAT DECIDED IT'));
  const fits = ctx.vocab.verdicts.filter((v) => v.needs_placed === null || v.needs_placed === h.run.placed);
  box.append(choices(fits, r.verdict, (v) => {
    if (v === 'circumstances') d.causes = true;
    ctx.onSave({ verdict: v }, { advance: v !== null && v !== 'circumstances' });
  }, 'rv-verdicts'));

  if (d.causes) {
    box.append(el('div', 'rv-label', 'WHICH CIRCUMSTANCES'));
    const named = new Set(h.record.map((x) => x.key));
    const mine = new Set(r.circumstances ?? []);
    const chips = el('div', 'rv-choices');
    ctx.vocab.circumstances.forEach((c) => {
      const b = el('button', `chip${mine.has(c.key) ? ' on' : ''}`, c.label);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(mine.has(c.key)));
      if (named.has(c.key)) {
        b.append(el('span', 'rv-rec', ' · in the report'));
        b.title = h.record.find((x) => x.key === c.key)?.quote ?? '';
      }
      b.addEventListener('click', () => {
        const next = new Set(mine);
        if (next.has(c.key)) next.delete(c.key); else next.add(c.key);
        ctx.onSave({ circumstances: [...next] });
      });
      chips.append(b);
    });
    box.append(chips);
  } else if (r.verdict) {
    const more = el('button', 'act ghost rv-add', '+ CIRCUMSTANCES PLAYED A PART');
    more.type = 'button';
    more.addEventListener('click', () => { d.causes = true; ctx.redraw(); });
    box.append(more);
  }
  const note = el('input', 'rv-note');
  note.placeholder = 'what happened, or what you would do differently (optional)';
  note.value = d.verdict_note;
  note.addEventListener('input', () => { d.verdict_note = note.value; });
  note.addEventListener('change', () => ctx.onSave({ verdict_note: note.value.trim() || null }));
  box.append(note);
  return box;
}

/** ctx: {vocab, draft, busy, msg, onSave(body, {advance}), redraw(), onNext?} */
export function formEl(h, ctx) {
  const d = ctx.draft;
  const box = el('div', 'rv-form');
  const cols = el('div', 'rv-cols');
  cols.append(beforeEl(h, d, ctx), afterEl(h, d, ctx));
  box.append(cols);
  const foot = el('div', 'rv-acts');
  foot.append(el('span', `rv-state${ctx.busy ? ' busy' : ''}`, ctx.busy ? 'SAVING…'
    : h.review.verdict_at ? `SAVED · JUDGED ${stamp(h.review.verdict_at)}` : 'EVERY TAP SAVES'));
  if (ctx.onNext) {
    const next = el('button', 'act ghost', 'NEXT ›');
    next.type = 'button';
    next.addEventListener('click', ctx.onNext);
    foot.append(next);
  }
  box.append(foot);
  if (ctx.msg) box.append(el('div', 'no-match', ctx.msg));
  return box;
}

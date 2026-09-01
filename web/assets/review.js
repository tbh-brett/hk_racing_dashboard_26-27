/* review.js — write a note on a run, and promote that run to the blackbook.
 *
 * The Results artboard puts the requirement in one line under the form:
 * "same form as the Form Guide — reviewing and booking is one action". So it
 * is one form, in one module, called by both pages. Copying it into the second
 * page would give the two surfaces different tag vocabularies, different
 * pre-fill behaviour and eventually different rules about what a promotion
 * means, which is the shape the old dashboard was in.
 *
 * Design brief 06 Part 0 sets the rule the form encodes: a note is a RECORD, a
 * blackbook entry is a JUDGEMENT. The promotion is one deliberate click,
 * pre-filled from the note so nothing is retyped — never automatic. Auto-
 * promoting every note would fill the book with noise, and a book that only
 * grows is unusable within a season.
 */
import { api } from './api.js';
import { el } from './vocab.js';


/* The tag vocabulary is the one the imported book actually uses, loaded once
   from the API rather than hard-coded here — the real one has 19 definitions
   written against actual use, and a guess would offer a different set on each
   page that guessed. */
let TAG_OPTIONS = [];
let loaded = false;

export async function loadTags() {
  if (loaded) return TAG_OPTIONS;
  try {
    const body = await api.blackbookTags();
    TAG_OPTIONS = body.tags.map((t) => t.tag).sort();
  } catch {
    TAG_OPTIONS = [];
  }
  loaded = true;
  return TAG_OPTIONS;
}

/* WHAT IS BEING ANNOTATED.
 *
 * The form is the same for a race and a trial — a note, then one deliberate
 * click to promote it — but the two are addressed differently: a race has a
 * race number and a trial has a batch number, they share a date, and they are
 * stored in separate tables for exactly that reason. So the difference is one
 * small object rather than a second copy of the form, which is what would
 * eventually give trials their own tag vocabulary and their own quiet rules
 * about what a promotion means.
 */
export function runSubject(run) {
  return {
    kind: 'run',
    title: 'RUN NOTE',
    label: `${run.race_date} R${run.race_no}`,
    detail: `${run.race_date} R${run.race_no} · ${conditionLabel(run)}`,
    save: (horseName, note) => api.saveNote({
      horse_name: horseName, race_date: run.race_date,
      race_no: run.race_no, note,
    }),
    source: { source_date: run.race_date, source_race_no: run.race_no },
  };
}

export function trialSubject(trial) {
  const cond = [trial.venue, trial.surface,
                trial.going ? `${trial.going}` : null]
    .filter(Boolean).join(' ');
  return {
    kind: 'trial',
    title: 'TRIAL NOTE',
    label: `${trial.trial_date} T${trial.trial_no}`,
    detail: `${trial.trial_date} T${trial.trial_no}${cond ? ` · ${cond}` : ''}`,
    save: (horseName, note) => api.saveTrialNote({
      horse_name: horseName, trial_date: trial.trial_date,
      trial_no: trial.trial_no, note,
    }),
    // `source_trial_no`, never `source_race_no` — a trial written into the
    // race column makes the book link back to a race that was never run.
    source: { source_date: trial.trial_date, source_trial_no: trial.trial_no },
  };
}

export function conditionLabel(run) {
  return [run.venue, run.course, run.distance ? `${run.distance}m` : null,
          run.going, run.race_class ? `C${run.race_class}` : null]
    .filter(Boolean).join(' ');
}

/**
 * Fill `host` with the run-note form, and the promote form behind one click.
 *
 * `run` needs race_date, race_no and the condition fields. `existingNote` is
 * the note already on this run, if any; `booked` is the blackbook entry for
 * this horse, if any. `onSaved` and `onPromoted` hand the result back so the
 * calling page updates its own state — this module holds none.
 */
export function renderReview(host, {
  horseName, run = null, subject = null, existingNote = null, booked = null,
  onSaved = () => {}, onPromoted = () => {}, onClose = () => {},
}) {
  // A caller that passes a bare `run` gets the run subject, which is what the
  // Form Guide and Results already do.
  const subj = subject ?? runSubject(run);
  host.replaceChildren();

  const hd = el('div', 'hd');
  hd.append(document.createTextNode(subj.title));
  hd.append(el('span', 'meta', `${horseName} · ${subj.label}`));
  const close = el('button', 'close', '✕');
  close.addEventListener('click', onClose);
  hd.append(close);
  host.append(hd);

  if (existingNote) host.append(el('div', 'body', existingNote.note));

  const row = el('div', 'row');
  const input = el('input');
  input.value = existingNote?.note ?? '';
  input.placeholder = subj.kind === 'trial'
    ? 'note on this trial' : 'note on this run';
  row.append(input);
  const save = el('button', 'act', 'SAVE');
  const err = el('div', 'err');
  save.addEventListener('click', async () => {
    save.disabled = true;
    err.textContent = '';
    try {
      onSaved(await subj.save(horseName, input.value));
    } catch (e) {
      err.textContent = e.message;
      save.disabled = false;
    }
  });
  row.append(save);
  host.append(row);
  host.append(err);

  // A note is a record, the book is a judgement. One deliberate click.
  const sep = el('div', 'sep');
  if (booked) {
    const tags = booked.tags ?? [];
    sep.append(el('div', 'hint',
      `■ ALREADY IN THE BLACKBOOK — ${booked.added_date}`
      + (tags.length ? ` · ${tags.join(' · ')}` : '')));
  } else {
    const open = el('button', 'act', '+ ADD TO BLACKBOOK');
    open.addEventListener('click', () => {
      open.remove();
      sep.append(promoteForm({ horseName, subject: subj, noteInput: input,
                               onPromoted, onClose }));
    });
    sep.append(open);
    sep.append(el('div', 'hint', 'a note is a record · the book is a judgement'));
  }
  host.append(sep);
  input.focus();
  return input;
}

function promoteForm({ horseName, subject, noteInput, onPromoted, onClose }) {
  const form = el('div');
  form.append(el('div', 'cap', 'HORSE'));
  form.append(el('div', 'val strong', horseName));
  form.append(el('div', 'cap',
    subject.kind === 'trial' ? 'SOURCE TRIAL' : 'SOURCE RUN'));
  form.append(el('div', 'val', subject.detail));
  form.append(el('div', 'cap', 'REASON'));
  const reason = el('textarea');
  reason.rows = 3;
  reason.value = noteInput.value;         // pre-filled — nothing to retype
  form.append(reason);

  const chosen = new Set();
  const cap = el('div', 'cap', 'TAGS');
  form.append(cap);
  const tags = el('div', 'tags');
  TAG_OPTIONS.forEach((t) => {
    const b = el('button', 'tag', t);
    b.setAttribute('aria-pressed', 'false');
    b.addEventListener('click', () => {
      if (chosen.has(t)) chosen.delete(t); else chosen.add(t);
      b.setAttribute('aria-pressed', String(chosen.has(t)));
      cap.textContent = `TAGS  ${chosen.size} SELECTED`;
    });
    tags.append(b);
  });
  form.append(tags);

  const err = el('div', 'err');
  const row = el('div', 'row');
  const confirm = el('button', 'act', 'ADD TO BLACKBOOK');
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    err.textContent = '';
    try {
      const entry = await api.createBlackbookEntry({
        horse_name: horseName, reasoning: reason.value,
        ...subject.source,
        tags: [...chosen],
      });
      onPromoted(entry);
    } catch (e) {
      err.textContent = e.message;
      confirm.disabled = false;
    }
  });
  row.append(confirm);
  const cancel = el('button', 'act ghost', 'CANCEL');
  cancel.addEventListener('click', onClose);
  row.append(cancel);
  form.append(row);
  form.append(err);
  return form;
}

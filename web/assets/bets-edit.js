/* bets-edit.js — correcting a bet on the ledger.
 *
 * The ledger was append-only from the page: a bet entered twice, filed to the
 * wrong account or typed with a short stake could only be fixed in the
 * database. This is the page half of that. What a correction does to the
 * record — and why the next statement import cannot quietly undo one — is in
 * store/bet_edits.py and is not repeated here.
 *
 * Three rules the interface keeps:
 *
 *   A delete is confirmed in place and undoable in place. No native dialog —
 *   the page never uses one — and no timed toast either: a toast that vanishes
 *   after five seconds is hostile on a phone at a racecourse, so the undo stays
 *   until something else happens.
 *
 *   The derived figures are never editable. You say what was staked and what
 *   came back; profit and hit follow on the server, so they cannot be left
 *   disagreeing with each other.
 *
 *   A refusal is shown as the server wrote it. They name the problem — "race 1
 *   on 2026-09-06 has no runner 22" — and a generic "could not save" would
 *   throw that away.
 */
import { api } from './api.js';
import { el, DASH } from './vocab.js';

const money = (v) => (v === null || v === undefined ? DASH
  : `$${Math.round(Math.abs(v)).toLocaleString()}`);

// The accounts the server accepts. Kept beside the form that offers them; the
// server refuses anything else with a reason, so a drift is loud.
const ACCOUNT_OPTIONS = [['brett', 'Brett'], ['kelvin', 'Kelvin']];

// Suggestions, not a list the field is limited to: the ledger holds statement
// types (TRIO, FCT_MB, QTT_MB…) this page never creates, and a select would
// silently rewrite one of those the moment the form was saved.
const COMMON_TYPES = ['WIN', 'PLACE', 'WP', 'QIN', 'QPL', 'QQP', 'QIN_BANKER',
  'QPL_BANKER', 'QQP_BANKER', 'TRIO', 'FCT', 'TCE', 'QTT'];

/** How a bet is named in a sentence: "$160 AU·2x1 on 6 Sep". */
export function betLabel(b) {
  const type = String(b.bet_type ?? '').replace('_BANKER', '·B')
    .replace('ALLUP_', 'AU·');
  const race = b.race_no === null || b.race_no === undefined ? '' : ` R${b.race_no}`;
  return `${money(b.stake)} ${type} on ${b.race_date}${race}`;
}

/* ── the row's two actions ─────────────────────────────────────────────── */

export function actionsCell(b, { onEdit, onDelete }) {
  const cell = el('div', 'acts');
  const edit = el('button', 'act edit', 'EDIT');
  edit.type = 'button';
  edit.title = 'correct this bet';
  edit.addEventListener('click', (e) => { e.stopPropagation(); onEdit(b); });
  const del = el('button', 'act del', '✕');
  del.type = 'button';
  del.title = 'delete this bet — it can be restored';
  del.setAttribute('aria-label', `Delete ${betLabel(b)}`);
  del.addEventListener('click', (e) => { e.stopPropagation(); onDelete(b); });
  cell.append(edit, del);
  return cell;
}

/* ── delete, confirmed in place ────────────────────────────────────────── */

export function confirmStrip(b, { onConfirm, onCancel }) {
  const strip = el('div', 'led-confirm');
  strip.append(el('span', 'q', `Delete ${betLabel(b)}?`));
  const reason = el('input', 'reason');
  reason.placeholder = 'why (optional) — entered twice, not mine…';
  reason.setAttribute('aria-label', 'Reason for deleting');
  strip.append(reason);

  const yes = el('button', 'act danger', 'DELETE');
  yes.type = 'button';
  const no = el('button', 'act', 'KEEP');
  no.type = 'button';
  const msg = el('span', 'err');
  yes.addEventListener('click', async () => {
    yes.disabled = true;
    msg.textContent = '';
    try {
      await onConfirm(b, reason.value.trim());
    } catch (err) {
      msg.textContent = err.message;
      yes.disabled = false;
    }
  });
  no.addEventListener('click', onCancel);
  reason.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') yes.click();
    if (e.key === 'Escape') onCancel();
  });
  strip.append(yes, no, msg);
  // Focus the reason, not the button: Enter should not delete a bet the moment
  // the strip appears.
  queueMicrotask(() => reason.focus());
  return strip;
}

export function undoBar(deleted, { onUndo, onDismiss }) {
  const bar = el('div', 'led-undo');
  bar.append(el('span', null,
    `Deleted ${betLabel(deleted)}${deleted.reason ? ` — ${deleted.reason}` : ''}.`));
  const undo = el('button', 'act', 'UNDO');
  undo.type = 'button';
  const msg = el('span', 'err');
  undo.addEventListener('click', async () => {
    undo.disabled = true;
    try {
      await onUndo(deleted);
    } catch (err) {
      msg.textContent = err.message;
      undo.disabled = false;
    }
  });
  const close = el('button', 'act ghost', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Dismiss');
  close.addEventListener('click', onDismiss);
  bar.append(undo, close, msg);
  return bar;
}

/* ── the deleted list, restorable ──────────────────────────────────────── */

export function deletedPanel(list, { onRestore }) {
  const box = el('div', 'led-deleted');
  if (!list.length) {
    box.append(el('div', 'none', 'Nothing has been deleted.'));
    return box;
  }
  box.append(el('div', 'head',
    'DELETED — kept in full, and not brought back by a re-import'));
  list.forEach((d) => {
    const row = el('div', 'drow');
    row.append(el('span', 'when', String(d.deleted_at ?? '').slice(0, 16).replace('T', ' ')));
    row.append(el('span', 'what', betLabel(d)));
    row.append(el('span', 'acct', d.account ?? DASH));
    row.append(el('span', 'why', d.reason ?? ''));
    const btn = el('button', 'act', 'RESTORE');
    btn.type = 'button';
    const msg = el('span', 'err');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await onRestore(d);
      } catch (err) {
        msg.textContent = err.message;
        btn.disabled = false;
      }
    });
    row.append(btn, msg);
    box.append(row);
  });
  return box;
}

/* ── the editor ────────────────────────────────────────────────────────── */

function field(label, input, hint) {
  const wrap = el('label', 'ed-field');
  wrap.append(el('span', 'k', label));
  wrap.append(input);
  if (hint) wrap.append(el('span', 'hint', hint));
  return wrap;
}

function textInput(value, { type = 'text', step, min, placeholder } = {}) {
  const i = el('input');
  i.type = type;
  if (step) i.step = step;
  if (min !== undefined) i.min = String(min);
  if (placeholder) i.placeholder = placeholder;
  i.value = value === null || value === undefined ? '' : String(value);
  return i;
}

/* The horses on one leg, as the card has them. Each chip cycles:
 *   not backed → backed → banker → not backed
 * One interaction, and the hint under it says so. A banker appears in every
 * combination, so at most one per leg — the server refuses a second one with
 * a reason rather than the page quietly dropping it. */
async function legPicker(date, raceNo, picked) {
  const box = el('div', 'ed-leg');
  const head = el('div', 'lh');
  head.append(el('span', 'r', `R${raceNo}`));
  box.append(head);
  const chips = el('div', 'chips');
  box.append(chips);

  let runners = null;
  try {
    runners = (await api.betCard(date, raceNo)).runners ?? null;
  } catch {
    // No card stored for this race — an old meeting outside the archive. The
    // current selections still show and still save; there is simply nothing
    // to choose from and the server has nothing to check them against.
    runners = null;
  }

  const draw = () => {
    chips.replaceChildren();
    const numbers = runners
      ? runners.map((r) => r.horse_no)
      : [...picked.keys()].sort((a, b) => a - b);
    numbers.forEach((no) => {
      const r = runners?.find((x) => x.horse_no === no);
      const stateOf = picked.get(no) ?? 'off';
      const chip = el('button', `ed-chip s-${stateOf}${r?.scratched ? ' scr' : ''}`);
      chip.type = 'button';
      chip.append(el('span', 'n', String(no)));
      if (r?.horse_name) chip.append(el('span', 'nm', r.horse_name));
      if (stateOf === 'banker') chip.append(el('span', 'b', '◆'));
      chip.title = `${no} ${r?.horse_name ?? ''} — click to cycle: `
        + 'not backed → backed → banker';
      chip.addEventListener('click', () => {
        const next = stateOf === 'off' ? 'on' : stateOf === 'on' ? 'banker' : 'off';
        if (next === 'off') picked.delete(no);
        else picked.set(no, next);
        draw();
      });
      chips.append(chip);
    });
    if (!runners) {
      chips.append(el('span', 'hint',
        'no card stored for this race — the horses above cannot be changed here'));
    }
  };
  draw();
  return box;
}

/** The whole editor for one bet. Resolves once it has fetched each leg's card. */
export async function editorPanel(b, { onSaved, onCancel }) {
  const panel = el('div', 'led-editor');
  const allUp = b.race_no === null || b.race_no === undefined;

  const account = el('select');
  ACCOUNT_OPTIONS.forEach(([key, label]) => {
    const o = el('option', null, label);
    o.value = key;
    if ((b.account ?? '').toLowerCase() === key) o.selected = true;
    account.append(o);
  });
  const date = textInput(b.race_date, { type: 'date' });
  const race = textInput(b.race_no, { type: 'number', min: 1 });
  const type = textInput(b.bet_type);
  const listId = `bet-types-${b.bet_id}`;
  const list = el('datalist');
  list.id = listId;
  COMMON_TYPES.forEach((t) => { const o = el('option'); o.value = t; list.append(o); });
  type.setAttribute('list', listId);
  const formula = textInput(b.all_up_formula, { placeholder: 'e.g. 3x4' });
  const stake = textInput(b.stake, { type: 'number', step: '0.01', min: 0 });
  const returned = textInput(b.returned, { type: 'number', step: '0.01', min: 0,
    placeholder: 'blank = not settled' });
  const status = el('select');
  ['open', 'settled', 'void'].forEach((s) => {
    const o = el('option', null, s.toUpperCase());
    o.value = s;
    if (b.status === s) o.selected = true;
    status.append(o);
  });
  const notes = textInput(b.notes);

  const grid = el('div', 'ed-grid');
  grid.append(
    field('ACCOUNT', account),
    field('DATE', date),
    ...(allUp ? [field('FORMULA', formula)] : [field('RACE', race)]),
    field('TYPE', type),
    field('STAKE', stake, 'what was actually debited'),
    field('RETURN', returned, 'blank if not settled · 0 if it lost'),
    field('STATUS', status, 'follows from RETURN — choose VOID for a refund'),
    field('NOTES', notes),
  );
  panel.append(grid, list);

  // Selections, leg by leg. `picked` maps horse number → 'on' | 'banker'.
  const legs = new Map();
  (b.selections ?? []).forEach((s) => {
    const key = `${s.leg_no ?? 0}:${s.race_no}`;
    if (!legs.has(key)) {
      legs.set(key, { leg_no: s.leg_no ?? 0, race_no: s.race_no, picked: new Map() });
    }
    legs.get(key).picked.set(s.horse_no, s.is_banker ? 'banker' : 'on');
  });
  const selBox = el('div', 'ed-sels');
  selBox.append(el('div', 'k',
    'HORSES BACKED — click a horse to cycle: not backed → backed → banker ◆'));
  panel.append(selBox);
  const ordered = [...legs.values()].sort((x, y) => x.leg_no - y.leg_no);
  const pickers = [];
  for (const leg of ordered) {
    const picker = await legPicker(b.race_date, leg.race_no, leg.picked);
    pickers.push(picker);
    selBox.append(picker);
  }

  // Moving a single-race bet to another race moves its horses with it. The
  // numbers are kept and redrawn against the NEW race's card, so a horse that
  // exists there stays picked and one that does not is visibly gone rather
  // than silently saved against a race it never ran in.
  if (!allUp && ordered.length === 1) {
    const reload = async () => {
      const no = Number(race.value);
      const d = date.value || b.race_date;
      if (!no) return;
      ordered[0].race_no = no;
      const fresh = await legPicker(d, no, ordered[0].picked);
      pickers[0].replaceWith(fresh);
      pickers[0] = fresh;
    };
    race.addEventListener('change', reload);
    date.addEventListener('change', reload);
  }

  const history = el('div', 'ed-history');
  panel.append(history);
  api.betHistory(b.bet_id).then(({ edits }) => {
    if (!edits?.length) return;
    history.append(el('div', 'k', `CORRECTED ${edits.length} TIME${edits.length === 1 ? '' : 'S'}`));
    edits.slice(-5).forEach((e) => {
      const show = (v) => (v === null || v === undefined ? DASH
        : Array.isArray(v) ? `${v.length} selection${v.length === 1 ? '' : 's'}` : String(v));
      history.append(el('div', 'h',
        `${String(e.edited_at).slice(0, 16).replace('T', ' ')} · ${e.field} `
        + `${show(e.old)} → ${show(e.new)}`));
    });
  }).catch(() => { /* history is a courtesy; the form works without it */ });

  const bar = el('div', 'ed-bar');
  const save = el('button', 'act primary', 'SAVE');
  save.type = 'button';
  const cancel = el('button', 'act', 'CANCEL');
  cancel.type = 'button';
  const msg = el('span', 'err');
  bar.append(save, cancel, msg);
  panel.append(bar);

  cancel.addEventListener('click', onCancel);
  save.addEventListener('click', async () => {
    msg.textContent = '';
    save.disabled = true;
    const changes = {
      account: account.value,
      race_date: date.value,
      bet_type: type.value,
      stake: stake.value,
      returned: returned.value === '' ? null : returned.value,
      status: status.value,
      notes: notes.value,
    };
    if (allUp) changes.all_up_formula = formula.value;
    else changes.race_no = race.value;
    // A settled bet cannot be both void and have a different return; the
    // server resolves that and says so. The page only stops the one case that
    // is certainly a slip: a return typed on a bet still marked open.
    if (changes.returned !== null && changes.status === 'open') {
      changes.status = 'settled';
    }
    const selections = [];
    ordered.forEach((leg) => {
      leg.picked.forEach((st, horseNo) => selections.push({
        race_no: leg.race_no,
        horse_no: horseNo, leg_no: leg.leg_no, is_banker: st === 'banker',
      }));
    });
    try {
      const out = await api.editBet(b.bet_id, changes, selections);
      await onSaved(out);
    } catch (err) {
      msg.textContent = err.message.replace(/^\d{3}\s+/, '');
      save.disabled = false;
    }
  });
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') onCancel();
  });
  return panel;
}


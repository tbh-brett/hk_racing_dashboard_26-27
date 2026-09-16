"""Writes the Form Guide makes: a note on a run, and promoting one to the book.

These are actions rather than batch jobs, but they live here for the same
reason every other write does -- `api/` reaches data through `query/` for reads
and `jobs/` for actions, and never touches `store/` itself.

Design brief 06 Part 0 draws the line these two functions keep apart:

    a note   is a record of what happened in one run
    an entry is a judgement that this horse is worth following

Most notes are records. Auto-promoting them would fill the book with noise and
destroy its value as a tracked signal -- so `save_note` never creates an entry,
and `promote_to_blackbook` is a separate call the user makes deliberately. What
it does save them is the typing: the note text and the run arrive pre-filled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from hkrd.store.connect import db_path, get_conn, transaction

__all__ = ["save_note", "delete_note", "save_trial_note",
           "delete_trial_note", "promote_to_blackbook",
           "next_entry_id", "set_status", "set_triggers"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_note(horse_name: str, race_date: str, race_no: int, note: str, *,
              db: Path | None = None) -> dict:
    """Write or replace the note on one run. Returns what was stored."""
    text = (note or "").strip()
    if not text:
        raise ValueError("a note needs text; use delete_note to remove one")
    horse = horse_name.strip().upper()
    conn = get_conn(db if db is not None else db_path())
    try:
        written = _now()
        with transaction(conn):
            conn.execute(
                "INSERT INTO run_notes (horse_name, race_date, race_no, note, "
                "written_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (horse_name, race_date, race_no) DO UPDATE SET "
                "note = excluded.note, written_at = excluded.written_at",
                (horse, race_date, race_no, text, written))
        return {"horse_name": horse, "race_date": race_date, "race_no": race_no,
                "note": text, "written_at": written}
    finally:
        conn.close()


def delete_note(horse_name: str, race_date: str, race_no: int, *,
                db: Path | None = None) -> bool:
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM run_notes WHERE horse_name = ? AND race_date = ? "
                "AND race_no = ?", (horse_name.strip().upper(), race_date, race_no))
        return cur.rowcount > 0
    finally:
        conn.close()


def save_trial_note(horse_name: str, trial_date: str, trial_no: int,
                    note: str, *, db: Path | None = None) -> dict:
    """Write or replace the note on one trial run.

    Its own table, not a row in `run_notes`. A trial and a race share a date
    and both carry a small number — batch 2 and race 2 — so filed together the
    second note written would silently replace the first. They are also
    different kinds of observation: "cruised, never asked" is about intent,
    which is what a trial is for, and it must not read as a comment on a race.
    """
    text = (note or "").strip()
    if not text:
        raise ValueError("a note needs text; use delete_trial_note to remove one")
    horse = horse_name.strip().upper()
    conn = get_conn(db if db is not None else db_path())
    try:
        written = _now()
        with transaction(conn):
            conn.execute(
                "INSERT INTO trial_notes (horse_name, trial_date, trial_no, "
                "note, written_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (horse_name, trial_date, trial_no) DO UPDATE SET "
                "note = excluded.note, written_at = excluded.written_at",
                (horse, trial_date, trial_no, text, written))
        return {"horse_name": horse, "trial_date": trial_date,
                "trial_no": trial_no, "note": text, "written_at": written}
    finally:
        conn.close()


def delete_trial_note(horse_name: str, trial_date: str, trial_no: int, *,
                      db: Path | None = None) -> bool:
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM trial_notes WHERE horse_name = ? "
                "AND trial_date = ? AND trial_no = ?",
                (horse_name.strip().upper(), trial_date, trial_no))
        return cur.rowcount > 0
    finally:
        conn.close()


def next_entry_id(conn) -> str:
    """bb_0197 after bb_0196. Continues the legacy sequence rather than
    starting a second one beside it."""
    row = conn.execute(
        "SELECT id FROM blackbook WHERE id LIKE 'bb_%' "
        "ORDER BY length(id) DESC, id DESC LIMIT 1").fetchone()
    n = 0
    if row:
        tail = row["id"].split("_", 1)[1]
        n = int(tail) if tail.isdigit() else 0
    return f"bb_{n + 1:04d}"


def promote_to_blackbook(horse_name: str, *, reasoning: str,
                         source_date: str | None = None,
                         source_race_no: int | None = None,
                         source_trial_no: int | None = None,
                         tags: list[str] | None = None,
                         conditions: list[dict] | None = None,
                         confidence: str = "medium",
                         db: Path | None = None) -> dict:
    """Create a blackbook entry from a run the user was looking at.

    The deliberate step. It is a separate call from save_note precisely so that
    writing an observation cannot quietly become a judgement.

    Nothing is stamped with an end date. An entry runs until it is retired or
    won out, both of which are decisions somebody takes; the 90-day expiry this
    used to write was a decision nobody took, and it closed entries quietly
    while the row still read ACTIVE everywhere the date was not checked.
    """
    from datetime import date

    horse = horse_name.strip().upper()
    reason = (reasoning or "").strip()
    if not reason:
        raise ValueError("an entry needs a reason; that is what makes it a thesis")
    # Checked BEFORE the entry is written, so a condition nothing can evaluate
    # cannot be saved. One that never matches is worse than none at all: the
    # horse silently stops appearing and the book looks empty rather than wrong.
    rows = _condition_rows(conditions)

    conn = get_conn(db if db is not None else db_path())
    try:
        added = date.today().isoformat()
        # A trial is a T, not an R. Writing "2026-08-21 R1" for a trial would
        # point the entry at a race that was never run, and every later reader
        # of `source_race` would believe it.
        if source_trial_no is not None:
            source = (f"{source_date} T{source_trial_no}" if source_date
                      else f"T{source_trial_no}")
        elif source_date and source_race_no:
            source = f"{source_date} R{source_race_no}"
        else:
            source = source_date or (f"R{source_race_no}" if source_race_no else None)
        with transaction(conn):
            entry_id = next_entry_id(conn)
            conn.execute(
                "INSERT INTO blackbook (id, horse_name, added_date, "
                "status, reasoning, confidence, source_race, source_date, "
                "source_race_no, source_date_from) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (entry_id, horse, added, reason, confidence, source,
                 source_date,
                 # Only a real race number goes in the race column. A trial's
                 # batch number left here would make the Blackbook link back
                 # to race 1 of a meeting that may not exist.
                 None if source_trial_no is not None else source_race_no,
                 "memo" if source_date else None))
            conn.executemany(
                "INSERT INTO blackbook_tags (id, tag) VALUES (?, ?) "
                "ON CONFLICT (id, tag) DO NOTHING",
                [(entry_id, t.strip()) for t in (tags or []) if t.strip()])
            conn.executemany(
                "INSERT INTO blackbook_trigger (id, kind, op, value) "
                "VALUES (?, ?, ?, ?)",
                [(entry_id, *row) for row in rows])
            _log_status(conn, entry_id, None, "active", reason, None)
        return {"id": entry_id, "horse_name": horse, "added_date": added,
                "closed_date": None, "status": "active", "reasoning": reason,
                "confidence": confidence, "source_race": source,
                "tags": sorted({t.strip() for t in (tags or []) if t.strip()}),
                "conditions": [{"kind": k, "op": o, "value": v}
                               for k, o, v in rows]}
    finally:
        conn.close()


def _condition_rows(conditions: list[dict] | None
                    ) -> list[tuple[str, str, str]]:
    """(kind, op, value) triples, every one checked before anything is written.

    `query/triggers.validate` is the rule, not a copy of it: a condition the
    band cannot evaluate would never match, so the horse would stop appearing
    and the book would look empty rather than broken.
    """
    from hkrd.query import triggers as trig_q

    out = []
    for c in conditions or []:
        kind = (c.get("kind") or "").strip().lower()
        op = (c.get("op") or "is").strip()
        value = str(c.get("value") or "").strip()
        trig_q.validate(kind, op, value)
        out.append((kind, op, value))
    return out


def set_triggers(entry_id: str, conditions: list[dict] | None, *,
                 db: Path | None = None) -> dict:
    """Replace the circumstances an entry's thesis depends on.

    Replace rather than append: editing "1200m" to "1200-1400m" has to be one
    condition afterwards, not two that contradict each other and match nothing
    between them. An empty list clears them, which is a real thing to want —
    it says the claim is about the horse rather than about a race.
    """
    rows = _condition_rows(conditions)
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            if not conn.execute("SELECT 1 FROM blackbook WHERE id = ?",
                                (entry_id,)).fetchone():
                raise KeyError(entry_id)
            conn.execute("DELETE FROM blackbook_trigger WHERE id = ?", (entry_id,))
            conn.executemany(
                "INSERT INTO blackbook_trigger (id, kind, op, value) "
                "VALUES (?, ?, ?, ?)", [(entry_id, *row) for row in rows])
        return {"id": entry_id,
                "conditions": [{"kind": k, "op": o, "value": v}
                               for k, o, v in rows]}
    finally:
        conn.close()


# The two ways a thesis ends, and the one way it lives. EXPIRED is gone: a
# ninety-day clock closed entries nobody had decided anything about and then
# sat beside RETIRE on the row as though it were a different outcome. Retiring
# is the decision; there is now one word for it.
STATUSES = ("active", "won_out", "retired")
CLOSED = ("won_out", "retired")


def _log_status(conn, entry_id: str, from_status: str | None, to_status: str,
                reason: str | None, reasoning: str | None) -> None:
    """One line of an entry's history. `reasoning` is the thesis as it STOOD."""
    conn.execute(
        "INSERT INTO blackbook_status_log (id, changed_at, from_status, "
        "to_status, reason, reasoning) VALUES (?, ?, ?, ?, ?, ?)",
        (entry_id, _now(), from_status, to_status, reason, reasoning))


def set_status(entry_id: str, status: str, *, reason: str | None = None,
               reasoning: str | None = None, db: Path | None = None) -> dict:
    """Close an entry, or reopen it on a new thesis.

    "Retiring an entry must be as easy as creating one. A blackbook that only
    ever grows becomes unusable within a season." — design brief 06. So this is
    still one call with no ceremony, and the page still puts it one click from
    the row.

    Two things it now records that it did not:

    **WHEN it closed.** `closed_date` is what an archived card reads to answer
    "was I watching this horse THAT day", which is a different question from
    "am I watching it now" and used to be answered by the expiry date. Without
    it, retiring a horse in December would rewrite every September card to say
    the thesis had never been live.

    **WHY, and what the last one said.** Reopening a horse on a fresh reason
    overwrote the reason it was booked for in the first place — and a thesis
    that failed is the most useful thing in the book. The old text goes to
    `blackbook_status_log` before the new one replaces it, so an entry reads as
    a sequence of theses rather than one field that has been typed over.

    `reasoning` is only meaningful when reopening; passing it on a close would
    be rewriting history rather than recording it, so it is refused there.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    reason = (reason or "").strip() or None
    reasoning = (reasoning or "").strip() or None
    if reasoning and status != "active":
        raise ValueError("a new thesis belongs to reopening an entry, not to "
                         "closing one; use `reason` to say why it closed")

    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            was = conn.execute(
                "SELECT status, reasoning FROM blackbook WHERE id = ?",
                (entry_id,)).fetchone()
            if was is None:
                raise KeyError(entry_id)
            if status in CLOSED:
                # `date.today()`, not the run that prompted it: the decision was
                # taken today whatever it was taken about.
                conn.execute(
                    "UPDATE blackbook SET status = ?, closed_date = date('now'), "
                    "closed_reason = ? WHERE id = ?", (status, reason, entry_id))
            else:
                # Reopening clears the close. A date left behind would make the
                # entry read as live now and closed then at the same time.
                conn.execute(
                    "UPDATE blackbook SET status = 'active', closed_date = NULL, "
                    "closed_reason = NULL, reasoning = coalesce(?, reasoning) "
                    "WHERE id = ?", (reasoning, entry_id))
            _log_status(conn, entry_id, was["status"], status,
                        reason or reasoning, was["reasoning"])
        row = conn.execute(
            "SELECT id, horse_name, status, closed_date, closed_reason, "
            "reasoning FROM blackbook WHERE id = ?", (entry_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()

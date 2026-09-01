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
           "next_entry_id", "set_status"]


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
                         confidence: str = "medium",
                         expiry_days: int = 90,
                         db: Path | None = None) -> dict:
    """Create a blackbook entry from a run the user was looking at.

    The deliberate step. It is a separate call from save_note precisely so that
    writing an observation cannot quietly become a judgement.
    """
    from datetime import date, timedelta

    horse = horse_name.strip().upper()
    reason = (reasoning or "").strip()
    if not reason:
        raise ValueError("an entry needs a reason; that is what makes it a thesis")

    conn = get_conn(db if db is not None else db_path())
    try:
        added = date.today().isoformat()
        expiry = (date.today() + timedelta(days=expiry_days)).isoformat()
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
                "INSERT INTO blackbook (id, horse_name, added_date, expiry_date, "
                "status, reasoning, confidence, source_race, source_date, "
                "source_race_no, source_date_from) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (entry_id, horse, added, expiry, reason, confidence, source,
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
        return {"id": entry_id, "horse_name": horse, "added_date": added,
                "expiry_date": expiry, "status": "active", "reasoning": reason,
                "confidence": confidence, "source_race": source,
                "tags": sorted({t.strip() for t in (tags or []) if t.strip()})}
    finally:
        conn.close()


# The three ways a thesis ends, plus the one way it lives.
STATUSES = ("active", "won_out", "retired", "expired")


def set_status(entry_id: str, status: str, *, db: Path | None = None) -> dict:
    """Resolve an entry — or put it back to active.

    "Retiring an entry must be as easy as creating one. A blackbook that only
    ever grows becomes unusable within a season." — design brief 06. So this is
    one call with no ceremony, and the page puts it one click from the row.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            cur = conn.execute(
                "UPDATE blackbook SET status = ? WHERE id = ?", (status, entry_id))
        if not cur.rowcount:
            raise KeyError(entry_id)
        row = conn.execute(
            "SELECT id, horse_name, status FROM blackbook WHERE id = ?",
            (entry_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()

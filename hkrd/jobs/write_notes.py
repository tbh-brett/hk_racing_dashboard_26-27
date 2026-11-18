"""Writes the Form Guide makes on a run: the note, on a race or on a trial.

These are actions rather than batch jobs, but they live here for the same
reason every other write does -- `api/` reaches data through `query/` for reads
and `jobs/` for actions, and never touches `store/` itself.

Design brief 06 Part 0 draws the line this module and `write_book` keep apart:

    a note   is a record of what happened in one run
    an entry is a judgement that this horse is worth following

Most notes are records. Auto-promoting them would fill the book with noise and
destroy its value as a tracked signal -- so `save_note` never creates an entry,
and `write_book.promote_to_blackbook` is a separate call the user makes
deliberately. What it does save them is the typing: the note text and the run
arrive pre-filled.
"""
from __future__ import annotations

from pathlib import Path

from hkrd.jobs.clock import _now
from hkrd.store.connect import db_path, get_conn, transaction

__all__ = ["save_note", "delete_note", "save_trial_note",
           "delete_trial_note", "add_system_note", "SYSTEM_NOTE", "RESPONSES"]

#: The trial note's second tap: what the horse found when it was asked.
RESPONSES = ("strong", "fair", "none")


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
                    note: str, *, asked: int | None = None,
                    response: str | None = None, db: Path | None = None) -> dict:
    """Write or replace the note on one trial run.

    Its own table, not a row in `run_notes`. A trial and a race share a date
    and both carry a small number — batch 2 and race 2 — so filed together the
    second note written would silently replace the first. They are also
    different kinds of observation: "cruised, never asked" is about intent,
    which is what a trial is for, and it must not read as a comment on a race.

    `asked` (1 or 0) and `response` (`RESPONSES`) are the two optional taps
    beside the words; None is "not said". A save carries both every time, so
    clearing a tap is saving it as None.

    `written_at` moves only when the WORDS change. It is what says a note was
    written before the race it is counted for (`query/book_tier`), so adding
    a tap to an old note must not make it read as written after the fact.
    """
    text = (note or "").strip()
    if not text:
        raise ValueError("a note needs text; use delete_trial_note to remove one")
    if asked not in (None, 0, 1):
        raise ValueError("asked is 1, 0 or empty")
    if response not in (None, *RESPONSES):
        raise ValueError(f"response is one of {', '.join(RESPONSES)}, or empty")
    horse = horse_name.strip().upper()
    conn = get_conn(db if db is not None else db_path())
    try:
        written = _now()
        with transaction(conn):
            conn.execute(
                "INSERT INTO trial_notes (horse_name, trial_date, trial_no, "
                "note, written_at, asked, response) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (horse_name, trial_date, trial_no) DO UPDATE SET "
                "written_at = CASE WHEN trial_notes.note = excluded.note "
                "THEN trial_notes.written_at ELSE excluded.written_at END, "
                "note = excluded.note, "
                "asked = excluded.asked, response = excluded.response",
                (horse, trial_date, trial_no, text, written, asked, response))
            written = conn.execute(
                "SELECT written_at FROM trial_notes WHERE horse_name = ? "
                "AND trial_date = ? AND trial_no = ?",
                (horse, trial_date, trial_no)).fetchone()[0]
        return {"horse_name": horse, "trial_date": trial_date,
                "trial_no": trial_no, "note": text, "written_at": written,
                "asked": asked, "response": response}
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


#: How a note the system wrote begins, so it never reads as the owner's own.
SYSTEM_NOTE = "System: "


def add_system_note(conn, horse_name: str, date: str, number: int, note: str,
                    *, trial: bool = False) -> bool:
    """The system's reading of a run, as that run's note — inside the caller's
    transaction, and only where the run has no note yet. True if written.

    `jobs/auto_book` writes one when it picks a horse the book already
    follows: there is no entry to write, and the reason it picked the run is
    still worth having on the run itself. A run carries one note, so an
    existing one — the owner's words — is never replaced.
    """
    table, number_col = (("trial_notes", "trial_no") if trial
                         else ("run_notes", "race_no"))
    date_col = "trial_date" if trial else "race_date"
    before = conn.total_changes
    conn.execute(
        f"INSERT INTO {table} (horse_name, {date_col}, {number_col}, note, "
        f"written_at) VALUES (?, ?, ?, ?, ?) "
        f"ON CONFLICT (horse_name, {date_col}, {number_col}) DO NOTHING",
        (horse_name.strip().upper(), date, number,
         SYSTEM_NOTE + note.strip(), _now()))
    return conn.total_changes > before

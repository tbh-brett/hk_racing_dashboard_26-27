"""Writes to the blackbook: booking an entry, and every decision taken on it.

Design brief 06 Part 0 draws the line this module and `write_notes` keep apart:

    a note   is a record of what happened in one run
    an entry is a judgement that this horse is worth following

So nothing in `write_notes` creates an entry. One is booked here -- by the
owner through `promote_to_blackbook`, or by `jobs/auto_book` through
`insert_entry` -- and runs until a decision ends it. `set_status` is the
owner's: close it, or reopen it on a new thesis. `close_tested` is the one
close the owner does not make, a system entry at the end of its test.

An action rather than a batch job, so it lives in `jobs/` for the reason every
write does -- `api/` reaches data through `query/` for reads and `jobs/` for
actions, and never touches `store/` itself.
"""
from __future__ import annotations

from pathlib import Path

from hkrd.jobs.clock import _now, _today
from hkrd.store.connect import db_path, get_conn, transaction

__all__ = ["promote_to_blackbook", "insert_entry", "next_entry_id",
           "set_status", "set_triggers", "adopt", "renew", "close_tested"]


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
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            return insert_entry(
                conn, horse_name, reasoning=reasoning,
                added_date=_today(), source_date=source_date,
                source_race_no=source_race_no, source_trial_no=source_trial_no,
                tags=tags, conditions=conditions, confidence=confidence)
    finally:
        conn.close()


def insert_entry(conn, horse_name: str, *, reasoning: str, added_date: str,
                 source_date: str | None = None,
                 source_race_no: int | None = None,
                 source_trial_no: int | None = None,
                 tags: list[str] | None = None,
                 conditions: list[dict] | None = None,
                 confidence: str = "medium",
                 origin: str = "owner") -> dict:
    """Write one entry inside the caller's transaction.

    The one INSERT every entry goes through, whoever books it. `jobs/auto_book`
    calls it directly so a meeting's entries and the row saying that meeting
    has been read commit together: a pass that died half-way would otherwise
    come back the next night and find its own entries in the book.
    """
    horse = horse_name.strip().upper()
    reason = (reasoning or "").strip()
    if not reason:
        raise ValueError("an entry needs a reason; that is what makes it a thesis")
    if origin not in ("owner", "system"):
        raise ValueError("origin must be owner or system")
    # Checked BEFORE the entry is written, so a condition nothing can evaluate
    # cannot be saved. One that never matches is worse than none at all: the
    # horse silently stops appearing and the book looks empty rather than wrong.
    rows = _condition_rows(conditions)

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
    entry_id = next_entry_id(conn)
    conn.execute(
        "INSERT INTO blackbook (id, horse_name, added_date, "
        "status, reasoning, confidence, source_race, source_date, "
        "source_race_no, source_date_from, origin) "
        "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, horse, added_date, reason, confidence, source,
         source_date,
         # Only a real race number goes in the race column. A trial's
         # batch number left here would make the Blackbook link back
         # to race 1 of a meeting that may not exist.
         None if source_trial_no is not None else source_race_no,
         # 'system' when the system read the date off the run it booked.
         (origin if origin == "system" else "memo") if source_date else None,
         origin))
    conn.executemany(
        "INSERT INTO blackbook_tags (id, tag) VALUES (?, ?) "
        "ON CONFLICT (id, tag) DO NOTHING",
        [(entry_id, t.strip()) for t in (tags or []) if t.strip()])
    conn.executemany(
        "INSERT INTO blackbook_trigger (id, kind, op, value) "
        "VALUES (?, ?, ?, ?)",
        [(entry_id, *row) for row in rows])
    _log_status(conn, entry_id, None, "active", reason, None)
    return {"id": entry_id, "horse_name": horse, "added_date": added_date,
            "closed_date": None, "status": "active", "reasoning": reason,
            "confidence": confidence, "source_race": source, "origin": origin,
            "tags": sorted({t.strip() for t in (tags or []) if t.strip()}),
            "conditions": [{"kind": k, "op": o, "value": v}
                           for k, o, v in rows]}


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
                # Today in Hong Kong, not the run that prompted it: the
                # decision was taken today whatever it was taken about.
                conn.execute(
                    "UPDATE blackbook SET status = ?, closed_date = ?, "
                    "closed_reason = ? WHERE id = ?",
                    (status, _today(), reason, entry_id))
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


def adopt(entry_id: str, *, db: Path | None = None) -> dict:
    """Take a system entry into the owner's book.

    Its origin stays 'system' -- the system's record keeps every horse it
    booked, adopted or not, or it stops being a fair control -- and from today
    it also counts as the owner's, which is what `adopted_date` says. It is
    no longer closed after its tested starts: the owner decides when it ends,
    as with any entry of theirs.

    Not a status change, so nothing is written to the status log: a line
    there going from ACTIVE to ACTIVE would read as a reopening.
    """
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            row = conn.execute(
                "SELECT origin, adopted_date FROM blackbook WHERE id = ?",
                (entry_id,)).fetchone()
            if row is None:
                raise KeyError(entry_id)
            if row["origin"] != "system":
                raise ValueError("only an entry the system booked can be "
                                 "adopted; this one is already yours")
            if row["adopted_date"] is None:
                conn.execute("UPDATE blackbook SET adopted_date = ? "
                             "WHERE id = ?", (_today(), entry_id))
        return dict(conn.execute(
            "SELECT id, horse_name, status, origin, adopted_date "
            "FROM blackbook WHERE id = ?", (entry_id,)).fetchone())
    finally:
        conn.close()


def renew(entry_id: str, *, today: str | None = None,
          db: Path | None = None) -> dict:
    """Keep following a stale entry: its starts are counted again from today.

    An entry goes STALE (`query/book_tier.TESTED_STARTS` starts since it was
    booked) and is muted on race day, never closed -- closing stays the
    owner's button. Renewing is the other answer to the same question. The
    entry, its date, its reasoning and its record are untouched; only the
    count restarts. A system entry is renewed by adopting it first, since
    the system closes its own entries at the end of their test.
    """
    today = today or _today()
    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            row = conn.execute(
                "SELECT status, origin, adopted_date FROM blackbook WHERE id = ?",
                (entry_id,)).fetchone()
            if row is None:
                raise KeyError(entry_id)
            if row["status"] != "active":
                raise ValueError("only a live entry can be renewed; this one is closed")
            if row["origin"] == "system" and row["adopted_date"] is None:
                raise ValueError("adopt this system entry first; the system "
                                 "closes its own entries after their test")
            conn.execute("UPDATE blackbook SET renewed_date = ? WHERE id = ?",
                         (today, entry_id))
        return dict(conn.execute(
            "SELECT id, horse_name, status, origin, adopted_date, renewed_date "
            "FROM blackbook WHERE id = ?", (entry_id,)).fetchone())
    finally:
        conn.close()


def close_tested(conn, entry_id: str, *, closed_date: str, reason: str) -> None:
    """Close a system entry at the end of its test, inside the caller's
    transaction.

    The one place anything but the owner closes an entry, and deliberately
    narrow: only a live system entry nobody adopted. It is closed the way the
    RETIRE button closes one -- RETIRED, a date, a reason, a line in the
    history -- so the row says it is closed everywhere it is read. The expiry
    removed in September 2026 did the opposite: it closed entries quietly
    while they still read ACTIVE, and they were the owner's.
    """
    was = conn.execute(
        "SELECT status, reasoning, origin, adopted_date FROM blackbook "
        "WHERE id = ?", (entry_id,)).fetchone()
    if was is None:
        raise KeyError(entry_id)
    if was["origin"] != "system" or was["adopted_date"] is not None:
        raise ValueError(f"{entry_id} is the owner's; only they close it")
    if was["status"] != "active":
        return
    conn.execute(
        "UPDATE blackbook SET status = 'retired', closed_date = ?, "
        "closed_reason = ? WHERE id = ?", (closed_date, reason, entry_id))
    _log_status(conn, entry_id, "active", "retired", reason, was["reasoning"])

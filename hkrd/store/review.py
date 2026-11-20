"""The write path for the banker review. Coerced here, on the way in.

`jobs/review_banker` decides what may be written -- a claim made before the
off is closed once the race has gone, a verdict needs a result -- and hands
this the finished row. This only stores it: one row per horse in its race, and
the circumstances he named replaced along with the verdict.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = ["review", "desk_claim", "effective_claim", "save_claim", "save_verdict",
           "save_claim_kind", "reflections", "save_reflection", "CLAIM", "VERDICT"]

_KEY = ("race_date", "race_no", "horse_no")
CLAIM = ("reasoning", "confidence", "picked_by", "claim_at", "claim_late", "claim_edited_at")
VERDICT = ("verdict", "verdict_note", "verdict_at")


def _key(race_date: object, race_no: object, horse_no: object
         ) -> tuple[str | None, int | None, int | None]:
    return (coerce.to_date(race_date), coerce.to_int(race_no, field="race_no"),
            coerce.to_int(horse_no, field="horse_no"))


def _text(v: object) -> str | None:
    """Words as he wrote them, with the edges trimmed; nothing at all is NULL."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _flag(v: object) -> int | None:
    return None if v is None else int(bool(v))


def review(conn: sqlite3.Connection, race_date: str, race_no: int,
           horse_no: int) -> dict[str, Any] | None:
    """One horse's review as stored, with its circumstances, or None."""
    key = _key(race_date, race_no, horse_no)
    row = conn.execute("SELECT * FROM banker_review WHERE race_date = ? AND race_no = ? "
                       "AND horse_no = ?", key).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["circumstances"] = [r[0] for r in conn.execute(
        "SELECT circumstance FROM banker_review_circumstance WHERE race_date = ? "
        "AND race_no = ? AND horse_no = ? ORDER BY circumstance", key)]
    return out


def desk_claim(conn: sqlite3.Connection, race_date: str, race_no: int,
               horse_no: int) -> dict[str, Any] | None:
    """The desk decision made before the off on this horse, if any: its
    sentence and when it was written."""
    row = conn.execute("SELECT reason, decided_at FROM ticket_decision WHERE race_date = ? "
                       "AND race_no = ? AND banker_no = ? AND late = 0",
                       _key(race_date, race_no, horse_no)).fetchone()
    return dict(row) if row else None


def effective_claim(stored: dict[str, Any] | None,
                    desk: dict[str, Any] | None) -> dict[str, Any]:
    """The claim as it stands. The review's own once it has one; before that,
    the sentence of a desk decision made before the off -- the desk's
    decisions from before the review existed wrote no claim of their own, and
    their sentence is still a claim made before the off."""
    s = stored or {}
    if s.get("claim_at") or not desk or not _text(desk.get("reason")):
        return {"reasoning": s.get("reasoning"), "confidence": s.get("confidence"),
                "picked_by": s.get("picked_by"), "claim_at": s.get("claim_at"),
                "claim_late": s.get("claim_late"),
                "claim_edited_at": s.get("claim_edited_at"),
                "source": "review" if s.get("claim_at") else None, "stored": stored is not None}
    return {"reasoning": _text(desk["reason"]), "confidence": s.get("confidence"),
            "picked_by": s.get("picked_by") or "me", "claim_at": desk["decided_at"],
            "claim_late": 0, "claim_edited_at": None, "source": "desk",
            "stored": stored is not None}


def save_claim(conn: sqlite3.Connection, race_date: str, race_no: int, horse_no: int,
               row: dict[str, Any]) -> None:
    """Write the claim half exactly as given (`CLAIM`); the verdict half is
    left as it is."""
    key = _key(race_date, race_no, horse_no)
    values = [_text(row.get("reasoning")), _text(row.get("confidence")),
              _text(row.get("picked_by")), _text(row.get("claim_at")),
              _flag(row.get("claim_late")), _text(row.get("claim_edited_at"))]
    conn.execute(
        f"INSERT INTO banker_review ({', '.join(_KEY + CLAIM)}) "
        f"VALUES ({', '.join('?' for _ in _KEY + CLAIM)}) "
        "ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in CLAIM), [*key, *values])


def save_verdict(conn: sqlite3.Connection, race_date: str, race_no: int, horse_no: int,
                 row: dict[str, Any], circumstances: Sequence[str]) -> None:
    """Write the verdict half exactly as given (`VERDICT`) and replace the
    circumstances with `circumstances`."""
    key = _key(race_date, race_no, horse_no)
    values = [_text(row.get("verdict")), _text(row.get("verdict_note")),
              _text(row.get("verdict_at"))]
    conn.execute(
        f"INSERT INTO banker_review ({', '.join(_KEY + VERDICT)}) "
        f"VALUES ({', '.join('?' for _ in _KEY + VERDICT)}) "
        "ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in VERDICT), [*key, *values])
    conn.execute("DELETE FROM banker_review_circumstance WHERE race_date = ? "
                 "AND race_no = ? AND horse_no = ?", key)
    conn.executemany(
        "INSERT INTO banker_review_circumstance (race_date, race_no, horse_no, "
        "circumstance) VALUES (?, ?, ?, ?)",
        [(*key, c) for c in sorted({str(c).strip() for c in circumstances})])


def save_claim_kind(conn: sqlite3.Connection, race_date: str, race_no: int,
                    horse_no: int, kind: str | None) -> int:
    """The fortnightly review's reading of what kind of reason a claim is.
    Only a horse that already has a review row; returns rows changed."""
    key = _key(race_date, race_no, horse_no)
    return conn.execute(
        "UPDATE banker_review SET claim_kind = ? WHERE race_date = ? AND race_no = ? "
        "AND horse_no = ?", [_text(kind), *key]).rowcount


def reflections(conn: sqlite3.Connection, race_date: str) -> dict[int, dict[str, Any]]:
    """{race_no: {note, written_at, edited_at}} for one meeting; 0 is the night."""
    return {r["race_no"]: {"note": r["note"], "written_at": r["written_at"],
                           "edited_at": r["edited_at"]}
            for r in conn.execute("SELECT race_no, note, written_at, edited_at FROM "
                                  "race_reflection WHERE race_date = ?",
                                  (coerce.to_date(race_date),))}


def save_reflection(conn: sqlite3.Connection, race_date: str, race_no: int,
                    note: str | None, now: str) -> None:
    """Write one race's note, or the night's (race 0). An empty note removes
    it. The first save's time is kept; a changed note is stamped as edited."""
    date = coerce.to_date(race_date)
    no = coerce.to_int(race_no, field="race_no")
    text = _text(note)
    if text is None:
        conn.execute("DELETE FROM race_reflection WHERE race_date = ? AND race_no = ?",
                     (date, no))
        return
    conn.execute(
        "INSERT INTO race_reflection (race_date, race_no, note, written_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (race_date, race_no) DO UPDATE SET "
        "note = excluded.note, edited_at = excluded.written_at "
        "WHERE race_reflection.note <> excluded.note", (date, no, text, now))

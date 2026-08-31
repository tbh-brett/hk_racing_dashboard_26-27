"""Veterinary records, and the judgement about which of them still matter.

The records have been scraped since the first build — own HKJC page, own table,
own parser, written into `vet_records` by `jobs/scrape_meeting`. Nothing ever
read them back. They reached the interface only as a tag on the runner row, and
the routine ones were filtered out there, so a horse barred from racing after a
bleeding attack looked exactly like a horse with nothing on its record.

Design brief 07 §2 says what to show: **recent records only.** "A horse with a
clean sheet from two years ago needs no badge." So this module is where the
recency judgement lives, and it lives here rather than in `ingest/` for a
reason the ingest module states outright — the old scraper scored each record
and dropped the ones below a threshold, which meant the scraper decided what
the interface was allowed to see, using numbers that lived nowhere else. A
record on the page that did not survive that filter simply was not there.

So ingest reports everything and this layer decides, because "is this worth a
badge" is not the same question on a race card as on a horse's own history.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["for_race", "for_horse", "SIGNIFICANT", "RECENT_DAYS",
           "ROUTINE_DAYS"]

# Categories that describe something that happened TO the horse, as opposed to
# a procedure it passed. A routine post-race examination is on almost every
# runner's record and a badge that fires on everyone stops being read.
SIGNIFICANT = frozenset({"RESPIRATORY", "CARDIAC", "PHYSICAL", "PERFORMANCE"})

# How far back a record still earns a badge. A bleeding attack matters for a
# season; a passed examination matters until the next one.
RECENT_DAYS = 365
ROUTINE_DAYS = 120


def _age_days(record_date: str, on: str) -> int | None:
    try:
        a = dt.date.fromisoformat(record_date)
        b = dt.date.fromisoformat(on)
    except (ValueError, TypeError):
        return None
    return (b - a).days


def _grade(row: dict[str, Any], age: int | None) -> str | None:
    """`significant`, `routine`, or None when it is too old to be worth a badge.

    Returning None is a decision, not an absence: the record stays in the
    database and stays available on the horse's own history. What it loses is
    the badge on today's card.
    """
    category = (row.get("category") or "UNKNOWN").upper()
    if age is None:
        # An unreadable date is not evidence of anything. Show it rather than
        # silently dropping it, and let the row say the date is unknown.
        return "significant" if category in SIGNIFICANT else "routine"
    if category in SIGNIFICANT:
        return "significant" if age <= RECENT_DAYS else None
    return "routine" if age <= ROUTINE_DAYS else None


def _rows(conn: Connection, sql: str, params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def for_race(date: str, race_no: int, *,
             conn: Connection | None = None) -> dict[str, list[dict[str, Any]]]:
    """Recent vet records for every runner in one race, keyed by horse name.

    Keyed on `horse_name`, never `horse_id` — `horse_id` is 0% populated from
    July 2026 and degrading from April, so a join on it returns partial history
    for every recent month.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _rows(conn, """
            SELECT v.horse_name, v.horse_no, v.record_date, v.detail,
                   v.passed_date, v.category
              FROM vet_records v
              JOIN runners r
                ON r.horse_name = v.horse_name
             WHERE r.race_date = ? AND r.race_no = ?
             ORDER BY v.horse_name, v.record_date DESC
        """, (date, race_no))

        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            age = _age_days(row["record_date"], date)
            grade = _grade(row, age)
            if grade is None:
                continue
            out.setdefault(row["horse_name"], []).append({
                "record_date": row["record_date"],
                "detail": row["detail"],
                "passed_date": row["passed_date"],
                "category": row["category"],
                "age_days": age,
                "grade": grade,
                # A clearance is the difference between "was lame" and "is
                # lame", and the badge must not read the same for both.
                "cleared": bool(row["passed_date"]),
            })
        return out
    finally:
        if own:
            conn.close()


def for_horse(horse_name: str, *, before: str | None = None, limit: int = 20,
              conn: Connection | None = None) -> list[dict[str, Any]]:
    """One horse's whole veterinary record, newest first.

    No recency filter. On a horse's own page the question is "what has this
    horse's history been", and an old record is part of the answer — the
    filtering above answers a different question about today's card.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = ("SELECT race_date, race_no, record_date, detail, passed_date, "
               "category FROM vet_records WHERE horse_name = ?")
        params: list[Any] = [horse_name.strip().upper()]
        if before:
            sql += " AND record_date <= ?"
            params.append(before)
        sql += " ORDER BY record_date DESC LIMIT ?"
        params.append(int(limit))

        on = before or dt.date.today().isoformat()
        out = []
        for row in _rows(conn, sql, params):
            age = _age_days(row["record_date"], on)
            out.append({**row, "age_days": age,
                        "grade": _grade(row, age) or "historic",
                        "cleared": bool(row["passed_date"])})
        return out
    finally:
        if own:
            conn.close()

"""Writers. Every one takes a list of plain dicts and is idempotent.

`INSERT ... ON CONFLICT DO UPDATE` throughout, so re-running a scrape after a
partial failure converges rather than duplicating. Every writer returns the row
count it wrote, because a job that reports nothing looks identical whether it
worked or silently did nothing — which is how the old pace column emptied for
weeks without anyone noticing.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = [
    "upsert_races", "upsert_runners", "upsert_dividends",
    "upsert_comments", "upsert_odds_snapshots", "upsert_odds_pairs",
    "upsert_trials",
]

Row = dict[str, Any]


def _upsert(
    conn: sqlite3.Connection, table: str, cols: Sequence[str],
    keys: Sequence[str], rows: Sequence[Row],
) -> int:
    """Generic idempotent write. Non-key columns are refreshed on conflict."""
    if not rows:
        return 0
    updates = [c for c in cols if c not in keys]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in updates)
    ) if updates else (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO NOTHING"
    )
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


# HK has two tracks and a small set of rail configurations. They are stored in
# separate columns and the values do not overlap, so a swap is detectable --
# and worth detecting: the legacy migration had them the wrong way round for
# all 1,712 races. SARR's Happy Valley style modifier never fired once, and its
# venue-mismatch weight penalised a change of RAIL rather than a change of
# TRACK, moving 23.5% of rankings and the top pick in 139 races. Nothing
# errored; every page just quietly said COURSE HV.
VENUES = frozenset({"ST", "HV"})


def _check_venue(row: dict) -> None:
    venue, course = row.get("venue"), row.get("course")
    if venue and venue not in VENUES:
        raise ValueError(
            f"venue must be one of {sorted(VENUES)}, got {venue!r} "
            f"(course is {course!r} -- are the two columns swapped?)")
    if course and course in VENUES:
        raise ValueError(
            f"course holds a venue code {course!r} -- the columns are swapped")


def upsert_races(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [{
        "race_date": coerce.to_date(r.get("race_date")),
        "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
        "venue": r.get("venue"),
        "course": r.get("course"),
        "surface": r.get("surface"),
        "going": r.get("going"),
        "distance": coerce.to_int(r.get("distance"), field="distance"),
        "race_class": r.get("race_class"),
        "race_name": r.get("race_name"),
        "off_time": r.get("off_time"),
    } for r in rows]
    for row in prepared:
        _check_venue(row)
    cols = ["race_date", "race_no", "venue", "course", "surface", "going",
            "distance", "race_class", "race_name", "off_time"]
    return _upsert(conn, "races", cols, ["race_date", "race_no"], prepared)


def upsert_runners(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = []
    for r in rows:
        place, code, dead_heat = coerce.to_place(r.get("place"))
        prepared.append({
            "race_date": coerce.to_date(r.get("race_date")),
            "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
            "horse_no": coerce.to_int(r.get("horse_no"), field="horse_no"),
            "horse_name": (r.get("horse_name") or "").strip().upper(),
            "place": place,
            "place_code": code,
            "dead_heat": int(dead_heat),
            "finish_time": coerce.parse_finish_time(r.get("finish_time")),
            "lengths_behind": coerce.parse_lbw(r.get("lengths_behind")),
            "draw": coerce.to_int(r.get("draw"), field="draw"),
            "jockey": r.get("jockey"),
            "trainer": r.get("trainer"),
            "actual_weight": coerce.to_int(r.get("actual_weight"), field="actual_weight"),
            "declared_weight": coerce.to_int(r.get("declared_weight"), field="declared_weight"),
            "gear": r.get("gear"),
            "rating": coerce.to_int(r.get("rating"), field="rating"),
            "win_odds": coerce.to_odds(r.get("win_odds")),
            "section_times": r.get("section_times"),
            "running_positions": r.get("running_positions"),
        })
    cols = ["race_date", "race_no", "horse_no", "horse_name", "place", "place_code",
            "dead_heat", "finish_time", "lengths_behind", "draw", "jockey", "trainer",
            "actual_weight", "declared_weight", "gear", "rating", "win_odds",
            "section_times", "running_positions"]
    return _upsert(conn, "runners", cols, ["race_date", "race_no", "horse_no"], prepared)


def upsert_dividends(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [{
        "race_date": coerce.to_date(r.get("race_date")),
        "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
        "pool": (r.get("pool") or "").strip().upper(),
        "combination": str(r.get("combination") or "").strip(),
        "dividend_per_10": coerce.to_odds(r.get("dividend_per_10")),
    } for r in rows]
    cols = ["race_date", "race_no", "pool", "combination", "dividend_per_10"]
    return _upsert(conn, "dividends", cols,
                   ["race_date", "race_no", "pool", "combination"], prepared)


def upsert_comments(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [{
        "race_date": coerce.to_date(r.get("race_date")),
        "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
        "horse_no": coerce.to_int(r.get("horse_no"), field="horse_no"),
        "comment_text": r.get("comment_text"),
        "source": r.get("source") or "incident",
    } for r in rows]
    cols = ["race_date", "race_no", "horse_no", "comment_text", "source"]
    return _upsert(conn, "runner_comments", cols,
                   ["race_date", "race_no", "horse_no", "source"], prepared)


def upsert_odds_snapshots(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    """Append-only in practice: captured_at is part of the key, and nothing
    in this system is ever permitted to delete from this table."""
    prepared = [{
        "race_date": coerce.to_date(r.get("race_date")),
        "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
        "horse_no": coerce.to_int(r.get("horse_no"), field="horse_no"),
        "captured_at": r.get("captured_at"),
        "win_odds": coerce.to_odds(r.get("win_odds")),
        "place_odds": coerce.to_odds(r.get("place_odds")),
    } for r in rows]
    cols = ["race_date", "race_no", "horse_no", "captured_at", "win_odds", "place_odds"]
    return _upsert(conn, "odds_snapshots", cols,
                   ["race_date", "race_no", "horse_no", "captured_at"], prepared)


def upsert_trials(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = []
    for r in rows:
        place, _code, _dh = coerce.to_place(r.get("place"))
        prepared.append({
            "trial_date": coerce.to_date(r.get("trial_date")),
            "trial_no": coerce.to_int(r.get("trial_no"), field="trial_no"),
            "horse_name": (r.get("horse_name") or "").strip().upper(),
            "place": place,
            "finish_time": coerce.parse_finish_time(r.get("finish_time")),
            "section_times": r.get("section_times"),
            "running_positions": r.get("running_positions"),
            "venue": r.get("venue"),
            "surface": r.get("surface"),
            "gear": r.get("gear"),
            "comment_text": r.get("comment_text"),
        })
    cols = ["trial_date", "trial_no", "horse_name", "place", "finish_time",
            "section_times", "running_positions", "venue", "surface", "gear",
            "comment_text"]
    return _upsert(conn, "trials", cols,
                   ["trial_date", "trial_no", "horse_name"], prepared)


def upsert_odds_pairs(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    """Quinella / quinella-place matrices. Append-only, like the win/place
    snapshots: captured_at is part of the key and nothing deletes from here."""
    prepared = [{
        "race_date": coerce.to_date(r.get("race_date")),
        "race_no": coerce.to_int(r.get("race_no"), field="race_no"),
        "pool": (r.get("pool") or "").strip().upper(),
        "horse_a": coerce.to_int(r.get("horse_a"), field="horse_a"),
        "horse_b": coerce.to_int(r.get("horse_b"), field="horse_b"),
        "captured_at": r.get("captured_at"),
        "odds": coerce.to_odds(r.get("odds")),
    } for r in rows]
    cols = ["race_date", "race_no", "pool", "horse_a", "horse_b", "captured_at", "odds"]
    return _upsert(conn, "odds_pairs", cols,
                   ["race_date", "race_no", "pool", "horse_a", "horse_b", "captured_at"],
                   prepared)

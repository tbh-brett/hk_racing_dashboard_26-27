"""The Screen's recorded order per race — written once, never rewritten.

See `screen_pick` in schema.sql for why this is kept rather than derived.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = ["record", "unrecorded_dates"]

_COLS = ("race_date", "race_no", "horse_no", "rank", "win_pct", "place_pct",
         "tier", "version", "fitted", "recorded_at")


def record(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    """Store what is not stored yet. Returns how many rows were added: a
    runner already recorded keeps the row it has."""
    prepared = [(
        coerce.to_date(r["race_date"]),
        coerce.to_int(r["race_no"], field="race_no"),
        coerce.to_int(r["horse_no"], field="horse_no"),
        coerce.to_int(r["rank"], field="rank"),
        float(r["win_pct"]) if r.get("win_pct") is not None else None,
        float(r["place_pct"]) if r.get("place_pct") is not None else None,
        r.get("tier"), r["version"], r["fitted"], r["recorded_at"],
    ) for r in rows]
    if not prepared:
        return 0
    before = conn.total_changes
    conn.executemany(
        f"INSERT INTO screen_pick ({', '.join(_COLS)}) VALUES "
        f"({', '.join('?' for _ in _COLS)}) "
        "ON CONFLICT (race_date, race_no, horse_no) DO NOTHING", prepared)
    return conn.total_changes - before


def unrecorded_dates(conn: sqlite3.Connection, after: str) -> list[str]:
    """Meetings after `after` with a settled race the Screen has no record
    of. A settled race is one with a placing stored on any runner."""
    return [r[0] for r in conn.execute(
        "WITH settled AS ("
        "  SELECT DISTINCT race_date, race_no FROM runners"
        "   WHERE race_date > ? AND place IS NOT NULL),"
        " kept AS ("
        "  SELECT DISTINCT race_date, race_no FROM screen_pick"
        "   WHERE race_date > ?)"
        "SELECT DISTINCT s.race_date FROM settled s"
        "  LEFT JOIN kept k ON k.race_date = s.race_date"
        "   AND k.race_no = s.race_no"
        " WHERE k.race_no IS NULL ORDER BY s.race_date", (after, after))]

"""Brett's race-day reads: where a placed horse came in the straight (the API's write path).

HKJC's Comments on Running say it, days later; on the day it is his eye on the replay. One read per
horse -- rail, middle or outside -- and None takes it back; `clear` removes a race's reads at once.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from hkrd.store import day as store
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["save", "clear"]


def save(race_date: str, race_no: int, horse_no: int, lane: str | None, *, db: Path | None = None) -> dict:
    """Store one read; returns what is now stored (lane None once removed)."""
    at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            store.save_lane(conn, race_date, int(race_no), int(horse_no), lane or None, at=at)
    finally:
        conn.close()
    return dict(race_date=race_date, race_no=int(race_no), horse_no=int(horse_no), lane=lane or None,
                entered_at=at if lane else None)


def clear(race_date: str, race_no: int, *, db: Path | None = None) -> dict:
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            n = store.clear_race(conn, race_date, int(race_no))
    finally:
        conn.close()
    return dict(race_date=race_date, race_no=int(race_no), cleared=n)

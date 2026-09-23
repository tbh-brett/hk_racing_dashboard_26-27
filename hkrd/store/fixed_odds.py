"""The write path for bookmakers' fixed odds. Coerced here, on the way in."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = ["upsert_fixed_odds"]


def _price(value: object) -> float | None:
    """A fixed price, or None where none is offered. A price of 1.0 or less
    pays nothing back and is a placeholder, not a price."""
    if value in (None, "", 0):
        return None
    v = float(value)
    return v if v > 1.0 else None


def upsert_fixed_odds(conn: sqlite3.Connection,
                      rows: Sequence[dict[str, Any]]) -> int:
    prepared = [(
        str(r["bookmaker"]).strip().lower(),
        coerce.to_date(r["race_date"]),
        coerce.to_int(r["race_no"], field="race_no"),
        coerce.to_int(r["horse_no"], field="horse_no"),
        str(r["captured_at"]),
        _price(r.get("win")), _price(r.get("place")),
        int(bool(r.get("scratched"))),
    ) for r in rows]
    if not prepared:
        return 0
    conn.executemany(
        "INSERT INTO fixed_odds (bookmaker, race_date, race_no, horse_no, "
        "  captured_at, win, place, scratched) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT (bookmaker, race_date, race_no, horse_no, captured_at) "
        "DO UPDATE SET win = excluded.win, place = excluded.place, "
        "  scratched = excluded.scratched", prepared)
    return len(prepared)

"""`horse_background` — where each import came from (ingest/newhorse).

The latest profile of a horse is the one kept: HKJC lists a horse again when
its first declaration was scratched, and the later text is the later word.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = ["upsert_backgrounds", "profiled", "COLS"]

COLS = ("horse_name", "brand_no", "profiled_for", "path", "url", "origin",
        "import_type", "sire", "dam", "sire_hk_starters", "sire_hk_winners",
        "sale_kind", "sale_ccy", "sale_amount", "sale_aud", "sales_text",
        "prev_name", "prev_trainer", "prev_owner", "buyer", "prev_country",
        "agents", "overseas_starts", "overseas_wins", "trial_won", "about",
        "fetched_at")
_INTS = {"sire_hk_starters", "sire_hk_winners", "overseas_starts",
         "overseas_wins"}
_REALS = {"sale_amount", "sale_aud"}


def _value(col: str, v: Any) -> Any:
    if v is None or v == "" or v == []:
        return None
    if col == "horse_name":
        return str(v).strip().upper()
    if col == "profiled_for":
        return coerce.to_date(v)
    if col in _INTS:
        return coerce.to_int(v, field=col)
    if col in _REALS:
        return float(v)
    if col == "trial_won":
        return int(bool(v))
    if col == "agents":
        return ",".join(v) if isinstance(v, (list, tuple)) else str(v)
    return str(v).strip() or None


def upsert_backgrounds(conn: sqlite3.Connection,
                       rows: Sequence[dict[str, Any]]) -> int:
    prepared = [tuple(_value(c, r.get(c)) for c in COLS) for r in rows]
    if not prepared:
        return 0
    sets = ", ".join(f"{c} = excluded.{c}" for c in COLS if c != "horse_name")
    conn.executemany(
        f"INSERT INTO horse_background ({', '.join(COLS)}) VALUES "
        f"({', '.join('?' for _ in COLS)}) "
        f"ON CONFLICT (horse_name) DO UPDATE SET {sets}", prepared)
    return len(prepared)


def profiled(conn: sqlite3.Connection) -> dict[str, str]:
    """horse -> the race date its stored profile was written for. A horse
    listed twice is fetched again only for a profile newer than this; by
    address alone, its two entries would overwrite each other every run."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT horse_name, profiled_for FROM horse_background")}

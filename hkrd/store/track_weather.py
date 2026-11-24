"""`track_weather` and `going_readings`: HKJC's own instruments, coerced here.

Both are raw in the strict sense -- HKJC's readings as published, no figure
computed from them -- and both are append-only in practice: every row is keyed
on the moment the instrument read, so a later capture adds rows and never
corrects one. Nothing deletes from either. The wind cannot be fetched again at
all, and the going readings only for about two months.

The two readers at the bottom are what `jobs/scrape_track_weather` asks the
database before it asks HKJC anything: when today's meeting runs, and which
meetings' going readings are still outstanding.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from . import coerce

__all__ = ["upsert_track_weather", "upsert_going_readings", "card_times",
           "going_pending", "WEATHER_COLS", "GOING_COLS", "INSTRUMENTS"]

VENUES = frozenset({"ST", "HV"})
INSTRUMENTS = frozenset({"penetrometer", "hammer"})

WEATHER_COLS = ("venue", "location", "observed_at", "captured_at", "wind_dir",
                "wind_speed", "gust_dir", "gust_speed", "temperature",
                "humidity", "rain_10min", "rain_day", "soil_water", "is_valid")
_WEATHER_KEY = ("venue", "location", "observed_at")
_REALS = ("wind_dir", "wind_speed", "gust_dir", "gust_speed", "temperature",
          "humidity", "rain_10min", "rain_day", "soil_water")

GOING_COLS = ("race_date", "venue", "instrument", "read_at", "reading")

# A later sighting of the same reading may fill a value the first one lacked,
# and never blanks one: `coalesce`, the rule `upsert._upsert` gives every
# table. `captured_at` is left out of the SET on purpose -- it stays the FIRST
# capture that saw the reading, which is the nearest this record can say to
# when HKJC published it.
_WEATHER_SQL = (
    f"INSERT INTO track_weather ({', '.join(WEATHER_COLS)}) "
    f"VALUES ({', '.join('?' for _ in WEATHER_COLS)}) "
    f"ON CONFLICT ({', '.join(_WEATHER_KEY)}) DO UPDATE SET "
    + ", ".join(f"{c} = coalesce(excluded.{c}, track_weather.{c})"
                for c in WEATHER_COLS
                if c not in _WEATHER_KEY and c != "captured_at"))

_GOING_SQL = (
    f"INSERT INTO going_readings ({', '.join(GOING_COLS)}) "
    f"VALUES ({', '.join('?' for _ in GOING_COLS)}) "
    "ON CONFLICT (race_date, venue, instrument, read_at) "
    "DO UPDATE SET reading = excluded.reading")


def _venue(value: Any) -> str:
    v = str(value or "").strip().upper()
    if v not in VENUES:
        raise coerce.CoerceError(f"venue: expected ST or HV, got {value!r}")
    return v


def _stamp(value: Any, *, field: str) -> str:
    """An ISO-8601 timestamp, kept as written once it is known to be one."""
    s = str(value or "").strip()
    try:
        datetime.fromisoformat(s)
    except ValueError:
        raise coerce.CoerceError(
            f"{field}: not an ISO-8601 timestamp {value!r}") from None
    return s


def _flag(value: Any) -> int | None:
    v = coerce.to_int(value, field="is_valid")
    if v not in (None, 0, 1):
        raise coerce.CoerceError(f"is_valid: expected 0 or 1, got {value!r}")
    return v


def upsert_track_weather(conn: sqlite3.Connection,
                         rows: Sequence[dict[str, Any]]) -> int:
    """One row per reading of one instrument. Returns the rows written."""
    prepared = []
    for r in rows:
        location = str(r.get("location") or "").strip().upper()
        if not location:
            raise coerce.CoerceError("location: a reading must name its "
                                     "instrument")
        row = {"venue": _venue(r.get("venue")), "location": location,
               "observed_at": _stamp(r.get("observed_at"), field="observed_at"),
               "captured_at": _stamp(r.get("captured_at"), field="captured_at"),
               "is_valid": _flag(r.get("is_valid"))}
        row.update({c: coerce.to_real(r.get(c), field=c) for c in _REALS})
        prepared.append(tuple(row[c] for c in WEATHER_COLS))
    if not prepared:
        return 0
    conn.executemany(_WEATHER_SQL, prepared)
    return len(prepared)


def upsert_going_readings(conn: sqlite3.Connection,
                          rows: Sequence[dict[str, Any]]) -> int:
    """One row per reading HKJC took. A re-read replaces the value: HKJC
    publishing the same reading time again is its later word on it."""
    prepared = []
    for r in rows:
        instrument = str(r.get("instrument") or "").strip().lower()
        if instrument not in INSTRUMENTS:
            raise coerce.CoerceError(
                f"instrument: expected one of {sorted(INSTRUMENTS)}, got "
                f"{r.get('instrument')!r}")
        value = coerce.to_real(r.get("reading"), field="reading")
        if value is None:
            raise coerce.CoerceError(
                f"reading: {r.get('race_date')} {instrument} at "
                f"{r.get('read_at')} has no value; a reading without one is "
                f"not a reading")
        prepared.append((coerce.to_date(r.get("race_date")),
                         _venue(r.get("venue")), instrument,
                         _stamp(r.get("read_at"), field="read_at"), value))
    if not prepared:
        return 0
    conn.executemany(_GOING_SQL, prepared)
    return len(prepared)


def card_times(conn: sqlite3.Connection, date: str
               ) -> tuple[str | None, list[str | None]]:
    """The venue and every race's off time on one date's stored card.

    An empty list is no meeting stored. An off time is returned as written
    ("18:45"), or None where the card carries none.
    """
    rows = conn.execute(
        "SELECT venue, off_time FROM races WHERE race_date = ? "
        "ORDER BY race_no", (date,)).fetchall()
    venue = next((r["venue"] for r in rows if r["venue"]), None)
    return venue, [r["off_time"] for r in rows]


def going_pending(conn: sqlite3.Connection, *, since: str, recheck_from: str,
                  until: str) -> list[str]:
    """Stored meeting dates whose going readings are worth asking for, newest
    first.

    Every meeting from `since` to `until` with no reading stored, and every one
    from `recheck_from` on whether it has some or not: a meeting's pre-race
    reading lands after a morning run that already stored its first.
    """
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT r.race_date FROM races r "
        " WHERE r.race_date BETWEEN ? AND ? "
        "   AND (r.race_date >= ? OR NOT EXISTS ("
        "        SELECT 1 FROM going_readings g "
        "         WHERE g.race_date = r.race_date)) "
        " ORDER BY r.race_date DESC", (since, until, recheck_from))]

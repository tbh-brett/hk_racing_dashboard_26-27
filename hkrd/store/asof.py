"""A copy of the database as it stood on the morning of one meeting.

The fundamental model's leak test (gbm-SPEC §14.2). A feature can leak in more
ways than being trained on the race it scores -- a rider's strike rate that
includes the day, a "last run" that is really a later one -- and the one check
that closes all of them at once is to rebuild the world as it was: copy the
database, delete everything dated on or after the meeting from every table,
and put the meeting back as a card, with only what HKJC publishes before the
off. Features built from that copy must equal the ones built from the full
database, value for value.

The card is the FINAL field: the runners that started, never the scratched --
the lab's replay made the same simplification, and a scratching re-divides a
race rather than changing any runner's inputs.

Writes only to the copy. The source is opened read-only and read through
SQLite's backup, so a copy of a live WAL database is consistent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["cut_copy", "RESULT_COLS"]

# Everything on a runner row that is only known once the race is run.
RESULT_COLS = ("place", "place_code", "dead_heat", "finish_time", "lengths_behind",
               "win_odds", "section_times", "running_positions")


def cut_copy(src: str | Path, dst: str | Path, date: str, *,
             night_before: bool = False) -> dict[str, int]:
    """Write to `dst` the database as it stood before `date`'s first race.

    Race morning by default: the declared body weights and the going are on
    the card. `night_before` blanks both, as they are when the card lands.
    Returns the checks: card runners, result cells left on the card (must be
    0) and runners dated after `date` (must be 0)."""
    dst = Path(dst)
    if dst.exists():
        dst.unlink()
    source = f"file:{Path(src).as_posix()}?mode=ro"
    s, d = sqlite3.connect(source, uri=True), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    # uri=True so the ATTACH below reads `source` as a read-only URI, not as a
    # literal file name that SQLite would create empty
    conn = sqlite3.connect(f"file:{dst.as_posix()}", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        conn.execute("ATTACH DATABASE ? AS f", (source,))   # outside any transaction
        with conn:
            for t in tables:
                cols = {r[1] for r in conn.execute(f'PRAGMA main.table_info("{t}")')}
                for c in ("race_date", "trial_date"):
                    if c in cols:
                        conn.execute(f'DELETE FROM main."{t}" WHERE {c} >= ?', (date,))
            conn.execute("INSERT INTO main.races SELECT * FROM f.races WHERE race_date = ?", (date,))
            rcols = [r[1] for r in conn.execute("PRAGMA main.table_info(runners)")]
            blank = {"dead_heat": "0"}
            picked = ", ".join((blank.get(c, "NULL") if c in RESULT_COLS else c) for c in rcols)
            conn.execute(f"INSERT INTO main.runners ({', '.join(rcols)}) SELECT {picked} "
                         "FROM f.runners WHERE race_date = ? AND win_odds > 0", (date,))
            if night_before:
                conn.execute("UPDATE main.runners SET declared_weight = NULL WHERE race_date = ?",
                             (date,))
                conn.execute("UPDATE main.races SET going = NULL WHERE race_date = ?", (date,))
        conn.execute("DETACH DATABASE f")
        one = lambda sql: conn.execute(sql, (date,)).fetchone()[0]  # noqa: E731
        return {
            "card_runners": one("SELECT count(*) FROM runners WHERE race_date = ?"),
            "result_cells_on_card": one(
                "SELECT count(*) FROM runners WHERE race_date = ? AND (place IS NOT NULL "
                "OR win_odds IS NOT NULL OR finish_time IS NOT NULL "
                "OR running_positions IS NOT NULL)"),
            "runners_after": one("SELECT count(*) FROM runners WHERE race_date > ?"),
        }
    finally:
        conn.close()

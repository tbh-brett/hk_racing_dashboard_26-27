"""Database connections. The only module in the package that imports sqlite3.

WAL is not optional here. The scraper writes while the API reads, twice a week,
on exactly the days the system must not stall — in the default rollback journal
mode a writer blocks every reader for the length of its transaction.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["get_conn", "transaction", "init_db", "db_path", "Connection",
           "StoreError", "CACHE_KIB", "MMAP_BYTES"]

# Layers above store/ need to name a connection in a type hint without
# importing the driver. They depend on this alias, so the driver stays
# swappable and the "only store/ imports sqlite3" rule stays literal.
Connection = sqlite3.Connection

# Re-exported so a caller outside store/ can catch a write that the database
# refused without importing sqlite3 itself — which the layering forbids, and
# for good reason: `import sqlite3` in a job is one line away from a query in
# a job. A job needs the EXCEPTION, not the driver.
StoreError = sqlite3.Error

_SCHEMA = Path(__file__).with_name("schema.sql")

# How much of the database to keep in memory. Measured rather than picked: the
# file is 38 MB (9,475 pages of 4 KB) and SQLite's default cache is 2 MB, so a
# query touching a fifth of the archive re-read most of it from disk every
# time. The Lookup page's insight panel was the worst of it. The machine has
# 1 GB and holds numpy, scipy and pandas resident; 64 MB is comfortably inside
# what is left and comfortably outside the size of the data.
CACHE_KIB = 64_000

# 256 MB of address space for the memory map, which is a ceiling and not an
# allocation — SQLite maps up to the size of the file. Room for the archive to
# grow several times over before this stops covering it.
MMAP_BYTES = 268_435_456


def db_path() -> Path:
    """Resolved from HKRD_DB, defaulting to ./hkrd.db for local work."""
    return Path(os.environ.get("HKRD_DB", "hkrd.db")).expanduser()


def get_conn(path: str | Path | None = None) -> sqlite3.Connection:
    """A configured connection. Callers outside store/ should not need this."""
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")   # WAL makes FULL unnecessary
    conn.execute("PRAGMA busy_timeout = 30000")   # wait out the scraper, don't fail
    # 64 MB of page cache, against a default of 2 MB and a database of 38 MB.
    # SQLite's default is sized for a machine that might be running a hundred
    # of these; this one runs one, on 1 GB, and the whole archive is smaller
    # than the cache. The negative form is KIBIBYTES rather than pages, so it
    # does not silently change meaning if the page size ever does.
    conn.execute(f"PRAGMA cache_size = -{CACHE_KIB}")
    # Read the file through the page cache the OS already has, instead of
    # copying every page into the process on the way past. The whole database
    # fits, so this is the difference between a read costing a memcpy and a
    # read costing a syscall.
    conn.execute(f"PRAGMA mmap_size = {MMAP_BYTES}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False
                ) -> Iterator[sqlite3.Connection]:
    """One atomic unit. Rolls back and re-raises — never swallows.

    `immediate` takes the write lock UP FRONT instead of on the first write,
    and it is the answer to a specific deadlock rather than a tuning knob.

    A plain `BEGIN` is deferred. A transaction that READS and then writes takes
    a read lock first and tries to upgrade — and in WAL mode, if another writer
    committed in between, that upgrade fails with SQLITE_BUSY *immediately*.
    The busy timeout does not help: SQLite refuses to wait there on purpose,
    because both holders would be waiting on each other. So the losing side
    gets "database is locked" no matter how long it is willing to wait.

    Measured, because it is the difference between a deploy and an outage: two
    processes calling `init_db` on the same un-migrated database failed 12 times
    out of 12. That is not a hypothetical — `ops/entrypoint.sh` starts cron
    BEFORE uvicorn, `scrape_odds` runs every minute and calls `init_db`, and so
    does the API's startup hook. The first boot after a schema change is
    exactly two processes racing to migrate one file.

    With `immediate` the second one simply waits out `busy_timeout`, then reads
    a schema the first has already migrated and does nothing.
    """
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql, then bring an older database up to its shape.

    Idempotent in both halves — every statement in the file is
    `CREATE ... IF NOT EXISTS`, and every migration below checks the table
    before it touches it.
    """
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    _migrate(conn)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Changes `schema.sql` cannot make on a database that already exists.

    `CREATE TABLE IF NOT EXISTS` is a no-op against a table that is already
    there, so a column added to the file reaches a fresh database and never the
    one the season is being run on — and then every reader of that column fails
    on the only database that matters. These run on the way in instead.
    """
    # IMMEDIATE, because every migration below reads the schema before it
    # changes it, and a deferred transaction cannot upgrade that read to a
    # write once another process has committed. See `transaction`.
    with transaction(conn, immediate=True):
        _migrate_blackbook_close(conn)
        _migrate_blackbook_prefs(conn)
        _migrate_races_restricted(conn)


def _migrate_races_restricted(conn: sqlite3.Connection) -> None:
    """`races.restricted`: is eligibility narrower than a rating band.

    Adds the column and decides nothing. The archive's classes are wrong in
    ways only HKJC can settle -- 196 races with none, two carrying a grade
    read off the site's menu -- so the values are left for `jobs/repair
    --only classes`, which asks HKJC, rather than guessed here.
    """
    cols = _columns(conn, "races")
    if cols and "restricted" not in cols:
        conn.execute("ALTER TABLE races ADD COLUMN restricted INTEGER")


def _migrate_blackbook_prefs(conn: sqlite3.Connection) -> None:
    """`pref_distance`, `pref_surface` and `pref_jockey` become conditions.

    They were the first attempt at saying what a thesis depends on, and they
    were write-only: the legacy import filled them and not one line anywhere
    else in the codebase ever read one back. So an entry could record that a
    horse wants 1200m on Dirt and nothing was ever going to check whether
    today's race was that race.

    `blackbook_trigger` is read — by the band, the record and the Form Guide —
    so the values move there and the dead columns go. `pref_distance` is a csv
    in the export ("1200,1400"), which is exactly the `in` operator.

    Safe to re-run: after the first pass the columns are gone, and the guard
    below stops a second copy of the rows if they are not.
    """
    cols = _columns(conn, "blackbook")
    if not cols or "pref_distance" not in cols:
        return

    for kind, column, op in (("distance", "pref_distance", "in"),
                             ("surface", "pref_surface", "is"),
                             ("jockey", "pref_jockey", "is")):
        conn.execute(f"""
            INSERT INTO blackbook_trigger (id, kind, op, value)
            SELECT b.id, ?, ?, trim(b.{column})
              FROM blackbook b
             WHERE b.{column} IS NOT NULL AND trim(b.{column}) != ''
               AND NOT EXISTS (SELECT 1 FROM blackbook_trigger g
                                WHERE g.id = b.id AND g.kind = ?)
        """, (kind, op, kind))

    # A single-valued `in` is an `is` said the long way. Both work, but the
    # page prints the operator and "distance 1200" reads better than
    # "distance in 1200".
    conn.execute("UPDATE blackbook_trigger SET op = 'is' "
                 "WHERE op = 'in' AND instr(value, ',') = 0")

    if sqlite3.sqlite_version_info >= (3, 35):
        for column in ("pref_distance", "pref_surface", "pref_jockey"):
            conn.execute(f"ALTER TABLE blackbook DROP COLUMN {column}")


def _migrate_blackbook_close(conn: sqlite3.Connection) -> None:
    """`expiry_date` becomes `closed_date`, and expiry closes NOTHING.

    The two were the same thing said twice. An entry could be RETIRED by the
    button on the row, or it could go quiet on its own ninety days after it was
    written because `promote_to_blackbook` stamped a date nobody chose — and
    the page then printed EXPIRED beside RETIRED as though they were different
    outcomes. They are not: both mean the thesis is no longer being followed,
    and only one of them was a decision.

    THE DEAD CLOCK DOES NOT GET A LAST RUN. An earlier version of this
    migration read the lapsed dates one final time and retired the entries
    behind them. Measured against the owner's book that was 147 of 179 entries,
    16 of them declared to run that evening — a migration quietly making
    hundreds of decisions on the way past, which is the exact thing the change
    exists to stop. An expiry was never a decision, so nothing is decided from
    one here: every entry keeps the status a person gave it, and `status` alone
    now says whether a thesis is being followed.

      - an entry whose expiry had passed stays ACTIVE, and is retired by the
        button on its row if and when the owner decides that
      - an entry marked `expired` — closed by the clock and by nothing else —
        goes back to ACTIVE for the same reason
      - an entry retired or won out by hand is left exactly as it is, with a
        NULL date that reads as "closed, day unknown" and never as "closed on
        day zero"

    The column is dropped once it can decide nothing: what it held is
    `added_date` plus ninety days, so no fact is lost with it. Safe to re-run —
    after the first pass there is no column left to find.
    """
    cols = _columns(conn, "blackbook")
    if not cols:
        return                                   # no blackbook table yet
    for name in ("closed_date", "closed_reason"):
        if name not in cols:
            conn.execute(f"ALTER TABLE blackbook ADD COLUMN {name} TEXT")
    if "expiry_date" not in cols:
        return                                   # already migrated

    # The one status change here, and it un-closes rather than closes: a clock
    # was the only thing that ever wrote `expired`, and the clock is gone.
    conn.execute("UPDATE blackbook SET status = 'active' "
                 "WHERE status = 'expired'")
    # SQLite has had DROP COLUMN since 3.35 (2021); the deploy image is
    # Debian bookworm, which carries 3.40. Nothing reads the column by the
    # time this runs, so a database on something older is merely carrying an
    # inert one rather than a broken one.
    if sqlite3.sqlite_version_info >= (3, 35):
        conn.execute("ALTER TABLE blackbook DROP COLUMN expiry_date")

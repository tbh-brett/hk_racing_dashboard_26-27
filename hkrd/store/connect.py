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
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One atomic unit. Rolls back and re-raises — never swallows."""
    conn.execute("BEGIN")
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
    with transaction(conn):
        _migrate_blackbook_close(conn)


def _migrate_blackbook_close(conn: sqlite3.Connection) -> None:
    """`expiry_date` becomes `closed_date`, and expiry stops closing anything.

    The two were the same thing said twice. An entry could be RETIRED by the
    button on the row, or it could go quiet on its own ninety days after it was
    written because `promote_to_blackbook` stamped a date nobody chose — and
    the page then printed EXPIRED beside RETIRED as though they were different
    outcomes. They are not: both mean the thesis is no longer being followed,
    and only one of them was a decision.

    The date itself is worth keeping, because it is the only record of WHEN an
    entry stopped being live, which is what an archived card needs to answer
    "was I watching this horse that day". So it moves rather than being dropped:

      - an entry whose expiry had passed is now RETIRED, closed on that date
      - an entry still inside its window keeps running, with no closing date
      - an entry retired by hand before this migration keeps a NULL date, which
        reads as "closed, day unknown" and never as "closed on day zero"

    Only then is the column dropped, so no meaning is destroyed ahead of being
    carried over. Safe to re-run: after the first pass there is no column left
    to find.
    """
    cols = _columns(conn, "blackbook")
    if not cols:
        return                                   # no blackbook table yet
    for name in ("closed_date", "closed_reason"):
        if name not in cols:
            conn.execute(f"ALTER TABLE blackbook ADD COLUMN {name} TEXT")
    if "expiry_date" not in cols:
        return                                   # already migrated

    # Only an entry the expiry actually closed. A WON OUT entry is closed too,
    # but it is closed because the thesis PAID, and rewriting that as "retired"
    # would throw away the one outcome the book exists to count. Its expiry
    # date is not when it was won out either, so nothing is stamped from it.
    conn.execute("""
        UPDATE blackbook
           SET status = 'retired',
               closed_date = expiry_date,
               closed_reason = 'lapsed under the old 90-day expiry'
         WHERE status IN ('active', 'expired')
           AND closed_date IS NULL
           AND expiry_date IS NOT NULL AND expiry_date < date('now')
    """)
    # A status of 'expired' with no date behind it is the same decision with
    # the evidence missing. It is still a closed thesis, so it reads as one —
    # but with no reason invented for it, because none was recorded.
    conn.execute("UPDATE blackbook SET status = 'retired' "
                 "WHERE status = 'expired'")
    # Every close from here on is stamped, so this row is the record of the
    # ones that were not.
    conn.execute("""
        INSERT INTO blackbook_status_log
               (id, changed_at, from_status, to_status, reason, reasoning)
        SELECT b.id, coalesce(b.closed_date, b.added_date) || 'T00:00:00+00:00',
               'active', 'retired', b.closed_reason, b.reasoning
          FROM blackbook b
         WHERE b.closed_reason = 'lapsed under the old 90-day expiry'
           AND NOT EXISTS (SELECT 1 FROM blackbook_status_log l
                            WHERE l.id = b.id)
    """)
    # SQLite has had DROP COLUMN since 3.35 (2021); the deploy image is
    # Debian bookworm, which carries 3.40. Nothing reads the column by the
    # time this runs, so a database on something older is merely carrying an
    # inert one rather than a broken one.
    if sqlite3.sqlite_version_info >= (3, 35):
        conn.execute("ALTER TABLE blackbook DROP COLUMN expiry_date")

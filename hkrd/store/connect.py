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
    """Apply schema.sql. Idempotent — every statement is CREATE ... IF NOT EXISTS."""
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))

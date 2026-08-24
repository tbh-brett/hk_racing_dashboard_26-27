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

__all__ = ["get_conn", "transaction", "init_db", "db_path"]

_SCHEMA = Path(__file__).with_name("schema.sql")


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

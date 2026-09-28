"""The automatic blackbook's record of which days it has read.

See `auto_book_pass` in schema.sql. Written in the same transaction as the
entries a pass books, so a day is either read and booked or neither.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from . import coerce

__all__ = ["record_pass", "KINDS", "BASES"]

KINDS = ("results", "trials")
BASES = ("full", "stewards")


def record_pass(conn: sqlite3.Connection, *, kind: str, source_date: str,
                ran_at: str, basis: str, considered: int, flagged: int,
                picked: int, added: int, in_book: Sequence[str] = ()) -> None:
    """One day read. A day already recorded keeps its first row: re-reading
    it would book again horses the owner has since dismissed."""
    if kind not in KINDS:
        raise ValueError(f"auto_book_pass.kind must be one of {KINDS}, not {kind!r}")
    if basis not in BASES:
        raise ValueError(f"auto_book_pass.basis must be one of {BASES}, not {basis!r}")
    conn.execute(
        "INSERT INTO auto_book_pass (kind, source_date, ran_at, basis, "
        "considered, flagged, picked, added, in_book) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (kind, source_date) DO NOTHING",
        (kind, coerce.to_date(source_date), ran_at, basis,
         int(considered), int(flagged), int(picked), int(added),
         ",".join(in_book) or None))

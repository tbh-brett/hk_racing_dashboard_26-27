"""Correct a bet on the ledger: edit it, delete it, or put a deleted one back.

    python -m hkrd.jobs.edit_bet delete  3f9c2a1b7e --reason "entered twice"
    python -m hkrd.jobs.edit_bet restore 3f9c2a1b7e
    python -m hkrd.jobs.edit_bet edit    3f9c2a1b7e --account kelvin --stake 160

The dashboard calls the same functions. They are here, rather than in the
router, for the reason `jobs/place_bet` is: a router reads through `query/` and
triggers work through `jobs/`, and never reaches into `store/` itself.

What a correction does to the ledger, and why a re-import cannot undo one, is
written once in `store/bet_edits.py`.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from hkrd.query import prebet
from hkrd.store import bet_edits
from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["edit", "delete", "restore", "BetEditError"]

BetEditError = bet_edits.BetEditError


def _conn(db: str | None):
    conn = get_conn(db) if db else get_conn(db_path())
    init_db(conn)
    return conn


def edit(bet_id: str, *, changes: dict[str, Any] | None = None,
         selections: Sequence[dict[str, Any]] | None = None,
         db: str | None = None) -> dict[str, Any]:
    """Apply a correction. Only fields that actually differ are written."""
    conn = _conn(db)
    try:
        return bet_edits.edit_bet(
            conn, bet_id, changes=changes, selections=selections,
            accounts=[a["key"] for a in prebet.ACCOUNTS])
    finally:
        conn.close()


def delete(bet_id: str, *, reason: str | None = None,
           db: str | None = None) -> dict[str, Any]:
    """Take a bet off the ledger. Archived in full, so it can be restored."""
    conn = _conn(db)
    try:
        return bet_edits.delete_bet(conn, bet_id, reason=reason)
    finally:
        conn.close()


def restore(bet_id: str, *, db: str | None = None) -> dict[str, Any]:
    conn = _conn(db)
    try:
        return bet_edits.restore_bet(conn, bet_id)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("edit", "delete", "restore"))
    ap.add_argument("bet_id")
    ap.add_argument("--db", default=None)
    ap.add_argument("--reason", default=None)
    for field in bet_edits.EDITABLE:
        ap.add_argument(f"--{field.replace('_', '-')}", dest=field, default=None)
    args = ap.parse_args(argv)

    try:
        if args.action == "delete":
            out = delete(args.bet_id, reason=args.reason, db=args.db)
        elif args.action == "restore":
            out = restore(args.bet_id, db=args.db)
        else:
            changes = {f: getattr(args, f) for f in bet_edits.EDITABLE
                       if getattr(args, f) is not None}
            if not changes:
                ap.error("edit needs at least one field to change")
            out = edit(args.bet_id, changes=changes, db=args.db)
    except BetEditError as exc:
        print(f"refused: {exc}")
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())

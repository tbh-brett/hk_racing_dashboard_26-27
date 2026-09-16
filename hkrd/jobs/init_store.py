"""Bring the configured database up to the current schema.

    python -m hkrd.jobs.init_store

This is what makes a schema change reach a database that predates it, and the
API calls it at startup for exactly that reason: a table added in a later
release must not surface as a 500 on the page that reads it.

IT IS NO LONGER ONLY `CREATE ... IF NOT EXISTS`. That was true while every
change was additive, and it stopped being true when a column had to be
REPLACED rather than added — `store/connect._migrate` also ALTERs and, having
moved the meaning across, DROPs. Still safe to run on a full database and still
idempotent, but it is a migration and not a no-op, so it is worth knowing that
running it is the thing that changes the file.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["run"]


def run(db: Path | None = None) -> dict:
    """Returns the tables present afterwards, so a no-op is distinguishable
    from a failure that was swallowed."""
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {"database": str(db or db_path()), "tables": tables}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    out = run(a.db)
    print(f"  {out['database']}")
    print(f"  {len(out['tables'])} tables")
    for t in out["tables"]:
        print(f"    {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

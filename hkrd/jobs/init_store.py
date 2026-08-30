"""Apply schema.sql to the configured database.

    python -m hkrd.jobs.init_store

Every statement is CREATE ... IF NOT EXISTS, so this is safe to run on a full
database and is what makes a schema addition reach an existing one. The API
calls it at startup for exactly that reason: a table added in a later release
must not surface as a 500 on the page that reads it.
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

"""Run the dashboard locally.

    python -m hkrd.serve                 # http://127.0.0.1:8000
    python -m hkrd.serve --port 8899
    HKRD_DB=/path/to/hkrd.db python -m hkrd.serve

One command rather than a uvicorn invocation to remember, and it fails with a
useful message rather than an empty page when the database is missing or has
not been built — an empty dashboard and a missing database look identical in a
browser, which is the confusion this avoids.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from hkrd.store.connect import db_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--reload", action="store_true",
                    help="restart on code changes, for development")
    a = ap.parse_args(argv)

    if a.db:
        import os
        os.environ["HKRD_DB"] = str(a.db.resolve())

    target = Path(a.db or db_path()).resolve()
    if not target.exists():
        print(f"no database at {target}\n")
        print("build one from the old repo first:")
        print("    python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard")
        return 1

    from hkrd.store.connect import get_conn
    conn = get_conn(target)
    try:
        runners = conn.execute("SELECT count(*) FROM runners").fetchone()[0]
    except Exception:
        runners = 0
    finally:
        conn.close()
    if not runners:
        print(f"database at {target} has no runners.\n")
        print("run the import:")
        print("    python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard")
        return 1

    import uvicorn

    print(f"  database {target}  ({runners:,} runners)")
    print(f"  dashboard  http://{a.host}:{a.port}/pages/raceday.html")
    print(f"  API docs   http://{a.host}:{a.port}/docs\n")
    uvicorn.run("hkrd.api.app:app", host=a.host, port=a.port, reload=a.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

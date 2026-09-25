"""Fetch HKJC's course standard times and reference sectionals.

    python -m hkrd.jobs.scrape_standards

One request. HKJC revises the page once a season ("Last Update on
25-8-2026"), so `jobs/nightly` calls `refresh` and it fetches only when the
stored copy is a week old or missing. Race pace is read against these figures
(`derive/tempo`); with none stored, the tempo step says so rather than reading
every race as unmeasured.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from hkrd.ingest.standards import fetch_standards
from hkrd.store import upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "refresh", "STALE_AFTER_DAYS"]

STALE_AFTER_DAYS = 7


@dataclass
class StandardsReport:
    rows: int
    updated: str | None

    def render(self) -> str:
        return (f"  standard times     {self.rows:>6} cells"
                f"   (HKJC last updated {self.updated or 'unknown'})")


def scrape(db: Path | None = None, *, session=None,
           now: dt.datetime | None = None) -> StandardsReport:
    rows = fetch_standards(session=session)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            n = upsert.upsert_standard_times(conn, rows, fetched_at=stamp)
    finally:
        conn.close()
    return StandardsReport(rows=n, updated=max((r["updated"] or "" for r in rows),
                                               default=None) or None)


def refresh(db: Path | None = None, *, session=None,
            now: dt.datetime | None = None) -> StandardsReport | None:
    """Fetch only when the stored copy is missing or a week old."""
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        last = conn.execute("SELECT max(fetched_at) v FROM standard_times").fetchone()["v"]
    finally:
        conn.close()
    now = now or dt.datetime.now(dt.timezone.utc)
    if last and (now - dt.datetime.fromisoformat(last)).days < STALE_AFTER_DAYS:
        return None
    return scrape(db, session=session, now=now)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    print(scrape(a.db).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

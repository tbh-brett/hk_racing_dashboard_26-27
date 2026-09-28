"""Fetch every new horse's background from HKJC's "Intro to New Horses".

    python -m hkrd.jobs.scrape_background              # what is not stored yet
    python -m hkrd.jobs.scrape_background --refresh    # every profile again

One request for the index, one per profile not already stored. A profile is
written for the race a horse is first declared for and not revised after, so
a routine run fetches the ten or twenty new ones a week and stops; the first
run fetches the whole index, about 600 at 1.2s each plus HKJC's answer —
run it with `nice -n 19` on the server, like any long job. Progress is
committed every twenty horses, so a first run cut short keeps what it read.

A profile that will not parse is an error in the report, not the end of the
run: one contractor's page laid out differently must not stop the other six
hundred being read.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import newhorse
from hkrd.ingest._client import FetchError
from hkrd.store import background, job_log
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "BackgroundReport", "BATCH"]

BATCH = 20


@dataclass
class BackgroundReport:
    """Counts. A zero is said, never left blank."""

    listed: int = 0
    stored: int = 0
    already: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"  profiles listed    {self.listed:>6}",
                 f"  stored             {self.stored:>6}",
                 f"  already held       {self.already:>6}"]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def scrape(db: Path | None = None, *, refresh: bool = False, session=None,
           limit: int | None = None) -> BackgroundReport:
    report = BackgroundReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        try:
            entries = newhorse.index(session=session)
        except FetchError as exc:
            report.errors.append(f"index: {exc}")
            _log(conn, report)
            return report
        report.listed = len(entries)
        held = {} if refresh else background.profiled(conn)
        todo = [e for e in entries
                if held.get(e["horse_name"], "") < e["race_date"]]
        report.already = len(entries) - len(todo)
        if limit is not None:
            todo = todo[:limit]
        batch = []
        for e in todo:
            try:
                row = newhorse.parse(newhorse.profile(e["path"], session=session), e)
            except FetchError as exc:
                report.errors.append(f"{e['horse_name']} ({e['race_date']}): {exc}")
                continue
            row["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds")
            batch.append(row)
            if len(batch) >= BATCH:
                report.stored += _write(conn, batch)
                batch = []
        report.stored += _write(conn, batch)
        _log(conn, report)
    finally:
        conn.close()
    return report


def _write(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    with transaction(conn):
        return background.upsert_backgrounds(conn, rows)


def _log(conn, report: BackgroundReport) -> None:
    with transaction(conn):
        job_log.record_source(
            conn, "scrape_background", ok=report.ok,
            detail=(f"{report.listed} listed · {report.stored} stored · "
                    f"{report.already} already held · {len(report.errors)} errors"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="fetch every listed profile again, stored or not")
    ap.add_argument("--limit", type=int, help="stop after this many profiles")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    report = scrape(a.db, refresh=a.refresh, limit=a.limit)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

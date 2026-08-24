"""Import surviving odds snapshots from cache/live_odds into the database.

    python -m hkrd.jobs.import_legacy_odds --cache ../hk_race_dashboard/cache/live_odds

These files are the most valuable thing in the archive and the most endangered.
The scraper called prune_old_snapshots(keep=20) after every capture, so 17
meetings survived a full season of racing. What remains cannot be recovered:
odds movement is the only data here that cannot be reconstructed after the
fact, and on the 99 usable races that did survive, the favourite changed
between the first and last snapshot in 44% of them.

Getting them into a table is what stops the next cleanup losing them.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import odds as odds_ingest
from hkrd.store import upsert
from hkrd.store.coerce import CoerceError
from hkrd.store.connect import db_path, get_conn, init_db, transaction


@dataclass
class OddsImportReport:
    files_read: int = 0
    meetings: int = 0
    snapshots: int = 0
    pair_rows: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  files read         {self.files_read:>7,}",
                 f"  meetings           {self.meetings:>7,}",
                 f"  odds_snapshots     {self.snapshots:>7,}",
                 f"  odds_pairs         {self.pair_rows:>7,}"]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def run(cache: Path, *, db: Path | None = None) -> OddsImportReport:
    report = OddsImportReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        meetings = sorted(d for d in cache.iterdir() if d.is_dir())
        report.meetings = len(meetings)
        for meeting in meetings:
            for path in sorted(meeting.glob("*.json")):
                report.files_read += 1
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    snapshot = odds_ingest.parse_snapshot(payload)
                except (OSError, json.JSONDecodeError, odds_ingest.OddsError) as e:
                    report.errors.append(f"{path.name}: {e}")
                    continue
                try:
                    with transaction(conn):
                        report.snapshots += upsert.upsert_odds_snapshots(
                            conn, odds_ingest.snapshot_rows(snapshot))
                        report.pair_rows += upsert.upsert_odds_pairs(
                            conn, odds_ingest.pair_rows(snapshot))
                except CoerceError as e:
                    report.errors.append(f"{path.name}: {e}")
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if not a.cache.is_dir():
        print(f"not a directory: {a.cache}")
        return 1
    print(run(a.cache, db=a.db).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

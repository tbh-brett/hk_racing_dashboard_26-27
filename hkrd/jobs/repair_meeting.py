"""Remove a meeting that was stored from the wrong source page.

    python -m hkrd.jobs.repair_meeting --date 2026-09-06 --venue ST

WHY THIS EXISTS. Asked for the results of a meeting that had not been run,
HKJC served a page rather than a 404, and `_store_race` stamped the date we
asked for onto whatever came back. Another meeting's runners, prices and
finishing positions were written under this one's date — a 6 Sep card showing
15 Jul's twelve horses at 15 Jul's prices, with the two genuinely new runners
underneath them.

The scrape is fixed so it cannot happen again. This clears what it already
wrote, so the meeting can be fetched cleanly.

It deletes ONLY the meeting named, and only the derived rows that hang off it.
Nothing else in the archive is touched, and odds snapshots are never deleted by
anything in this package — a meeting scraped from the wrong page has none of
its own anyway.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.connect import db_path, get_conn, transaction

__all__ = ["repair", "RepairReport"]

# Every table keyed by (race_date, race_no). Derived rows go first so nothing
# is left pointing at a race that no longer exists.
_TABLES = ("runner_tags", "runner_comments", "runner_et", "runner_pace",
           "runner_sarr", "runner_sarr_component", "dividends", "runners",
           "races")


@dataclass
class RepairReport:
    date: str = ""
    venue: str = ""
    deleted: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"  meeting  {self.date} {self.venue}"]
        if not any(self.deleted.values()):
            lines.append("  nothing stored under that date — nothing to remove")
            return "\n".join(lines)
        lines += [f"  {t:18} {n:>6} removed"
                  for t, n in self.deleted.items() if n]
        return "\n".join(lines)


def repair(date: str, *, db: Path | None = None) -> RepairReport:
    report = RepairReport(date=date)
    conn = get_conn(db if db is not None else db_path())
    try:
        report.venue = (conn.execute(
            "SELECT venue FROM races WHERE race_date = ? LIMIT 1",
            (date,)).fetchone() or [""])[0] or ""
        with transaction(conn):
            for table in _TABLES:
                # A table the schema has not grown yet is not an error.
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                if not exists:
                    continue
                cur = conn.execute(f"DELETE FROM {table} WHERE race_date = ?",
                                   (date,))
                report.deleted[table] = cur.rowcount
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    report = repair(a.date, db=a.db)
    print(report.render())
    print("\n  now re-fetch it:  python -m hkrd.jobs.scrape_meeting "
          f"--date {a.date} --venue <ST|HV>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

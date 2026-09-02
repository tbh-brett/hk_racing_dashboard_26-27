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

__all__ = ["repair", "clear_results", "RepairReport"]

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


# What a RESULT adds to a row the card already declared. Clearing these leaves
# the declared field — horse, draw, jockey, trainer, weight, gear — intact.
_RESULT_COLUMNS = ("place", "place_code", "finish_time", "lengths_behind",
                   "win_odds", "running_positions", "section_times")

# `dead_heat` is NOT NULL with a default of 0, so it resets rather than clears.
_RESULT_FLAGS = {"dead_heat": 0}

# Derived from results, so meaningless once the results are gone.
_RESULT_TABLES = ("runner_tags", "runner_comments", "runner_et", "runner_pace",
                  "runner_sarr", "runner_sarr_component", "dividends")


def clear_results(date: str, *, db: Path | None = None) -> RepairReport:
    """Undo the RESULTS of a meeting, keeping the card it declared.

    For a meeting whose results were stored from the wrong page, or stored
    before it was run. The declared field is correct and worth keeping; what
    has to go is everything claiming to be an outcome.

    Ordinarily nothing needs clearing before a re-scrape: a second scrape
    upserts the results onto the same rows, and `store/upsert` never lets an
    absent value overwrite a present one. This is for when what is stored is
    WRONG rather than missing.
    """
    report = RepairReport(date=date)
    conn = get_conn(db if db is not None else db_path())
    try:
        report.venue = (conn.execute(
            "SELECT venue FROM races WHERE race_date = ? LIMIT 1",
            (date,)).fetchone() or [""])[0] or ""
        with transaction(conn):
            for table in _RESULT_TABLES:
                if not _has_table(conn, table):
                    continue
                cur = conn.execute(f"DELETE FROM {table} WHERE race_date = ?",
                                   (date,))
                report.deleted[table] = cur.rowcount
            sets = ", ".join([*(f"{c} = NULL" for c in _RESULT_COLUMNS),
                              *(f"{c} = {v}" for c, v in _RESULT_FLAGS.items())])
            cur = conn.execute(
                f"UPDATE runners SET {sets} WHERE race_date = ?", (date,))
            report.deleted["runners (results cleared)"] = cur.rowcount
    finally:
        conn.close()
    return report


def _has_table(conn, name: str) -> bool:
    """A table the schema has not grown yet is not an error."""
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


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
                if not _has_table(conn, table):
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
    ap.add_argument("--results-only", action="store_true",
                    help="clear the results and keep the declared card")
    a = ap.parse_args(argv)
    report = clear_results(a.date, db=a.db) if a.results_only \
        else repair(a.date, db=a.db)
    print(report.render())
    print("\n  now re-fetch it:  python -m hkrd.jobs.scrape_meeting "
          f"--date {a.date} --venue <ST|HV>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

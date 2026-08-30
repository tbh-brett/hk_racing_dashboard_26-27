"""Migrate the legacy flat `results` table into the normalised schema.

The one module permitted to import sqlite3 outside store/ — it reads the old
database directly and is never imported by anything else.

The legacy table is one denormalised sheet: 21,423 rows, 57 columns, race facts
repeated on every runner, and seven Chinese-named columns duplicating their
English equivalents. This splits it into races + runners and drops the
duplicates.

    python -m hkrd.jobs.migrate_legacy --src hkjc.db --dest hkrd.db

Never writes to the source.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store import upsert
from hkrd.store.coerce import CoerceError, to_date, to_int
from hkrd.store.connect import get_conn, init_db, transaction

# Redundant with colour/sex/country/import_type/current_stable.../trainer/owner.
CHINESE_DUPLICATES = ("毛色", "性別", "出生地", "進口類別", "現在位置 (到達日期)", "練馬師", "馬主")


@dataclass
class MigrationReport:
    """Counts per destination table. A job that reports nothing looks the same
    whether it worked or silently did nothing."""
    races: int = 0
    runners: int = 0
    skipped_no_horse_no: int = 0
    skipped_no_date: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  races            {self.races:>7,}",
            f"  runners          {self.runners:>7,}",
            f"  skipped (no horse_no) {self.skipped_no_horse_no:>2,}   scratched/withdrawn",
            f"  skipped (no date)     {self.skipped_no_date:>2,}",
        ]
        if self.errors:
            lines.append(f"  ERRORS           {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
            if len(self.errors) > 10:
                lines.append(f"    ... and {len(self.errors) - 10} more")
        return "\n".join(lines)


def _surface(race_course: str | None, track_type: str | None) -> str:
    """AWT is its own surface and must never be pooled with Sha Tin turf.

    The v4 reference builder labelled AWT rows as Turf, which corrupted every
    par time that averaged the two.
    """
    blob = f"{race_course or ''} {track_type or ''}".upper()
    return "AWT" if "AWT" in blob or "ALL WEATHER" in blob else "Turf"


def read_legacy(src: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM results").fetchall()
    finally:
        conn.close()


def split_rows(rows: list[sqlite3.Row]) -> tuple[list[dict], list[dict], MigrationReport]:
    """One legacy row carries both a race and a runner. Deduplicate the races."""
    report = MigrationReport()
    races: dict[tuple[str, int], dict] = {}
    runners: list[dict] = []

    for r in rows:
        try:
            race_date = to_date(r["race_date"])
            race_no = to_int(r["race_number"], field="race_number")
        except CoerceError as e:
            report.errors.append(str(e))
            continue
        if race_date is None or race_no is None:
            report.skipped_no_date += 1
            continue

        # 143 legacy rows have no horse_number: scratched runners that never ran.
        # They carry no result, so there is nothing to store against a runner key.
        try:
            horse_no = to_int(r["horse_number"], field="horse_number")
        except CoerceError:
            horse_no = None
        if horse_no is None:
            report.skipped_no_horse_no += 1
            continue

        key = (race_date, race_no)
        if key not in races:
            races[key] = {
                "race_date": race_date, "race_no": race_no,
                # race_track is ST | HV (the VENUE); race_course is A | C+3 |
                # AWT (the rail configuration). These were mapped the wrong way
                # round, which put a course code in `venue` for all 1,712 races
                # -- so SARR's Happy Valley style modifier never fired once
                # across 648 HV races, and every "COURSE HV" on screen was the
                # venue wearing the course's label.
                "venue": r["race_track"], "course": r["race_course"],
                "surface": _surface(r["race_course"], r["track_type"]),
                "going": r["going"], "distance": r["distance"],
                "race_class": r["race_class"], "race_name": None, "off_time": None,
            }

        runners.append({
            "race_date": race_date, "race_no": race_no, "horse_no": horse_no,
            "horse_name": r["horse_name"],
            "place": r["place"],
            "finish_time": r["finish_time_seconds"],
            "lengths_behind": r["lbw"],
            "draw": r["draw"], "jockey": r["jockey"], "trainer": r["trainer"],
            "actual_weight": r["actual_weight"],
            "declared_weight": r["declared_weight"],
            "gear": r["gear"], "rating": r["rating"], "win_odds": r["win_odds"],
            "section_times": r["sectiontimes"],
            # running_positions survived the 2026 regression; `positions` (the
            # semicolon-separated duplicate of the same fact) did not. Store one.
            "running_positions": r["running_positions"],
        })

    return list(races.values()), runners, report


def migrate(src: Path, dest: Path, *, batch: int = 2000) -> MigrationReport:
    rows = read_legacy(src)
    print(f"read {len(rows):,} legacy rows from {src}")
    races, runners, report = split_rows(rows)

    conn = get_conn(dest)
    try:
        init_db(conn)
        with transaction(conn):
            report.races = upsert.upsert_races(conn, races)
            for i in range(0, len(runners), batch):
                report.runners += upsert.upsert_runners(conn, runners[i:i + batch])
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=Path("hkjc.db"))
    ap.add_argument("--dest", type=Path, default=Path("hkrd.db"))
    a = ap.parse_args(argv)

    if not a.src.exists():
        print(f"source not found: {a.src}", file=sys.stderr)
        return 1
    report = migrate(a.src, a.dest)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

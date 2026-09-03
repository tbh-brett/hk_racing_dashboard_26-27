"""Recompute the derived tables from raw. PROMPTS.md Phase 2.

    python -m hkrd.jobs.derive_all                    # everything
    python -m hkrd.jobs.derive_all --only pace
    python -m hkrd.jobs.derive_all --date 2026-07-15 --only pace

The spec's requirement, and the reason this exists: "`--date 2026-07-15 --only
pace` must DELETE existing pace rows for that date and recompute from raw."
Delete-then-recompute, not upsert: a runner that should no longer have a pace
row -- because the race was re-scraped shorter, or the sectionals turned out
malformed -- must lose it. An upsert would leave the stale row behind, and a
stale derived row is indistinguishable from a current one at read time.

`runner_pace` was populated once by a script that never became a job, so the
table could not be rebuilt after a raw-data fix. That is the exact failure mode
this package exists to remove, and it is why pace is here rather than in
whatever produced it the first time.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.derive import pace as pace_d
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["rebuild_pace", "run"]

STEPS = ("pace", "et", "sarr", "tags")


@dataclass
class DeriveReport:
    written: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  {k:<18} {v:>7,} rows" for k, v in self.written.items()]
        lines += [f"  skipped, {k:<9} {v:>7,}" for k, v in self.skipped.items() if v]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def rebuild_pace(db: Path | None = None, *, date: str | None = None,
                 report: DeriveReport | None = None) -> DeriveReport:
    """Sectional-derived pace for every race, or one date's races."""
    report = report or DeriveReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        sql = ("SELECT race_date, race_no, distance FROM races "
               "WHERE distance IS NOT NULL")
        params: list = []
        if date:
            sql += " AND race_date = ?"
            params.append(date)
        races = conn.execute(sql + " ORDER BY race_date, race_no", params).fetchall()

        rows: list[tuple] = []
        malformed = 0
        for race in races:
            runners = [dict(r) for r in conn.execute(
                "SELECT horse_no, section_times, running_positions, finish_time "
                "FROM runners WHERE race_date = ? AND race_no = ?",
                (race["race_date"], race["race_no"]))]
            if not runners:
                continue
            try:
                computed = pace_d.race_pace_rows(runners, race["distance"])
            except pace_d.PaceError as exc:
                # Named, counted, and skipped -- never silently defaulted. A
                # race with unreadable sectionals has no pace, which is
                # different from a race that was run evenly.
                report.errors.append(
                    f"{race['race_date']} R{race['race_no']}: {exc}")
                malformed += 1
                continue
            for row in computed:
                rows.append((
                    race["race_date"], race["race_no"], row["horse_no"],
                    row.get("sec_400"), row.get("early_pace"), row.get("late_pace"),
                    row.get("early_dev"), row.get("late_dev"), row.get("ssi"),
                    row.get("pace_style"), pace_d.DERIVE_VERSION))

        with transaction(conn):
            if date:
                conn.execute("DELETE FROM runner_pace WHERE race_date = ?", (date,))
            else:
                conn.execute("DELETE FROM runner_pace")
            conn.executemany(
                "INSERT INTO runner_pace (race_date, race_no, horse_no, sec_400, "
                "early_pace, late_pace, early_dev, late_dev, ssi, pace_style, "
                "derive_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        report.written["runner_pace"] = len(rows)
        report.skipped["pace"] = malformed
    finally:
        conn.close()
    return report


def run(db: Path | None = None, *, date: str | None = None,
        only: tuple[str, ...] = STEPS) -> DeriveReport:
    report = DeriveReport()
    target = db if db is not None else db_path()

    if "pace" in only:
        rebuild_pace(target, date=date, report=report)
    if "et" in only:
        from hkrd.jobs import rebuild_et
        out = rebuild_et.rebuild(target)
        report.written["runner_et"] = out.rows_written
        report.errors += out.errors
    if "sarr" in only:
        from hkrd.jobs import rebuild_sarr
        out = rebuild_sarr.rebuild(target, date=date)
        report.written["runner_sarr"] = out.rows_written
        report.written["sarr_component"] = out.component_rows
        report.skipped["sarr"] = out.skipped_no_history + out.skipped_no_distance
    if "tags" in only:
        from hkrd.jobs import rebuild_tags
        out = rebuild_tags.rebuild(target)
        report.written["runner_tags"] = out.tags_written
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--date", default=None, help="one meeting; omit for all")
    ap.add_argument("--only", default=None,
                    help=f"comma-separated subset of {', '.join(STEPS)}")
    a = ap.parse_args(argv)

    only = tuple(s.strip() for s in a.only.split(",")) if a.only else STEPS
    unknown = [s for s in only if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {', '.join(unknown)}")
        return 2
    # Order matters: SARR reads pace, so a partial run must not silently score
    # against the previous version's rows.
    if "sarr" in only and "pace" not in only and a.date is None:
        print("  note: rebuilding sarr without pace — sarr reads runner_pace")

    report = run(a.db, date=a.date, only=only)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

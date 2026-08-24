"""Rebuild runner_sarr — field-relative sectional strength.

    python -m hkrd.jobs.rebuild_sarr

Walk-forward by construction: a runner's profile is built only from that
horse's runs STRICTLY BEFORE the race being scored. There is no configuration
that makes it otherwise, because a rating that has seen the race it is rating
tells you nothing.

Read what SARR is before building on it. Walk-forward over 299 races it ranks
properly -- rank 1 wins 22.4%, rank 2 15.7%, rank 3 11.0% -- but at median odds
4.8 it performs like the market's second favourite at 4.7. It agrees with the
favourite in only 31% of races, so it carries independent information, and in
exactly those disagreement races it returns what a 6.5-shot should return. No
edge in the disagreement, which is the only place an edge could live. Treat it
as a descriptive read of relative strength, not a selection rule.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hkrd.model import sarr
from hkrd.store.connect import db_path, get_conn, init_db, transaction

# SARR was written against the legacy column names; alias rather than edit the
# model, so its backtested behaviour is untouched.
RUNS_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
       r.finish_time, r.draw, r.rating,
       r.section_times AS sectiontimes, r.running_positions,
       a.distance, a.going, a.venue, a.surface
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.finish_time IS NOT NULL
ORDER BY r.race_date, r.race_no, r.horse_no
"""


@dataclass
class SarrReport:
    runs_loaded: int = 0
    races_scored: int = 0
    rows_written: int = 0
    skipped_no_history: int = 0
    skipped_no_distance: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  runs loaded        {self.runs_loaded:>7,}",
            f"  races scored       {self.races_scored:>7,}",
            f"  runner_sarr rows   {self.rows_written:>7,}",
            f"  skipped, no prior history {self.skipped_no_history:>7,}",
            f"  skipped, no distance      {self.skipped_no_distance:>7,}",
        ]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def rebuild(db: Path | None = None, *, min_prior: int = 2) -> SarrReport:
    report = SarrReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        raw = pd.read_sql(RUNS_SQL, conn)
        report.runs_loaded = len(raw)
        if raw.empty:
            report.errors.append("no runs with a finish time; nothing to build")
            return report

        runs = sarr.annotate_runs(raw)

        # Index each horse's runs by date once, rather than filtering the whole
        # frame per runner -- 21,280 runners against a full scan is minutes.
        by_horse: dict[str, list[dict]] = defaultdict(list)
        for rec in runs.to_dict("records"):
            by_horse[rec["horse_name"]].append(rec)
        for recs in by_horse.values():
            recs.sort(key=lambda r: (r["race_date"], r["race_no"]), reverse=True)

        rows: list[tuple] = []
        for (date, race_no), race in runs.groupby(["race_date", "race_no"]):
            med_rating = pd.to_numeric(race["rating"], errors="coerce").median()
            scored: list[tuple[int, float]] = []
            for rec in race.to_dict("records"):
                # SARR's distance term needs a distance. Five legacy races
                # (55 runners) have none -- their venue column holds a course
                # code rather than ST/HV, so the source rows are malformed.
                # Skip and count them; do not invent a distance.
                if pd.isna(rec["distance"]):
                    report.skipped_no_distance += 1
                    continue
                prior = [r for r in by_horse[rec["horse_name"]]
                         if (r["race_date"], r["race_no"]) < (date, race_no)]
                if len(prior) < min_prior:
                    report.skipped_no_history += 1
                    continue
                profile = sarr.build_profile(
                    prior, rec["distance"], rec["venue"], rec["surface"], rec["going"])
                if profile is None:
                    report.skipped_no_history += 1
                    continue
                value = sarr.score(profile, rec["distance"], rec["venue"], med_rating)
                if value is None or pd.isna(value):
                    continue
                scored.append((rec["horse_no"], float(value), len(prior)))

            if not scored:
                continue
            report.races_scored += 1
            # Lower is better, so rank ascending.
            for rank, (horse_no, value, n_prior) in enumerate(
                    sorted(scored, key=lambda s: s[1]), start=1):
                rows.append((date, race_no, horse_no, value, rank, n_prior,
                             sarr.DERIVE_VERSION if hasattr(sarr, "DERIVE_VERSION")
                             else "sarr-1.0"))

        with transaction(conn):
            conn.executemany(
                "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, "
                "sarr_rank, n_prior, derive_version) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET "
                "sarr = excluded.sarr, sarr_rank = excluded.sarr_rank, "
                "n_prior = excluded.n_prior, derive_version = excluded.derive_version",
                rows)
        report.rows_written = len(rows)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--min-prior", type=int, default=2,
                    help="runs of history required before a horse is rated")
    a = ap.parse_args(argv)
    report = rebuild(a.db, min_prior=a.min_prior)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

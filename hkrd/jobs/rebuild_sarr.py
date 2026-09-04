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

from hkrd.derive import draw as draw_d
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

CARD_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
       r.finish_time, r.draw, r.rating,
       r.section_times AS sectiontimes, r.running_positions,
       a.distance, a.going, a.venue, a.surface
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.race_date = ?
ORDER BY r.race_date, r.race_no, r.horse_no
"""


@dataclass
class SarrReport:
    runs_loaded: int = 0
    races_scored: int = 0
    rows_written: int = 0
    component_rows: int = 0
    skipped_no_history: int = 0
    skipped_no_distance: int = 0
    scored_without_draw: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  runs loaded        {self.runs_loaded:>7,}",
            f"  races scored       {self.races_scored:>7,}",
            f"  runner_sarr rows   {self.rows_written:>7,}",
            f"  component rows     {self.component_rows:>7,}",
            f"  skipped, no prior history {self.skipped_no_history:>7,}",
            f"  skipped, no distance      {self.skipped_no_distance:>7,}",
            f"  scored, but no gate       {self.scored_without_draw:>7,}",
        ]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def rebuild(db: Path | None = None, *, min_prior: int = 2,
            date: str | None = None) -> SarrReport:
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
        targets = (pd.read_sql(CARD_SQL, conn, params=(date,))
                   if date else runs)

        # Index each horse's runs by date once, rather than filtering the whole
        # frame per runner -- 21,280 runners against a full scan is minutes.
        by_horse: dict[str, list[dict]] = defaultdict(list)
        for rec in runs.to_dict("records"):
            by_horse[rec["horse_name"]].append(rec)
        for recs in by_horse.values():
            recs.sort(key=lambda r: (r["race_date"], r["race_no"]), reverse=True)

        # One draw table per MEETING, fitted from runs strictly before it -- the
        # same walk-forward rule the horse profiles already obey. Cached because
        # a meeting's races all share it, and refitting per race would be the
        # same answer computed eleven times.
        draw_tables: dict[str, draw_d.DrawTable | None] = {}

        def table_for(meeting_date: str) -> draw_d.DrawTable | None:
            if meeting_date not in draw_tables:
                hist = runs[runs["race_date"] < meeting_date]
                try:
                    draw_tables[meeting_date] = draw_d.draw_table(hist)
                except draw_d.DrawError:
                    # The first meetings in the archive have nothing before
                    # them. They score on the other eight terms, as they did
                    # before this term existed.
                    draw_tables[meeting_date] = None
            return draw_tables[meeting_date]

        rows: list[tuple] = []
        component_rows: list[tuple] = []
        for (race_date, race_no), race in targets.groupby(["race_date", "race_no"]):
            med_rating = pd.to_numeric(race["rating"], errors="coerce").median()
            dtable = table_for(race_date)
            # The declared field, not the count that ends up scored: both axes
            # of the draw score are normalised by it, so a horse dropped for
            # thin history must not shrink the field its rivals are measured in.
            field_size = len(race)
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
                         if (r["race_date"], r["race_no"]) < (race_date, race_no)]
                if len(prior) < min_prior:
                    report.skipped_no_history += 1
                    continue
                profile = sarr.build_profile(
                    prior, rec["distance"], rec["venue"], rec["surface"], rec["going"])
                if profile is None:
                    report.skipped_no_history += 1
                    continue
                ds = (0.0 if dtable is None else draw_d.draw_score(
                    rec["draw"], field_size, rec["venue"], rec["distance"], dtable))
                if dtable is not None and pd.isna(rec["draw"]):
                    report.scored_without_draw += 1
                parts = sarr.contributions(
                    profile, rec["distance"], rec["venue"], med_rating,
                    draw_score=ds)
                value = sum(parts.values())
                if value is None or pd.isna(value):
                    continue
                scored.append((rec["horse_no"], float(value), len(prior)))
                component_rows.extend(
                    (race_date, race_no, rec["horse_no"], k, float(v))
                    for k, v in parts.items())

            if not scored:
                continue
            report.races_scored += 1
            # Lower is better, so rank ascending.
            for rank, (horse_no, value, n_prior) in enumerate(
                    sorted(scored, key=lambda s: s[1]), start=1):
                rows.append((race_date, race_no, horse_no, value, rank, n_prior,
                             sarr.DERIVE_VERSION if hasattr(sarr, "DERIVE_VERSION")
                             else "sarr-1.0"))

        with transaction(conn):
            if date:
                conn.execute("DELETE FROM runner_sarr_component WHERE race_date = ?",
                             (date,))
                conn.execute("DELETE FROM runner_sarr WHERE race_date = ?", (date,))
            conn.executemany(
                "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, "
                "sarr_rank, n_prior, derive_version) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET "
                "sarr = excluded.sarr, sarr_rank = excluded.sarr_rank, "
                "n_prior = excluded.n_prior, derive_version = excluded.derive_version",
                rows)
            # Only for the runners that scored -- a race skipped for a missing
            # distance must not leave orphaned components behind from a
            # previous run.
            scored_keys = {(r[0], r[1], r[2]) for r in rows}
            kept = [c for c in component_rows if (c[0], c[1], c[2]) in scored_keys]
            conn.executemany(
                "INSERT INTO runner_sarr_component (race_date, race_no, "
                "horse_no, component, contribution) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (race_date, race_no, horse_no, component) "
                "DO UPDATE SET contribution = excluded.contribution", kept)
        report.rows_written = len(rows)
        report.component_rows = len(kept)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--date", default=None, help="one meeting; omit for all")
    ap.add_argument("--min-prior", type=int, default=2,
                    help="runs of history required before a horse is rated")
    a = ap.parse_args(argv)
    report = rebuild(a.db, min_prior=a.min_prior, date=a.date)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

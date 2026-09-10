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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hkrd.derive import draw as draw_d, tags as tags_d
from hkrd.model import sarr
from hkrd.store.connect import db_path, get_conn, init_db, transaction

#: A variant, expressed as a change to the profile a runner is scored from.
#: Takes (profile, the runner's row, its prior runs) and returns the profile to
#: score. Candidate model changes live behind this until they have earned a
#: place in `model/sarr`, so an experiment costs the shipped model nothing.
ProfileAdjust = Callable[[dict, dict, list[dict]], dict]

__all__ = ["SarrReport", "ProfileAdjust", "score_runners", "rebuild",
           "RUNS_SQL", "CARD_SQL", "VET_SQL"]

# SARR was written against the legacy column names; alias rather than edit the
# model, so its backtested behaviour is untouched.
RUNS_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
       r.finish_time, r.draw, r.rating,
       r.section_times AS sectiontimes, r.running_positions,
       a.distance, a.going, a.venue, a.surface, a.race_class
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.finish_time IS NOT NULL
ORDER BY r.race_date, r.race_no, r.horse_no
"""

CARD_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
       r.finish_time, r.draw, r.rating,
       r.section_times AS sectiontimes, r.running_positions,
       a.distance, a.going, a.venue, a.surface, a.race_class
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.race_date = ?
ORDER BY r.race_date, r.race_no, r.horse_no
"""


# Which prior runs were compromised, in one vocabulary, from the two records
# that hold the fact. Neither alone is enough: the tags cover the archive but
# only see what the stewards wrote about the run, and the vet page names the
# finding but is scraped per meeting. `record_date` is when the finding was
# made, which is the day of the run it belongs to, so the join lands it on that
# run -- and the flag is therefore known from that day on, which is what keeps
# the walk-forward rule intact. `passed_date` is deliberately not read: it is a
# fact about a later day, and a run must be scored on what was knowable then.
VET_SQL = """
SELECT race_date, race_no, horse_no, category FROM (
    SELECT t.race_date, t.race_no, t.horse_no, t.tag AS category
      FROM runner_tags t
     WHERE t.tag IN (%s)
    UNION ALL
    SELECT r.race_date, r.race_no, r.horse_no, v.category
      FROM vet_records v
      JOIN runners r ON r.horse_name = v.horse_name
                    AND r.race_date  = v.record_date
     WHERE v.category IS NOT NULL
)
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
    vet_flagged_runs: int = 0
    profiles_with_vet_run: int = 0
    stale_rows_cleared: int = 0
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
            f"  prior runs vet-flagged    {self.vet_flagged_runs:>7,}",
            f"  profiles using one        {self.profiles_with_vet_run:>7,}",
            f"  stale rows cleared        {self.stale_rows_cleared:>7,}",
        ]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _clear_stale(conn, scored_keys: set[tuple]) -> int:
    """Drop rows for runners this pass did NOT score, in races it DID.

    The write is an upsert, so a runner that used to score and no longer does
    keeps its old row forever. That happens on every meeting: a card is scored
    the day before, a horse is then scratched, and the results write leaves it
    with no finish time -- so it drops out of RUNS_SQL and is never rescored,
    while its row sits in the table holding a RANK. 2026-09-06 race 7 carried
    ten rows for nine runners and INVINCIBLE SHIELD, which never left the
    stalls, held third; every real runner below it read one place too low.

    Scoped to races this pass scored, so an unrun card written by a
    date-scoped rebuild is never touched by a full one.
    """
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _scored "
                 "(race_date TEXT, race_no INTEGER, horse_no INTEGER)")
    conn.execute("DELETE FROM _scored")
    conn.executemany("INSERT INTO _scored VALUES (?, ?, ?)", sorted(scored_keys))
    conn.execute("CREATE INDEX IF NOT EXISTS _scored_ix "
                 "ON _scored (race_date, race_no, horse_no)")
    where = ("""
        WHERE EXISTS (SELECT 1 FROM _scored x
                       WHERE x.race_date = %(t)s.race_date
                         AND x.race_no   = %(t)s.race_no)
          AND NOT EXISTS (SELECT 1 FROM _scored x
                       WHERE x.race_date = %(t)s.race_date
                         AND x.race_no   = %(t)s.race_no
                         AND x.horse_no  = %(t)s.horse_no)""")
    cleared = 0
    for table in ("runner_sarr_component", "runner_sarr"):
        cur = conn.execute(f"DELETE FROM {table} " + where % {"t": table})
        if table == "runner_sarr":
            cleared = cur.rowcount
    return cleared


def _vet_flags(conn, runs: pd.DataFrame) -> pd.Series:
    """One category per run that carried a veterinary finding, else None.

    Where both records name the same run they are reconciled by taking the
    FIRST match, which is arbitrary and allowed to be: the trust factor is one
    number for every category today, so the choice cannot change a score. It
    will matter on the day the categories separate, and the fit that separates
    them is the thing that gets to decide how.
    """
    vet_tags = sorted(tags_d.VET_TAGS)
    flags = pd.read_sql(VET_SQL % ",".join("?" * len(vet_tags)),
                        conn, params=vet_tags)
    if flags.empty:
        return pd.Series([None] * len(runs), index=runs.index, dtype=object)
    flags["category"] = flags["category"].map(
        lambda c: tags_d.VET_CATEGORY.get(c, c))
    lookup = (flags.drop_duplicates(subset=["race_date", "race_no", "horse_no"])
                   .set_index(["race_date", "race_no", "horse_no"])["category"])
    keys = pd.MultiIndex.from_arrays(
        [runs["race_date"], runs["race_no"], runs["horse_no"]])
    got = lookup.reindex(keys).to_numpy()
    # reindex fills misses with NaN, and NaN is TRUTHY. Left alone it reaches
    # `vet_trust` as the string "NAN", misses the table, and takes the
    # unrecognised-category branch -- which would discount every clean run in
    # the archive to 0.21 while every counter and every log line still read
    # normally. None is the only value that means "no finding".
    return pd.Series([None if pd.isna(c) else c for c in got],
                     index=runs.index, dtype=object)


def score_runners(runs: pd.DataFrame, targets: pd.DataFrame, *,
                  min_prior: int = 2, report: SarrReport | None = None,
                  adjust: ProfileAdjust | None = None
                  ) -> tuple[list[tuple], list[tuple]]:
    """Score every target walk-forward. The one place a SARR score is produced.

    Lifted out of `rebuild` so an evaluation can score the archive under a
    changed model WITHOUT a second copy of this loop. A copy is how the two
    drift: the harness keeps the old draw-table caching, or the old skip rule,
    and then reports a difference that is really the difference between two
    loops. `model/evaluate` calls this, and a test asserts it reproduces
    `runner_sarr` row for row.

    `adjust` receives each profile with the runner's row and its prior runs and
    returns a profile to score instead. That is the seam a variant is expressed
    through, so a candidate change costs nothing in the shipped model until it
    has earned its way in.
    """
    report = report or SarrReport()
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
                prior, rec["distance"], rec["venue"], rec["surface"],
                today_class=rec.get("race_class"))
            if profile is None:
                report.skipped_no_history += 1
                continue
            if any(r.get("vet_category") for r in prior[:sarr.MAX_PRIOR_RUNS]):
                report.profiles_with_vet_run += 1
            if adjust is not None:
                profile = adjust(profile, rec, prior)
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
    return rows, component_rows


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
        runs["vet_category"] = _vet_flags(conn, runs)
        report.vet_flagged_runs = int(runs["vet_category"].notna().sum())
        targets = (pd.read_sql(CARD_SQL, conn, params=(date,))
                   if date else runs)
        rows, component_rows = score_runners(
            runs, targets, min_prior=min_prior, report=report)
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
            report.stale_rows_cleared = _clear_stale(conn, scored_keys)
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

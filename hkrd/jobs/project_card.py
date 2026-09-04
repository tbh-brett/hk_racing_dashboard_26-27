"""Project a card that has not been run — the pre-race speed map's data.

    python -m hkrd.jobs.project_card --date 2026-09-06

SARR IS ALREADY A PRE-RACE MODEL, and that is what makes this cheap. Nothing in
`build_profile` or `contributions` touches the race being scored: every input is
either the horse's prior form or a card field that exists before the off — the
draw, the distance, the venue, the surface, the going, the rating. The only
thing that ever tied SARR to a finished race was the loader, and
`rebuild_sarr --date` already reads the card without a `finish_time` filter.

So this job adds no model logic. It builds the same profiles, takes the ESZ
TRAIT out of them, ranks it within the race, and applies `derive/settle`.

WHY A JOB AND NOT A QUERY. `annotate_runs` over 21,000 runs plus a profile per
runner is tens of seconds. That is fine once a night and impossible inside a
request, so this writes a table and `query/speedmap` reads it.

SCRATCHINGS ARE THE THING THAT WILL BITE. `field_size` is the declared field,
and both `ndraw` and `esz_rank` are normalised by it. A projection built on
Tuesday and shown on Saturday, after three horses came out, is subtly wrong
across the whole card -- every gate reads wider than it is. Re-run this on the
last card scrape before the off; it is idempotent and costs one pass.

WHAT IT REFUSES TO INVENT. A runner with no gate, or with fewer than the two
prior runs a profile needs, is written with a NULL settle and named on the page
as an absence. `query/model.py:_unscored` is the pattern: name them, do not
count them, and never draw a bar for a horse we know nothing about.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hkrd.derive import draw as draw_d
from hkrd.derive import settle as settle_d
from hkrd.model import sarr
from hkrd.store.connect import db_path, get_conn, init_db, transaction

# History: only finished races can inform a profile.
HIST_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
       r.finish_time, r.draw, r.rating,
       r.section_times AS sectiontimes, r.running_positions,
       a.distance, a.going, a.venue, a.surface
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.finish_time IS NOT NULL AND r.race_date < ?
ORDER BY r.race_date, r.race_no, r.horse_no
"""

# The card: NO result columns are selected. Not because they would be used --
# `build_profile` never looks at the race being scored -- but because a query
# that cannot return a result cannot leak one, and this is the file where
# somebody will one day be tempted.
CARD_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.draw, r.rating,
       a.distance, a.going, a.venue, a.surface
FROM runners r
JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
WHERE r.race_date = ?
ORDER BY r.race_no, r.horse_no
"""


@dataclass
class ProjectionReport:
    card_date: str = ""
    history_runs: int = 0
    races: int = 0
    runners: int = 0
    projected: int = 0
    no_history: int = 0
    no_gate: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  card                  {self.card_date}",
            f"  history runs      {self.history_runs:>9,}",
            f"  races             {self.races:>9,}",
            f"  runners on card   {self.runners:>9,}",
            f"  projected         {self.projected:>9,}",
            f"  no prior form     {self.no_history:>9,}",
            f"  no gate           {self.no_gate:>9,}",
        ]
        if self.errors:
            lines.append(f"  ERRORS            {len(self.errors):>9,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def project(date: str, db: Path | None = None, *,
            min_prior: int = 2) -> ProjectionReport:
    report = ProjectionReport(card_date=date)
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        raw = pd.read_sql(HIST_SQL, conn, params=(date,))
        report.history_runs = len(raw)
        card = pd.read_sql(CARD_SQL, conn, params=(date,))
        report.runners = len(card)
        if card.empty:
            report.errors.append(f"no card stored for {date}")
            return report
        if raw.empty:
            report.errors.append(f"no history before {date}; nothing to project")
            return report

        runs = sarr.annotate_runs(raw)
        by_horse: dict[str, list[dict]] = defaultdict(list)
        for rec in runs.to_dict("records"):
            by_horse[rec["horse_name"]].append(rec)
        for recs in by_horse.values():
            recs.sort(key=lambda r: (r["race_date"], r["race_no"]), reverse=True)

        # One draw table for the meeting, fitted from runs strictly before it --
        # the same walk-forward rule rebuild_sarr and the horse profiles obey.
        try:
            dtable = draw_d.draw_table(runs)
        except draw_d.DrawError:
            dtable = None

        rows: list[tuple] = []
        for race_no, race in card.groupby("race_no"):
            # The DECLARED field. Both axes are scaled by it, so a horse dropped
            # for thin form must not shrink the field its rivals are measured in.
            field_size = len(race)
            recs = race.to_dict("records")

            profiles: list[dict | None] = []
            for rec in recs:
                if pd.isna(rec["distance"]):
                    profiles.append(None)
                    continue
                prior = by_horse.get(rec["horse_name"], [])
                if len(prior) < min_prior:
                    profiles.append(None)
                    continue
                profiles.append(sarr.build_profile(
                    prior, rec["distance"], rec["venue"],
                    rec["surface"], rec["going"]))

            esz_values = [
                (None if p is None or p.get("esz") is None
                 or p["esz"] != p["esz"] else float(p["esz"]))
                for p in profiles]
            ranks = settle_d.esz_ranks(esz_values)

            for rec, prof, esz, rank in zip(recs, profiles, esz_values, ranks):
                if prof is None:
                    report.no_history += 1
                nd = settle_d.normalised_draw(rec["draw"], field_size)
                if nd is None:
                    report.no_gate += 1
                style = prof.get("style") if prof else None
                value = settle_d.settle_score(rank, nd, style)
                band = settle_d.settle_band(value)
                ds = None
                if dtable is not None and not pd.isna(rec["draw"]):
                    ds = draw_d.draw_score(rec["draw"], field_size,
                                           rec["venue"], rec["distance"], dtable)
                if value is not None:
                    report.projected += 1
                rows.append((
                    date, int(race_no), int(rec["horse_no"]), esz, rank, nd, ds,
                    value, band, style,
                    len(by_horse.get(rec["horse_name"], [])) or None,
                    field_size, settle_d.DERIVE_VERSION))
            report.races += 1

        with transaction(conn):
            conn.execute("DELETE FROM runner_projection WHERE race_date = ?",
                         (date,))
            conn.executemany(
                "INSERT INTO runner_projection (race_date, race_no, horse_no, "
                "esz, esz_rank, ndraw, draw_score, settle, settle_band, style, "
                "n_prior, field_size, derive_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    finally:
        conn.close()
    return report


PENDING_SQL = """
SELECT race_date FROM runners
GROUP BY race_date HAVING COUNT(finish_time) = 0
ORDER BY race_date
"""


def pending_cards(db: Path | None = None) -> list[str]:
    """Every stored card that has not been run.

    The schedule stays dumb and the job stays smart, which is the rule
    `ops/crontab` states for itself: racing moves for typhoons, holidays and
    international meetings, so a cron line that named dates would put the
    calendar in two places and let them drift. This asks the database instead.
    """
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        return [r[0] for r in conn.execute(PENDING_SQL)]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="YYYY-MM-DD, the card to project")
    group.add_argument("--pending", action="store_true",
                       help="every stored card that has not been run")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--min-prior", type=int, default=2,
                    help="runs of history required before a horse is projected")
    a = ap.parse_args(argv)

    dates = pending_cards(a.db) if a.pending else [a.date]
    if not dates:
        # Not an error. Most days there is no card waiting, and a job that
        # exits 1 on a quiet Tuesday trains everyone to ignore it.
        print("  no card waiting to be projected")
        return 0
    failed = False
    for date in dates:
        report = project(date, a.db, min_prior=a.min_prior)
        print(report.render())
        failed = failed or bool(report.errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

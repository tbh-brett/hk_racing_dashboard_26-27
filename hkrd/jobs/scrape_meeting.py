"""Scrape one meeting end to end.

    python -m hkrd.jobs.scrape_meeting --date 2026-07-15 --venue HV --post-race

This replaces run_meeting.generate_analysis_script(), which wrote a Python file
per meeting by copying a 4,536-line template and patching a config block, then
executed it. Those generated files were committed and froze at whatever template
version was current that day: the April copies still carry a hardcoded Windows
path and lack a helper the template later gained.

The consequence was measurable. Every report family written by a dedicated
scraper held one schema for its whole life -- results 1 across 56 files,
dividends 1 across 56, incidents 1 across 87, trials 1 across 159. The one
family written by generated code, race_day_report, reached 3 schemas with 17
inconsistent keys, and the dashboard read those files at 98 sites. A key absent
for one meeting hit one of the 94 silent exception handlers and rendered an
empty column three days later.

So: one versioned function, always the same schema, derive_version recorded in
the row. No code generation, ever.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import corunning, results as results_ingest
from hkrd.ingest._client import FetchError
from hkrd.store import upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction


@dataclass
class ScrapeReport:
    """Row counts per table. Never a bare "done": a zero has to be visible."""

    date: str = ""
    venue: str = ""
    races: int = 0
    runners: int = 0
    comments: int = 0
    lane_tags: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.races > 0

    def render(self) -> str:
        lines = [f"  meeting            {self.date} {self.venue}",
                 f"  races              {self.races:>6}",
                 f"  runners            {self.runners:>6}",
                 f"  comments           {self.comments:>6}",
                 f"  lane tags          {self.lane_tags:>6}"]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def scrape_meeting(date: str, venue: str, *, post_race: bool = False,
                   db: Path | None = None, max_races: int = 11,
                   session=None) -> ScrapeReport:
    """Fetch a meeting and store it.

    Running this twice must produce identical counts -- every write is an upsert
    keyed on (race_date, race_no, horse_no).
    """
    report = ScrapeReport(date=date, venue=venue)
    try:
        races = results_ingest.fetch_meeting(date, venue, max_races=max_races,
                                             session=session)
    except (FetchError, results_ingest.ResultsError) as e:
        report.errors.append(f"results: {e}")
        return report

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            for race in races:
                report.races += 1
                report.runners += _store_race(conn, date, race)
    finally:
        conn.close()

    if post_race:
        # Comments on running are only published after the race is run.
        try:
            from hkrd.jobs import scrape_corunning
            cr = scrape_corunning.scrape(date, db=db, max_races=max_races,
                                         session=session)
            report.comments += cr.comments
            report.lane_tags += cr.lane_tags
            report.errors.extend(f"corunning: {e}" for e in cr.errors)
        except (FetchError, corunning.CoRunningError) as e:
            report.errors.append(f"corunning: {e}")

    return report


def _store_race(conn, date: str, race: dict) -> int:
    upsert.upsert_races(conn, [{
        "race_date": date, "race_no": race.get("race_no"),
        "venue": race.get("venue"), "course": race.get("course"),
        "surface": race.get("surface"), "going": race.get("going"),
        "distance": race.get("distance"), "race_class": race.get("race_class"),
        "race_name": race.get("race_name"),
    }])
    runners = [{
        "race_date": date, "race_no": race.get("race_no"),
        "horse_no": r.get("horse_no"), "horse_name": r.get("horse_name"),
        "place": r.get("place"), "finish_time": r.get("finish_time"),
        "lengths_behind": r.get("lbw"), "draw": r.get("draw"),
        "jockey": r.get("jockey"), "trainer": r.get("trainer"),
        "actual_weight": r.get("actual_weight"),
        "declared_weight": r.get("declared_weight"),
        "win_odds": r.get("win_odds"),
        "running_positions": r.get("running_position"),
        "section_times": r.get("section_times"),
    } for r in race.get("runners", [])]
    return upsert.upsert_runners(conn, runners)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--venue", required=True, choices=["ST", "HV"])
    ap.add_argument("--post-race", action="store_true",
                    help="also fetch comments on running")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--max-races", type=int, default=11)
    a = ap.parse_args(argv)

    report = scrape_meeting(a.date, a.venue, post_race=a.post_race, db=a.db,
                            max_races=a.max_races)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

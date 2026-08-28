"""Scrape barrier trials and store them.

    python -m hkrd.jobs.scrape_trials --date 2026-08-21   # one day
    python -m hkrd.jobs.scrape_trials                     # whatever is new

With no date it asks HKJC which trial days exist and fetches the recent ones
the database does not have. That question has an answer at source — the page
carries its own list of dates — and it is the only correct one: measured over
the 2025-26 archive, trials fall on Tue, Thu and Fri equally (26.4% of 159
days each), Mon 16.4%, and Sat and Wed a handful of times. A crontab that
guessed Tuesday and Thursday from habit would miss 47% of them, and miss them
silently.

Separate from `scrape_meeting` because trials are held on their own days, at
their own venues, and a trial day is not a meeting. Folding them into the
meeting job would mean either scraping trials on race days that have none, or
never scraping the days that do.

The rows written carry two fields the archive has never had: `distance`, which
HKJC publishes in the batch header and the legacy import dropped, and `going`.
An archived trial reads None for both, and that is a gap in the archive rather
than in the source.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import trials as trials_ingest
from hkrd.ingest._client import FetchError, NotFound
from hkrd.store import upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "catch_up", "TrialScrapeReport"]


@dataclass
class TrialScrapeReport:
    """Row counts. Never a bare "done": a zero has to be visible."""

    date: str = ""
    batches: int = 0
    runners: int = 0
    with_distance: int = 0
    errors: list[str] = field(default_factory=list)
    # HKJC answering "there is no trial page for that date" is an answer, not
    # a failure. It only became worth distinguishing when this went on a
    # schedule: asked by hand for a known trial day, an empty result is a
    # fault; asked every morning, it is most mornings.
    no_such_day: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        if self.no_such_day:
            return f"  trial day          {self.date}  — none published"
        lines = [f"  trial day          {self.date}",
                 f"  batches            {self.batches:>6}",
                 f"  runners            {self.runners:>6}",
                 f"  with a distance    {self.with_distance:>6}"]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def scrape(date: str, *, db: Path | None = None,
           session=None) -> TrialScrapeReport:
    """Fetch one trial day and store it. Running it twice is idempotent."""
    report = TrialScrapeReport(date=date)
    try:
        batches = trials_ingest.fetch_day(date, session=session)
    except NotFound:
        report.no_such_day = True
        return report
    except (FetchError, trials_ingest.TrialsError) as exc:
        report.errors.append(f"trials: {exc}")
        return report

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            for batch in batches:
                report.batches += 1
                # The batch's conditions are repeated onto every runner: the
                # trials table is one row per RUN, and a reader asking what
                # going a horse trialled on should not have to join back to a
                # batch table that does not exist.
                rows = [{
                    "trial_date": batch.get("trial_date") or date,
                    "trial_no": batch.get("trial_no"),
                    "horse_name": r.get("horse_name"),
                    "place": r.get("place"),
                    "finish_time": r.get("finish_time"),
                    "section_times": "; ".join(batch.get("section_times") or []),
                    "running_positions": " ".join(
                        str(p) for p in (r.get("running_positions") or [])),
                    "venue": batch.get("venue"),
                    "course": batch.get("course"),
                    "surface": batch.get("surface"),
                    "distance": batch.get("distance"),
                    "going": batch.get("going"),
                    "jockey": r.get("jockey"),
                    "trainer": r.get("trainer"),
                    "draw": r.get("draw"),
                    "lengths_behind": r.get("lbw"),
                    "gear": r.get("gear"),
                    "comment_text": r.get("comment"),
                } for r in batch.get("runners", [])]
                report.runners += upsert.upsert_trials(conn, rows)
                if batch.get("distance"):
                    report.with_distance += len(rows)
    finally:
        conn.close()
    return report


def outstanding(days: Sequence[str], *, db: Path | None = None,
                limit: int = 6) -> list[str]:
    """Of the days HKJC lists, the recent ones not already in the database.

    Oldest first, so a run that is cut short has filled the gap from the far
    end rather than leaving a hole in the middle of the archive.
    """
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT trial_date FROM trials")}
    finally:
        conn.close()
    missing = [d for d in days if d not in have]
    # `days` arrives newest first; take the most recent few, then run them in
    # order. The limit is a guard against a first run against an empty
    # database fetching a year of trial days in one go.
    return sorted(missing[:limit])


def catch_up(*, db: Path | None = None, limit: int = 6,
             session=None) -> list[TrialScrapeReport]:
    """Ask which trial days exist, fetch the recent ones we are missing."""
    days = trials_ingest.list_days(session=session)
    return [scrape(d, db=db, session=session)
            for d in outstanding(days, db=db, limit=limit)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD; omit to fetch whatever is new")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=6,
                    help="most trial days to fetch in one catch-up run")
    a = ap.parse_args(argv)

    if a.date:
        report = scrape(a.date, db=a.db)
        print(report.render())
        return 0 if report.ok else 1

    try:
        reports = catch_up(db=a.db, limit=a.limit)
    except (FetchError, trials_ingest.TrialsError) as exc:
        # The day list itself failed. That is not "no new trials" and must not
        # exit 0 — it is the one failure that would make this job look like it
        # is working forever while never fetching anything again.
        print(f"  could not read the trial day list — {exc}")
        return 1

    if not reports:
        print("  trials             nothing new")
        return 0
    for report in reports:
        print(report.render())
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

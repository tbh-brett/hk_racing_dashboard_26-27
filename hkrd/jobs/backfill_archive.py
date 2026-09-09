"""Extend the archive backwards, one meeting at a time.

    python -m hkrd.jobs.backfill_archive --from 2022-09-01 --to 2024-07-31
    python -m hkrd.jobs.backfill_archive --from 2022-09-01 --to 2024-07-31 --dry-run

WHY THIS EXISTS. The archive holds two complete seasons, 178 meetings, and no
internal gaps -- so nothing was missing and nothing needed repairing. What it
could not do was settle an argument. Every model change measured so far lands
somewhere around +0.002 on held-out rank correlation, and the held-out window
is 306 races: the standard error on a paired difference that size is roughly
+/-0.002, so a real improvement and no improvement at all produce the same
number. Three changes in a row were rejected for want of resolution rather
than for want of an effect. More seasons is the only thing that changes that,
and HKJC still serves 2021.

FINDING THE MEETINGS. HKJC's date picker is populated by JavaScript, so the
calendar cannot be read from the page and nothing here opens a browser to
look. `results.meeting_venue` asks the results page for a date WITHOUT naming
a course; the site answers with the meeting that actually ran and names the
course in the body. One request per candidate day settles both questions.

Every day in the range is probed, not just Wednesday and Sunday. Hong Kong
races on public holidays too, and a calendar built from the usual days would
drop them silently -- which is the failure this whole exercise exists to stop.

RESUMABLE. A meeting already holding results is skipped without a request, so
an interrupted run continues where it stopped and a completed one is cheap to
repeat. Nothing is deleted and every write is an upsert.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import results as results_ingest
from hkrd.ingest._client import FetchError, get_session
from hkrd.jobs import scrape_meeting as scrape_job
from hkrd.store.connect import db_path, get_conn, init_db

# Hong Kong cards run 8 to 11 races; below this a stored meeting is far more
# likely to be a partial write than a real card.
_FULL_CARD = 8

__all__ = ["BackfillReport", "meetings_in_range", "backfill", "_FULL_CARD"]


@dataclass
class BackfillReport:
    first: str = ""
    last: str = ""
    days_probed: int = 0
    already_held: int = 0
    meetings_found: int = 0
    meetings_scraped: int = 0
    races: int = 0
    runners: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  range              {self.first} .. {self.last}",
            f"  days probed        {self.days_probed:>7,}",
            f"  meetings found     {self.meetings_found:>7,}",
            f"  already held       {self.already_held:>7,}",
            f"  meetings scraped   {self.meetings_scraped:>7,}",
            f"  races stored       {self.races:>7,}",
            f"  runners stored     {self.runners:>7,}",
            f"  elapsed            {self.seconds/60:>7.1f} min",
        ]
        if self.warnings:
            lines.append(f"  warnings           {len(self.warnings):>7,}")
            lines += [f"    {w}" for w in self.warnings[:10]]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _held(conn, date: str) -> bool:
    """Does the database already have RESULTS for this date?

    Results, not rows. A date can hold a declared card with no finishing times
    -- that is what a meeting scraped before it was run looks like -- and
    skipping on the card alone would leave it permanently empty.
    """
    row = conn.execute("""
        SELECT count(*) n, coalesce(min(race_no), 0) lo, coalesce(max(race_no), 0) hi
          FROM races
         WHERE race_date = ?
           AND EXISTS (SELECT 1 FROM runners r
                        WHERE r.race_date = races.race_date
                          AND r.race_no = races.race_no
                          AND r.finish_time IS NOT NULL)
    """, (date,)).fetchone()
    n, lo, hi = row[0], row[1], row[2]
    # Contiguous from race 1, and long enough to be a card. "Any result at all"
    # was the first rule and it silently skipped a PARTIAL meeting: 2022-01-09
    # stored race 1, hit an unparseable margin on race 2, and was recorded as a
    # failure -- but the retry pass saw race 1's finishing times, called the
    # date held and never went back. Ten races sat missing behind a run that
    # reported success.
    #
    # A genuinely short card -- 2024-11-13 HV was abandoned after race 6 -- is
    # re-scraped on each full run instead. That costs a couple of minutes and
    # writes the same rows again; the other way round costs data nobody is
    # told about.
    return n >= _FULL_CARD and lo == 1 and hi == n


def meetings_in_range(first: str, last: str, *, db: Path | None = None,
                      report: BackfillReport | None = None,
                      session=None) -> Iterator[tuple[str, str]]:
    """Yield each (date, venue) that raced in the range and is not held yet.

    A GENERATOR, so probing and scraping interleave. Probing the whole range
    first cost fifteen minutes before a single meeting was stored, gave no
    sign of progress while it ran, and -- because a probe result lives only in
    memory -- threw all of it away if the run was interrupted, so every restart
    paid it again. Yielding means the first meeting lands about a minute in and
    an interrupted run has already banked everything before the break.
    """
    report = report or BackfillReport()
    session = session or get_session()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        day = dt.date.fromisoformat(first)
        end = dt.date.fromisoformat(last)
        while day <= end:
            date = day.isoformat()
            day += dt.timedelta(days=1)
            # Re-read per day rather than once: the caller is storing meetings
            # into this same database as we go.
            if _held(conn, date):
                report.already_held += 1
                report.meetings_found += 1
                continue
            report.days_probed += 1
            try:
                venue = results_ingest.meeting_venue(date, session=session)
            except FetchError as e:
                # A probe that could not be made is not a day without racing.
                # Record it; the run can be repeated and will re-probe.
                report.errors.append(f"probe {date}: {e}")
                continue
            if venue:
                report.meetings_found += 1
                yield date, venue
    finally:
        conn.close()


def backfill(first: str, last: str, *, db: Path | None = None,
             dry_run: bool = False, max_meetings: int | None = None,
             max_races: int = 11) -> BackfillReport:
    started = time.monotonic()
    report = BackfillReport(first=first, last=last)
    session = get_session()
    found = meetings_in_range(first, last, db=db, report=report, session=session)

    for n, (date, venue) in enumerate(found, start=1):
        if max_meetings is not None and n > max_meetings:
            break
        if dry_run:
            continue
        try:
            out = scrape_job.scrape_meeting(
                date, venue, post_race=True, db=db,
                max_races=max_races, session=session)
        except Exception as e:                          # noqa: BLE001
            # One unreachable meeting must not end a four-hour run. It is
            # recorded by name and the next one is attempted; re-running picks
            # up whatever failed, because a meeting with no results is never
            # treated as held.
            report.errors.append(f"{date} {venue}: {type(e).__name__}: {e}")
            continue
        report.meetings_scraped += 1
        report.races += out.races
        report.runners += out.runners
        # The racecard is gone for old meetings -- HKJC keeps results far
        # longer than cards -- so that warning is expected here and would
        # drown everything else. Anything else is worth seeing.
        report.warnings.extend(
            f"{date} {venue}: {w}" for w in out.warnings
            if not w.startswith("racecard:"))
        if out.races == 0:
            report.errors.append(f"{date} {venue}: probe said it raced, "
                                 "results returned nothing")
        print(f"  {date} {venue}  {out.races:>2} races {out.runners:>4} runners"
              f"   [{report.meetings_scraped} done,"
              f" {(time.monotonic()-started)/60:.0f} min]", flush=True)
    report.seconds = time.monotonic() - started
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="first", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="last", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="find the meetings and report them; fetch nothing")
    ap.add_argument("--max-meetings", type=int, default=None,
                    help="stop after this many, for a trial run")
    ap.add_argument("--max-races", type=int, default=11)
    a = ap.parse_args(argv)
    report = backfill(a.first, a.last, db=a.db, dry_run=a.dry_run,
                      max_meetings=a.max_meetings, max_races=a.max_races)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

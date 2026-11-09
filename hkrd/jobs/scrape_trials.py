"""Scrape barrier trials and store them.

    python -m hkrd.jobs.scrape_trials --date 2026-08-21   # one day
    python -m hkrd.jobs.scrape_trials                     # whatever is new
    python -m hkrd.jobs.scrape_trials --backfill 2019-05-01   # every older day, once

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
import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import trials as trials_ingest
from hkrd.ingest._client import FetchError, NotFound
from hkrd.store import job_log, upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "catch_up", "backfill", "TrialScrapeReport", "BackfillReport"]


@dataclass
class TrialScrapeReport:
    """Row counts. Never a bare "done": a zero has to be visible."""

    date: str = ""
    batches: int = 0
    runners: int = 0
    with_distance: int = 0
    # HKJC publishes these after the times. A day fetched before it has, or
    # fetched again while it still has not, stores every runner and fills
    # neither — which only these two counts can tell apart from a day that
    # landed whole.
    with_positions: int = 0
    with_comment: int = 0
    # Batches with no position or no comment on ANY runner — the same test
    # `outstanding` uses to fetch the day again.
    unfinished: int = 0
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
                 f"  with a distance    {self.with_distance:>6}",
                 f"  with positions     {self.with_positions:>6}",
                 f"  with a comment     {self.with_comment:>6}"]
        if self.unfinished:
            lines.append(f"  NOT YET PUBLISHED  {self.unfinished:>6} batches "
                         "— fetched again next run")
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
                    # NULL, not "", when the page has none. A stored day is
                    # fetched again while HKJC finishes it, and the upsert
                    # keeps a stored value against a NULL but not against an
                    # empty string — "" would blank positions already held.
                    "section_times": "; ".join(
                        batch.get("section_times") or []) or None,
                    "running_positions": " ".join(
                        str(p) for p in (r.get("running_positions") or [])
                    ) or None,
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
                positions = sum(1 for r in rows if r["running_positions"])
                comments = sum(1 for r in rows if r["comment_text"])
                report.with_positions += positions
                report.with_comment += comments
                if rows and not (positions and comments):
                    report.unfinished += 1

        # Recorded so the freshness strip can say when trials last LANDED.
        # Trials are published weekly, so three days old is current for this
        # source and stale for every other one — which is why the strip judges
        # each source against its own normal rather than one shared threshold.
        #
        # A morning with no trial day writes NOTHING. Recording a success would
        # advance the clock and make the strip read fresh when nothing arrived;
        # recording a failure would cry wolf on most mornings, which is how a
        # warning strip becomes wallpaper. The previous success stands, and it
        # is the honest answer to "when did trials last land".
        if report.runners or report.errors:
            with transaction(conn):
                job_log.record_source(
                    conn, "scrape_trials", ok=bool(report.runners),
                    detail=f"{report.batches} batches · {report.runners} trials")
    finally:
        conn.close()
    return report


# A batch HKJC has not finished publishing: the times are up, but no runner in
# it has a running position or no runner has a comment. One runner short is a
# horse that did not finish and is complete; a whole batch short is a page
# that was read too early.
_UNFINISHED = """
    SELECT DISTINCT trial_date FROM (
        SELECT trial_date FROM trials
        WHERE trial_date >= ?
        GROUP BY trial_date, venue, trial_no
        HAVING max(length(coalesce(running_positions, ''))) = 0
            OR max(length(coalesce(comment_text, ''))) = 0)
"""


def outstanding(days: Sequence[str], *, db: Path | None = None,
                limit: int = 6, recheck: int = 8) -> list[str]:
    """Of the days HKJC lists, the recent ones the database lacks or holds
    only half of.

    HKJC puts a trial day's times up first and the running positions and
    comments later. A day read in between used to count as HAD, so it was
    never asked about again: 2026-09-19 and 2026-09-25 sat in production with
    all 116 runners carrying a time and no position, no placing and no
    comment, and HKJC had published all three for every one of them by
    2026-09-26. So the most recent `recheck` listed days are fetched again
    while a batch on them is unfinished — about a fortnight of trial days,
    room for a page that is days late, and short enough that a day HKJC never
    completes stops costing a request.

    Oldest first, so a run that is cut short has filled the gap from the far
    end rather than leaving a hole in the middle of the archive.
    """
    window = list(days[:recheck])
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT trial_date FROM trials")}
        unfinished = {r[0] for r in conn.execute(
            _UNFINISHED, (min(window),))} if window else set()
    finally:
        conn.close()
    missing = [d for d in days
               if d not in have or (d in unfinished and d in window)]
    # `days` arrives newest first; take the most recent few, then run them in
    # order. The limit is a guard against a first run against an empty
    # database fetching a year of trial days in one go.
    return sorted(missing[:limit])


def catch_up(*, db: Path | None = None, limit: int = 6,
             session=None) -> list[TrialScrapeReport]:
    """Ask which trial days exist, fetch the recent ones we are missing or
    hold only half of."""
    days = trials_ingest.list_days(session=session)
    return [scrape(d, db=db, session=session)
            for d in outstanding(days, db=db, limit=limit)]


# ── the archive before HKJC's list ───────────────────────────────────────────
#
# HKJC's day list reaches back about thirteen months (6 Oct 2025 on 9 Nov
# 2026), and the archive started 21 Aug 2025 -- one season of trials, which is
# why the model could not read them. But the page still serves any older day
# asked for by its date: 2019 days parse in full (positions, times, comments,
# going, distance), and a date with no trials answers with HKJC's "No
# information." panel. So the only way back is to ask for every date.

# Ten failures in a row is HKJC refusing or the page changing, not ten bad
# days: stop rather than ask it two thousand more times.
STOP_AFTER_ERRORS = 10


@dataclass
class BackfillReport:
    """A walk over every date in a range: what was asked, what was there."""

    start: str = ""
    end: str = ""
    asked: int = 0              # dates fetched from HKJC
    held: int = 0               # dates skipped: the database already had trials on them
    trial_days: int = 0
    no_trials: int = 0          # dates HKJC answered "No information."
    runners: int = 0
    unfinished: int = 0         # batches stored without positions or comments
    stopped: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.stopped

    def render(self) -> str:
        lines = [f"  trial backfill     {self.end} back to {self.start}",
                 f"  dates asked        {self.asked:>6}",
                 f"  already held       {self.held:>6}",
                 f"  trial days found   {self.trial_days:>6}",
                 f"  no trials that day {self.no_trials:>6}",
                 f"  runners stored     {self.runners:>6}"]
        if self.unfinished:
            lines.append(f"  NOT YET PUBLISHED  {self.unfinished:>6} batches")
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        if self.stopped:
            lines.append(f"  STOPPED            {self.stopped}")
        return "\n".join(lines)


def backfill(start: str, end: str | None = None, *, db: Path | None = None,
             session=None, echo: Callable[[str], None] | None = None) -> BackfillReport:
    """Every date from `end` (default yesterday) back to `start`, newest first,
    on which the database holds no trial: asked one by one, stored the way the
    daily scrape stores a day. Idempotent, and a rerun skips what an earlier
    one stored -- the dates with no trials are asked again, which is the cost
    of not keeping a table of them."""
    end = end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    report = BackfillReport(start=start, end=end)
    say = echo or (lambda _line: None)
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        have = {r[0] for r in conn.execute("SELECT DISTINCT trial_date FROM trials")}
    finally:
        conn.close()
    day, first = dt.date.fromisoformat(end), dt.date.fromisoformat(start)
    in_a_row = 0
    while day >= first:
        date = day.isoformat()
        day -= dt.timedelta(days=1)
        if date in have:
            report.held += 1
            continue
        one = scrape(date, db=db, session=session)
        report.asked += 1
        if one.errors:
            report.errors += one.errors
            in_a_row += 1
            say(f"  {date}  ERROR {one.errors[0]}")
            if in_a_row >= STOP_AFTER_ERRORS:
                report.stopped = (f"{in_a_row} dates in a row failed, the last {date}; "
                                  f"rerun with --to {date} once HKJC answers again")
                break
            continue
        in_a_row = 0
        if one.no_such_day:
            report.no_trials += 1
        else:
            report.trial_days += 1
            report.runners += one.runners
            report.unfinished += one.unfinished
            say(f"  {date}  {one.batches:>2} batches {one.runners:>4} runners")
        if report.asked % 50 == 0:
            say(f"  ... {date}: {report.asked} asked, {report.trial_days} trial days, "
                f"{report.runners} runners")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD; omit to fetch whatever is new")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=6,
                    help="most trial days to fetch in one catch-up run")
    ap.add_argument("--backfill", metavar="FROM", default=None,
                    help="YYYY-MM-DD: ask for every date from --to back to FROM that the "
                         "database holds no trial for (HKJC lists only ~13 months)")
    ap.add_argument("--to", default=None,
                    help="YYYY-MM-DD: where --backfill starts (default yesterday)")
    a = ap.parse_args(argv)

    if a.backfill:
        filled = backfill(a.backfill, a.to, db=a.db, echo=lambda line: print(line, flush=True))
        print(filled.render())
        return 0 if filled.ok else 1

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

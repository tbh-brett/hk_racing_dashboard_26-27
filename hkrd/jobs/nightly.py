"""The one job cron runs. Scrape whatever is outstanding, then derive.

    python -m hkrd.jobs.nightly                      # the default window
    python -m hkrd.jobs.nightly --back 7 --ahead 3
    python -m hkrd.jobs.nightly --dry-run            # decide, fetch nothing

WHY A WINDOW AND NOT A FIXTURE LIST. Hong Kong races roughly twice a week, but
the calendar moves for typhoons, public holidays and international meetings, so
a hardcoded "Wednesday and Sunday" would be wrong several times a season and
wrong silently. Keeping a fixture table in sync is the same problem one layer
down. Instead this walks a few days either side of today and asks HKJC, which
is the only source that always knows.

WHY IT SKIPS SETTLED MEETINGS. A meeting with results and dividends for every
race will never change again, so re-fetching it is 11 requests of pure noise
against a public site run for punters. Nothing outstanding means a night that
makes two requests and stops.

WHY IT WRITES A ROW BEFORE IT STARTS. See store/job_log — a scrape that fails
at 23:15 on a Wednesday leaves a dashboard that looks exactly like a working
one, and the only difference is a date nobody checks. The health endpoint reads
what this writes.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import racecard
from hkrd.ingest._client import FetchError, NotFound
from hkrd.jobs import derive_all, scrape_meeting as scrape_job
from hkrd.store import job_log
from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["Plan", "plan_window", "probe", "run"]

VENUES = ("ST", "HV")

# Far enough back to catch a meeting whose results were late or whose scrape
# failed on the night, and one day forward because the card for tomorrow is
# published the day before and Race Day wants it.
DEFAULT_BACK = 4
DEFAULT_AHEAD = 1


@dataclass
class Plan:
    """One date's decision, and why. The reason is printed — a night that does
    nothing has to say what it looked at."""

    date: str
    venue: str | None          # known from the database, else None (probe both)
    reason: str                # settled | results outstanding | unknown
    act: bool


@dataclass
class NightlyReport:
    plans: list[Plan] = field(default_factory=list)
    scraped: list[str] = field(default_factory=list)
    derived: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = ["  window"]
        for p in self.plans:
            mark = "→" if p.act else " "
            venue = p.venue or "??"
            lines.append(f"    {mark} {p.date}  {venue:<2}  {p.reason}")
        lines.append(f"  scraped            {len(self.scraped):>6}")
        for s in self.scraped:
            lines.append(f"    {s}")
        if self.derived:
            lines.append("  derived")
            lines += [f"  {line}" for line in self.derived.splitlines()]
        if self.warnings:
            lines.append(f"  not available      {len(self.warnings):>6}")
            lines += [f"    {w}" for w in self.warnings[:8]]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)

    def one_line(self) -> str:
        """What goes in job_runs.detail and, from there, onto the page."""
        if self.errors:
            return f"{len(self.errors)} error(s): {self.errors[0][:160]}"
        if self.scraped:
            return "; ".join(self.scraped)
        return "nothing outstanding"


def plan_window(db: Path | None = None, *, today: dt.date | None = None,
                back: int = DEFAULT_BACK, ahead: int = DEFAULT_AHEAD
                ) -> list[Plan]:
    """Decide what to touch, using only the database. Makes no requests."""
    today = today or dt.date.today()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        plans = []
        for offset in range(-back, ahead + 1):
            date = (today + dt.timedelta(days=offset)).isoformat()
            plans.append(_decide(conn, date, future=offset > 0))
        return plans
    finally:
        conn.close()


def _decide(conn, date: str, *, future: bool) -> Plan:
    row = conn.execute(
        "SELECT r.venue AS venue, count(*) AS races, "
        "       sum(CASE WHEN r.finished  THEN 1 ELSE 0 END) AS finished, "
        "       sum(CASE WHEN r.paid      THEN 1 ELSE 0 END) AS paid "
        "FROM ("
        "  SELECT ra.race_date, ra.race_no, ra.venue,"
        "         EXISTS (SELECT 1 FROM runners ru WHERE ru.race_date = ra.race_date"
        "                  AND ru.race_no = ra.race_no AND ru.place IS NOT NULL)"
        "           AS finished,"
        "         EXISTS (SELECT 1 FROM dividends d WHERE d.race_date = ra.race_date"
        "                  AND d.race_no = ra.race_no) AS paid"
        "  FROM races ra WHERE ra.race_date = ?"
        ") r", (date,)).fetchone()

    races = row["races"] or 0
    venue = row["venue"]
    if not races:
        # The database has never seen this date. It is either not a race day or
        # a meeting nobody has fetched yet, and only HKJC can say which.
        return Plan(date, None, "not in the database yet", act=True)
    if future:
        return Plan(date, venue, f"{races} races carded", act=True)
    if row["finished"] == races and row["paid"] == races:
        return Plan(date, venue, f"settled, {races} races", act=False)
    missing = []
    if row["finished"] != races:
        missing.append(f"{races - (row['finished'] or 0)} without results")
    if row["paid"] != races:
        missing.append(f"{races - (row['paid'] or 0)} without dividends")
    return Plan(date, venue, ", ".join(missing), act=True)


def probe(date: str, venue: str, *, session=None) -> tuple[str, str]:
    """One request: is there a meeting here?

    Returns (verdict, detail) where verdict is `card`, `none` or `unparsed`.

    A full scrape walks up to eleven races before it concludes a date is not a
    race day, and five nights a week no date in the window is. One request for
    race 1 answers the same question, so a quiet night costs two requests
    instead of a hundred and seventy.

    `unparsed` is kept separate from `none` on purpose. HKJC's behaviour for a
    date with no meeting could not be measured from the machine this was
    written on — the network policy there blocks racing.hkjc.com — so it may
    404 (NotFound) or it may serve a page with no card table (RacecardError).
    Either way there is no meeting and the decision is the same, but a window
    where EVERY probe came back unparsed is the shape a layout change makes,
    and that gets said out loud rather than passing as five quiet nights.
    """
    try:
        got = racecard.fetch_race(date, venue, 1, session=session)
    except NotFound:
        return "none", "no card published"
    except racecard.RacecardError as exc:
        return "unparsed", str(exc)
    except FetchError as exc:
        # Transport. Not an answer about the meeting at all.
        return "unparsed", str(exc)
    runners = got.get("runners") or []
    if not runners:
        return "none", "card table present but empty"
    return "card", f"{len(runners)} declared"


def run(db: Path | None = None, *, today: dt.date | None = None,
        back: int = DEFAULT_BACK, ahead: int = DEFAULT_AHEAD,
        dry_run: bool = False, derive: bool = True,
        session=None) -> NightlyReport:
    """Scrape everything the window says is outstanding, then re-derive."""
    today = today or dt.date.today()
    report = NightlyReport(plans=plan_window(db, today=today, back=back,
                                             ahead=ahead))
    if dry_run:
        return report

    touched = False
    probes = unparsed = 0
    for plan in report.plans:
        if not plan.act:
            continue
        past = dt.date.fromisoformat(plan.date) <= today

        if plan.venue:
            # The database already knows this meeting and its venue. No probe.
            touched |= _scrape(report, plan.date, plan.venue, past=past,
                                db=db, session=session, expected=True)
            continue

        for venue in VENUES:
            probes += 1
            verdict, detail = probe(plan.date, venue, session=session)
            if verdict == "unparsed":
                unparsed += 1
                report.warnings.append(f"{plan.date} {venue}: {detail}")
                continue
            if verdict == "none":
                continue
            touched |= _scrape(report, plan.date, venue, past=past, db=db,
                                session=session, expected=False)
            break        # one meeting per date; the other venue is not racing

    if probes and probes == unparsed:
        # Every question asked came back as a page that would not parse. That
        # is what a layout change looks like from here, and it is also what a
        # week of no racing looks like — so it is a warning naming both, not a
        # silent nothing.
        report.errors.append(
            f"all {probes} probes returned a page that would not parse. Either "
            "HKJC changed the race card layout or the site is unreachable — "
            "check one date by hand before trusting an empty window.")

    if derive and touched:
        # Only when something landed. ET, SARR and tags rebuild across the
        # whole history, so running them on a night with no new racing is
        # minutes of work to write back the numbers already there.
        out = derive_all.run(db)
        report.derived = out.render()
        report.errors.extend(out.errors)
    return report


def _scrape(report: NightlyReport, date: str, venue: str, *, past: bool,
            db: Path | None, session, expected: bool) -> bool:
    """Fetch one meeting into the database and fold the counts into `report`.

    Returns whether anything landed, which is what decides if derive runs."""
    # A meeting in the future has no results yet; asking for them is not a
    # failure, so post_race is decided by the calendar and not by what comes
    # back.
    got = scrape_job.scrape_meeting(date, venue, post_race=past, db=db,
                                    session=session)
    if got.races:
        report.scraped.append(
            f"{date} {venue}: {got.races} races, {got.runners} runners, "
            f"{got.dividends} dividends")
        report.warnings.extend(f"{date} {venue}: {w}" for w in got.warnings)
        report.errors.extend(f"{date} {venue}: {e}" for e in got.errors)
        return True
    if expected:
        # The database said there was a meeting here. Coming back with nothing
        # is a failure, not a quiet night.
        report.errors.append(
            f"{date} {venue}: the database has this meeting but the scrape "
            "returned no races — " +
            (got.errors[0] if got.errors else "no error given"))
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--back", type=int, default=DEFAULT_BACK)
    ap.add_argument("--ahead", type=int, default=DEFAULT_AHEAD)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and make no requests")
    ap.add_argument("--no-derive", action="store_true")
    a = ap.parse_args(argv)

    if a.dry_run:
        report = run(a.db, back=a.back, ahead=a.ahead, dry_run=True)
        print(report.render())
        return 0

    with job_log.running("nightly", a.db) as outcome:
        report = run(a.db, back=a.back, ahead=a.ahead,
                     derive=not a.no_derive)
        outcome["ok"] = report.ok
        outcome["detail"] = report.one_line()
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

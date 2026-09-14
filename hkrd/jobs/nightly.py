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
    # Cards scored for SARR this run. Its own line because it is the step that
    # did not exist: the dateless derive below only reaches races with results,
    # so an upcoming card was fetched here five times a day and ranked by
    # nobody. A run that scored one says so.
    scored_cards: list[str] = field(default_factory=list)
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
        lines.append(f"  cards scored       {len(self.scored_cards):>6}")
        for c in self.scored_cards:
            lines.append(f"    {c}")
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
        if self.scraped or self.scored_cards:
            return "; ".join([*self.scraped, *self.scored_cards])
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
    # WHAT "SETTLED" HAS TO MEAN. Results and dividends are published within
    # the hour; the comments on running and the sectional times are not. A
    # meeting called settled is never looked at again, so testing only the two
    # fast sources meant the two slow ones never arrived: 2026-09-06 was marked
    # settled with all 119 of its comments still reading "No Comments on
    # Running information for this horse.", and therefore with no tags on any
    # runner and nothing on the strip to say why.
    #
    # The window is four days, so a source HKJC never publishes is retried a
    # handful of times and then falls out of range on its own. There is no
    # runaway here to guard against.
    row = conn.execute(
        "SELECT r.venue AS venue, count(*) AS races, "
        "       sum(CASE WHEN r.finished  THEN 1 ELSE 0 END) AS finished, "
        "       sum(CASE WHEN r.paid      THEN 1 ELSE 0 END) AS paid, "
        "       sum(CASE WHEN r.timed     THEN 1 ELSE 0 END) AS timed, "
        "       sum(CASE WHEN r.commented THEN 1 ELSE 0 END) AS commented "
        "FROM ("
        "  SELECT ra.race_date, ra.race_no, ra.venue,"
        "         EXISTS (SELECT 1 FROM runners ru WHERE ru.race_date = ra.race_date"
        "                  AND ru.race_no = ra.race_no AND ru.place IS NOT NULL)"
        "           AS finished,"
        "         EXISTS (SELECT 1 FROM dividends d WHERE d.race_date = ra.race_date"
        "                  AND d.race_no = ra.race_no) AS paid,"
        "         EXISTS (SELECT 1 FROM runners rs WHERE rs.race_date = ra.race_date"
        "                  AND rs.race_no = ra.race_no"
        "                  AND rs.section_times IS NOT NULL AND rs.section_times != '')"
        "           AS timed,"
        # A race whose every comment is HKJC's "No Comments on Running
        # information for this horse." has not been commented on yet. Measured:
        # a meeting that HAS them carries none of that placeholder at all, and
        # one that has not carries nothing else — it is all or nothing per
        # meeting, which is what makes this test reliable.
        "         EXISTS (SELECT 1 FROM runner_comments rc"
        "                  WHERE rc.race_date = ra.race_date"
        "                    AND rc.race_no = ra.race_no"
        "                    AND rc.comment_text IS NOT NULL"
        "                    AND rc.comment_text NOT LIKE 'No Comments on Running%')"
        "           AS commented"
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
    complete = all(row[k] == races
                   for k in ("finished", "paid", "timed", "commented"))
    if complete:
        return Plan(date, venue, f"settled, {races} races", act=False)
    missing = []
    for key, label in (("finished", "without results"),
                       ("paid", "without dividends"),
                       ("timed", "without sectionals"),
                       ("commented", "without comments")):
        if row[key] != races:
            missing.append(f"{races - (row[key] or 0)} {label}")
    return Plan(date, venue, ", ".join(missing), act=True)


def probe(date: str, venue: str, *, session=None) -> tuple[str, str]:
    """One request: is there a meeting here?

    Returns (verdict, detail) where verdict is `card`, `none` or `unparsed`.

    A full scrape walks up to eleven races before it concludes a date is not a
    race day, and five nights a week no date in the window is. One request for
    race 1 answers the same question, so a quiet night costs two requests
    instead of a hundred and seventy.

    `unparsed` is kept separate from `none` on purpose. Measured since: for a
    date with no meeting HKJC answers 200 with an error panel rather than a
    404, and `racecard.fetch_race` recognises that panel and raises NotFound —
    so a quiet night arrives here as `none`. A page that is neither a card nor
    that panel is a third thing, and a window where EVERY probe came back
    unparsed is the shape a layout change makes: that gets said out loud
    rather than passing as five quiet nights.
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
    cards: list[str] = []          # dates whose card landed this run
    probes = unparsed = 0
    for plan in report.plans:
        if not plan.act:
            continue
        past = dt.date.fromisoformat(plan.date) <= today

        if plan.venue:
            # The database already knows this meeting and its venue. No probe.
            results, card = _scrape(report, plan.date, plan.venue, past=past,
                                    db=db, session=session, expected=True)
            touched |= results
            if results or card:
                cards.append(plan.date)
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
            results, card = _scrape(report, plan.date, venue, past=past, db=db,
                                    session=session, expected=False)
            touched |= results
            if results or card:
                cards.append(plan.date)
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

    if derive:
        # After the full derive, so a card is ranked against any results that
        # landed earlier in this same run.
        _score_cards(report, cards, db=db)
    return report


def _unrun(db: Path | None, date: str) -> bool:
    """Does this date have a race nobody has run yet?

    A race with NO placing on any runner, not a runner with no placing: every
    settled meeting carries scratched horses with a NULL there, and testing for
    those would rescore every recent meeting every night.

    `place`, not `finish_time`, because `_decide` above already defines a race
    as finished that way and one module must not hold two definitions of the
    same fact -- a card this called unrun and that called finished would be
    scraped as settled and scored as upcoming. On real data the two agree.
    """
    conn = get_conn(db if db is not None else db_path())
    try:
        return bool(conn.execute(
            "SELECT 1 FROM races ra WHERE ra.race_date = ? AND NOT EXISTS ("
            "  SELECT 1 FROM runners r WHERE r.race_date = ra.race_date"
            "     AND r.race_no = ra.race_no AND r.place IS NOT NULL) "
            "LIMIT 1", (date,)).fetchone())
    finally:
        conn.close()


def _score_cards(report: NightlyReport, dates: list[str], *,
                 db: Path | None) -> None:
    """SARR for every card this run fetched that still has races to run.

    THE DATELESS DERIVE ABOVE NEVER REACHES AN UPCOMING CARD. It rebuilds the
    races that have finishing times, which an unrun card has none of, so a card
    fetched here was ranked only if someone pressed Card in the header -- the
    one path that passed a date. 2026-09-13 had ranks on race day because the
    card was scored by hand three days earlier; left to this job it would have
    had none.

    RESCORED EVERY TIME THE CARD LANDS, not once. This job refetches a card
    through to race day, which is how scratchings and replacements arrive, and
    a card scored once is a card whose replacement runner shows a blank that
    looks exactly like a debutant's. The rebuild is walk-forward -- a profile
    reads only runs before the race -- so scoring it again is the same numbers
    for the same field, and new numbers only where the field or the history
    changed.

    One date at a time and each failure recorded against its date, so a card
    that will not score does not take the others down with it.
    """
    for date in dict.fromkeys(dates):          # de-duplicated, order kept
        if not _unrun(db, date):
            continue
        try:
            out = derive_all.run(db, date=date, only=("sarr",))
        except Exception as exc:                   # noqa: BLE001 - recorded
            report.errors.append(f"{date} SARR: {type(exc).__name__}: {exc}")
            continue
        report.errors.extend(f"{date} SARR: {e}" for e in out.errors)
        n = out.written.get("runner_sarr", 0)
        report.scored_cards.append(f"{date}: SARR {n} runners")


def _scrape(report: NightlyReport, date: str, venue: str, *, past: bool,
            db: Path | None, session, expected: bool) -> tuple[bool, bool]:
    """Fetch one meeting into the database and fold the counts into `report`.

    Returns (results landed, card landed). Results decide whether the full
    derive runs; a card decides whether that date is scored for SARR."""
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
        return True, bool(got.declared)
    if got.declared:
        # A DECLARED FIELD IS A RESULT. Before a meeting is run the scrape
        # stores the card and nothing else, so `races` is zero -- and this used
        # to fall through to the error below. Every refresh of an upcoming card
        # was logged as "the database has this meeting but the scrape returned
        # no races": seven in a row from 2026-09-11 to race morning on 09-13,
        # each one a successful fetch that picked up the scratchings.
        report.scraped.append(f"{date} {venue}: card, {got.declared} declared")
        report.warnings.extend(f"{date} {venue}: {w}" for w in got.warnings)
        report.errors.extend(f"{date} {venue}: {e}" for e in got.errors)
        return False, True
    if expected:
        # The database said there was a meeting here. Coming back with nothing
        # is a failure, not a quiet night.
        report.errors.append(
            f"{date} {venue}: the database has this meeting but the scrape "
            "returned no races — " +
            (got.errors[0] if got.errors else "no error given"))
    return False, False


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

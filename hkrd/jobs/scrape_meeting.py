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
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import (corunning, dividends as dividends_ingest,
                         racecard as racecard_ingest,
                         results as results_ingest, vet as vet_ingest)
from hkrd.ingest._client import FetchError
from hkrd.store import job_log
from hkrd.store import upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction


@dataclass
class ScrapeReport:
    """Row counts per table. Never a bare "done": a zero has to be visible."""

    date: str = ""
    venue: str = ""
    races: int = 0
    runners: int = 0
    declared: int = 0
    comments: int = 0
    lane_tags: int = 0
    dividends: int = 0
    vet_records: int = 0
    errors: list[str] = field(default_factory=list)
    # A source that is absent is not a scrape that failed. The card is not
    # published for every meeting this package can reach, and dividends and
    # vet records only exist after the race — so those go here and do not
    # make `ok` false, while a results failure does.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Did this run land anything it could land?

        `races > 0` alone made every PRE-RACE scrape a failure — which is the
        only kind the card button exists for. A meeting whose card is up and
        whose results do not exist yet has been scraped completely: there is
        nothing else to fetch until it is run. A declared field is a result.
        """
        return not self.errors and (self.races > 0 or self.declared > 0)

    def render(self) -> str:
        lines = [f"  meeting            {self.date} {self.venue}",
                 f"  races              {self.races:>6}",
                 f"  runners            {self.runners:>6}",
                 f"  declared           {self.declared:>6}",
                 f"  comments           {self.comments:>6}",
                 f"  lane tags          {self.lane_tags:>6}",
                 f"  dividends          {self.dividends:>6}",
                 f"  vet records        {self.vet_records:>6}"]
        if self.warnings:
            lines.append(f"  not available      {len(self.warnings):>6}")
            lines += [f"    {w}" for w in self.warnings[:6]]
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

    # BEFORE the race: the card is the declared field, and it is the only
    # source for rating, gear and days-since-last. It is fetched first so a
    # meeting can be scraped ahead of time and again after — the second pass
    # upserts the results onto the same rows.
    declared: dict[int, set[str]] = {}
    try:
        card = racecard_ingest.fetch_meeting(date, venue, max_races=max_races,
                                             session=session)
        report.declared = _store_card(db, card)
        report.warnings.extend(f"racecard: {e}" for e in card["errors"])
        # Kept to check the results against. See `_is_same_meeting`.
        for race in card["races"]:
            declared[race["race"]["race_no"]] = {
                (r.get("horse_name") or "").strip().upper()
                for r in race["runners"] if r.get("horse_name")}
    except (FetchError, racecard_ingest.RacecardError) as e:
        report.warnings.append(f"racecard: {e}")

    # NOTHING POST-RACE IS FETCHED BEFORE THE RACE. Asked for the results of a
    # meeting that has not been run, HKJC does not answer 404 — it serves a
    # page, and `_store_race` stamps the date we ASKED for onto whatever came
    # back. That wrote another meeting's runners, odds and finishing positions
    # under this date: a 6 Sep card showing 15 Jul's twelve horses at 15 Jul's
    # prices, with the two genuinely new runners left underneath.
    #
    # There is nothing to fetch yet, so the fix is to not ask.
    if date >= _today():
        # The VET RECORD is the exception, and the reason it is fetched here
        # rather than with the post-race sources: it is not an account of the
        # race, it is each declared runner's veterinary history — examinations
        # from previous months, attached to today's field. It is published with
        # the card and it is the single most useful thing to know before
        # backing anything.
        try:
            notes = vet_ingest.fetch_meeting(date, venue, max_races=max_races,
                                             session=session)
            report.vet_records = _store_vet(db, notes)
        except (FetchError, vet_ingest.VetError) as e:
            report.warnings.append(f"vet: {e}")
        report.warnings.append(
            f"results: not published yet — {date} has not been run")
        _log_sources(db, report, post_race=post_race, pre_race=True)
        return report

    try:
        races = results_ingest.fetch_meeting(date, venue, max_races=max_races,
                                             session=session)
    except (FetchError, results_ingest.ResultsError) as e:
        # A meeting that has not been run yet HAS no results, and saying so is
        # a fact about the calendar rather than a failed scrape. After the day
        # it is a real failure and stays an error.
        if date >= _today():
            report.warnings.append(
                f"results: not published yet — {date} has not been run")
        else:
            report.errors.append(f"results: {e}")
        # Nothing further to fetch, but the card that just landed must still be
        # recorded: returning here without logging is why the strip said "no
        # successful run on record" after a card scrape that worked.
        _log_sources(db, report, post_race=post_race)
        return report

    # A second line of defence for every other way a page can be substituted:
    # the results must describe the field the card declared. Storing a race
    # whose runners this meeting never had is worse than storing nothing,
    # because nothing about the rows afterwards says they are from elsewhere.
    wrong = [r for r in races if not _is_same_meeting(r, declared)]
    if wrong:
        report.errors.append(
            f"results: the page returned for {date} {venue} lists runners this "
            f"meeting did not declare (races "
            f"{', '.join(str(r.get('race_no')) for r in wrong)}) — refusing to "
            f"store another meeting's results under this date")
        races = [r for r in races if r not in wrong]

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

        # Dividends and vet records are also post-race only.
        try:
            paid = dividends_ingest.fetch_meeting(
                date, venue, max_races=max_races, session=session)
            report.dividends = _store_dividends(db, date, paid)
        except (FetchError, dividends_ingest.DividendsError) as e:
            report.warnings.append(f"dividends: {e}")

        try:
            notes = vet_ingest.fetch_meeting(date, venue, max_races=max_races,
                                             session=session)
            report.vet_records = _store_vet(db, notes)
        except (FetchError, vet_ingest.VetError) as e:
            report.warnings.append(f"vet: {e}")

    _log_sources(db, report, post_race=post_race)
    return report


# The meetings, the dates on the cards and the person reading them are all in
# Hong Kong. A host running in UTC is eight hours behind, which on a Saturday
# evening would call a card that is up "in the future" and file a real results
# failure as "not run yet".
_HK = dt.timezone(dt.timedelta(hours=8))


def _is_same_meeting(race: dict, declared: dict[int, set[str]]) -> bool:
    """Do these results belong to the card we declared?

    Only checkable when the card was read — with no declared field there is
    nothing to compare against and the results are taken at their word.
    A field changes between declaration and running (scratchings, reserves),
    so this asks for a MAJORITY overlap rather than an exact match: a wholly
    different meeting shares almost no horses, and a real card shares nearly
    all of them.
    """
    want = declared.get(race.get("race_no"))
    if not want:
        return True
    got = {(r.get("horse_name") or "").strip().upper()
           for r in race.get("runners", []) if r.get("horse_name")}
    if not got:
        return True
    return len(got & want) * 2 > len(got)


def _today() -> str:
    """Today in Hong Kong, where the meetings are."""
    return dt.datetime.now(_HK).date().isoformat()


def _log_sources(db, report: ScrapeReport, *, post_race: bool,
                 pre_race: bool = False) -> None:
    """Record each source separately, for the freshness strip.

    One job fetches four sources that go stale at very different rates, and a
    vet scrape that failed while the card succeeded is a fact the strip has to
    be able to show. Recording only the job would average them into one mark
    and hide exactly the case worth seeing.

    A source with nothing to fetch yet is NOT a failure: results and vet
    records do not exist before the race is run, and marking them failed would
    train the eye to ignore the strip on every race morning.
    """
    failed = {w.split(":", 1)[0] for w in report.warnings}
    failed |= {e.split(":", 1)[0] for e in report.errors}
    entries = [
        ("card", "racecard" not in failed and report.declared >= 0,
         f"{report.declared} declared"),
    ]
    # Nothing that only exists AFTER a race is claimed either way before the
    # race. There is no success or failure to record when the meeting has not
    # been run — and a strip that marks three sources failed every race
    # morning is a strip nobody reads by the afternoon.
    # The vet record is published with the card, so it is claimed either way
    # whenever it was actually fetched — before the race as well as after.
    if pre_race or post_race:
        entries.append(("vet", "vet" not in failed,
                        f"{report.vet_records} records"))
    run_yet = not (report.date >= _today() and report.races == 0)
    if run_yet:
        entries.append(
            ("results", report.races > 0,
             f"{report.races} races · {report.runners} runners"))
        if post_race:
            entries.append(("dividends", "dividends" not in failed,
                            f"{report.dividends} dividends"))
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            for source, ok, detail in entries:
                job_log.record_source(conn, f"scrape_meeting:{source}",
                                      ok=ok, detail=detail)
    finally:
        conn.close()


def _open(db: Path | None):
    conn = get_conn(db if db is not None else db_path())
    init_db(conn)
    return conn


def _store_card(db: Path | None, card: dict) -> int:
    """The declared field. Written through the same upsert the results use, so
    a race scraped twice is one set of rows rather than two."""
    written = 0
    conn = _open(db)
    try:
        with transaction(conn):
            for race in card["races"]:
                head = race["race"]
                upsert.upsert_races(conn, [head])
                written += upsert.upsert_runners(conn, [{
                    "race_date": head["race_date"],
                    "race_no": r.get("race_no"),
                    "horse_no": r.get("horse_no"),
                    "horse_name": r.get("horse_name"),
                    "draw": r.get("draw"), "jockey": r.get("jockey"),
                    "trainer": r.get("trainer"),
                    "actual_weight": r.get("actual_weight"),
                    "declared_weight": r.get("declared_weight"),
                    "rating": r.get("rating"), "gear": r.get("gear"),
                } for r in race["runners"]])
    finally:
        conn.close()
    return written


def _store_dividends(db: Path | None, date: str,
                     paid: dict[int, list[dict]]) -> int:
    written = 0
    conn = _open(db)
    try:
        with transaction(conn):
            for race_no, rows in paid.items():
                written += upsert.upsert_dividends(conn, [
                    {**r, "race_date": date, "race_no": race_no} for r in rows])
    finally:
        conn.close()
    return written


def _store_vet(db: Path | None, notes: dict[int, list[dict]]) -> int:
    written = 0
    conn = _open(db)
    try:
        with transaction(conn):
            for rows in notes.values():
                written += upsert.upsert_vet_records(conn, rows)
    finally:
        conn.close()
    return written


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

"""Capture live odds for a meeting and store every snapshot.

    python -m hkrd.jobs.scrape_odds --date 2026-07-15 --venue HV
    python -m hkrd.jobs.scrape_odds                  # today's meeting, if any

This is the job the previous build never had. `ingest/odds.py` could parse a
snapshot from the moment it was written, but nothing produced one, so
`odds_snapshots` held only what the legacy import rescued — and every
odds-dependent figure in the dashboard read a stale price while the project's
own rule says it must read the latest.

Two rules govern it:

**Nothing here deletes.** The old scraper called `prune_old_snapshots(keep=20)`
after every capture and 17 meetings of a full season survived. Odds movement is
the only data in this system that cannot be reconstructed after the fact — the
favourite changes between morning and post time in 44% of races — and a season
of it is a few hundred megabytes.

**Row counts, never silence.** Every run reports what it wrote per race. A zero
is visible immediately, because a scrape that silently captured nothing looks
exactly like one that captured everything until someone checks a price.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from hkrd.ingest import doubles as doubles_ingest
from hkrd.ingest import odds as odds_ingest
from hkrd.ingest import turnover as turnover_ingest
from hkrd.store import job_log, upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run", "OddsRun"]


@dataclass
class OddsRun:
    """What one capture wrote. Printed, and returned to the caller."""
    race_date: str
    venue: str
    races: int = 0
    attempted: int = 0
    win_place: int = 0
    pairs: int = 0
    legs: int = 0
    doubles: int = 0
    turnover: int = 0
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        if not self.attempted:
            return f"{self.race_date}: nothing to price"
        head = (f"{self.race_date} {self.venue}: {self.races} races · "
                f"{self.win_place} win/place · {self.pairs} pair odds")
        # Reported separately rather than folded into one number. A run that
        # captured every price and no turnover is a specific, fixable failure,
        # and a single total would hide it behind a plausible figure.
        if self.turnover:
            head += f" · {self.turnover} turnover"
        if self.legs:
            head += f" · {self.legs} legs/{self.doubles} doubles"
        if self.skipped:
            head += f" · {len(self.skipped)} SKIPPED"
        return head


# A race whose off time is this far past is settled: its price will not move
# again, and re-capturing it forever would grow the one table nothing prunes
# without adding a single new fact about the market.
SETTLED_AFTER_MINUTES = 30


def _meeting_races(conn, date: str, *, now: dt.datetime | None = None
                   ) -> tuple[str | None, list[int], int, int]:
    """The venue and the races still worth pricing on this date.

    The card is scraped first, so the meeting shape is a fact by the time odds
    are wanted. Guessing a race count would either miss races or ask HKJC for
    ones that do not exist.

    Races already run are dropped when their off time is known. A price cannot
    move after the off, so capturing it again adds rows and no information.
    """
    rows = conn.execute(
        "SELECT race_no, venue, off_time FROM races WHERE race_date = ? "
        "ORDER BY race_no", (date,)).fetchall()
    if not rows:
        return None, [], 0, 0

    now = now or dt.datetime.now()
    live: list[int] = []
    for r in rows:
        off = (r["off_time"] or "").strip()
        if off and _is_settled(date, off, now):
            continue
        live.append(r["race_no"])
    # The LAST race number, not the count. A card that loses a race keeps the
    # numbering of the ones that remain, so counting rows would place the final
    # double a leg short and stop capturing it.
    return rows[0]["venue"], live, len(rows), max(r["race_no"] for r in rows)


def _is_settled(date: str, off_time: str, now: dt.datetime) -> bool:
    """True when this race went off more than SETTLED_AFTER_MINUTES ago.

    An unreadable off time is treated as NOT settled: dropping a race because
    its time could not be parsed would silently stop pricing it, which is the
    class of failure this rebuild exists to remove.
    """
    try:
        hh, mm = off_time.split(":")[:2]
        off = dt.datetime.fromisoformat(date).replace(
            hour=int(hh), minute=int(mm))
    except (ValueError, IndexError):
        return False
    return now - off > dt.timedelta(minutes=SETTLED_AFTER_MINUTES)


def live_legs(live_races: list[int], last_race: int) -> list[int]:
    """The doubles legs still bettable, given which races are still to run.

    Leg N couples race N with race N+1, so it closes when race N goes off — not
    when race N+1 does. The list therefore shortens through the afternoon on
    its own, which is also what bounds the cost of capturing it: nine legs at
    midday, one by the second-last race.

    There is no leg on the last race: it has nothing to pair with.
    """
    return [n for n in sorted(live_races) if n < last_race]


def _capture_extras(date: str, venue: str, races: list[int], legs: list[int], *,
                    headless: bool = True, executable_path: str | None = None
                    ) -> tuple[list[dict], list[dict], list[str]]:
    """Pool turnover per race and doubles grids per leg, through ONE browser.

    A separate pass from the win/place capture rather than one interleaved
    loop, because the two have different failure modes and different value: a
    price that fails to render is the thing this job exists for, while a
    turnover figure that fails to render costs a denominator and nothing else.
    Kept behind one function so a caller can substitute it whole.

    Failures are collected and returned, never raised: a doubles grid that did
    not render must not cost the meeting its prices.
    """
    turnovers: list[dict] = []
    doubles: list[dict] = []
    notes: list[str] = []
    with odds_ingest.browser_page(headless=headless,
                                  executable_path=executable_path) as page:
        for race_no in races:
            try:
                parsed = turnover_ingest.fetch_race(page, date, venue, race_no)
            except Exception as exc:                # noqa: BLE001 - recorded
                notes.append(f"R{race_no} turnover: {type(exc).__name__}: {exc}")
                continue
            notes.extend(f"R{race_no} turnover: {n}" for n in parsed["notes"])
            turnovers.extend(turnover_ingest.turnover_rows(parsed))
        for leg_no in legs:
            try:
                parsed = doubles_ingest.fetch_leg(page, date, venue, leg_no)
            except Exception as exc:                # noqa: BLE001 - recorded
                notes.append(f"leg {leg_no} doubles: {type(exc).__name__}: {exc}")
                continue
            notes.extend(f"leg {leg_no}: {n}" for n in parsed["notes"])
            doubles.extend(doubles_ingest.double_rows(parsed))
    return turnovers, doubles, notes


def run(date: str | None = None, venue: str | None = None, *,
        races: list[int] | None = None, db: str | None = None,
        headless: bool = True, executable_path: str | None = None,
        today: dt.date | None = None,
        turnover: bool = True, doubles: bool = True) -> OddsRun:
    """Fetch and store one meeting's odds, turnover and doubles.

    `turnover` and `doubles` are switches rather than always-on because they
    roughly triple the pages a capture loads, and on a small machine the win
    and place prices are the ones that must not be missed. Both default on:
    turnover is the denominator every other figure needs, and neither can be
    reconstructed after the meeting.
    """
    conn = get_conn(db) if db else get_conn(db_path())
    try:
        init_db(conn)
        # Default to TODAY, not the latest meeting. Odds are a live signal:
        # re-pricing a meeting that finished last week would capture settled
        # dividends as if they were a market. A day with no meeting is not an
        # error -- it is what most days are -- so this returns an empty report
        # and never opens a browser, which is what makes the job safe to run
        # every quarter of an hour.
        explicit = date is not None
        date = date or (today or dt.date.today()).isoformat()

        known_venue, live_races, stored, last_race = _meeting_races(conn, date)
        venue = venue or known_venue
        targets = races or live_races

        report = OddsRun(race_date=date, venue=venue or "—")
        report.attempted = len(targets)

        if not stored:
            # Nothing on the card at all. On an unattended run that is simply
            # what most days are; asked for by name, it means the card has not
            # been scraped and the caller should hear so rather than read a
            # zero and assume the market was quiet.
            report.notes.append(f"no meeting stored for {date}")
            if explicit:
                raise ValueError(
                    f"no races stored for {date}; scrape the card before the odds")
            return report
        if not targets:
            report.notes.append(
                f"all {stored} races on {date} are more than "
                f"{SETTLED_AFTER_MINUTES} minutes past their off time")
            return report
        snaps = odds_ingest.fetch_meeting(
            date, venue, targets, headless=headless,
            executable_path=executable_path)

        for snap in snaps:
            race_no = snap["race_no"]
            report.notes.extend(f"R{race_no}: {n}" for n in snap.get("notes", []))
            if snap.get("stale_dom"):
                # A race whose DOM never changed is the previous race's odds
                # wearing this race's number. Writing it would corrupt the one
                # table that cannot be reconstructed, so it is refused loudly.
                report.skipped.append(
                    f"R{race_no}: stale DOM — would have stored another race's prices")
                continue

            parsed = odds_ingest.parse_snapshot(snap)
            win_place = odds_ingest.snapshot_rows(parsed)
            pairs = odds_ingest.pair_rows(parsed)
            if not win_place:
                report.skipped.append(f"R{race_no}: no priced runners")
                continue

            with transaction(conn):
                report.win_place += upsert.upsert_odds_snapshots(conn, win_place)
                report.pairs += upsert.upsert_odds_pairs(conn, pairs)
            report.races += 1

        # Turnover and doubles. Second, and deliberately after the prices are
        # already committed: if the browser dies here the meeting still has its
        # win, place and quinella odds for this moment, which is the half that
        # cannot be reconstructed.
        legs = live_legs(targets, last_race) if doubles else []
        if turnover or legs:
            try:
                turnover_rows, double_rows, extra_notes = _capture_extras(
                    date, venue, targets if turnover else [], legs,
                    headless=headless, executable_path=executable_path)
            except Exception as exc:                # noqa: BLE001 - recorded
                # The browser itself failed to start for the second pass --
                # chromium wants ~512MB and the deployed machine has 1GB, so
                # this is a real outcome rather than a theoretical one. The
                # prices are already committed; letting it propagate would skip
                # the job_log write below and report the whole capture as
                # missing, when the half that cannot be reconstructed landed.
                report.notes.append(
                    f"turnover/doubles pass did not run: "
                    f"{type(exc).__name__}: {exc}")
            else:
                report.notes.extend(extra_notes)
                with transaction(conn):
                    report.turnover += upsert.upsert_pool_turnover(
                        conn, turnover_rows)
                    report.doubles += upsert.upsert_odds_doubles(
                        conn, double_rows)
                report.legs = len({r["leg_no"] for r in double_rows})

        # Recorded so the freshness strip can say when odds last landed, and
        # so a run that stored nothing is visible as such rather than as
        # silence indistinguishable from a run that never happened.
        with transaction(conn):
            job_log.record_source(
                conn, "scrape_odds", ok=report.races > 0, detail=report.line())
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    ap.add_argument("--venue", help="HV or ST; read from the stored card if omitted")
    ap.add_argument("--race", type=int, action="append", dest="races",
                    help="one race number; repeat for several. Default: all")
    ap.add_argument("--db", help="database path; defaults to HKRD_DB")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser, for diagnosing a render problem")
    ap.add_argument("--chromium", help="path to a chromium binary, if not the default")
    ap.add_argument("--no-turnover", action="store_true",
                    help="skip pool turnover (one extra page per race)")
    ap.add_argument("--no-doubles", action="store_true",
                    help="skip doubles grids (one extra page per live leg)")
    args = ap.parse_args(argv)

    report = run(args.date, args.venue, races=args.races, db=args.db,
                 headless=not args.headed, executable_path=args.chromium,
                 turnover=not args.no_turnover, doubles=not args.no_doubles)
    print(report.line())
    for note in report.notes:
        print(f"  note: {note}")
    for skip in report.skipped:
        print(f"  SKIPPED {skip}")
    # Nothing to price is not a failure -- most days have no meeting. Having
    # something to price and storing none of it is.
    if report.attempted and not report.races:
        return 1
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())

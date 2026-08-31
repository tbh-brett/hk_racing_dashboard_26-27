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

from hkrd.ingest import odds as odds_ingest
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
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        if not self.attempted:
            return f"{self.race_date}: nothing to price"
        head = (f"{self.race_date} {self.venue}: {self.races} races · "
                f"{self.win_place} win/place · {self.pairs} pair odds")
        if self.skipped:
            head += f" · {len(self.skipped)} SKIPPED"
        return head


# A race whose off time is this far past is settled: its price will not move
# again, and re-capturing it forever would grow the one table nothing prunes
# without adding a single new fact about the market.
SETTLED_AFTER_MINUTES = 30


def _meeting_races(conn, date: str, *, now: dt.datetime | None = None
                   ) -> tuple[str | None, list[int], int]:
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
        return None, [], 0

    now = now or dt.datetime.now()
    live: list[int] = []
    for r in rows:
        off = (r["off_time"] or "").strip()
        if off and _is_settled(date, off, now):
            continue
        live.append(r["race_no"])
    return rows[0]["venue"], live, len(rows)


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


def run(date: str | None = None, venue: str | None = None, *,
        races: list[int] | None = None, db: str | None = None,
        headless: bool = True, executable_path: str | None = None,
        today: dt.date | None = None) -> OddsRun:
    """Fetch and store one meeting's odds. Returns the counts it wrote."""
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

        known_venue, live_races, stored = _meeting_races(conn, date)
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
    args = ap.parse_args(argv)

    report = run(args.date, args.venue, races=args.races, db=args.db,
                 headless=not args.headed, executable_path=args.chromium)
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

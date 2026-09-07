"""Capture live odds for a meeting and store every snapshot.

    python -m hkrd.jobs.scrape_odds --date 2026-07-15 --venue HV
    python -m hkrd.jobs.scrape_odds                  # today's meeting, if any

This is the job the previous build never had. `ingest/odds.py` could parse a
snapshot from the moment it was written, but nothing produced one, so
`odds_snapshots` held only what the legacy import rescued — and every
odds-dependent figure in the dashboard read a stale price while the project's
own rule says it must read the latest.

One meeting is one HTTP request. It used to be a browser and eleven page
loads, which is why this ran on the PC by hand and the deploy image's cron
line stayed commented out; see the note at the top of `ingest/odds.py`.

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
from hkrd.query import market as market_q
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
    closed: list[int] = field(default_factory=list)

    def line(self) -> str:
        if not self.attempted:
            return f"{self.race_date}: nothing to price"
        head = (f"{self.race_date} {self.venue}: {self.races} races · "
                f"{self.win_place} win/place · {self.pairs} pair odds")
        if self.closed:
            head += (" · market shut on R"
                     + ", R".join(str(n) for n in sorted(self.closed)))
        if self.skipped:
            head += f" · {len(self.skipped)} SKIPPED"
        if not self.races:
            # A bare row of zeros is the shape a silent failure and a quiet
            # market share, and the whole point of this report is that they
            # must never look the same. Whatever the run knows about why,
            # goes on the line the freshness strip shows.
            why = (self.skipped or self.notes or ["no reason recorded"])[0]
            head += f" — {why}"
        return head


# The cadence ladder lives in `query/market`, not here. It is one definition
# with two readers: this job uses it to decide when a race is worth
# re-pricing, and `api/` uses it to tell the browser when it is worth asking
# for a new price. A second copy would drift, and the failure would be silent —
# a page polling every thirty seconds against an hourly capture, or an hourly
# page against a market moving every minute.
SETTLED_AFTER_MINUTES = market_q.SETTLED_AFTER_MINUTES
CADENCE_MINUTES = market_q.CADENCE_MINUTES
PAIR_CADENCE_MINUTES = market_q.PAIR_CADENCE_MINUTES
_minutes_to_off = market_q.minutes_to_off
_interval_for = market_q.interval_for


def _due(last: str | None, interval: float, now: dt.datetime) -> bool:
    """True when the last capture of this race is older than its interval.

    A race never captured is always due. An unparseable timestamp is treated
    as never captured: refusing to price a race because a stored string could
    not be read would stop the capture silently, which is the class of failure
    this rebuild exists to remove.
    """
    if not last:
        return True
    try:
        when = dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    # A tolerance, because cron fires on the minute and the previous capture
    # landed a second or two into it. Without it every other tick is skipped.
    return (now - when).total_seconds() / 60.0 >= interval - 0.25


def _last_captures(conn, date: str) -> tuple[dict[int, str], dict[int, str]]:
    """The most recent capture per race, for win/place and for pairs.

    Two lookups rather than one because the two are sampled on different
    cadences, so a race can be due for a win price and not for a pair price.
    """
    wp = {r["race_no"]: r["at"] for r in conn.execute(
        "SELECT race_no, max(captured_at) at FROM odds_snapshots "
        "WHERE race_date = ? GROUP BY race_no", (date,))}
    pairs = {r["race_no"]: r["at"] for r in conn.execute(
        "SELECT race_no, max(captured_at) at FROM odds_pairs "
        "WHERE race_date = ? GROUP BY race_no", (date,))}
    return wp, pairs


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
    shut = closed_races(conn, date)
    live: list[int] = []
    for r in rows:
        # Two reasons to stop, and the first is the one that is actually true.
        # HKJC shutting the pool is the event; the clock is only a backstop for
        # a race whose close was never observed -- a capture that was down at
        # the moment it happened, or a card with no off time at all.
        if r["race_no"] in shut:
            continue
        off = (r["off_time"] or "").strip()
        if off and _is_settled(date, off, now):
            continue
        live.append(r["race_no"])
    return rows[0]["venue"], live, len(rows)


def closed_races(conn, date: str) -> set[int]:
    """Races whose betting HKJC has already shut, from the local record.

    One indexed lookup per tick, so a meeting that finished at six o'clock
    costs nothing at all for the rest of the evening rather than a request a
    minute per race.
    """
    return {r["race_no"] for r in conn.execute(
        "SELECT race_no FROM market_close WHERE race_date = ?", (date,))}


def _shut(snap: dict[str, Any], to_off: float | None, *,
          priced: bool) -> str | None:
    """The status to record when this race's market has closed for good.

    Returns None while it is still worth asking again. Three cases have to stay
    apart, and conflating any two of them either loses the late money or fills
    the table with rows about a race that is over:

      selling                     the market is open. Keep capturing.
      shut, never priced          a declared card before the market opens at
                                  13:00 the day before racing. Not an ending.
      shut before the off         a suspension, or a pool briefly withdrawn.
                                  Not an ending either, and treating it as one
                                  would drop the race for good over a blip.
      shut at or after the off    the race has been run. Stop.
    """
    status = str(snap.get("sell_status") or "").strip()
    if status.upper() == odds_ingest.SELLING:
        return None
    if not priced:
        return None
    if to_off is not None and to_off > 0:
        return None
    return status or "no status offered"


def _schedule(conn, date: str, races: list[int], *, now: dt.datetime
              ) -> tuple[set[int], set[int]]:
    """Which races are due a win/place capture, and which a pair capture.

    This is what makes a one-minute cron affordable. Every tick asks the
    database a question it can answer locally; only a tick with something due
    reaches HKJC at all.
    """
    offs = {r["race_no"]: (r["off_time"] or "").strip() for r in conn.execute(
        "SELECT race_no, off_time FROM races WHERE race_date = ?", (date,))}
    last_wp, last_pairs = _last_captures(conn, date)

    due_wp: set[int] = set()
    due_pairs: set[int] = set()
    for race_no in races:
        to_off = _minutes_to_off(date, offs.get(race_no, ""), now)
        if _due(last_wp.get(race_no), _interval_for(to_off), now):
            due_wp.add(race_no)
        if _due(last_pairs.get(race_no),
                _interval_for(to_off, PAIR_CADENCE_MINUTES), now):
            due_pairs.add(race_no)
    return due_wp, due_pairs


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
        today: dt.date | None = None, now: dt.datetime | None = None,
        session=None) -> OddsRun:
    """Fetch and store one meeting's odds. Returns the counts it wrote."""
    conn = get_conn(db) if db else get_conn(db_path())
    try:
        init_db(conn)
        # Never a past meeting. Odds are a live signal: re-pricing one that
        # finished last week would capture settled dividends as if they were a
        # market. A day with no meeting is not an error -- it is what most days
        # are -- so this returns an empty report and never reaches the network,
        # which is what makes the job safe to run every minute.
        explicit = date is not None
        now = now or dt.datetime.now()
        if not explicit:
            # Today's meeting, or -- once today's races are all settled, or if
            # there is no meeting today at all -- tomorrow's. HKJC opens a
            # market at 13:00 the day before racing, so by the time the first
            # race-day tick runs at noon the price has already been moving for
            # 23 hours. Race 1 goes off at 12:30. Without this the whole
            # overnight market is lost, and it is the one thing in this
            # database that cannot be reconstructed afterwards.
            #
            # Today first, always: its money is the perishable half.
            base = today or dt.date.today()
            date = base.isoformat()
            known_venue, live_races, stored = _meeting_races(
                conn, date, now=now)
            if not live_races:
                ahead = (base + dt.timedelta(days=1)).isoformat()
                venue_a, races_a, stored_a = _meeting_races(
                    conn, ahead, now=now)
                # Only move on if tomorrow actually has something. With no
                # meeting on either day the report must name TODAY, or the
                # commonest line in the log is about a date nobody asked about.
                if races_a:
                    date, known_venue = ahead, venue_a
                    live_races, stored = races_a, stored_a
        else:
            known_venue, live_races, stored = _meeting_races(
                conn, date, now=now)

        venue = venue or known_venue
        targets = races or live_races

        report = OddsRun(race_date=date, venue=venue or "—")

        # The cadence ladder. A race is priced often near its off and rarely
        # the day before, because that is where the money moves; see
        # CADENCE_MINUTES. Asked for by name, every requested race is captured
        # -- the ladder is what makes an unattended one-minute cron affordable,
        # not a rule about what a person may look at.
        if explicit or races:
            due_wp = due_pairs = set(targets)
        else:
            due_wp, due_pairs = _schedule(conn, date, targets, now=now)
            targets = sorted(due_wp | due_pairs)

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
            # Two different quiet outcomes, and they must not share a sentence.
            # Every race settled means the meeting is over; nothing due means
            # the ladder is holding off, which is what most of the ~1,400 ticks
            # a day are doing and is not a state anyone should read as a fault.
            if not live_races:
                report.notes.append(
                    f"all {stored} races on {date} are more than "
                    f"{SETTLED_AFTER_MINUTES} minutes past their off time")
            else:
                report.notes.append(
                    f"{len(live_races)} of {stored} races on {date} still to "
                    f"run; none due a capture yet at this distance from the off")
            return report
        if not venue:
            # Every race stored for the date but no venue on any of them. The
            # endpoint needs one and guessing between HV and ST would ask
            # about the wrong track.
            report.notes.append(f"no venue recorded for {date}")
            if explicit:
                raise ValueError(
                    f"no venue stored for {date}; pass --venue, or re-scrape the card")
            return report

        try:
            snaps = odds_ingest.fetch_meeting(date, venue, targets,
                                              session=session)
        except odds_ingest.OddsError as exc:
            # Reaching HKJC and being told something different from what was
            # asked for is not a crash, it is an answer -- and the next run is
            # fifteen minutes away. Recorded as a failed run so the freshness
            # strip goes amber and says why, rather than raising a traceback
            # into the cron log every quarter of an hour.
            report.skipped.append(str(exc))
            with transaction(conn):
                job_log.record_source(conn, "scrape_odds", ok=False,
                                      detail=str(exc)[:300])
            return report

        offs = {r["race_no"]: (r["off_time"] or "").strip()
                for r in conn.execute(
                    "SELECT race_no, off_time FROM races WHERE race_date = ?",
                    (date,))}
        already_priced = set(_last_captures(conn, date)[0])

        for snap in snaps:
            race_no = snap["race_no"]
            report.notes.extend(f"R{race_no}: {n}" for n in snap.get("notes", []))

            parsed = odds_ingest.parse_snapshot(snap)
            win_place = odds_ingest.snapshot_rows(parsed)
            pairs = odds_ingest.pair_rows(parsed)

            # Whether this race is over is decided BEFORE the empty-capture
            # check below, because a shut pool is one of the ways a capture
            # comes back empty — and that is the case this whole mechanism
            # exists for. Deciding it afterwards would leave a finished race
            # being asked about once a minute until the clock backstop.
            shut = _shut(parsed, _minutes_to_off(date, offs.get(race_no, ""), now),
                         priced=bool(win_place) or race_no in already_priced)
            if shut is not None:
                with transaction(conn):
                    upsert.upsert_market_close(conn, [{
                        "race_date": date, "race_no": race_no,
                        "closed_at": now.isoformat(timespec="seconds"),
                        "status": shut}])
                report.closed.append(race_no)

            if not win_place:
                # A declared card whose market has not opened is the normal
                # state before 13:00 the day before racing, and its own note
                # already says so. A race whose pool has just shut is the same
                # thing at the other end. Only an unexplained empty race is a
                # skip.
                if shut is None and not any(
                        "market not open" in n for n in snap.get("notes", [])):
                    report.skipped.append(f"R{race_no}: no priced runners")
                continue

            # One request brought back the whole card; the ladder decides what
            # is worth KEEPING. A race due a win price but not a pair price
            # writes only the first, which is what holds a season inside the
            # volume while still recording the last ten minutes minute by
            # minute.
            with transaction(conn):
                if race_no in due_wp:
                    report.win_place += upsert.upsert_odds_snapshots(
                        conn, win_place)
                if race_no in due_pairs:
                    report.pairs += upsert.upsert_odds_pairs(conn, pairs)
            report.races += 1

        # Recorded so the freshness strip can say when odds last landed, and
        # so a run that stored nothing is visible as such rather than as
        # silence indistinguishable from a run that never happened.
        with transaction(conn):
            job_log.record_source(
                conn, "scrape_odds",
                ok=bool(report.races or report.closed), detail=report.line())
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
    ap.add_argument("--quiet", action="store_true",
                    help="say nothing on a tick that had nothing to do. For "
                         "the every-minute cron line: without it the log is "
                         "~1,400 lines a day of 'nothing to price' and the "
                         "runs that DID capture something are buried in them.")
    args = ap.parse_args(argv)

    report = run(args.date, args.venue, races=args.races, db=args.db)
    # Quiet covers exactly one case: a tick that attempted nothing and has
    # nothing to complain about. A zero WITH something attempted still speaks,
    # and so does every skip -- silent success and silent failure must never
    # look the same, and this does not make them.
    idle = args.quiet and not report.attempted and not report.skipped
    if not idle:
        print(report.line())
        for note in report.notes:
            print(f"  note: {note}")
        for skip in report.skipped:
            print(f"  SKIPPED {skip}")
    # Nothing to price is not a failure -- most days have no meeting. Having
    # something to price and storing none of it is, UNLESS what the run
    # learned was that the races are over: recording a close is an outcome,
    # not a miss.
    if report.attempted and not report.races and not report.closed:
        return 1
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())

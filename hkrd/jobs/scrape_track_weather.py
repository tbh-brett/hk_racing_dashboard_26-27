"""Record HKJC's own track weather through every meeting, and its going readings.

    python -m hkrd.jobs.scrape_track_weather              # a tick: captures inside a meeting
    python -m hkrd.jobs.scrape_track_weather --force      # capture now, meeting or not
    python -m hkrd.jobs.scrape_track_weather --going      # the going readings outstanding
    python -m hkrd.jobs.scrape_track_weather --going --date 2026-11-22

Asked on 23 Nov 2026 whether the wind at the races matters. The Observatory's
archive answered for Sha Tin -- a tailwind up the straight makes the closing
sections faster, 0.18 s per 10 km/h, and does not change how often leaders win
-- and could not answer for Happy Valley at all, because none of its stations
tracks the bowl. HKJC measures the wind on both courses and keeps none of it,
so this records it from now on. A season of meetings is what the question
needs: Happy Valley tested for the first time, Sha Tin on the track's own
instruments.

THE WIND IS LIVE ONLY (`ingest/track_weather`). So `ops/crontab` runs this
every five minutes and the job decides, the way `scrape_odds` decides: a tick
captures when today has a stored card and the clock is between an hour before
the first race and half an hour after the last, the margin a late start needs.
Every other tick is one local query and an exit, and with --quiet says
nothing. A card with no readable off time is captured on every tick rather
than missed.

THE GOING READINGS ARE KEPT ABOUT TWO MONTHS (`ingest/going`), per meeting.
`--going` asks about every stored meeting from that span with no reading on
record -- which on a first run is the backfill -- and about today's and
yesterday's whatever is stored, because a pre-race reading lands after a
morning run has stored the first. One request per meeting date.

Row counts, never silence: every run that asks HKJC anything says what it
stored, per course or per meeting, and records it in `job_runs`. An idle tick
writes nothing at all.
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hkrd.ingest import going as going_ingest
from hkrd.ingest import track_weather as weather_ingest
from hkrd.jobs import clock
from hkrd.store import job_log
from hkrd.store import track_weather as weather_store
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["capture", "going", "window", "WeatherRun", "GoingRun", "main",
           "JOB", "GOING_JOB", "BEFORE_FIRST_MINUTES", "AFTER_LAST_MINUTES",
           "STALE_MINUTES", "GOING_LOOKBACK_DAYS", "GOING_RECHECK_DAYS"]

# What `job_runs` files each half under, one source per job the way
# `scrape_meeting:card` is.
JOB = "scrape_track_weather"
GOING_JOB = "scrape_track_weather:going"

# The capture runs from this long before the first race's off to this long
# after the last race's. The tail is for a late start: a race goes off when
# HKJC sends it, not when the card says.
BEFORE_FIRST_MINUTES = 60
AFTER_LAST_MINUTES = 30

# An instrument whose own clock is further behind than this when it is read is
# named in the report. A sensor that has stopped reporting otherwise looks, in
# a row count, exactly like one that has not.
STALE_MINUTES = 15

# How far back `--going` looks for a meeting with no reading on record. HKJC
# served sixty-one days on 23 Nov 2026; a meeting past what it still serves
# costs one request and comes back as nothing, which the report says.
GOING_LOOKBACK_DAYS = 62
# Meetings this many days back, or nearer, are asked about on every run.
GOING_RECHECK_DAYS = 1


# ── the live weather ─────────────────────────────────────────────────────────

def _off(date: str, off_time: str | None) -> dt.datetime | None:
    """A race's scheduled off on its date, or None where it cannot be read."""
    try:
        hh, mm = str(off_time or "").split(":")[:2]
        return dt.datetime.fromisoformat(date).replace(hour=int(hh),
                                                       minute=int(mm))
    except (ValueError, IndexError):
        return None


def window(date: str, offs: Sequence[str | None]
           ) -> tuple[dt.datetime, dt.datetime] | None:
    """When a meeting's weather is worth recording: an hour before its first
    off to half an hour after its last. None when no off time can be read."""
    times = [t for t in (_off(date, o) for o in offs) if t is not None]
    if not times:
        return None
    return (min(times) - dt.timedelta(minutes=BEFORE_FIRST_MINUTES),
            max(times) + dt.timedelta(minutes=AFTER_LAST_MINUTES))


@dataclass
class WeatherRun:
    """What one tick did. Printed, recorded, and returned to the caller."""
    at: str                                  # when it ran, HK, to the second
    venue: str = ""                          # today's meeting, from the card
    attempted: bool = False                  # whether HKJC was asked
    rows: int = 0
    by_venue: dict[str, int] = field(default_factory=dict)
    idle: str = ""                           # why HKJC was not asked
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Asked, and stored something. A capture of nothing is a failure:
        every reply carries both courses, race day or not."""
        return self.attempted and not self.skipped and self.rows > 0

    def line(self) -> str:
        stamp = self.at[:16].replace("T", " ")
        if not self.attempted:
            return f"{stamp} track weather: {self.idle}"
        head = f"{stamp} track weather"
        if self.venue:
            head += f" ({self.venue} meeting)"
        head += f": {self.rows} readings"
        if self.by_venue:
            head += " · " + " · ".join(
                f"{v} {n}" for v, n in sorted(self.by_venue.items()))
        if self.skipped:
            head += f" · SKIPPED — {self.skipped[0]}"
        elif not self.rows:
            # The shape a silent failure and a quiet sensor share; whatever
            # the run knows about why goes on the line itself.
            head += " — " + (self.notes or ["no reading in the reply"])[0]
        return head


def _stale(rows: list[dict[str, Any]], now: dt.datetime) -> list[str]:
    """Instruments whose last reading is older than STALE_MINUTES."""
    out = []
    for r in rows:
        age = (now - dt.datetime.fromisoformat(r["observed_at"])
               ).total_seconds() / 60.0
        if age > STALE_MINUTES:
            out.append(f"{r['venue']}/{r['location']} last read at "
                       f"{r['observed_at'][:16].replace('T', ' ')}, "
                       f"{age:.0f} min before this capture")
    return out


def _detail(line: str, extra: list[str]) -> str:
    """What `job_runs` keeps: the line, then as much of the rest as fits."""
    return "; ".join([line, *extra])[:2000]


def capture(db: str | Path | None = None, *, force: bool = False,
            now: dt.datetime | None = None, session=None) -> WeatherRun:
    """One tick: both courses' instruments, when today's meeting is on.

    `force` captures whatever the card says, for a reading on demand. `now`
    is HK wall-clock time and defaults to `clock._hk_now`, so a test can stop
    it.
    """
    now = now or clock._hk_now()
    date = now.date().isoformat()
    report = WeatherRun(at=now.isoformat(timespec="seconds"))
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        venue, offs = weather_store.card_times(conn, date)
        report.venue = venue or ""
        if not force:
            if not offs:
                report.idle = f"no meeting stored for {date}"
                return report
            span = window(date, offs)
            if span is None:
                report.notes.append(
                    f"no off time readable on the {date} card: captured on "
                    f"every tick rather than risk missing the meeting")
            elif not span[0] <= now <= span[1]:
                report.idle = (f"outside the {date} {report.venue} meeting "
                               f"({span[0]:%H:%M} to {span[1]:%H:%M})")
                return report

        report.attempted = True
        try:
            rows, notes = weather_ingest.fetch_weather(captured_at=report.at,
                                                       session=session)
        except weather_ingest.TrackWeatherError as exc:
            # An answer, not a crash: recorded as a failed run so it is seen,
            # and the next tick is five minutes away.
            report.skipped.append(str(exc))
        else:
            report.notes.extend(notes)
            report.notes.extend(_stale(rows, now))
            with transaction(conn):
                report.rows = weather_store.upsert_track_weather(conn, rows)
            for r in rows:
                report.by_venue[r["venue"]] = report.by_venue.get(r["venue"], 0) + 1
        with transaction(conn):
            job_log.record_source(conn, JOB, ok=report.ok,
                                  detail=_detail(report.line(), report.notes))
        return report
    finally:
        conn.close()


# ── the going readings ───────────────────────────────────────────────────────

@dataclass
class GoingRun:
    """What one `--going` run asked for and stored, per meeting date."""
    asked: list[str] = field(default_factory=list)
    rows: int = 0
    by_date: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Every date asked was answered. A date answered with no reading is
        a note, not a failure: today's meeting before its morning reading is
        published, or a meeting older than HKJC still serves."""
        return not self.skipped

    def line(self) -> str:
        if not self.asked:
            return "going readings: nothing outstanding"
        head = (f"going readings: {self.rows} from {len(self.asked)} meeting "
                f"date{'s' if len(self.asked) != 1 else ''} · "
                + " · ".join(f"{d} {self.by_date[d]}" if d in self.by_date
                             else f"{d} —" for d in self.asked))
        if self.skipped:
            head += f" · {len(self.skipped)} SKIPPED"
        return head


def going(db: str | Path | None = None, *, dates: Sequence[str] | None = None,
          today: dt.date | None = None, session=None) -> GoingRun:
    """The penetrometer and hammer readings for `dates`, or whatever is
    outstanding: see the module docstring."""
    report = GoingRun()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        if dates is None:
            day = today or clock._hk_now().date()
            dates = weather_store.going_pending(
                conn,
                since=(day - dt.timedelta(days=GOING_LOOKBACK_DAYS)).isoformat(),
                recheck_from=(day - dt.timedelta(days=GOING_RECHECK_DAYS)
                              ).isoformat(),
                until=day.isoformat())
        report.asked = [str(d) for d in dates]
        for date in report.asked:
            try:
                rows, notes = going_ingest.fetch_going(date, session=session)
            except weather_ingest.TrackWeatherError as exc:
                report.skipped.append(f"{date}: {exc}")
                continue
            report.notes.extend(notes)
            with transaction(conn):
                report.by_date[date] = weather_store.upsert_going_readings(
                    conn, rows)
            report.rows += report.by_date[date]
        if report.asked:
            with transaction(conn):
                job_log.record_source(
                    conn, GOING_JOB, ok=report.ok,
                    detail=_detail(report.line(), report.skipped + report.notes))
        return report
    finally:
        conn.close()


# ── the command line ─────────────────────────────────────────────────────────

def _date(text: str) -> str:
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a date: {text!r} (YYYY-MM-DD)") from None


def _say(line: str, notes: list[str], skipped: list[str]) -> None:
    print(line)
    for note in notes:
        print(f"  note: {note}")
    for skip in skipped:
        print(f"  SKIPPED {skip}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None,
                    help="database path; defaults to HKRD_DB")
    ap.add_argument("--going", action="store_true",
                    help="the going readings (penetrometer and Clegg hammer) "
                         "instead of the live weather")
    ap.add_argument("--date", type=_date, action="append", dest="dates",
                    help="with --going: one meeting date; repeat for several. "
                         "Default: whatever is outstanding")
    ap.add_argument("--force", action="store_true",
                    help="capture the live reading now, meeting or not")
    ap.add_argument("--quiet", action="store_true",
                    help="say nothing on a run that asked HKJC nothing. For "
                         "the cron lines: the five-minute one is idle on all "
                         "but a few hours a week")
    args = ap.parse_args(argv)
    if args.dates and not args.going:
        ap.error("--date goes with --going: the live reading takes no date")
    if args.force and args.going:
        ap.error("--force is for the live reading; with --going, name the "
                 "meeting with --date")

    if args.going:
        got = going(args.db, dates=args.dates)
        if got.asked or not args.quiet:
            _say(got.line(), got.notes, got.skipped)
        return 0 if got.ok else 1

    report = capture(args.db, force=args.force)
    if report.attempted or not args.quiet:
        _say(report.line(), report.notes, report.skipped)
    # Idle is not a failure: most ticks of most days have no meeting on.
    return 0 if report.ok or not report.attempted else 1


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())

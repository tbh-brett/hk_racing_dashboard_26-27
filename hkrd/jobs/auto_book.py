"""The automatic blackbook: one pass per meeting, once its results are complete.

    python -m hkrd.jobs.auto_book --pending                  # what the cron runs
    python -m hkrd.jobs.auto_book --pending --since 2026-09-06   # a whole season
    python -m hkrd.jobs.auto_book --date 2026-09-27 --dry-run    # print, write nothing

Reads a meeting once HKJC's comments on running are in (or at the backstop,
`query/auto_book.BACKSTOP_DAYS`), books its top `RESULTS_CAP` runs by the
owner's own reasons, and writes them to the blackbook as SYSTEM entries — the
page shows them in their own colour, with the reason written out and the day
they were added. A trial day is read the same way once HKJC has finished
publishing it, and its STANDOUT trials are booked.

What it does not do is book a horse the book already follows — it writes the
reason on that run as a note instead, "System: …", and never over a note that
is already there — or read a day twice: `auto_book_pass` records every day
read, so a horse the owner dismissed is not written back the next night.

A system entry is tested over its next `TESTED_RUNS` starts and then closed —
RETIRED, with the date and its record in the reason — unless the owner adopted
it. That rule was the owner's choice (docs/auto-book.md) and it
touches system entries only: nothing here ever closes an entry the owner
booked or adopted.

Its own cron line rather than a step in `nightly`, for the reason the trials
and the speed map have theirs: a failure here must not take the race-day
scrape down with it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.derive import book_candidates as bc
from hkrd.jobs import write_notes
from hkrd.query import auto_book as auto_q
from hkrd.store import auto_book as auto_store, job_log
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run", "AutoBookReport", "FIRST_DAY"]

# The first day the pass reads unless told otherwise: the opening of the
# 2026-27 season (`query/period.SEASON_START_MONTH`). It began at 27
# September, the meeting the owner went through by hand the day this was
# asked for; the owner then asked for the whole season. A start later than a
# meeting still waiting for its comments would strand that meeting, since
# `--pending` never looks before it.
FIRST_DAY = "2026-09-01"


@dataclass
class AutoBookReport:
    """What each pass did, in counts. A pass that booked nothing says so."""

    passes: list[str] = field(default_factory=list)
    picks: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"  {p}" for p in self.passes] or ["  auto book          nothing ready"]
        lines += [f"      {p}" for p in self.picks]
        lines += [f"  closed             {c}" for c in self.closed]
        lines += [f"  waiting            {w}" for w in self.waiting]
        lines += [f"  ERROR              {e}" for e in self.errors]
        return "\n".join(lines)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _added(source_date: str, today: str) -> str:
    """The day after the meeting or trial, the first day the entry could have
    been written — never later than today. Nothing the entry is written from
    comes after that day, so a run between it and the pass is a fair test."""
    after = (dt.date.fromisoformat(source_date) + dt.timedelta(days=1)).isoformat()
    return min(after, today)


def _note_in_book(report: AutoBookReport, written: list[bool]) -> None:
    """A picked horse the book already follows gets no second entry; the
    reason it was picked goes on the run as a note instead, unless the run
    already has one. Said either way."""
    if written:
        report.passes[-1] += (f"; noted on {sum(written)} of their runs"
                              + (f", {len(written) - sum(written)} already had "
                                 "a note" if not all(written) else ""))


def _results(conn, report: AutoBookReport, date: str, basis: str, *,
             today: str, dry_run: bool) -> None:
    by_race = auto_q.meeting_runners(conn, date)
    considered = sum(len(v) for v in by_race.values())
    flagged = [c for runners in by_race.values()
               for c in bc.race_candidates(runners)]
    picked = bc.pick(flagged, bc.RESULTS_CAP)
    in_book = auto_q.followed(conn, [c.horse_name for c in picked])
    new = [c for c in picked if c.horse_name not in in_book]
    report.passes.append(
        f"results {date}   {considered:>4} runners, {len(flagged)} flagged, "
        f"{len(picked)} picked: {len(new)} added"
        + (f", {len(in_book)} already in the book ({', '.join(sorted(in_book))})"
           if in_book else "")
        + ("" if basis == "full" else "  [no comments on running]")
        + ("  DRY RUN" if dry_run else ""))
    report.picks += [f"R{c.source_no:<2} {c.horse_name:<20} "
                     f"{'/'.join(c.tags):<34} {c.reasoning(basis=basis)}"
                     for c in picked]
    if dry_run:
        return
    with transaction(conn):
        for c in new:
            write_notes.insert_entry(
                conn, c.horse_name, reasoning=c.reasoning(basis=basis),
                added_date=_added(date, today), source_date=date,
                source_race_no=c.source_no, tags=c.tags,
                confidence=c.confidence, origin="system")
        _note_in_book(report, [
            write_notes.add_system_note(conn, c.horse_name, date, c.source_no,
                                        c.reasoning(basis=basis))
            for c in picked if c.horse_name in in_book])
        auto_store.record_pass(
            conn, kind="results", source_date=date, ran_at=_now(), basis=basis,
            considered=considered, flagged=len(flagged), picked=len(picked),
            added=len(new), in_book=sorted(in_book))


def _trials(conn, report: AutoBookReport, date: str, *, today: str,
            dry_run: bool) -> None:
    rated = auto_q.trial_day(conn, date)
    flagged = bc.trial_candidates(rated)
    picked = bc.pick(flagged, bc.TRIALS_CAP)
    in_book = auto_q.followed(conn, [c.horse_name for c in picked])
    new = [c for c in picked if c.horse_name not in in_book]
    report.passes.append(
        f"trials  {date}   {len(rated):>4} runners, {len(flagged)} STANDOUT, "
        f"{len(new)} added"
        + (f", {len(in_book)} already in the book ({', '.join(sorted(in_book))})"
           if in_book else "")
        + ("  DRY RUN" if dry_run else ""))
    report.picks += [f"T{c.source_no:<2} {c.horse_name:<20} {c.reasoning()}"
                     for c in picked]
    if dry_run:
        return
    with transaction(conn):
        for c in new:
            write_notes.insert_entry(
                conn, c.horse_name, reasoning=c.reasoning(),
                added_date=_added(date, today), source_date=date,
                source_trial_no=c.source_no, tags=c.tags,
                confidence=c.confidence, origin="system")
        _note_in_book(report, [
            write_notes.add_system_note(conn, c.horse_name, date, c.source_no,
                                        c.reasoning(), trial=True)
            for c in picked if c.horse_name in in_book])
        auto_store.record_pass(
            conn, kind="trials", source_date=date, ran_at=_now(), basis="full",
            considered=len(rated), flagged=len(flagged), picked=len(picked),
            added=len(new), in_book=sorted(in_book))


def _close_tested(conn, report: AutoBookReport) -> None:
    rows = auto_q.tested_entries(conn)
    if not rows:
        return
    with transaction(conn):
        for r in rows:
            closed = (dt.date.fromisoformat(r["last_test"])
                      + dt.timedelta(days=1)).isoformat()
            wins, top3 = r["wins"], r["top3"]
            reason = (f"System entry, tested over its {auto_q.TESTED_RUNS} "
                      f"starts since booking: {wins} win{'s' * (wins != 1)}, "
                      f"{top3} in the first three. Not adopted.")
            write_notes.close_tested(conn, r["id"], closed_date=closed,
                                     reason=reason)
            report.closed.append(f"{r['id']}  {wins}W {top3} top-3 in "
                                 f"{auto_q.TESTED_RUNS}")


def _asked(conn, report: AutoBookReport, dates: list[str], *, dry_run: bool
           ) -> tuple[list[tuple[str, str]], list[str]]:
    """Exactly the days named, read now whatever the clock says — the way to
    look at one meeting by hand. A day already read is not read again unless
    nothing is being written."""
    meetings: list[tuple[str, str]] = []
    trial_days: list[str] = []
    for d in dates:
        got = auto_q.meetings(conn, since=d, until=d)
        for m in got:
            if m["read_at"] and not dry_run:
                report.waiting.append(f"{d}: meeting already read at {m['read_at']}")
            elif m["settled"] < m["races"]:
                report.waiting.append(f"{d}: {m['races'] - m['settled']} races "
                                      "without a result")
            else:
                meetings.append((d, "full" if m["commented"] else "stewards"))
        read = auto_q.trial_read_at(conn, d)
        if auto_q.trial_day(conn, d):
            if read and not dry_run:
                report.waiting.append(f"trials {d}: already read at {read}")
            else:
                trial_days.append(d)
        elif not got:
            report.waiting.append(f"{d}: no meeting and no trials on this day")
    return meetings, trial_days


def run(db: Path | None = None, *, since: str = FIRST_DAY,
        today: str | None = None, dates: list[str] | None = None,
        dry_run: bool = False) -> AutoBookReport:
    """Read every day that is ready, or exactly `dates`, then close the
    system entries whose test is over."""
    report = AutoBookReport()
    today = today or dt.date.today().isoformat()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        if dates:
            meetings, trial_days = _asked(conn, report, dates, dry_run=dry_run)
        else:
            meetings, waiting = auto_q.ready_meetings(conn, since=since,
                                                      today=today)
            trial_days, t_waiting = auto_q.ready_trial_days(conn, since=since,
                                                            today=today)
            report.waiting += waiting + t_waiting

        for date, basis in meetings:
            try:
                _results(conn, report, date, basis, today=today, dry_run=dry_run)
            except Exception as exc:          # noqa: BLE001 - reported, see below
                # One meeting failing must not stop the next being read. It is
                # not swallowed: it is in the report, the job exits 1, and the
                # day is not marked as read, so it is tried again.
                report.errors.append(f"results {date}: {type(exc).__name__}: {exc}")
        for date in trial_days:
            try:
                _trials(conn, report, date, today=today, dry_run=dry_run)
            except Exception as exc:          # noqa: BLE001 - reported, as above
                report.errors.append(f"trials {date}: {type(exc).__name__}: {exc}")
        if not dry_run:
            _close_tested(conn, report)
            with transaction(conn):
                job_log.record_source(
                    conn, "auto_book", ok=report.ok,
                    detail=("; ".join(report.passes + report.errors)
                            or "nothing ready")
                           + (f"; closed {len(report.closed)}"
                              if report.closed else ""))
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--pending", action="store_true",
                       help="every meeting and trial day that is ready")
    which.add_argument("--date", action="append",
                       help="YYYY-MM-DD; repeat for several")
    ap.add_argument("--since", default=FIRST_DAY,
                    help=f"with --pending, the first day to read (default {FIRST_DAY})")
    ap.add_argument("--today", default=None,
                    help="read the database as if it were this day")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be booked and write nothing")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    report = run(a.db, since=a.since, today=a.today, dates=a.date,
                 dry_run=a.dry_run)
    # A reason quotes HKJC and writes margins as "1¾L". A Windows console on a
    # Chinese code page cannot print either, and the job must not fail on the
    # way out after it has already written everything.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

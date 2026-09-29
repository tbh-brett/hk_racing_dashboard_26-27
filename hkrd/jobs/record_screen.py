"""Keep what the Screen said about each race once its result is in.

    python -m hkrd.jobs.record_screen --pending
    python -m hkrd.jobs.record_screen --date 2026-09-27

The sources' record (`query/tips_record`) scores the Screen's first choice
and its four beside every tipster. It reads the order recorded here, never a
fresh one, because the weights move when the Screen is refitted and a refit
would rescore every race it has already been judged on.

ONLY RACES THE MODEL NEVER SAW. Since gbm-SPEC §6 the Screen's chances are
the fundamental model's, scored before the off (`runner_gbm`), and each
meeting's scores name the model that made them. A meeting on or before the
date that model was trained through would be the Screen marking its own
homework: it is skipped, and the report says so. A meeting the model never
scored has nothing to record.

AS AT THE RACE. The inputs are read strictly before the meeting's date
(`query/screen_inputs`), so recording after the result changes nothing the
Screen saw — except that a horse scratched late is no longer in the field,
which is the field that actually ran. `--pending` is what the nightly job
calls: every settled meeting after the fit with a race not yet recorded.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.query import screen
from hkrd.store import job_log, screen_picks
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["record", "pending", "RecordReport"]


@dataclass
class RecordReport:
    """Row counts per meeting. A zero is said, never left blank."""

    recorded: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  screen recorded    {d}  {n:>4} runners"
                 for d, n in self.recorded.items()]
        lines += [f"  skipped            {s}" for s in self.skipped]
        return "\n".join(lines) or "  screen recorded    nothing outstanding"


def pending(*, db: Path | None = None) -> list[str]:
    """Settled meetings the model scored that have a race not yet recorded."""
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        first = conn.execute("SELECT min(race_date) FROM runner_gbm").fetchone()[0]
        if first is None:
            return []
        before = (dt.date.fromisoformat(first) - dt.timedelta(days=1)).isoformat()
        return screen_picks.unrecorded_dates(conn, before)
    finally:
        conn.close()


def record(dates: list[str], *, db: Path | None = None) -> RecordReport:
    report = RecordReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for date in dates:
            m = screen.meeting(date, conn=conn)
            fitted, version = m.get("fit", {}).get("fitted"), m.get("version")
            if version is None:
                report.skipped.append(f"{date}: the model did not score this meeting")
                continue
            if date <= fitted:
                report.skipped.append(
                    f"{date}: inside the model's fit (trained through "
                    f"{fitted}), so not a fair test of it")
                continue
            rows = [{"race_date": date, "race_no": race["race_no"],
                     "horse_no": x["horse_no"], "rank": x["rank"],
                     "win_pct": x["win_pct"], "place_pct": x["place_pct"],
                     "tier": x["tier"], "version": version,
                     "fitted": fitted, "recorded_at": now}
                    for race in m["races"]
                    if race["run"] for x in race["runners"] if x["rank"] is not None]
            with transaction(conn):
                report.recorded[date] = screen_picks.record(conn, rows)
                job_log.record_source(conn, "record_screen", ok=True,
                                      detail=f"{date} · "
                                             f"{report.recorded[date]} runners")
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--date", help="YYYY-MM-DD")
    which.add_argument("--pending", action="store_true",
                       help="every settled meeting after the fit not yet recorded")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    dates = pending(db=a.db) if a.pending else [a.date]
    print(record(dates, db=a.db).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

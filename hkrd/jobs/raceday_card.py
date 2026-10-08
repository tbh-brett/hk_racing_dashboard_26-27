"""Race day: re-read the card for every race still to run, and re-score it.

    python -m hkrd.jobs.raceday_card [--db PATH] [--date YYYY-MM-DD]

WHY. On race day the card was read at 07:00 and 13:00 (`nightly`) and not again
until 19:00, so a change made after 13:00 never reached the model. On
2026-10-04 race 8 FOREVER FOLKS went out under A Atzeni: the page showed 6.7%,
worked out for the rider the card named at 13:01, where for Atzeni the model
reads 7.6%. A horse withdrawn on the day stayed in the field the same way.

WHAT. One query first: has today a race still to run -- its pool not shut, its
result not in (`store/gbm.unrun_races`)? Most runs it has not, and the job says
so and stops without touching HKJC. Otherwise each such race's card is fetched
(`ingest/racecard.fetch_race`, one page a race) and stored through the card
scrape's own write (`scrape_meeting.store_card`: a new rider, a weight, a
withdrawal land on the same rows), and `score_gbm` re-scores the card -- a card
that has not changed costs it one query and builds nothing. What changed is
named in the report: rider, weight, withdrawn, added.

WHEN. `ops/crontab`, every 20 minutes from 10:00 to 22:40: ahead of a Sha Tin
afternoon and through a Happy Valley evening. A race that has gone off is
never fetched, and never re-scored (`store/gbm._UNRUN`).
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import racecard
from hkrd.ingest._client import FetchError
from hkrd.jobs import score_gbm, scrape_meeting
from hkrd.store import gbm as store
from hkrd.store import job_log
from hkrd.store import tips as tips_store
from hkrd.store.connect import get_conn, init_db

__all__ = ["run", "main", "RacedayReport"]


@dataclass
class RacedayReport:
    date: str = ""
    venue: str = ""
    races: list[int] = field(default_factory=list)       # re-read this run
    runners: int = 0
    changes: list[str] = field(default_factory=list)
    scored: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.races and not self.errors:
            return f"  raceday_card {self.date}: no race still to run"
        lines = [f"  raceday_card {self.date} {self.venue}: {len(self.races)} races still to run "
                 f"re-read ({', '.join(f'R{r}' for r in self.races)}), {self.runners} runners"]
        lines += [f"  changed    {c}" for c in self.changes] or ["  changed    nothing"]
        lines += [f"  model      {s}" for s in self.scored]
        lines += [f"  ERROR      {e}" for e in self.errors]
        return "\n".join(lines)


def _field(db: Path | None, date: str) -> dict[tuple[int, int], dict]:
    conn = get_conn(db)
    try:
        return {(f["race_no"], f["horse_no"]): f for f in store.card_facts(conn, date)}
    finally:
        conn.close()


def _changes(before: dict, after: dict, races: list[int]) -> list[str]:
    """What the re-read changed in the races it re-read."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key[0] not in races:
            continue
        was, now = before.get(key), after.get(key)
        who = f"R{key[0]} #{key[1]} {(now or was)['horse_name']}"
        if now is None:
            out.append(f"{who}: withdrawn")
        elif was is None:
            out.append(f"{who}: added to the field")
        else:
            for col, label in (("jockey", "rider"), ("actual_weight", "weight")):
                if was[col] != now[col]:
                    out.append(f"{who}: {label} {was[col]} -> {now[col]}")
    return out


def run(db: Path | None = None, *, date: str | None = None, session=None) -> RacedayReport:
    report = RacedayReport(date=date or dt.date.today().isoformat())
    conn = get_conn(db)
    try:
        init_db(conn)
        races = store.unrun_races(conn, report.date)
        report.venue = tips_store.meeting_venue(conn, report.date) or ""
    finally:
        conn.close()
    if not races or not report.venue:
        return report
    before = _field(db, report.date)
    card: dict = {"races": [], "errors": []}
    for no in races:
        try:
            card["races"].append(racecard.fetch_race(report.date, report.venue, no,
                                                     session=session))
        except (FetchError, racecard.RacecardError) as exc:
            report.errors.append(f"R{no}: {exc}")
    report.races = [r["race"]["race_no"] for r in card["races"]]
    report.runners = scrape_meeting.store_card(db, card)
    report.changes = _changes(before, _field(db, report.date), report.races)
    scored = score_gbm.score(report.date, db)
    report.scored = scored.scored + [f"{d}: unchanged" for d in scored.unchanged] + scored.notes
    report.errors += scored.errors
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    conn = get_conn(a.db)
    try:
        init_db(conn)
        due = bool(store.unrun_races(conn, a.date or dt.date.today().isoformat()))
    finally:
        conn.close()
    if not due:
        # Most runs, and not an error: the job log is for runs that did something.
        print(RacedayReport(date=a.date or dt.date.today().isoformat()).render())
        return 0
    with job_log.running("raceday_card", a.db) as outcome:
        report = run(a.db, date=a.date)
        outcome["ok"] = not report.errors
        outcome["detail"] = "; ".join(
            [f"{report.date} {report.venue}: {len(report.races)} races re-read"]
            + report.changes + report.scored + report.errors)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

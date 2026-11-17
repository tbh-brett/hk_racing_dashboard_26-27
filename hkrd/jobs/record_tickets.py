"""Keep the desk's paper tickets for each race once its pool has shut.

    python -m hkrd.jobs.record_tickets --pending
    python -m hkrd.jobs.record_tickets --date 2026-11-18
    python -m hkrd.jobs.record_tickets --pending --since 2026-09-06

The record (`query/ticket_record`) scores every banker the owner had evidence
on, taken or not, against the tickets he did take. It reads what is kept here
and never a fresh build, because the engine's rules will move and a rebuild
would rescore every race under rules it was not judged by (`record_screen`
keeps the Screen's order for the same reason). A banker on record is never
written again.

NOT TIME-CRITICAL, BY CONSTRUCTION. A paper ticket is read from the last capture
at least `suggest.LEAD_MINUTES` before the pool shut, and captures are never
deleted, so it comes out the same whether this runs a minute after the off or a
week. It reads prices only from before the close and nothing of the result.

`--pending` looks back a fortnight and never before the day the engine's rules
were written (`suggest.ENGINE_FROM`); a race with no candidate banker is asked
about again within that window and answers nothing. `--since` reaches further
by hand: those earlier races are tickets the engine was written after looking
at, and the record keeps them apart.

THE FACTS AS AT EACH RACE are kept here too, for every runner of every race
with a result (`query/banker_facts`, into `runner_fact`): the banker scorecard
reads them instead of working them out on each request. They wait for the
result because the market fact is the starting price. `--pending` looks back
a fortnight; `--facts-since` reaches back by hand, once, to the start of the
book:

    python -m hkrd.jobs.record_tickets --pending --facts-since 2026-04-01
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hkrd.query import banker_facts, suggest
from hkrd.store import job_log, tickets
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["record", "pending", "pending_facts", "record_facts", "RecordReport",
           "LOOK_BACK_DAYS"]

LOOK_BACK_DAYS = 14


@dataclass
class RecordReport:
    """Row counts per meeting. A zero is said, never left blank."""

    recorded: dict[str, int] = field(default_factory=dict)   # date -> tickets added
    races: dict[str, int] = field(default_factory=dict)      # date -> races read
    facts: dict[str, int] = field(default_factory=dict)      # date -> runners' facts kept
    fact_races: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"  tickets recorded   {d}  {n:>3} bankers over {self.races[d]} races"
                 for d, n in self.recorded.items()]
        lines += [f"  facts recorded     {d}  {n:>3} runners over {self.fact_races[d]} races"
                  for d, n in self.facts.items()]
        return "\n".join(lines) or "  tickets recorded   nothing outstanding"

    def wrote(self) -> bool:
        return any(self.recorded.values()) or any(self.facts.values())


def pending(*, db: Path | None = None, since: str | None = None,
            today: dt.date | None = None) -> list[tuple[str, int]]:
    """Races that have gone off and have no paper ticket on record."""
    if since is None:
        back = ((today or dt.date.today()) - dt.timedelta(days=LOOK_BACK_DAYS)).isoformat()
        since = max(back, suggest.ENGINE_FROM)
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        return tickets.unrecorded_races(conn, since)
    finally:
        conn.close()


def pending_facts(*, db: Path | None = None, since: str | None = None,
                  today: dt.date | None = None) -> list[tuple[str, int]]:
    """Races with a result and no runner's facts kept under this version."""
    if since is None:
        since = ((today or dt.date.today()) - dt.timedelta(days=LOOK_BACK_DAYS)).isoformat()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        return tickets.unrecorded_fact_races(conn, since, banker_facts.VERSION)
    finally:
        conn.close()


def record_facts(races: list[tuple[str, int]], *, db: Path | None = None,
                 report: RecordReport | None = None) -> RecordReport:
    """Keep every runner's facts as at its race, a meeting at a time."""
    report = report or RecordReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for date in sorted({d for d, _ in races}):
            nos = {n for d, n in races if d == date}
            runs = [dict(r) for r in conn.execute(
                "SELECT race_date, race_no, horse_no, horse_name FROM runners "
                "WHERE race_date = ? AND coalesce(place_code, '') NOT LIKE 'W%'", (date,))
                if r["race_no"] in nos]
            facts = banker_facts.facts_for(conn, runs)
            rows = [{"race_date": d, "race_no": n, "horse_no": h, **f}
                    for (d, n, h), f in facts.items()]
            with transaction(conn, immediate=True):
                added = tickets.record_facts(conn, rows, version=banker_facts.VERSION, at=now)
                if added:
                    job_log.record_source(conn, "record_tickets:facts", ok=True,
                                          detail=f"{date} · {added} runners over {len(nos)} races")
            report.facts[date], report.fact_races[date] = added, len(nos)
    finally:
        conn.close()
    return report


def _row(t: dict[str, Any], now: str) -> dict[str, Any]:
    b, ev = t["banker"], t["evidence"]
    return {
        "race_date": t["race_date"], "race_no": t["race_no"], "banker_no": b["horse_no"],
        "tier": ev["tier"] if ev else None, "own": bool(ev and ev["own"]),
        "partner_source": t["partner_source"], "unit_stake": t["unit_stake"],
        "field_size": t["field_size"], "market_rank": b["market_rank"],
        "win_odds": b["win_odds"], "place_pct": b["place_pct"],
        "place_source": b["place_source"], "model_pct": b["model_pct"],
        "top2_pct": b["top2_pct"], "win_captured_at": t["captured"]["win"],
        "pair_captured_at": t["captured"]["pairs"], "lead_minutes": t["lead_minutes"],
        "engine": t["engine"], "recorded_at": now,
        "partners": [{"horse_no": p["horse_no"], "rank": p["rank"],
                      "win_odds": p["win_odds"], "qin_odds": p["qin_odds"],
                      "qpl_odds": p["qpl_odds"]} for p in t["partners"]]}


def record(races: list[tuple[str, int]], *, db: Path | None = None) -> RecordReport:
    report = RecordReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for date in sorted({d for d, _ in races}):
            added = read = 0
            for _, race_no in sorted(r for r in races if r[0] == date):
                rows = [_row(t, now) for t in suggest.paper(date, race_no, conn=conn)]
                with transaction(conn, immediate=True):
                    added += tickets.record_suggestions(conn, rows)
                read += 1
            report.recorded[date], report.races[date] = added, read
            if added:
                with transaction(conn, immediate=True):
                    job_log.record_source(
                        conn, "record_tickets", ok=True,
                        detail=f"{date} · {added} bankers over {read} races")
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--date", help="YYYY-MM-DD: every race of that meeting gone off")
    which.add_argument("--pending", action="store_true",
                       help="every race gone off in the last fortnight with none recorded")
    ap.add_argument("--since", help="with --pending: look back to this date instead")
    ap.add_argument("--facts-since", help="keep the facts of every race with a result from this date")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true",
                    help="say nothing when there was nothing to record")
    a = ap.parse_args(argv)
    todo = pending(db=a.db, since=a.date or a.since)
    if a.date:
        todo = [r for r in todo if r[0] == a.date]
    report = record(todo, db=a.db)
    facts = pending_facts(db=a.db, since=a.facts_since or a.date)
    if a.date:
        facts = [r for r in facts if r[0] == a.date]
    record_facts(facts, db=a.db, report=report)
    if not (a.quiet and not report.wrote()):
        print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Rebuild `runner_trip` and `race_trip` -- the 2D trip of every run -- from raw.

    python -m hkrd.jobs.rebuild_trip
    python -m hkrd.jobs.rebuild_trip --date 2026-10-19

Derived and droppable: delete-then-insert for the dates it covers, like every step in
`jobs/derive_all` ("trip", after "tags"). Reads the Comments on Running and the stewards' report
(`runner_comments`) and the running positions; see `derive/trip` for what is read. A meeting
whose comments have not been published yet carries HKJC's placeholder on every runner, and every
one of its rows says `has_comment` 0 until they land.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from hkrd.derive import trip as trip_d
from hkrd.store import shape as store
from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["rebuild", "TripReport"]


@dataclass
class TripReport:
    rows_written: int = 0
    races_written: int = 0
    with_comment: int = 0
    contacts: int = 0


def rebuild(db: Path | None = None, *, date: str | None = None) -> TripReport:
    report = TripReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        races = store.trip_races(conn, date)
        runners = store.trip_runners(conn, date)
        texts = store.trip_comments(conn, date)
        runs, events = [], []
        for race in races:
            key = (race["race_date"], race["race_no"])
            field_ = runners.get(key, [])
            if not field_:
                continue
            course = dict(distance=int(race["distance"]), venue=race["venue"], surface=race["surface"])
            for u in field_:
                row = trip_d.run_row(texts.get(key + (u["horse_no"], "corunning")),
                                     texts.get(key + (u["horse_no"], "incident")),
                                     running_positions=u["running_positions"], **course)
                report.with_comment += row["has_comment"]
                runs.append(key + (u["horse_no"],) + tuple(row[c] for c in trip_d.COLUMNS))
            ev = trip_d.race_events(
                [dict(no=u["horse_no"], name=u["horse_name"],
                      inc=texts.get(key + (u["horse_no"], "incident"))) for u in field_], **course)
            report.contacts += len(ev)
            events.append(key + (json.dumps(ev, separators=(",", ":")), trip_d.DERIVE_VERSION))
        store.write_trip(conn, runs, events, date=date)
        report.rows_written, report.races_written = len(runs), len(events)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    r = rebuild(a.db, date=a.date)
    print(f"  runner_trip        {r.rows_written:>7,} rows ({r.with_comment:,} with a comment)")
    print(f"  race_trip          {r.races_written:>7,} rows ({r.contacts:,} contacts placed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

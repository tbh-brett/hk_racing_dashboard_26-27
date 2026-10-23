"""A meeting's results so far, race by race, while it is still being run (Speed Map, TODAY'S RACES).

    python -m hkrd.jobs.fetch_run_races --date 2026-10-25

`scrape_meeting` fetches no results while a race is still to run, for the reason it gives: asked
about a race that has not been run, HKJC serves another meeting's page rather than a 404. So this
asks only about races that have gone off -- their pool shut (market_close) -- and have no result
stored, one page each, and stores a race only when its runners are the field this meeting declared
(`scrape_meeting._is_same_meeting`). A race just off whose result is not up yet says so and is
asked about again next time. The evening's full scrape upserts the same rows.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import results as results_ingest
from hkrd.ingest._client import FetchError, NotFound
from hkrd.jobs.scrape_meeting import _is_same_meeting, _store_race
from hkrd.store import upsert
from hkrd.store.connect import StoreError, db_path, get_conn, init_db, transaction

__all__ = ["fetch", "FetchReport"]


@dataclass
class FetchReport:
    stored: list[int] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        bits = [f"stored R{', R'.join(map(str, self.stored))}" if self.stored else "nothing new to store"]
        bits += self.waiting + self.errors
        return "; ".join(bits)


def fetch(date: str, db: Path | None = None, *, session=None) -> FetchReport:
    report = FetchReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        rows = conn.execute("""
            SELECT r.race_no, r.venue FROM races r
             WHERE r.race_date = ?
               AND EXISTS (SELECT 1 FROM market_close m WHERE m.race_date = r.race_date AND m.race_no = r.race_no)
               AND NOT EXISTS (SELECT 1 FROM runners u WHERE u.race_date = r.race_date AND u.race_no = r.race_no
                                  AND u.place IS NOT NULL)
             ORDER BY r.race_no""", (date,)).fetchall()
        declared: dict[int, set[str]] = {}
        for r in conn.execute("SELECT race_no, horse_name FROM runners WHERE race_date = ?", (date,)):
            declared.setdefault(r[0], set()).add((r[1] or "").strip().upper())
        for race_no, venue in rows:
            try:
                race = results_ingest.fetch_race(date, venue, race_no, session=session)
            except (NotFound, FetchError, results_ingest.ResultsError) as e:
                report.waiting.append(f"R{race_no}: result not up yet ({type(e).__name__})")
                continue
            if not race.get("runners") or not _is_same_meeting(race, declared):
                # HKJC served a page for a race whose result is not published: another meeting's
                report.waiting.append(f"R{race_no}: result not up yet")
                continue
            try:
                with transaction(conn):
                    _store_race(conn, date, race)
                    upsert.upsert_comments(conn, [
                        {"race_date": date, "race_no": race_no, "horse_no": i["horse_no"],
                         "comment_text": i["comment"], "source": "incident"} for i in race.get("incidents") or []])
            except StoreError as e:
                report.errors.append(f"R{race_no}: not stored -- {type(e).__name__}: {e}")
                continue
            report.stored.append(race_no)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    r = fetch(a.date, a.db)
    print(r.render())
    return 1 if r.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

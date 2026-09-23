"""Capture Ladbrokes' fixed odds, and the Racing & Sports tips it carries.

    python -m hkrd.jobs.scrape_fixed_odds --date 2026-09-23
    python -m hkrd.jobs.scrape_fixed_odds             # today's meeting

Ladbrokes answers the Fly machine directly, so unlike the YouTube sources
this runs on the server's own schedule. Sportsbet refuses the server (403,
measured 2026-09-23) and is read from the PC instead, by
`tools/harvest_sportsbet.py`; Unibet's racing prices come from an app whose
feed has not been found.

Two things per race, and both are checked against the stored HKJC card:

  PRICES   stored only for a runner whose Ladbrokes number AND name agree
           with the card. A price under the wrong number would put one
           horse's odds beside another's tote — the one mistake a price
           comparison cannot survive — so a runner that disagrees is left
           out and named in the report.
  TIPS     the top four in order, each with its reason, and the race's
           whole comment. They are Racing & Sports' words, which Sportsbet
           carries too, so they are stored as that source and counted once
           (`ingest.racing_sports`). Sent through `import_tips` like every
           other source — the same checks, the same quarantine, and the
           latest capture replacing the one before.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.derive import names
from hkrd.ingest import ladbrokes, racing_sports
from hkrd.ingest._client import FetchError
from hkrd.jobs import import_tips
from hkrd.store import fixed_odds, job_log, tips
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "FixedOddsReport"]

_HK = dt.timezone(dt.timedelta(hours=8))
EXTRACTOR = "rule:ladbrokes-v2"


@dataclass
class FixedOddsReport:
    date: str = ""
    venue: str = ""
    races: int = 0
    prices: int = 0
    tips: int = 0
    tips_held: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.prices)

    def render(self) -> str:
        lines = [f"  fixed odds         {self.date} {self.venue} (ladbrokes)",
                 f"  races              {self.races:>6}",
                 f"  prices stored      {self.prices:>6}",
                 f"  tips stored        {self.tips:>6}   "
                 f"({self.tips_held} held for review)"]
        for label, items in (("NOT STORED", self.skipped),
                             ("ERRORS", self.errors)):
            if items:
                lines.append(f"  {label:<19}{len(items):>6}")
                lines += [f"    {i}" for i in items[:12]]
        return "\n".join(lines)


def scrape(date: str, *, venue: str | None = None, db: Path | None = None,
           session=None) -> FixedOddsReport:
    report = FixedOddsReport(date=date)
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        card = tips.meeting_card(conn, date)
        report.venue = venue or tips.meeting_venue(conn, date) or ""
        if not card or not report.venue:
            report.errors.append(f"{date}: no card stored — scrape the "
                                 f"meeting first")
            return report
        try:
            ids = ladbrokes.race_ids(date, report.venue, session=session)
        except (FetchError, ladbrokes.LadbrokesError, ValueError) as exc:
            report.errors.append(f"ladbrokes meeting list: {exc}")
            return report
        if not ids:
            report.errors.append(f"Ladbrokes lists no {report.venue} meeting "
                                 f"on {date}")
            return report

        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) \
            .isoformat()
        rows, picks, said = [], [], []
        for race_no in sorted(card):
            if race_no not in ids:
                report.skipped.append(f"R{race_no}: not listed by Ladbrokes")
                continue
            try:
                rec = ladbrokes.fetch_race(ids[race_no], session=session)
            except (FetchError, ladbrokes.LadbrokesError, ValueError) as exc:
                report.errors.append(f"R{race_no}: {exc}")
                continue
            report.races += 1
            field_ = card[race_no]
            for p in ladbrokes.prices(rec):
                ours = field_.get(p["horse_no"])
                if ours is None or names.similarity(p["name"], ours) \
                        < names.SPELLED_ALIKE:
                    report.skipped.append(
                        f"R{race_no} #{p['horse_no']} {p['name']}: the card "
                        f"has {ours or 'no such runner'}")
                    continue
                rows.append({"bookmaker": "ladbrokes", "race_date": date,
                             "race_no": race_no, "captured_at": now, **p})
            url = ladbrokes.page_url(rec)
            picks += racing_sports.selections(
                race_no, ladbrokes.tips(rec), ladbrokes.comment(rec), url)
            said += racing_sports.race_comment(
                race_no, ladbrokes.comment(rec), url, extractor=EXTRACTOR)

        with transaction(conn):
            report.prices = fixed_odds.upsert_fixed_odds(conn, rows)
    finally:
        conn.close()

    if picks:
        got = import_tips.run({
            "payload_version": 1, "race_date": date,
            "generated_at": now.replace("+00:00", "Z"),
            "extractor": EXTRACTOR, "sources": [racing_sports.SOURCE],
            "selections": picks, "quotes": said}, db=db)
        report.tips, report.tips_held = got.selections, got.quarantined

    conn = get_conn(db if db is not None else db_path())
    try:
        with transaction(conn):
            job_log.record_source(
                conn, "scrape_fixed_odds", ok=report.ok,
                detail=(f"{date} · {report.races} races · {report.prices} "
                        f"prices · {report.tips} tips"))
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; default today")
    ap.add_argument("--venue", default=None, help="HV or ST; default the card's")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    date = a.date or dt.datetime.now(_HK).date().isoformat()
    report = scrape(date, venue=a.venue, db=a.db)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

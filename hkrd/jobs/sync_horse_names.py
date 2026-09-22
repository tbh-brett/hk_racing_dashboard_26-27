"""Learn the Chinese name of every horse on a stored card.

    python -m hkrd.jobs.sync_horse_names --date 2026-09-23
    python -m hkrd.jobs.sync_horse_names --pending

`horse_name_zh` is the only road from a Chinese-language source to a runner.
賽馬Fact Check's subtitles name every horse correctly and number none of them,
so until a horse's Chinese name is here, everything that source says about it
lands in quarantine as `name_unknown`.

Each race is read in BOTH languages and the two are paired on the brand
number, which is printed on both and belongs to the horse for life. Not on the
saddle-cloth number against `runners`: that trusts whatever card is stored,
and a meeting stored from the wrong page (see `repair_meeting`) would pair
one horse's English name with another's Chinese one — permanently, because
this table only grows. Where the two cards disagree about a horse, nothing is
written for it and the report says so.

`--pending` asks the database which stored cards from today on still have a
horse with no Chinese name, so a quiet day costs no requests at all.
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hkrd.ingest import racecard, racecard_zh
from hkrd.ingest._client import FetchError, NotFound
from hkrd.store import job_log, tips
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["sync", "pending", "pair_names", "SyncReport"]

# Hong Kong keeps no daylight saving, so a fixed offset is the whole rule.
_HK = dt.timezone(dt.timedelta(hours=8))


@dataclass
class SyncReport:
    """Row counts. Never a bare "done": a zero has to be visible."""

    date: str = ""
    venue: str = ""
    races: int = 0
    pairs: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"  horse names        {self.date} {self.venue}",
                 f"  races read         {self.races:>6}",
                 f"  names paired       {self.pairs:>6}"]
        for label, items in (("NOT PAIRED", self.skipped),
                             ("ERRORS", self.errors)):
            if items:
                lines.append(f"  {label:<19}{len(items):>6}")
                lines += [f"    {i}" for i in items[:12]]
        return "\n".join(lines)


def pair_names(en: Sequence[dict[str, Any]], zh: Sequence[dict[str, Any]],
               *, label: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """One race's English and Chinese runners joined on brand number.

    Returns the pairs, and a line for every runner that could not be paired
    — a missing brand, a horse on one card only, or a brand the two cards
    give different saddle-cloth numbers, which means one of them is stale.
    """
    by_brand: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for z in zh:
        if z.get("brand_no"):
            by_brand[z["brand_no"]] = z
        else:
            skipped.append(f"{label} #{z['horse_no']} {z['name_zh']}: no "
                           f"brand number on the Chinese card")

    pairs: list[dict[str, Any]] = []
    for r in en:
        brand = r.get("brand_no")
        who = f"{label} #{r['horse_no']} {r['horse_name']}"
        if not brand:
            skipped.append(f"{who}: no brand number on the English card")
            continue
        z = by_brand.pop(brand, None)
        if z is None:
            skipped.append(f"{who} ({brand}): not on the Chinese card")
        elif z["horse_no"] != r["horse_no"]:
            skipped.append(f"{who} ({brand}): #{z['horse_no']} on the Chinese "
                           f"card — the cards disagree, nothing written")
        else:
            pairs.append({"horse_name": r["horse_name"],
                          "name_zh": z["name_zh"], "brand_no": brand})
    skipped += [f"{label} #{z['horse_no']} {z['name_zh']} ({b}): on the "
                f"Chinese card only" for b, z in by_brand.items()]
    return pairs, skipped


def sync(date: str, *, venue: str | None = None, db: Path | None = None,
         session=None) -> SyncReport:
    """Read every stored race of one meeting in both languages; store the pairs."""
    report = SyncReport(date=date)
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        card = tips.meeting_card(conn, date)
        report.venue = venue or tips.meeting_venue(conn, date) or ""
        if not card or not report.venue:
            report.errors.append(f"{date}: no card stored — scrape the "
                                 f"meeting before learning its names")
            return report

        rows: list[dict[str, Any]] = []
        for race_no in sorted(card):
            label = f"R{race_no}"
            try:
                en = racecard.fetch_race(date, report.venue, race_no,
                                         session=session)["runners"]
                zh = racecard_zh.fetch_race_zh(date, report.venue, race_no,
                                               session=session)
            except NotFound as exc:
                # The race is stored, so HKJC published it. A card that has
                # since gone (the meeting is over) is an answer; say it.
                report.errors.append(f"{label}: {exc}")
                continue
            except FetchError as exc:
                # Transport. If the host will not answer for this race it
                # will not answer for the next one either.
                report.errors.append(f"{label}: {exc}")
                break
            except racecard.RacecardError as exc:
                report.errors.append(f"{label}: {exc}")
                continue
            pairs, skipped = pair_names(en, zh, label=label)
            report.races += 1
            report.skipped += skipped
            rows += [{**p, "source": "racecard_zh", "seen_at": date}
                     for p in pairs]

        with transaction(conn):
            report.pairs = tips.upsert_horse_names(conn, rows)
            job_log.record_source(
                conn, "sync_horse_names", ok=bool(report.pairs) and report.ok,
                detail=(f"{date} · {report.races} races · {report.pairs} "
                        f"names · {len(report.skipped)} not paired"
                        + (f" · {len(report.errors)} errors"
                           if report.errors else "")))
    finally:
        conn.close()
    return report


def pending(*, db: Path | None = None,
            today: dt.date | None = None) -> list[str]:
    """Stored cards from today on with a horse whose Chinese name is unknown."""
    since = (today or dt.datetime.now(_HK).date()).isoformat()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        return tips.dates_missing_names(conn, since)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="YYYY-MM-DD")
    group.add_argument("--pending", action="store_true",
                       help="every stored card from today on missing a name")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)

    dates = pending(db=a.db) if a.pending else [a.date]
    if not dates:
        print("  horse names        nothing outstanding")
        return 0
    reports = [sync(d, db=a.db) for d in dates]
    for report in reports:
        print(report.render())
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

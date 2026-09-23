#!/usr/bin/env python3
"""Sportsbet's fixed odds and the Racing & Sports comments, from this PC.

    python tools/harvest_sportsbet.py                    # the next HK meeting
    python tools/harvest_sportsbet.py --date 2026-09-23
    python tools/harvest_sportsbet.py --push             # and send it

Sportsbet refuses the dashboard's server (403, measured 2026-09-23) and
answers a home connection, so like the YouTube sources this runs here and
sends rows. One file per meeting, out/<date>.sportsbet.tips.json, which
`push_tips.py --upcoming` sends along with the rest.

What it carries, per race (`hkrd.ingest.sportsbet`):

  fixed_odds   Sportsbet's fixed win and place price for every runner
  selections   Racing & Sports' four tips — the same four Ladbrokes shows,
  quotes         and the same race comment; stored as ONE source whichever
                 bookmaker brought them (`hkrd.ingest.racing_sports`)
               and Racing & Sports' line on every runner, which Ladbrokes
                 does not have

A source is claimed only when every race had it. The dashboard treats a
payload as the whole answer for its sources, so a race that came back
without its comment must not take away the comment an earlier capture got.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hkrd.ingest import racing_sports as rs                 # noqa: E402
from hkrd.ingest import sportsbet as sb                     # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HK = dt.timezone(dt.timedelta(hours=8))
EXTRACTOR = "rule:sportsbet-v1"
LOOK_AHEAD = 3          # days: the evening-before run finds the next meeting


def harvest(date: str, *, session=None) -> tuple[dict[str, Any] | None,
                                                  list[str]]:
    """(payload, problems) for the HK meeting on `date`; no payload when
    Sportsbet lists none that day."""
    session = session or sb.session()
    venue, ids = sb.race_ids(date, session=session)
    if not ids:
        return None, []
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    prices, picks, quotes, form, problems = [], [], [], [], []
    tipped_races = form_races = 0
    for race_no in sorted(ids):
        try:
            rec = sb.fetch_race(ids[race_no], session=session)
        except (sb.FetchError, sb.SportsbetError) as exc:
            problems.append(f"R{race_no}: {exc}")
            continue
        url = sb.page_url(rec)
        prices += [{"bookmaker": "sportsbet", "race_no": race_no,
                    "horse_no": p["horse_no"], "name_seen": p["name"],
                    "win": p["win"], "place": p["place"],
                    "scratched": p["scratched"], "captured_at": stamp}
                   for p in sb.prices(rec) if p["horse_no"]]
        tipped = sb.tips(rec)
        if tipped:
            tipped_races += 1
            picks += rs.selections(race_no, tipped, sb.comment(rec), url)
            quotes += rs.race_comment(race_no, sb.comment(rec), url,
                                      extractor=EXTRACTOR)
        else:
            problems.append(f"R{race_no}: no Racing & Sports tips "
                            f"(provider {sb.provider(rec)!r})")
        lines = rs.runner_comments(race_no, sb.runner_comments(rec), url,
                                   extractor=EXTRACTOR)
        if lines:
            form_races += 1
            form += lines

    sources = []
    if picks and tipped_races == len(ids):
        sources.append(rs.SOURCE)
    else:
        picks, quotes = [], []
    if form and form_races == len(ids):
        sources.append(rs.FORM_SOURCE)
        quotes += form
    return {"payload_version": 1, "race_date": date, "generated_at": stamp,
            "extractor": EXTRACTOR, "_venue": venue, "sources": sources,
            "selections": picks, "quotes": quotes,
            "fixed_odds": prices}, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD; default the next HK meeting "
                                   f"within {LOOK_AHEAD} days")
    ap.add_argument("--out", type=Path, default=REPO / "out")
    ap.add_argument("--push", action="store_true",
                    help="send it to the dashboard as well")
    ap.add_argument("--base", help="dashboard URL, for --push")
    a = ap.parse_args(argv)

    today = dt.datetime.now(HK).date()
    dates = [a.date] if a.date else [
        (today + dt.timedelta(days=k)).isoformat() for k in range(LOOK_AHEAD)]
    session = sb.session()
    written = []
    for date in dates:
        try:
            payload, problems = harvest(date, session=session)
        except (sb.FetchError, sb.SportsbetError) as exc:
            print(f"  {date}: Sportsbet did not answer — {exc}",
                  file=sys.stderr)
            return 1
        if payload is None:
            continue
        a.out.mkdir(parents=True, exist_ok=True)
        path = a.out / f"{date}.sportsbet.tips.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        written.append(path)
        races = len({p["race_no"] for p in payload["fixed_odds"]})
        print(f"  {date} {payload['_venue']}  {races} races · "
              f"{len(payload['fixed_odds'])} prices · "
              f"{len(payload['selections'])} tips · "
              f"{sum(1 for q in payload['quotes'] if q['horse_no'] is None)} "
              f"race comments · "
              f"{sum(1 for q in payload['quotes'] if q['horse_no'])} runner "
              f"comments -> {path}")
        for p in problems:
            print(f"    {p}")
    if not written:
        print(f"  Sportsbet lists no Hong Kong meeting on {', '.join(dates)}")
        return 0
    if a.push:
        import push_tips
        return push_tips.main([str(p) for p in written]
                              + (["--base", a.base] if a.base else []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

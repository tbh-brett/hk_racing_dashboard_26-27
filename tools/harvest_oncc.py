#!/usr/bin/env python3
"""Harvest on.cc 東網馬經 tipster pages to disk, before the archive rolls off.

WHY THIS EXISTS, AND WHY IT ONLY SAVES HTML.

on.cc keeps a rolling window of its tipster pages -- measured on 2026-09-21,
29/04/2026 answers and 11/03/2026 is a 404, so the window is roughly six
months and it moves every day. Whatever falls off the back is gone: there is
no paid archive and the Wayback Machine does not have these pages at meeting
granularity.

So the expiring asset is the HTML, not the parser. Writing a parser first and
harvesting second gets the order backwards -- a parser can be written in a
fortnight, and the meetings it would have read cannot be recovered. This
script therefore does the one thing that has a deadline and nothing else: it
fetches pages and writes them, byte for byte, to disk. It does not parse, it
does not touch a database, and it has no dependencies outside the standard
library, so it runs anywhere Python does without a virtualenv.

Re-running is safe and cheap: a date already on disk is skipped, so an
interrupted run resumes where it stopped, and a weekly run picks up the new
meetings and nothing else.

USAGE

    python harvest_oncc.py --from 2026-03-20 --to 2026-09-21 --out ./raw/oncc
    python harvest_oncc.py --recent 30 --out ./raw/oncc          # weekly top-up

OUTPUT

    <out>/<YYYYMMDD>/<page_id>.html    the page, verbatim
    <out>/manifest.jsonl               one line per fetch: what happened, when

The manifest is what the parser reads later. It records a 404 as a fact --
"on.cc has no tipster page for this date", which is usually "not a race day"
-- rather than as an absence, so a later pass can tell a date that was never
published from a date the harvest never reached.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://racing.on.cc/racing/fav/{date}/{page}.html"

# The pages under /racing/fav/, as seen on the site's own navigation on
# 2026-09-21. The first is the one that matters -- five named tipsters, four
# selections each, every race -- and the rest are harvested because they cost
# 1.2 seconds each and cannot be fetched retroactively if they turn out to
# matter later.
#
# Names are the site's own labels, kept in the original so that a later parser
# can be checked against the page it came from rather than against a
# translation.
PAGES: dict[str, str] = {
    "rjfavf0201x0": "五名家貼士 — five named tipsters, four picks per race",
    "rjfavf0101x0": "23T",
    "rjfavf0301x0": "練馬師貼士 — trainer tips",
    "rjfavf0401x0": "貼士 (fourth panel)",
    "rjfavg0001x0": "馬場boss",
    "rjfavi0101x0": "晨操分析 — morning work analysis",
}

# The same courtesy the dashboard's own scraper extends to HKJC: one request
# every 1.2 seconds, across everything. on.cc is a newspaper run for readers,
# not an API with a quota.
MIN_INTERVAL = 1.2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_last = 0.0


def _throttle() -> None:
    global _last
    wait = MIN_INTERVAL - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def fetch(url: str, *, timeout: float = 30.0) -> tuple[int, bytes]:
    """GET a page. Returns (status, body). A 404 is an answer, not a failure.

    Decompresses gzip itself rather than trusting urllib, because on.cc serves
    gzip when asked and urllib does not unwrap it.
    """
    _throttle()
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, b""


def looks_like_a_tips_page(body: bytes) -> bool:
    """A cheap sanity check, so a soft-404 is not filed as a good page.

    Some sites answer a missing page with 200 and a generic shell. The tipster
    pages all carry the meeting date label, so its absence means whatever came
    back is not the page asked for -- worth recording as such rather than
    writing 40 KB of navigation to disk under a date it does not describe.
    """
    return "賽事日期".encode("big5") in body or "賽事日期".encode("utf-8") in body


def harvest(dates: list[dt.date], out: Path, pages: dict[str, str],
            *, force: bool = False) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.jsonl"
    tally = {"saved": 0, "skipped": 0, "missing": 0, "suspect": 0, "error": 0}

    with manifest.open("a", encoding="utf-8") as log:
        for date in dates:
            stamp = date.strftime("%Y%m%d")
            day = out / stamp
            for page in pages:
                target = day / f"{page}.html"
                if target.exists() and not force:
                    tally["skipped"] += 1
                    continue

                url = BASE.format(date=stamp, page=page)
                try:
                    status, body = fetch(url)
                except Exception as exc:                     # network, DNS, TLS
                    tally["error"] += 1
                    record = {"date": stamp, "page": page, "url": url,
                              "status": None, "outcome": "error",
                              "detail": f"{type(exc).__name__}: {exc}"}
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    print(f"  ! {stamp} {page}  {type(exc).__name__}",
                          file=sys.stderr)
                    continue

                if status == 404:
                    tally["missing"] += 1
                    outcome, detail = "missing", "404 — no page for this date"
                elif status != 200:
                    tally["error"] += 1
                    outcome, detail = "error", f"HTTP {status}"
                elif not looks_like_a_tips_page(body):
                    tally["suspect"] += 1
                    outcome, detail = "suspect", "200 but no 賽事日期 label"
                else:
                    day.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    tally["saved"] += 1
                    outcome, detail = "saved", f"{len(body)} bytes"

                record = {"date": stamp, "page": page, "url": url,
                          "status": status, "outcome": outcome,
                          "detail": detail,
                          "fetched_at": dt.datetime.now(
                              dt.timezone.utc).isoformat(timespec="seconds")}
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()

            if (day / f"{next(iter(pages))}.html").exists():
                print(f"  · {date.isoformat()}  saved")

    return tally


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="start", type=parse_date,
                   help="first date, YYYY-MM-DD")
    p.add_argument("--to", dest="end", type=parse_date,
                   help="last date, YYYY-MM-DD (default: today)")
    p.add_argument("--recent", type=int,
                   help="instead of a range, the last N days ending today")
    p.add_argument("--out", type=Path, default=Path("./raw/oncc"),
                   help="where to write (default: ./raw/oncc)")
    p.add_argument("--only", nargs="*", choices=sorted(PAGES),
                   help="fetch only these page ids (default: all)")
    p.add_argument("--force", action="store_true",
                   help="refetch dates already on disk")
    args = p.parse_args(argv)

    today = dt.date.today()
    if args.recent:
        start, end = today - dt.timedelta(days=args.recent - 1), today
    elif args.start:
        start, end = args.start, args.end or today
    else:
        p.error("give --from (with optional --to), or --recent N")
        return 2

    if start > end:
        p.error("--from is after --to")
        return 2

    pages = {k: PAGES[k] for k in (args.only or PAGES)}
    dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]

    print(f"on.cc harvest — {start} to {end}  ({len(dates)} days, "
          f"{len(pages)} pages each)")
    print(f"  destination  {args.out.resolve()}")
    print(f"  at {MIN_INTERVAL}s per request, up to "
          f"{len(dates) * len(pages) * MIN_INTERVAL / 60:.0f} minutes\n")

    tally = harvest(dates, args.out, pages, force=args.force)

    print(f"\n  saved {tally['saved']}   skipped {tally['skipped']}   "
          f"no page {tally['missing']}   suspect {tally['suspect']}   "
          f"errors {tally['error']}")
    if tally["suspect"]:
        print("  NOTE: 'suspect' means a 200 that did not carry the meeting "
              "date label. Open one and check whether the page layout has "
              "changed before trusting the rest of the harvest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

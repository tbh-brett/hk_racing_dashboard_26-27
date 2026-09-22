#!/usr/bin/env python3
"""Poll 神探賽馬 Horse Detective on Threads, through a feed rather than a scrape.

WHAT WAS TESTED, 2026-09-21, in a real browser against the live account.

THE PROFILE IS PUBLIC BUT CAPPED. threads.com/@horsedetective renders without a
login and the posts are embedded in the page as JSON — but only FOUR of them,
and scrolling loads no more: the page ends with "Log in to see more from
horsedetective." Four was the cap before and after five scroll passes.

THREADS HAS NO NATIVE RSS. /@horsedetective/rss answers 404 with the app shell.

OPEN RSS DOES, AND IT WORKS. https://openrss.org/www.threads.com/@horsedetective
returns `application/xml`, RSS 2.0, four items carrying the full post text, the
permalink and an exact pubDate. That is the route this script uses, and it
matters for three reasons: it is a plain GET of XML so it needs no browser and
no login; Open RSS does the fetching, so nothing here touches a page Threads
disallows in robots.txt; and it runs anywhere, including a server.

RSSHub's public instance (rsshub.app/threads/horsedetective) sits behind a
Cloudflare challenge and is not usable unattended. Self-hosting RSSHub is the
alternative if the four-item cap ever becomes the binding constraint.

THE FOUR-ITEM CAP IS THE WHOLE DESIGN CONSTRAINT. There is no backfill: what
has scrolled past is gone. The fix is polling frequency, not cleverness.
Observed posting on 16 Sep: 12:51, 13:22 and 13:58 UTC — three posts in 67
minutes on a race evening, then nothing for three days. So:

    race days   every 20–30 minutes from mid-afternoon until late
    other days  hourly is ample

Run it more often than feels necessary. A missed post cannot be recovered, and
a redundant poll costs one HTTP request.

WHAT THEY POST, AND THE TRAP IN IT

Two kinds. The first is pre-race analysis, signed with the author's handle,
and it is genuinely good — this is a real post, verbatim:

    [王子]
    R10 10 金勝名駒
    呢匹馬歷來跑13次沙田1400米，只有3次落第 … 而今仗賽前亦有試閘備戰，
    喺試閘當中輕鬆取勝，狀態無需懷疑。雖然排10檔未必好跑，但呢匹馬跑法
    非常彈性 …

Race number, horse number and Chinese horse name in a fixed leading line, then
the reasoning. `R10 10 金勝名駒` parses deterministically, and the number joins
straight onto runners(race_date, race_no, horse_no).

The second kind is results — `R5 16倍Q✅`, `R2 11倍Q✅` — and these are a trap.
They post winners. Nothing in the feed records the bets that lost, so a strike
rate computed from this account's own posts is meaningless and must never be
put on the leaderboard. Score them only against their PRE-RACE posts, matched
to results out of your own database. That is what the `kind` field below is
for: `analysis` is scoreable, `result_claim` is not.

USAGE

    python harvest_threads.py --out ./raw/threads                # one poll
    python harvest_threads.py --out ./raw/threads --watch 1800   # every 30 min

WHAT THIS CANNOT GET, MEASURED

The feed is TEXT ONLY. Its items carry no <enclosure>, no <media:content> and
no <img> — checked on every item. So the screenshot of the all-races table that
Horse Detective attaches to the analysis posts does NOT come through here.

Individual post pages do not help either: logged out,
threads.com/@horsedetective/post/<code> renders
「此內容並未開放所有人查看」 and nothing else.

Both of those need a signed-in session, which means a browser profile you log
into once and a Playwright script that reuses it — on your machine, not on
Fly.io. This script is the part that needs no login and should run regardless:
it catches the text of every post while it is still inside the four-item
window, which is the part that expires.

OUTPUT

    <out>/posts.jsonl   one line per post, deduped on the Threads shortcode
    <out>/poll.log      one line per poll: when, how many seen, how many new
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FEED = "https://openrss.org/www.threads.com/@{handle}"

USER_AGENT = "hkrd-threads-harvester/1.0 (+personal racing research; contact via GitHub tbh-brett)"

# `R10 10 金勝名駒` — race, horse number, then the Chinese name.
#
# The name capture is BEST-EFFORT and deliberately capped at four characters.
# In the post itself the name sits on its own line, but Open RSS collapses the
# line breaks, so what actually arrives is
#
#     [王子]R10 10 金勝名駒呢匹馬歷來跑13次沙田1400米…
#
# with nothing between the name and the sentence that follows it. Four is the
# cap because HK registered Chinese names are two to four characters and
# almost always four; a greedy match here silently swallowed 呢匹馬歷 on the
# first run of this parser.
#
# None of which is load-bearing, because the NUMBER is the join key, not the
# name: `race_no` and `horse_no` go straight onto runners(race_date, race_no,
# horse_no). The name is only a checksum — when it disagrees with the runner
# the number points at, quarantine the row rather than file a plausible wrong
# horse.
SELECTION = re.compile(
    r"R\s*(?P<race>\d{1,2})\s+(?P<horse>\d{1,2})\s*(?P<name>[一-鿿]{2,4})")

# `R5 16倍Q✅`, `R6 1 2 10 單T✅✅✅` — a claim about a bet that landed.
RESULT = re.compile(r"R\s*\d{1,2}[^\n]{0,40}(?:✅|倍[QT]|單T)")

# `[王子]` — which of the two runs the account signs the post.
AUTHOR = re.compile(r"^\s*\[([^\]]{1,12})\]")


def fetch(url: str, *, timeout: float = 30.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S | re.I)
    if not m:
        return ""
    v = m.group(1).strip()
    cd = re.match(r"^<!\[CDATA\[(.*?)\]\]>$", v, re.S)
    if cd:
        v = cd.group(1)
    return html.unescape(v).strip()


def parse_feed(xml: str) -> list[dict]:
    """RSS 2.0 items → post records.

    The description Open RSS builds carries the post text plus two pieces of
    furniture: a trailing "- (@handle) September 16, 2026" attribution and the
    literal word "Translate" from the rendered page. Both are stripped, because
    they are the feed service's and Threads' text, not the tipster's, and an
    LLM handed "Translate" at the end of every post will eventually comment
    on it.
    """
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S | re.I):
        block = m.group(1)
        link = _tag(block, "link")
        code = (link.rstrip("/").rsplit("/", 1) or [""])[-1]

        body = re.sub(r"<[^>]+>", " ", _tag(block, "description"))
        body = body.replace("&nbsp;", " ")
        body = re.sub(r"\s*-\s*\(@[\w.]+\)\s+\w+\s+\d{1,2},\s*\d{4}\s*$", "", body)
        body = re.sub(r"\s*Translate\s*$", "", body)
        body = re.sub(r"[ \t]+", " ", body).strip()

        pub = _tag(block, "pubDate")
        iso = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                iso = dt.datetime.strptime(pub, fmt).replace(
                    tzinfo=dt.timezone.utc).isoformat()
                break
            except ValueError:
                continue

        sels = [{"race_no": int(s["race"]), "horse_no": int(s["horse"]),
                 "name_zh": s["name"]} for s in SELECTION.finditer(body)]
        author = AUTHOR.match(body)

        if sels:
            kind = "analysis"
        elif RESULT.search(body):
            kind = "result_claim"
        else:
            kind = "other"

        out.append({
            "code": code,
            "url": link,
            "published": iso,
            "published_raw": pub,
            "author_tag": author.group(1) if author else None,
            "kind": kind,
            "selections": sels,
            "text": body,
        })
    return out


def run_once(handle: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    store = out / "posts.jsonl"

    known: set[str] = set()
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            try:
                known.add(json.loads(line)["code"])
            except Exception:
                continue

    # Open RSS answers 200 with an empty shell while it regenerates, and serves
    # the real feed on a retry seconds later. Measured on 2026-09-21: first
    # request 0 items, second request the same 4 items it had held all along.
    # So a single empty answer means "ask again", not "nothing was posted" —
    # treating it as the latter would silently skip a race evening.
    status, xml = 0, ""
    for attempt in range(3):
        status, xml = fetch(FEED.format(handle=handle))
        if status == 200 and "<item>" in xml:
            break
        time.sleep(6 * (attempt + 1))
    if status != 200 or "<item>" not in xml:
        return {"status": status, "seen": 0, "new": 0,
                "note": "no items after three tries — Open RSS is regenerating, "
                        "or the profile is unreachable. Not the same as "
                        "'they posted nothing'."}

    posts = parse_feed(xml)
    fresh = [p for p in posts if p["code"] not in known]

    if fresh:
        with store.open("a", encoding="utf-8") as f:
            for p in sorted(fresh, key=lambda x: x["published_raw"]):
                p["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds")
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    return {"status": status, "seen": len(posts), "new": len(fresh),
            "items": fresh}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--handle", default="horsedetective")
    p.add_argument("--out", type=Path, default=Path("./raw/threads"))
    p.add_argument("--watch", type=int, metavar="SECONDS",
                   help="poll forever at this interval instead of once "
                        "(1800 = every 30 min)")
    args = p.parse_args(argv)

    def poll() -> None:
        r = run_once(args.handle, args.out)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        line = (f"{now}  http {r['status']}  seen {r['seen']}  new {r['new']}"
                + (f"  — {r['note']}" if r.get("note") else ""))
        print(line)
        for it in r.get("items", []):
            sel = " ".join(f"R{s['race_no']}#{s['horse_no']}·{s['name_zh']}"
                           for s in it["selections"]) or "—"
            print(f"    {it['kind']:13s} {it['published'] or '?'}  "
                  f"{it['author_tag'] or '·'}  {sel}")
            print(f"      {it['text'][:110]}")
        (args.out / "poll.log").open("a", encoding="utf-8").write(line + "\n")

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.watch:
        poll()
        return 0

    print(f"watching @{args.handle} every {args.watch}s — Ctrl-C to stop\n")
    try:
        while True:
            try:
                poll()
            except Exception as exc:                 # a poll must never end the watch
                print(f"  ! {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

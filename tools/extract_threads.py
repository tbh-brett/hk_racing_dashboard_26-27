"""神探賽馬 Horse Detective on Threads — its signed pre-race analysis.

`harvest_threads.py` keeps every post it has seen in raw/threads/posts.jsonl,
already sorted into two kinds:

  analysis       opens with the race, the number and the Chinese name, then
                 the reasoning — 「[王子] R10 10 金勝名駒 呢匹馬歷來跑13次…」.
                 A pick, and counted as one.
  result_claim   「R5 16倍Q✅」: a bet that landed, posted after the race. The
                 account posts its winners and never its losers, so these are
                 never counted as anything.

A post names no meeting. It is matched to one by the day it was published in
Hong Kong: the meeting day or up to two days before. Hong Kong meetings are
at least three days apart, so no post can fall to two of them. The one
analysis in the feed on 25 Sep 2026 went up at 11:49 on race day.

The number is the identity and the Chinese name only the checksum, which
`jobs/import_tips` tests against the card — so, like Fact Check, nothing is
sent until the dashboard holds the card's Chinese names.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

SOURCE = "threads"
TIPSTER = "Horse Detective"
LEAD_DAYS = 2
_HK = dt.timezone(dt.timedelta(hours=8))
_HASHTAGS = re.compile(r"(\s*#\S+)+\s*$")


def read(folder: Path) -> list[dict]:
    store = folder / "posts.jsonl"
    if not store.exists():
        return []
    return [json.loads(line) for line in
            store.read_text(encoding="utf-8").splitlines() if line.strip()]


def hk_day(post: dict) -> str | None:
    stamp = post.get("published")
    if not stamp:
        return None
    return dt.datetime.fromisoformat(stamp).astimezone(_HK).date().isoformat()


def is_for(post: dict, race_date: str) -> bool:
    """An analysis post published on the meeting day or the two before."""
    day = hk_day(post)
    if post.get("kind") != "analysis" or not post.get("selections") or not day:
        return False
    lead = (dt.date.fromisoformat(race_date) - dt.date.fromisoformat(day)).days
    return 0 <= lead <= LEAD_DAYS


def selections(posts: list[dict], race_date: str, fetched_at: str
               ) -> list[dict]:
    """One pick per horse the posts name, the latest post's words kept when
    two posts name the same horse — two rows on one key is a payload the
    import refuses, rightly."""
    out: dict[tuple[int, int], dict] = {}
    for post in sorted(posts, key=lambda p: p.get("published") or ""):
        if not is_for(post, race_date):
            continue
        note = _HASHTAGS.sub("", post.get("text") or "").strip() or None
        for s in post["selections"]:
            out[(s["race_no"], s["horse_no"])] = {
                "source": SOURCE, "tipster": TIPSTER,
                "race_no": s["race_no"], "horse_no": s["horse_no"],
                "pick_rank": None, "name_seen": s.get("name_zh"),
                "note": note, "caption_kind": None, "url": post.get("url"),
                "published_at": post.get("published"), "fetched_at": fetched_at}
    return [out[k] for k in sorted(out)]

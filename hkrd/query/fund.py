"""What the HKTB fund app may read: the meetings, a card, its Six Up pools.

The fund app is a separate system for four people (docs/fund-api.md). It reads
these over Fly's private network, keeps its own copy, and is shown nothing
beyond what is here: no model, no commentary, no ledger, no blackbook. Every
function is a plain read of what the scrapers already stored; none of them
starts a scrape, a fit or a simulation.

Every answer is wrapped by `envelope`, which stamps a `version` -- a hash of
the content WITHOUT `updated_at`. `updated_at` is when hkrd last read the data
from HKJC, which moves every time the card is re-scraped whether or not
anything on it changed; hashing it would make every poll a fresh download, and
the fund app's whole design is that an unchanged card answers 304.

Prices, results and a horse's later runs are in `query/fund_results`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["NotHeld", "BadQuery", "V", "SCRATCHED", "CARD_JOBS", "RESULT_JOBS",
           "envelope", "reading", "hk_time", "parse_day", "parse_at",
           "check_meeting", "last_read", "post_time", "class_text",
           "meetings", "card", "pools", "health"]

V = 1
HK = dt.timezone(dt.timedelta(hours=8))
VENUES = ("ST", "HV")

# A runner the card or the result calls withdrawn. Every other code (UR, PU,
# FE, DNF, TNP, DISQ, dead heats) is a horse that started.
SCRATCHED = ("WV", "WV-A", "WX", "WX-A", "WXNR")

SIX_UP_POOL = "SixUP"
SIX_UP_LEGS = 6

# Which job's last good run says when hkrd last read each kind of data.
CARD_JOBS = ("scrape_meeting:card",)
RESULT_JOBS = ("scrape_meeting:results", "scrape_meeting:dividends")
SCRAPE_JOBS = ("scrape_odds",) + CARD_JOBS + RESULT_JOBS

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


class NotHeld(LookupError):
    """hkrd holds no such data, or not yet. The router answers 404 with this."""


class BadQuery(ValueError):
    """The request itself is malformed. The router answers 400 with this."""


@contextmanager
def reading(conn: Connection | None) -> Iterator[Connection]:
    """The caller's connection, or one of our own closed on the way out."""
    if conn is not None:
        yield conn
        return
    own = get_conn()
    try:
        yield own
    finally:
        own.close()


def envelope(kind: str, updated_at: str | None,
             content: dict[str, Any]) -> dict[str, Any]:
    """One answer: `v`, `kind`, `version`, `updated_at` and the content.

    `version` hashes everything except `updated_at` (see the module note), with
    keys sorted so the same content always hashes the same.
    """
    hashed = {"v": V, "kind": kind, **content}
    blob = json.dumps(hashed, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    version = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return {"v": V, "kind": kind, "version": version,
            "updated_at": updated_at, **content}


def hk_time(ts: str | None) -> str | None:
    """A stored time as ISO 8601 at +08:00.

    Captures are stored as naive Hong Kong time; the job log is UTC with an
    offset. Both come out the same way.
    """
    if not ts:
        return None
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=HK)
    return t.astimezone(HK).isoformat(timespec="seconds")


def parse_day(value: str | None, name: str) -> str:
    """YYYY-MM-DD, or BadQuery naming the parameter."""
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise BadQuery(f"{name}: {value!r} is not a date (YYYY-MM-DD)") from None


def parse_at(value: str | None) -> str:
    """An ISO time as the naive Hong Kong time captures are stored in.

    No offset means Hong Kong time, which is the only clock races run on.
    """
    try:
        t = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise BadQuery(f"at: {value!r} is not an ISO 8601 time") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=HK)
    return t.astimezone(HK).replace(tzinfo=None).isoformat(timespec="seconds")


def check_meeting(conn: Connection, date: str, venue: str) -> None:
    """NotHeld unless hkrd has a meeting at this venue on this date."""
    if venue not in VENUES:
        raise BadQuery(f"venue: {venue!r} is not ST or HV")
    held = {r[0] for r in conn.execute(
        "SELECT DISTINCT venue FROM races WHERE race_date = ?", (date,))}
    if not held:
        raise NotHeld(f"no meeting on {date}")
    if venue not in held:
        raise NotHeld(f"no {venue} meeting on {date}; hkrd has "
                      f"{', '.join(sorted(v for v in held if v))}")


def last_read(conn: Connection, jobs: Sequence[str]) -> str | None:
    """When the newest successful run of any of these jobs finished, +08:00.

    One indexed lookup per job, newest first, stopping at the first good run.
    """
    found = []
    for job in jobs:
        row = conn.execute(
            "SELECT finished_at FROM job_runs WHERE job = ? AND ok = 1 "
            "ORDER BY started_at DESC LIMIT 1", (job,)).fetchone()
        if row and row[0]:
            found.append(hk_time(row[0]))
    return max(found) if found else None


def post_time(date: str, off_time: str | None) -> str | None:
    """The card's off time as ISO at +08:00. None for last season's archive,
    which was stored from results alone and never carried one."""
    m = _HHMM.match(off_time or "")
    if not m:
        return None
    return f"{date}T{int(m.group(1)):02d}:{m.group(2)}:00+08:00"


def class_text(race_class: str | None) -> str | None:
    """'4' -> 'Class 4', 'G1' -> 'Group 1'; anything else as stored."""
    if not race_class:
        return None
    if race_class == "0":
        return "Group"          # the archive's Group race of unknown grade
    if race_class.isdigit():
        return f"Class {race_class}"
    if re.fullmatch(r"G[123]", race_class):
        return f"Group {race_class[1]}"
    return race_class


def _meeting_course(courses: Sequence[str | None]) -> str | None:
    """The rail the turf races run on. An all-weather night is 'AWT'."""
    turf = Counter(c for c in courses if c and c != "AWT")
    if turf:
        return turf.most_common(1)[0][0]
    return "AWT" if "AWT" in courses else None


def meetings(start: str, end: str, *,
             conn: Connection | None = None) -> dict[str, Any]:
    """Every meeting hkrd holds a card for between two dates, inclusive.

    A range with no racing in it (the summer break) is an empty list, not an
    error: that is the true answer.
    """
    start, end = parse_day(start, "from"), parse_day(end, "to")
    if end < start:
        raise BadQuery(f"to ({end}) is before from ({start})")
    with reading(conn) as c:
        rows = c.execute(
            "SELECT race_date, venue, course, off_time FROM races "
            "WHERE race_date BETWEEN ? AND ? ORDER BY race_date, race_no",
            (start, end)).fetchall()
        updated = last_read(c, CARD_JOBS)

    by_date: dict[str, list[Any]] = {}
    for r in rows:
        by_date.setdefault(r["race_date"], []).append(r)
    out = []
    for date, races in by_date.items():
        venue = Counter(r["venue"] for r in races).most_common(1)[0][0]
        posts = [p for p in (post_time(date, r["off_time"]) for r in races) if p]
        item: dict[str, Any] = {"date": date, "venue": venue, "races": len(races)}
        course = _meeting_course([r["course"] for r in races])
        if course:
            item["course"] = course
        if posts:
            item["first_post"], item["last_post"] = min(posts), max(posts)
        out.append(item)
    return envelope("meetings", updated, {"meetings": out})


def card(date: str, venue: str, *,
         conn: Connection | None = None) -> dict[str, Any]:
    """The declared field of every race, with brand numbers and scratchings.

    The brand number is read from the two tables that pair it with a name
    (the English-Chinese card sync, and the new-horse profiles). `runners`
    has none, and the join key here is the name, never `horse_id`.
    """
    date = parse_day(date, "date")
    with reading(conn) as c:
        check_meeting(c, date, venue)
        races = c.execute(
            "SELECT race_no, course, surface, distance, race_class, off_time "
            "FROM races WHERE race_date = ? ORDER BY race_no", (date,)).fetchall()
        runners = c.execute("""
            SELECT r.race_no, r.horse_no, r.horse_name, r.draw, r.jockey,
                   r.trainer, r.place_code, z.name_zh,
                   COALESCE(z.brand_no, b.brand_no) AS brand_no
              FROM runners r
              LEFT JOIN horse_name_zh z ON z.horse_name = r.horse_name
              LEFT JOIN horse_background b ON b.horse_name = r.horse_name
             WHERE r.race_date = ?
             ORDER BY r.race_no, r.horse_no""", (date,)).fetchall()
        updated = last_read(c, CARD_JOBS)

    field: dict[int, list[dict[str, Any]]] = {}
    for r in runners:
        runner: dict[str, Any] = {"horse_no": r["horse_no"], "name": r["horse_name"],
                                  "brand_no": r["brand_no"], "draw": r["draw"],
                                  "scratched": r["place_code"] in SCRATCHED}
        for key, col in (("name_zh", "name_zh"), ("jockey", "jockey"),
                         ("trainer", "trainer")):
            if r[col]:
                runner[key] = r[col]
        field.setdefault(r["race_no"], []).append(runner)

    out = []
    for r in races:
        race: dict[str, Any] = {"race_no": r["race_no"],
                                "post_time": post_time(date, r["off_time"]),
                                "distance": r["distance"],
                                "runners": field.get(r["race_no"], [])}
        if cls := class_text(r["race_class"]):
            race["class"] = cls
        if r["surface"]:
            race["track"] = "awt" if r["surface"].upper() == "AWT" else "turf"
        out.append(race)
    meeting: dict[str, Any] = {"date": date, "venue": venue}
    if course := _meeting_course([r["course"] for r in races]):
        meeting["course"] = course
    return envelope("card", updated, {"meeting": meeting, "races": out})


def pools(date: str, venue: str, *,
          conn: Connection | None = None) -> dict[str, Any]:
    """The meeting's Six Up pools: legs and money invested, latest capture.

    The turnover capture files a cross-race pool under its first leg (HKJC's
    `leg.races`), and a Six Up's legs are six consecutive races. A card of ten
    or more runs two -- 1 to 6 and 6 to 11 on 25 Oct 2026 -- and the earlier
    is the early Six Up. hkrd does not capture the jackpot, so it is null.
    """
    date = parse_day(date, "date")
    with reading(conn) as c:
        check_meeting(c, date, venue)
        race_nos = [r[0] for r in c.execute(
            "SELECT race_no FROM races WHERE race_date = ? ORDER BY race_no", (date,))]
        # One primary-key seek per race. A GROUP BY over the meeting's turnover
        # reads every pool's every capture -- 62 ms on 25 Oct 2026; this is 1.
        rows = [r for r in (c.execute(
            "SELECT race_no, turnover, captured_at FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND pool = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (date, n, SIX_UP_POOL)).fetchone() for n in race_nos) if r]
        last_race = max(race_nos)
    if not rows:
        raise NotHeld(f"no Six Up pool captured for {date} {venue}")
    if len(rows) > 2:
        raise RuntimeError(f"{date}: {len(rows)} Six Up pools (first legs "
                           f"{[r['race_no'] for r in rows]}); HKJC runs two at most")
    kinds = ["six_up"] if len(rows) == 1 else ["six_up_early", "six_up"]
    out = []
    for kind, r in zip(kinds, rows):
        legs = list(range(r["race_no"], r["race_no"] + SIX_UP_LEGS))
        if legs[-1] > last_race:
            raise RuntimeError(f"{date}: a Six Up from race {r['race_no']} runs "
                               f"past the last race ({last_race})")
        out.append({"type": kind, "legs": legs, "investment_hkd": r["turnover"],
                    "jackpot_hkd": None})
    updated = hk_time(max(r["captured_at"] for r in rows))
    return envelope("pool", updated, {"meeting": {"date": date, "venue": venue},
                                      "pools": out})


def health(*, conn: Connection | None = None) -> dict[str, Any]:
    """Can the database be read, and when did hkrd last read HKJC."""
    with reading(conn) as c:
        c.execute("SELECT 1 FROM races LIMIT 1").fetchone()
        return {"ok": True, "db_updated_at": last_read(c, SCRAPE_JOBS)}

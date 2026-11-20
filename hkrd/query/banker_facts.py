"""What was knowable about a runner before the off, in the owner's terms.

Asked for on 2026-11-17: *the banker is what I need most help with -- I pick on
intuition after plenty of deliberation and seldom keep track of why; I want to
know whether my preferences pay.* His reasons were never written down, but the
horses he chose were, and every one of them carried facts like these. Read for
his bankers and for the rest of each field, they say what he leans towards
(`query/banker_record`); read for a candidate on a card, they say where it
stands against that record (`query/banker_brief`).

One definition, used by both, so the brief and the scorecard cannot describe the
same horse two ways. Every fact is AS AT THE RACE: runs, trials and notes from
strictly before the race day, a book entry live on it, a model score from before
the off. A fact that did not exist yet is None ("not kept then") rather than a
value -- the tips are kept from 6 Sep 2026 and the model's scores from 1 Oct
2026, and counting those races as "not tipped" would be a fact that is false.

THE MARKET is the starting price for a run that has a result and, for a card,
whatever capture the caller hands in (`market=`): the price the decision was
made on.
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
from collections import defaultdict
from typing import Any

from hkrd.derive.features import class_num
from hkrd.query import book_tier
from hkrd.store.connect import Connection

__all__ = ["VERSION", "FACTS", "LABEL", "VALUES", "TIPS_FROM", "MODEL_FROM", "evidence",
           "facts_for"]

# key -> (label, the values in the order a page lists them)
FACTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("own", "Your own evidence before race day", ("yours", "system's only", "none")),
    ("model", "The model's rank", ("top 3", "4th or worse")),
    ("course_trip", "Placed before at this course and trip", ("yes", "no")),
    ("market", "Its place in the market", ("favourite", "2nd-3rd", "4th-6th", "7th or longer")),
    ("last_start", "Last start", ("won", "2nd-3rd", "4th-6th", "7th or worse", "debut")),
    ("close_last", "Beaten narrowly last start", ("within 2 lengths", "further back", "won", "debut")),
    ("since_last", "Since its last run", ("21 days or less", "22-59 days", "first-up (60 days+)", "debut")),
    ("class", "Class against its last start", ("down", "same", "up")),
    ("draw", "Draw", ("1-3", "middle", "outside 30%")),
    ("style", "How it usually runs", ("on the pace", "midfield", "back")),
    ("rider", "Rider's last 365 days", ("15%+ winners", "8-15%", "under 8% or few rides")),
    ("trial", "Trialled since its last run", ("yes", "no")),
    ("gear", "Gear on for the first time", ("yes", "no")),
    ("tips", "Tipped", ("2+ sources", "1 source", "none")),
)
LABEL = {k: label for k, label, _ in FACTS}
VALUES = {k: values for k, _, values in FACTS}

# runner_fact.version: a changed rule is a new version. 1.1 (20 Nov 2026): his
# run note on the last start is his own evidence whatever the horse's tier.
VERSION = "facts-1.1"
TIPS_FROM = "2026-09-06"       # the first meeting the tips layer kept
MODEL_FROM = "2026-10-01"      # the first card the fundamental model scored

_FRONT = {"Leader", "On-Pace"}
_CLAIM = re.compile(r"\s*\(-?\d+\)\s*$")          # "H Y Yuen (-7)" on a card
_FIRST_TIME_GEAR = re.compile(r"\b[A-Z]{1,3}1\b")  # B1, TT1, XB1 (derive/gear)
_COMPARABLE_CLASS = {1.0, 2.0, 3.0, 4.0, 5.0, 6.0}


def evidence(tier: dict[str, Any] | None) -> dict[str, Any] | None:
    """A runner's tier, with whether it is the OWNER'S evidence: his trial or
    run note, or an entry he booked or adopted. An entry only the system wrote
    is shown and is not his -- unless he wrote a run note on its last start,
    which is his eye on the horse whoever booked it."""
    if tier is None:
        return None
    booking = tier.get("booking") or {}
    own = (tier["tier"] in ("NOTE", "RUN_NOTE") or booking.get("origin") == "owner"
           or bool(booking.get("adopted_date")) or bool(tier.get("run_note")))
    return {**tier, "own": own}


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _rider(name: str | None) -> str | None:
    return _CLAIM.sub("", name).strip() if name else None


def _band(rank: int | None) -> str | None:
    if rank is None:
        return None
    return ("favourite" if rank == 1 else "2nd-3rd" if rank <= 3
            else "4th-6th" if rank <= 6 else "7th or longer")


def _style(styles: list[str]) -> str | None:
    s = [x for x in styles if x and x != "Unknown"]
    if not s:
        return None
    if sum(x in _FRONT for x in s) * 2 >= len(s):
        return "on the pace"
    return "back" if sum(x == "Closer" for x in s) * 2 >= len(s) else "midfield"


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def facts_for(conn: Connection, runs: list[dict[str, Any]], *,
              market: dict[tuple, int] | None = None) -> dict[tuple, dict[str, Any]]:
    """{(race_date, race_no, horse_no): {fact: value or None, "tier": ...}} for
    each of `runs` (race_date, race_no, horse_no, horse_name). `market` gives a
    runner's place in the market where the starting price is not the one meant
    (a card's latest capture). Seven queries, however many runs."""
    if not runs:
        return {}
    keys = {(r["race_date"], r["race_no"]) for r in runs}
    dates = sorted({d for d, _ in keys})
    names = sorted({r["horse_name"] for r in runs if r.get("horse_name")})
    first, last = dates[0], dates[-1]

    # The race each run is in: its field, draw, gear, rider, and starting prices.
    race: dict[tuple, dict[int, dict]] = defaultdict(dict)
    info: dict[tuple, dict] = {}
    for r in conn.execute(
            f"SELECT u.race_date, u.race_no, u.horse_no, u.draw, u.gear, u.jockey, "
            f"       u.win_odds, a.venue, a.surface, a.distance, a.race_class, a.off_time "
            f"FROM runners u JOIN races a USING (race_date, race_no) "
            f"WHERE u.race_date IN ({_marks(len(dates))}) "
            f"  AND coalesce(u.place_code, '') NOT LIKE 'W%'", dates):
        k = (r["race_date"], r["race_no"])
        if k in keys:
            race[k][r["horse_no"]] = dict(r)
            info[k] = dict(r)

    # Every earlier run of these horses, oldest first.
    past: dict[str, list[tuple]] = defaultdict(list)
    for r in conn.execute(
            f"SELECT u.horse_name, u.race_date, u.place, u.lengths_behind, a.venue, "
            f"       a.surface, a.distance, a.race_class, p.pace_style "
            f"FROM runners u JOIN races a USING (race_date, race_no) "
            f"LEFT JOIN runner_pace p USING (race_date, race_no, horse_no) "
            f"WHERE u.horse_name IN ({_marks(len(names))}) AND u.race_date < ? "
            f"  AND coalesce(u.place_code, '') NOT LIKE 'W%' "
            f"  AND (u.place IS NOT NULL OR u.place_code IS NOT NULL) "
            f"ORDER BY u.race_date, u.race_no", [*names, last]):
        past[r["horse_name"]].append(tuple(r)[1:])

    # Riders: every ride in the year before the earliest race, for a running rate.
    rides: dict[str, tuple[list[str], list[int]]] = {}
    since = (dt.date.fromisoformat(first) - dt.timedelta(days=365)).isoformat()
    acc: dict[str, list] = defaultdict(lambda: [[], []])
    for d, jockey, place in conn.execute(
            "SELECT race_date, jockey, place FROM runners WHERE race_date >= ? "
            "AND race_date < ? AND jockey IS NOT NULL AND place IS NOT NULL "
            "ORDER BY race_date", (since, last)):
        a = acc[_rider(jockey)]
        a[0].append(d)
        a[1].append((a[1][-1] if a[1] else 0) + (place == 1))
    rides = {k: (v[0], v[1]) for k, v in acc.items()}

    trials: dict[str, list[str]] = defaultdict(list)
    for name, d in conn.execute(
            f"SELECT horse_name, trial_date FROM trials WHERE horse_name IN "
            f"({_marks(len(names))}) AND trial_date < ? ORDER BY trial_date", [*names, last]):
        trials[name].append(d)

    tips: dict[tuple, set] = defaultdict(set)
    for d, n, h, src in conn.execute(
            f"SELECT race_date, race_no, horse_no, source FROM tipster_selection "
            f"WHERE race_date IN ({_marks(len(dates))})", dates):
        tips[(d, n, h)].add(src)

    scored: dict[tuple, dict[int, float]] = defaultdict(dict)
    for d, n, h, p in conn.execute(
            f"SELECT race_date, race_no, horse_no, p_win FROM runner_gbm "
            f"WHERE stage = 'latest' AND race_date IN ({_marks(len(dates))})", dates):
        scored[(d, n)][h] = p

    tiered = book_tier.tiers_for(
        [{**r, "off_time": info.get((r["race_date"], r["race_no"]), {}).get("off_time")}
         for r in runs], conn=conn)

    # Per-race and per-horse constants, worked out once rather than per runner.
    hist_dates = {name: [x[0] for x in rows] for name, rows in past.items()}
    sp_rank = {k: {h: i + 1 for i, (_, h) in enumerate(sorted(
        (x["win_odds"], x["horse_no"]) for x in field.values()
        if x.get("win_odds") and x["win_odds"] < 999))} for k, field in race.items()}
    model_rank = {k: {h: i + 1 for i, h in enumerate(sorted(sc, key=lambda x: (-sc[x], x)))}
                  for k, sc in scored.items()}

    out = {}
    for r in runs:
        d, n, h, name = r["race_date"], r["race_no"], r["horse_no"], r.get("horse_name")
        field = race.get((d, n), {})
        me = field.get(h, {})
        ri = info.get((d, n), {})
        hist = past.get(name, [])
        hist = hist[:bisect.bisect_left(hist_dates.get(name, []), d)]
        prev = hist[-1] if hist else None
        f: dict[str, Any] = {}

        ev = evidence(tiered.get((d, n, h)))
        f["own"] = "yours" if ev and ev["own"] else "system's only" if ev else "none"
        f["tier"] = ev["tier"] if ev else None

        rank = model_rank.get((d, n), {}).get(h)
        f["model"] = None if rank is None else "top 3" if rank <= 3 else "4th or worse"

        here = (ri.get("venue"), ri.get("surface"), ri.get("distance"))
        f["course_trip"] = ("yes" if any((x[3], x[4], x[5]) == here and x[1] is not None
                                         and x[1] <= 3 for x in hist) else "no")

        if market is not None and (d, n, h) in market:
            f["market"] = _band(market[(d, n, h)])
        else:
            f["market"] = _band(sp_rank.get((d, n), {}).get(h))

        if prev is None:
            f["last_start"] = f["close_last"] = f["since_last"] = "debut"
        else:
            p, lb = prev[1], prev[2]
            f["last_start"] = (None if p is None else "won" if p == 1 else "2nd-3rd" if p <= 3
                               else "4th-6th" if p <= 6 else "7th or worse")
            f["close_last"] = ("won" if p == 1 else None if p is None or lb is None
                               else "within 2 lengths" if lb <= 2.0 else "further back")
            gap = _days(prev[0], d)
            f["since_last"] = ("21 days or less" if gap <= 21 else "first-up (60 days+)"
                               if gap >= 60 else "22-59 days")

        now_c, then_c = class_num(ri.get("race_class")), class_num(prev[6]) if prev else 0.0
        f["class"] = (None if now_c not in _COMPARABLE_CLASS or then_c not in _COMPARABLE_CLASS
                      else "down" if now_c > then_c else "up" if now_c < then_c else "same")

        draw, size = me.get("draw"), len(field)
        f["draw"] = (None if not draw or size < 2 else "1-3" if draw <= 3
                     else "outside 30%" if (draw - 1) / (size - 1) >= 0.7 else "middle")

        f["style"] = _style([x[7] for x in hist[-3:]])

        rider = _rider(me.get("jockey"))
        got = rides.get(rider)
        if got:
            start = (dt.date.fromisoformat(d) - dt.timedelta(days=365)).isoformat()
            lo, hi = bisect.bisect_left(got[0], start), bisect.bisect_left(got[0], d)
            n_rides = hi - lo
            wins = (got[1][hi - 1] if hi else 0) - (got[1][lo - 1] if lo else 0)
            rate = wins / n_rides if n_rides >= 60 else None
        else:
            rate = None
        f["rider"] = ("under 8% or few rides" if rate is None or rate < 0.08
                      else "15%+ winners" if rate >= 0.15 else "8-15%")

        floor = prev[0] if prev else (dt.date.fromisoformat(d) - dt.timedelta(days=365)).isoformat()
        f["trial"] = "yes" if any(floor < t < d for t in trials.get(name, [])) else "no"

        f["gear"] = "yes" if _FIRST_TIME_GEAR.search(me.get("gear") or "") else "no"

        if d < TIPS_FROM:
            f["tips"] = None
        else:
            k = len(tips.get((d, n, h), ()))
            f["tips"] = "2+ sources" if k >= 2 else "1 source" if k == 1 else "none"
        out[(d, n, h)] = f
    return out

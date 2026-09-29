"""The Screen for one meeting — the Briefing's first section.

Per race: every runner's chance to win and to place before any price exists
(the fundamental model, `runner_gbm` via `query/gbm` -- the engine since
gbm-SPEC §6; `model/screen` is kept for its rider-rate helper and nothing
else), the factor groups for and against it, and the four the Screen would
look at first. Around that, the manual layer the dashboard
already holds and nobody should have to go looking for: the blackbook entry
and whether today's set-up suits it, the owner's own run and trial notes, the
last start in HKJC's words, and the horses in the field it could turn around.

THE SHORTLIST IS FOUR, and the page says why: walk-forward over five seasons
the model's top four held the winner in the share its record gives
(`top4_has_winner`), against the closing market's top four beside it. It is a
list to read, not a list to back.

HEAD-TO-HEAD, AND THE WEIGHT SWING IT LEAVES OUT. Measured over 91,856 pairs
meeting again within a year: a last-time margin of a length or less repeats
51% of the time -- a coin toss -- and 8L+ only 68%. A better draw or a
better rider for the beaten horse moves that toward 50/50. A weight swing
does NOT: in a handicap the weight follows the rating, so the horse the swing
went against is usually the one that has been winning, and pairs with the
beaten horse 8lb+ WORSE off reversed more often than pairs with it 8lb+
better off. So a reversal note here names the margin, the draw and the rider,
and never the weight.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from hkrd.model import screen as model
from hkrd.query import blackbook_band
from hkrd.query import gbm as gbm_q
from hkrd.query.formguide import notes_for_horses
from hkrd.query.screen_inputs import gather
from hkrd.store.connect import Connection, get_conn

__all__ = ["meeting", "SHORTLIST", "CASE_AT", "SETUP_AT", "REVERSAL_MARGIN"]

SHORTLIST = 4
# Outside the shortlist, a horse "has a case" when its circumstances --
# everything except its own form record, the market's past view of it and the
# rider -- are worth x1.2 or more.
CASE_AT = 0.18
# The blackbook set-up verdict: conditions worth x1.16 either way.
SETUP_AT = 0.15
# A reversal is only worth naming when the last margin was this close.
REVERSAL_MARGIN = 2.0
REVERSAL_DAYS = 365
# What each margin band repeated at, measured (see module docstring): the
# winner finished in front again. The rest of the time the beaten horse
# turned it round, and that is the figure the page shows.
_REPEAT = ((1.0, 51), (2.0, 55))
# The model's groups that describe today's circumstances rather than the horse.
_SITUATION = ("draw", "weight", "campaign", "trip & track", "pace & sectionals")
# A group is named for or against a runner at x1.05 or x0.95 and beyond;
# smaller pushes are the model's bookkeeping, not reasons.
_NAMED_AT = math.log(1.05)
_NOT_REASONS = ("rider", "race")          # the rider has its own line; race is per race


def _group_line(key: str, x: float) -> dict[str, Any]:
    return {"key": key, "group": key, "label": key.capitalize(), "why": None,
            "x": x, "caveat": None}


def _jrate(jockeys: dict, name: str | None, base: float) -> float:
    wins, rides = jockeys.get(name, (0, 0))
    return model.jockey_rate(wins, rides, base)


def _reversals(runners: list[dict[str, Any]], race: dict[str, Any]
               ) -> dict[int, list[dict[str, Any]]]:
    """For each runner: horses in this field that beat it narrowly last time
    they met, and what has moved since between the two of them."""
    date, base, jockeys = race["race_date"], race["j_base"], race["jockeys"]
    floor = (dt.date.fromisoformat(date) - dt.timedelta(days=REVERSAL_DAYS)).isoformat()
    runs = {r["horse_name"]: {(h["race_date"], h["race_no"]): h
                              for h in r["history"] if h["race_date"] >= floor}
            for r in runners}
    out: dict[int, list[dict[str, Any]]] = {}
    for x in runners:
        for y in runners:
            if x is y:
                continue
            shared = sorted(set(runs[x["horse_name"]]) & set(runs[y["horse_name"]]),
                            reverse=True)
            if not shared:
                continue
            hx, hy = runs[x["horse_name"]][shared[0]], runs[y["horse_name"]][shared[0]]
            if not (hx["place"] and hy["place"] and hy["place"] < hx["place"]):
                continue
            margin = (hx["lengths_behind"] or 0.0) - (hy["lengths_behind"] or 0.0)
            if margin > REVERSAL_MARGIN:
                continue
            moved = []
            if None not in (x["draw"], y["draw"], hx["draw"], hy["draw"]):
                swing = (hx["draw"] - hy["draw"]) - (x["draw"] - y["draw"])
                if swing >= 3:
                    moved.append(f"its draw is {swing} gates better against "
                                 f"#{y['horse_no']}'s")
            jswing = ((_jrate(jockeys, x["jockey"], base) - _jrate(jockeys, y["jockey"], base))
                      - (_jrate(jockeys, hx["jockey"], base) - _jrate(jockeys, hy["jockey"], base)))
            if jswing >= 0.02:
                moved.append(f"its rider is {100 * jswing:.0f} pts of strike rate "
                             f"better against #{y['horse_no']}'s")
            repeat = next(p for m, p in _REPEAT if margin <= m)
            # Under a tenth of a length is a nose or a head: HKJC's own words
            # read better than "0.05L", and a dead heat is not "beaten".
            by = ("a short margin" if margin < 0.1 else f"{margin:.2f}L")
            out.setdefault(x["horse_no"], []).append({
                "vs_no": y["horse_no"], "vs_name": y["horse_name"],
                "met": shared[0][0], "margin": round(margin, 2),
                "then": f"{hx['place']} v {hy['place']}",
                "moved": moved, "repeat_pct": repeat, "reverse_pct": 100 - repeat,
                # Said from the side of the horse that was beaten, which is
                # the one this note is about: "repeat 51%" was the same fact
                # from the winner's side, and read as the opposite.
                "note": (f"beat it by {by} on {shared[0][0]}, "
                         f"{_nth(hy['place'])} to its {_nth(hx['place'])}; from "
                         f"a margin that close, the horse beaten finished in "
                         f"front next time {100 - repeat}% of the time"),
            })
    for notes in out.values():
        notes.sort(key=lambda n: (-len(n["moved"]), n["margin"]))
        del notes[3:]
    return out


def _nth(n: int) -> str:
    teen = 11 <= n % 100 <= 13
    return f"{n}{'th' if teen else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _pace(runners: list[dict[str, Any]]) -> dict[str, Any]:
    by = {s: [r for r in runners if r["style"] == s]
          for s in ("Leader", "On-Pace", "Midfield", "Closer")}
    leaders = [{"horse_no": r["horse_no"], "horse_name": r["horse_name"],
                "draw": r["draw"]} for r in by["Leader"]]
    # leader_x was the old Screen's multiplier for a lone leader; the model
    # learns that interaction itself (factor review §3.6), so there is no one
    # number to quote. The Briefing omits the figure when it is absent.
    return {"leaders": leaders,
            "counts": {s: len(v) for s, v in by.items()},
            "unknown": sum(1 for r in runners if not r["style"]),
            "leader_x": None}


def _setup(groups: dict[str, float]) -> tuple[float, str, list[dict[str, Any]]]:
    """How much today's circumstances are worth, apart from the horse's form:
    the draw, the weight, the campaign, the trip and track, the pace, and the
    rider against this field's riders. The verdict, and what made it."""
    keys = [k for k in (*_SITUATION, "rider") if k in groups]
    pieces = [{"label": ("Rider against this field's riders" if k == "rider"
                         else k.capitalize()), "x": groups[k]}
              for k in keys if abs(math.log(groups[k])) >= 0.01]
    s = sum(math.log(groups[k]) for k in keys)
    verdict = ("FAVOURABLE" if s >= SETUP_AT else
               "AGAINST" if s <= -SETUP_AT else "NEUTRAL")
    return s, verdict, sorted(pieces, key=lambda p: -abs(math.log(p["x"])))


def _last_start(prev: dict[str, Any] | None) -> dict[str, Any] | None:
    if not prev:
        return None
    return {"race_date": prev["race_date"], "race_no": prev["race_no"],
            "place": prev["place"], "field_size": prev["field_size"],
            "draw": prev["draw"], "jockey": prev["jockey"],
            "venue": prev["venue"], "distance": prev["distance"],
            "race_class": prev["race_class"],
            "tags": sorted(t for t in prev["tags"] if not t.startswith("lane:")),
            "running_comment": prev["running_comment"],
            "incident_comment": prev["incident_comment"]}


def _runner(r: dict, sc: dict | None, book: dict | None, notes: list,
            reversals: list) -> dict[str, Any]:
    groups = (sc or {}).get("groups", {})
    lines = [_group_line(k, x) for k, x in groups.items()
             if k not in _NOT_REASONS and abs(math.log(x)) >= _NAMED_AT]
    setup, verdict, pieces = _setup(groups)
    trial = next((t for t in r["trials"]), None)
    return {
        "horse_no": r["horse_no"], "horse_name": r["horse_name"],
        "draw": r["draw"], "jockey": r["jockey"], "trainer": r["trainer"],
        "rating": r["rating"], "weight": r["actual_weight"], "gear": r["gear"],
        "style": r["style"], "style_runs": r["style_n"],
        "win_pct": sc["model_pct"] if sc else None,
        "place_pct": sc["place_pct"] if sc else None,
        "rider": {"why": None, "x_vs_field": groups.get("rider")},
        "for": sorted((x for x in lines if x["x"] > 1), key=lambda x: -x["x"]),
        "against": sorted((x for x in lines if x["x"] < 1), key=lambda x: x["x"]),
        "setup_x": round(math.exp(setup), 2), "setup": verdict, "setup_parts": pieces,
        "case_x": round(math.exp(sum(math.log(groups[k]) for k in _SITUATION
                                     if k in groups)), 2),
        "flags": (sc or {}).get("flags", []), "assumed": (sc or {}).get("assumed", []),
        "last_start": _last_start(r["prev"]),
        "trial": trial,
        "notes": notes,
        "reversals": reversals,
        "blackbook": book,
        "result": r["place"],
        # Hong Kong starts before this one: the Briefing shows a horse's
        # background for its first few (query/background).
        "starts": len(r["prior_dates"]),
    }


def _book(b: dict[str, Any]) -> dict[str, Any]:
    return {"id": b["id"], "status": b["status"], "confidence": b["confidence"],
            "reasoning": b["reasoning"], "added_date": b["added_date"],
            # A system entry is shown in its own colour until it is adopted
            # (`jobs/auto_book`); the page needs both to tell them apart.
            "origin": b["origin"], "adopted_date": b["adopted_date"],
            "live": bool(b["live_at_race"]),
            "on_conditions": bool(b["on_conditions"]),
            "conditions_text": b["conditions_text"],
            "tags": sorted((b["tag_csv"] or "").split(",")) if b["tag_csv"] else []}


def _race(block: dict, books: dict, notes: dict, scores: dict) -> dict[str, Any]:
    race, runners = block["race"], block["runners"]
    rev = _reversals(runners, race)
    lines = [_runner(r, scores.get((race["race_no"], r["horse_no"])),
                     books.get((race["race_no"], r["horse_no"])),
                     notes.get(r["horse_name"], []), rev.get(r["horse_no"], []))
             for r in runners]
    # Read in order of the chance to place; a runner the model has not scored
    # (a late replacement) goes to the foot, named but not ranked.
    order = sorted(lines, key=lambda x: (x["place_pct"] is None, -(x["place_pct"] or 0)))
    by_win = sorted((x for x in lines if x["win_pct"] is not None), key=lambda x: -x["win_pct"])
    win_pos = {x["horse_no"]: i + 1 for i, x in enumerate(by_win)}
    for i, x in enumerate(order):
        scored = x["place_pct"] is not None
        x["rank"] = i + 1 if scored else None
        x["form_rank"] = win_pos.get(x["horse_no"])
        x["tier"] = ("SHORTLIST" if scored and i < SHORTLIST else
                     "CASE" if scored and x["case_x"] >= math.exp(CASE_AT) else "FIELD")
    return {**{k: race[k] for k in ("race_no", "venue", "course", "surface", "going",
                                     "distance", "race_class", "off_time", "field_size",
                                     "restricted")},
            "run": any(r["place"] is not None for r in runners),
            "scored": any(x["place_pct"] is not None for x in lines),
            "pace": _pace(runners),
            "runners": order}


def meeting(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """The Screen for every race on one date. `races` is empty when no card
    is stored for it; a card the model has not scored has no chances yet."""
    own = conn is None
    conn = conn or get_conn()
    try:
        blocks = gather(date, conn=conn)
        if not blocks:
            return {"race_date": date, "races": []}
        books = {(b["race_no"], b["horse_no"]): _book(b)
                 for b in blackbook_band.declared_on(date, conn=conn)}
        names = [r["horse_name"] for b in blocks for r in b["runners"]]
        notes = notes_for_horses(names, conn=conn)
        scores = gbm_q.scores(date, conn=conn)
        races = [_race(b, books, notes, scores) for b in blocks]
        version = next((x["model_version"] for x in scores.values()), None)
        fit = gbm_q.version_fit(conn, version)
    finally:
        if own:
            conn.close()
    return {
        "race_date": date, "version": version,
        "fit": {**fit, "shortlist": SHORTLIST},
        "factors": [{"key": k, "group": k, "label": k.capitalize(), "x": None,
                     "runs": None, "caveat": None} for k in gbm_q.GROUPS],
        "races": races,
    }

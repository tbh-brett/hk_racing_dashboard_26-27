"""The Screen for one meeting — the Briefing's first section.

Per race: every runner's chance to win and to place before any price exists
(`model/screen`), the measured reasons for and against it, and the four the
Screen would look at first. Around that, the manual layer the dashboard
already holds and nobody should have to go looking for: the blackbook entry
and whether today's set-up suits it, the owner's own run and trial notes, the
last start in HKJC's words, and the horses in the field it could turn around.

THE SHORTLIST IS FOUR, and the page says why: walk-forward over four seasons
the Screen's top four held the winner in `FIT["top4_has_winner"]` of races,
against the closing market's top four in its own figure beside it. It is a
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
from hkrd.query.formguide import notes_for_horses
from hkrd.query.screen_inputs import gather
from hkrd.store.connect import Connection, get_conn

__all__ = ["meeting", "SHORTLIST", "CASE_AT", "SETUP_AT", "REVERSAL_MARGIN"]

SHORTLIST = 4
# Outside the shortlist, a horse "has a case" when its circumstances --
# everything except the form rating and the rider -- are worth x1.2 or more.
CASE_AT = 0.18
# The blackbook set-up verdict: conditions worth x1.16 either way.
SETUP_AT = 0.15
# A reversal is only worth naming when the last margin was this close.
REVERSAL_MARGIN = 2.0
REVERSAL_DAYS = 365
# What each margin band repeated at, measured (see module docstring).
_REPEAT = ((1.0, 51), (2.0, 55))
_SITUATION = {"PACE", "CHANGE", "CAMPAIGN", "TRIAL"}


def _factor_line(key: str, value: float, why: str | None) -> dict[str, Any]:
    f = model.BY_KEY[key]
    return {"key": key, "group": f.group, "label": f.label, "why": why,
            "x": round(f.multiplier ** value, 2), "caveat": f.caveat}


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
                    moved.append(f"draw {swing} gates better relative to it")
            jswing = ((_jrate(jockeys, x["jockey"], base) - _jrate(jockeys, y["jockey"], base))
                      - (_jrate(jockeys, hx["jockey"], base) - _jrate(jockeys, hy["jockey"], base)))
            if jswing >= 0.02:
                moved.append(f"rider {100 * jswing:.0f} pts better relative to it")
            repeat = next(p for m, p in _REPEAT if margin <= m)
            # Under a tenth of a length is a nose or a head: HKJC's own words
            # read better than "0.05L", and a dead heat is not "beaten".
            by = ("a short margin" if margin < 0.1 else f"{margin:.2f}L")
            out.setdefault(x["horse_no"], []).append({
                "vs_no": y["horse_no"], "vs_name": y["horse_name"],
                "met": shared[0][0], "margin": round(margin, 2),
                "then": f"{hx['place']} v {hy['place']}",
                "moved": moved,
                "note": (f"beaten {by} by #{y['horse_no']} on {shared[0][0]}; "
                         f"margins this close repeat {repeat}% of the time"),
            })
    for notes in out.values():
        notes.sort(key=lambda n: (-len(n["moved"]), n["margin"]))
        del notes[3:]
    return out


def _pace(runners: list[dict[str, Any]]) -> dict[str, Any]:
    by = {s: [r for r in runners if r["style"] == s]
          for s in ("Leader", "On-Pace", "Midfield", "Closer")}
    leaders = [{"horse_no": r["horse_no"], "horse_name": r["horse_name"],
                "draw": r["draw"]} for r in by["Leader"]]
    n = len(leaders)
    key = "leader_alone" if n == 1 else "leader_pair" if n == 2 else "leader_crowd"
    return {"leaders": leaders,
            "counts": {s: len(v) for s, v in by.items()},
            "unknown": sum(1 for r in runners if not r["style"]),
            "leader_x": round(model.BY_KEY[key].multiplier, 2) if n else None}


def _setup(parts: dict[str, float], rider_vs_field: float
           ) -> tuple[float, str, list[dict[str, Any]]]:
    """How much today's circumstances are worth, apart from the horse's form:
    the pace, the campaign, a trial, the changes since last start, and the
    rider against this field's riders. The verdict, and what made it."""
    pieces = [{"label": model.BY_KEY[k].label, "x": round(math.exp(v), 2)}
              for k, v in parts.items() if model.BY_KEY[k].group in _SITUATION]
    if abs(rider_vs_field) >= 0.01:
        pieces.append({"label": "Rider against this field's riders",
                       "x": round(math.exp(rider_vs_field), 2)})
    s = sum(v for k, v in parts.items() if model.BY_KEY[k].group in _SITUATION)
    s += rider_vs_field
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


def _runner(r: dict, sc: dict, race: dict, book: dict | None, notes: list,
            reversals: list, rider_mean: float) -> dict[str, Any]:
    shown = [(k, v) for k, v in sc["values"].items()
             if v and model.BY_KEY[k].shown and k not in ("form", "jockey")]
    lines = [_factor_line(k, v, sc["why"].get(k)) for k, v in shown]
    rider_vs = sc["parts"].get("jockey", 0.0) - rider_mean
    setup, verdict, pieces = _setup(sc["parts"], rider_vs)
    trial = next((t for t in r["trials"]), None)
    return {
        "horse_no": r["horse_no"], "horse_name": r["horse_name"],
        "draw": r["draw"], "jockey": r["jockey"], "trainer": r["trainer"],
        "rating": r["rating"], "weight": r["actual_weight"], "gear": r["gear"],
        "style": r["style"], "style_runs": r["style_n"],
        "sarr_rank": r["sarr_rank"],
        "win_pct": round(100 * sc["win"], 1), "place_pct": round(100 * sc["place"], 1),
        "rider": {"why": sc["why"].get("jockey"),
                  "x_vs_field": round(math.exp(rider_vs), 2)},
        "for": sorted((x for x in lines if x["x"] > 1), key=lambda x: -x["x"]),
        "against": sorted((x for x in lines if x["x"] < 1), key=lambda x: x["x"]),
        "setup_x": round(math.exp(setup), 2), "setup": verdict, "setup_parts": pieces,
        "case_x": round(math.exp(sum(
            v for k, v in sc["parts"].items() if model.BY_KEY[k].group in _SITUATION)), 2),
        "last_start": _last_start(r["prev"]),
        "trial": trial,
        "notes": notes,
        "reversals": reversals,
        "blackbook": book,
        "result": r["place"],
    }


def _book(b: dict[str, Any]) -> dict[str, Any]:
    return {"id": b["id"], "status": b["status"], "confidence": b["confidence"],
            "reasoning": b["reasoning"], "added_date": b["added_date"],
            "live": bool(b["live_at_race"]),
            "on_conditions": bool(b["on_conditions"]),
            "conditions_text": b["conditions_text"],
            "tags": sorted((b["tag_csv"] or "").split(",")) if b["tag_csv"] else []}


def _race(block: dict, books: dict, notes: dict) -> dict[str, Any]:
    race, runners = block["race"], block["runners"]
    scored = model.score_race(race, runners)
    rev = _reversals(runners, race)
    rider_mean = sum(s["parts"].get("jockey", 0.0) for s in scored) / len(scored)
    lines = [_runner(r, s, race, books.get((race["race_no"], r["horse_no"])),
                     notes.get(r["horse_name"], []), rev.get(r["horse_no"], []),
                     rider_mean) for r, s in zip(runners, scored)]
    order = sorted(lines, key=lambda x: -x["place_pct"])
    form_order = sorted((x for x in lines if x["sarr_rank"]), key=lambda x: x["sarr_rank"])
    form_pos = {x["horse_no"]: i + 1 for i, x in enumerate(form_order)}
    for i, x in enumerate(order):
        x["rank"] = i + 1
        x["form_rank"] = form_pos.get(x["horse_no"])
        x["tier"] = ("SHORTLIST" if i < SHORTLIST else
                     "CASE" if x["case_x"] >= math.exp(CASE_AT) else "FIELD")
    return {**{k: race[k] for k in ("race_no", "venue", "course", "surface", "going",
                                     "distance", "race_class", "off_time", "field_size",
                                     "restricted")},
            "run": any(r["place"] is not None for r in runners),
            "pace": _pace(runners),
            "runners": order}


def meeting(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """The Screen for every race on one date. `races` is empty when no card
    is stored for it."""
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
        races = [_race(b, books, notes) for b in blocks]
    finally:
        if own:
            conn.close()
    return {
        "race_date": date, "version": model.VERSION,
        "fit": {**model.FIT, "shortlist": SHORTLIST},
        "factors": [{"key": f.key, "group": f.group, "label": f.label,
                     "x": round(f.multiplier, 2), "runs": f.runs, "caveat": f.caveat}
                    for f in model.FACTORS if f.shown],
        "races": races,
    }

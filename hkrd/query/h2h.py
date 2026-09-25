"""Runners in today's field that have met before, and whether it could turn.

Race Day's HEAD TO HEAD band and the runner panel under it. Carved out of
`query/raceday`, which asked `formguide.head_to_head` once per pair -- 66 to
91 queries a card. This reads the field's history once and pairs it here.

WHAT MOVES A REMATCH, measured over 91,856 pairs meeting again within a year
(docs/screen.md): the earlier order repeats 58% of the time. The margin
decides most of it -- a length or less repeats 51%, a coin toss; eight or
more, 68%. The beaten horse drawn 6+ gates better relatively: 52%. On a
rider 5+ points of strike rate better relatively: 50%, where the form rating
alone expects 56%.

THE WEIGHT SWING IS NOT ONE OF THEM, and this band used to be sorted by it
and badged at 4, 6 and 8lb. In a handicap the weight follows the rating, so
the horse a swing goes against is usually the one that has been winning:
pairs where the beaten horse is now 8lb+ WORSE off repeated 49% of the time,
against 61-62% where it is 8lb+ better off. The weights are still shown -- a
card without them would be missing a fact -- but they neither sort the band
nor earn a mark.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.model.screen import jockey_rate
from hkrd.query.screen_inputs import jockey_year
from hkrd.store.connect import Connection

__all__ = ["pairs", "REPEAT_BY_MARGIN", "CLOSE_MARGIN", "DRAW_SWING",
           "RIDER_SWING", "RECENT_DAYS"]

# The measured repeat rate by the margin between them last time.
REPEAT_BY_MARGIN = ((1.0, 51), (2.0, 55), (4.0, 59), (8.0, 65), (float("inf"), 68))
CLOSE_MARGIN = 2.0         # lengths: inside this a rematch is close to open
DRAW_SWING = 3             # gates, relative, in the beaten horse's favour
RIDER_SWING = 0.02         # a year's win rate, relative, in its favour
RECENT_DAYS = 365          # the measurements are for pairs meeting within a year


def _repeat(margin: float) -> int:
    return next(p for m, p in REPEAT_BY_MARGIN if margin <= m)


def _swing_favours(a, b, gap_then: int | None, gap_now: int | None) -> dict[str, Any]:
    """Which horse the weight change favours, and by how much. Context only."""
    if gap_then is None or gap_now is None:
        return {"favours_no": None, "favours_name": None, "favours_lb": None}
    move = gap_now - gap_then
    if move == 0:
        return {"favours_no": None, "favours_name": None, "favours_lb": 0}
    better = b if move > 0 else a
    return {"favours_no": better.horse_no, "favours_name": better.horse_name,
            "favours_lb": abs(move)}


def pairs(conn: Connection, date: str, runners, *, limit: int = 40
          ) -> list[dict[str, Any]]:
    """Every pair in the field that has met, most open rematch first."""
    names = sorted({r.horse_name for r in runners})
    if len(names) < 2:
        return []
    marks = ",".join("?" * len(names))
    runs: dict[tuple[str, int], dict[str, Any]] = {}
    for row in conn.execute(f"""
            SELECT r.horse_name, r.race_date, r.race_no, r.place, r.lengths_behind,
                   r.draw, r.actual_weight, r.jockey, a.distance, a.going
              FROM runners r
              JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
             WHERE r.horse_name IN ({marks}) AND r.race_date < ? AND r.place IS NOT NULL
            """, [*names, date]):
        runs.setdefault((row["race_date"], row["race_no"]), {})[row["horse_name"]] = dict(row)
    jockeys, base = jockey_year(conn, date)

    def rate(name: str | None) -> float:
        wins, rides = jockeys.get(name, (0, 0))
        return jockey_rate(wins, rides, base)

    recent = (dt.date.fromisoformat(date) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    out: list[dict[str, Any]] = []
    for i, a in enumerate(runners):
        for b in runners[i + 1:]:
            met = sorted((k for k, v in runs.items()
                          if a.horse_name in v and b.horse_name in v), reverse=True)
            if not met:
                continue
            last = runs[met[0]]
            la, lb = last[a.horse_name], last[b.horse_name]
            ahead_a = sum(runs[k][a.horse_name]["place"] < runs[k][b.horse_name]["place"]
                          for k in met)
            ahead_b = sum(runs[k][b.horse_name]["place"] < runs[k][a.horse_name]["place"]
                          for k in met)
            gap_then = (la["actual_weight"] - lb["actual_weight"]
                        if la["actual_weight"] and lb["actual_weight"] else None)
            gap_now = (a.actual_weight - b.actual_weight
                       if a.actual_weight and b.actual_weight else None)
            out.append({
                "a_no": a.horse_no, "a_name": a.horse_name,
                "b_no": b.horse_no, "b_name": b.horse_name,
                "record": f"{ahead_a}-{ahead_b}", "meetings": len(met),
                "last_date": met[0][0],
                "last_cond": f"{la['distance']}m {la['going']}",
                "a_place": la["place"], "b_place": lb["place"],
                "a_weight_then": la["actual_weight"], "b_weight_then": lb["actual_weight"],
                "gap_then": gap_then, "gap_now": gap_now,
                "swing": (abs(gap_now - gap_then)
                          if gap_then is not None and gap_now is not None else None),
                **_swing_favours(a, b, gap_then, gap_now),
                "a_gate_then": la["draw"], "a_gate_now": a.draw,
                "b_gate_then": lb["draw"], "b_gate_now": b.draw,
                **_rematch(a, b, la, lb, rate, recent=met[0][0] >= recent),
            })
    out.sort(key=lambda p: (-p["turn_level"], p["margin"] if p["margin"] is not None
                            else 99.0, -p["meetings"]))
    return out[:limit]


def _rematch(a, b, la, lb, rate, *, recent: bool) -> dict[str, Any]:
    """Who was beaten last time, by how much, and what has moved since in its
    favour -- the draw and the rider, never the weight."""
    if la["place"] == lb["place"]:
        return {"beaten_no": None, "beaten_name": None, "margin": 0.0,
                "repeat_pct": None, "draw_swing": None, "rider_swing": None,
                "turn": [], "turn_level": 0}
    # x beat y last time; everything below is from y's side.
    (x, lx), (y, ly) = ((a, la), (b, lb)) if la["place"] < lb["place"] else ((b, lb), (a, la))
    margin = max(0.0, (ly["lengths_behind"] or 0.0) - (lx["lengths_behind"] or 0.0))
    draw = (None if None in (lx["draw"], ly["draw"], x.draw, y.draw)
            else (ly["draw"] - lx["draw"]) - (y.draw - x.draw))
    rider = ((rate(y.jockey) - rate(x.jockey)) - (rate(ly["jockey"]) - rate(lx["jockey"])))
    turn = []
    if draw is not None and draw >= DRAW_SWING:
        turn.append(f"drawn {draw} gates better relatively")
    if rider >= RIDER_SWING:
        turn.append(f"rider {100 * rider:.0f} pts better relatively")
    close = recent and margin <= CLOSE_MARGIN
    return {"beaten_no": y.horse_no, "beaten_name": y.horse_name,
            "margin": round(margin, 2), "repeat_pct": _repeat(margin),
            "draw_swing": draw, "rider_swing": round(rider, 3),
            "turn": turn, "turn_level": (1 + len(turn)) if close else 0}

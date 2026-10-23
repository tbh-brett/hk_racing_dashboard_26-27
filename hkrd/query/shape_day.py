"""A race day as it is being run: what its finished races say, beside the record (Speed Map).

For each race with a result: the first three, where each was at the first call (HKJC's running
positions), its draw, and Brett's read of its lane in the straight. For the day: how often the
winner came from the front third against what each race's simulation expected, the placed horses'
lanes against the course's usual mix, and the day's bias fitted on its finished races (front,
inside, rail; model/day_bias), in lengths, with its error.

THE RECORD comes with it, because it decides how to read the rest: on the archive the bias of a
meeting's early races has not carried to its later ones beyond the price (jobs/fit_day_bias).
Nothing here moves a simulation; the page's scenario sliders do, only when Brett sets them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hkrd.model.day_bias import fit, logit_to_lengths
from hkrd.store import day as store
from hkrd.store.connect import Connection, get_conn

__all__ = ["day", "SIGMA_LENGTHS"]

# The simulator's finishing noise (sim_tables.noise.sd_lengths x 0.95, ~3.8 lengths on 25 Oct):
# what turns a coefficient on the winner's log odds into lengths at the post.
SIGMA_LENGTHS = 3.8


def _first_call(rp: str | None) -> int | None:
    pos = [int(p) for p in (rp or "").split() if p.isdigit()]
    return pos[0] if pos else None


def day(date: str, *, conn: Connection | None = None) -> dict:
    conn = conn or get_conn()
    m = store.meeting(conn, date)
    reads = store.lanes(conn, date)
    sims = store.race_sims(conn, date)
    record = store.latest_record(conn)
    field: dict[int, list[dict]] = {}
    for u in m["runners"]:
        field.setdefault(u["race_no"], []).append(u)
    races, rows = [], []
    tally = dict(races=0, winner_front_third=0, expected_front_third=0.0, expected_races=0,
                 placed_lanes={"rail": 0, "middle": 0, "outside": 0})
    venue = m["races"][0]["venue"] if m["races"] else None
    for r in m["races"]:
        no = r["race_no"]
        item = dict(no=no, off=r["off_time"], distance=r["distance"],
                    status="result" if r["has_result"] else "off" if r["gone"] else "to run")
        if r["has_result"]:
            ran = [u for u in field.get(no, []) if u["place"] is not None or (u["win_odds"] or 0) > 0]
            n = len(ran)
            fc = {u["horse_no"]: _first_call(u["running_positions"]) for u in ran}
            share = {h: (p - 1) / max(1, n - 1) for h, p in fc.items() if p is not None}
            placed = sorted((u for u in ran if u["place"] in (1, 2, 3)), key=lambda u: u["place"])
            item["placed"] = [dict(place=u["place"], no=u["horse_no"], name=u["horse_name"], draw=u["draw"],
                                   first_call=fc[u["horse_no"]], lane=reads.get((no, u["horse_no"])))
                              for u in placed]
            winner = next((u["horse_no"] for u in placed if u["place"] == 1), None)
            if winner is not None and winner in share:
                tally["races"] += 1
                tally["winner_front_third"] += int(share[winner] < 1 / 3)
                s = sims.get(no)
                if s:
                    exp = sum(v["sim_win"] * v["p_front_third"] for v in s.values() if v["sim_win"] is not None)
                    item["expected_winner_front_third"] = round(exp, 3)
                    tally["expected_front_third"] += exp
                    tally["expected_races"] += 1
            for u in placed:
                lane = reads.get((no, u["horse_no"]))
                if lane:
                    tally["placed_lanes"][lane] += 1
            odds = [u["win_odds"] for u in ran]
            if winner is not None and all(o and o > 0 for o in odds) and len(share) == n:
                inv = sum(1 / o for o in odds)
                for u in ran:
                    lane = reads.get((no, u["horse_no"]))
                    rows.append(dict(race_id=no, won=float(u["horse_no"] == winner), p_mkt=(1 / u["win_odds"]) / inv,
                                     front=1 - share[u["horse_no"]],
                                     inside=1 - ((u["draw"] or 1) - 1) / max(1, n - 1),
                                     rail_obs=np.nan if lane is None else float(lane == "rail")))
        races.append(item)
    tally["expected_front_third"] = round(tally["expected_front_third"], 2)
    return dict(date=date, venue=venue, races=races, tally=tally, read=_read(rows), record=record,
                usual=(record or {}).get("base_rates", {}).get(venue) if venue else None)


def _read(rows: list[dict]) -> dict | None:
    """The day's bias on its finished races, shrunk towards none (a handful of races is mostly
    noise), each in lengths: front = leading against last, inside = gate one against the widest,
    rail = on the rail against off it, where Brett has read the lanes."""
    if not rows:
        return None
    d = pd.DataFrame(rows)
    n_races = d.race_id.nunique()
    if n_races < 2:
        return dict(races=n_races, note="one finished race is not a read")
    cols = ["front", "inside"]
    if d.rail_obs.notna().any():
        d["rail"] = d.rail_obs.fillna(d.groupby("race_id").rail_obs.transform("mean")).fillna(0.0)
        cols.append("rail")
    beta, se = fit(d, cols, ridge=1.0)
    return dict(races=n_races, **{c: dict(beta=round(float(b), 3), se=round(float(s), 3),
                                          lengths=round(logit_to_lengths(b, SIGMA_LENGTHS), 2),
                                          lengths_se=round(logit_to_lengths(s, SIGMA_LENGTHS), 2))
                                  for c, b, s in zip(cols, beta, se)})

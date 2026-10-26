"""A race day as it is being run: what its finished races say, beside the record (Speed Map).

For each race with a result: the first three, where each was at the first call (HKJC's running
positions), its draw, and Brett's read of its lane in the straight. For the day: how often the
winner came from the front third against what each race's simulation expected, the placed horses'
lanes against the course's usual mix, and the day's bias fitted on its finished races (front,
inside, rail; model/day_bias), in lengths, with its error.

THE RECORD comes with it, because it decides how to read the rest: on the archive the bias of a
meeting's early races has not carried to its later ones beyond the price (jobs/fit_day_bias).
Nothing here moves a simulation; the page's scenario sliders do, only when Brett sets them.

Each SURFACE is read on its own (26 Oct): an AWT race says nothing of the turf the same night, so
a card's AWT and turf races get a read, a tally and a record each, and a read is in the lengths of
its own races' finishing spread (the simulator's, by trip group: about 5 on the AWT, 4 on turf).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hkrd.model.day_bias import fit, logit_to_lengths
from hkrd.model.sim_tables import cost_group
from hkrd.query import shape_sim
from hkrd.store import day as store
from hkrd.store.connect import Connection, get_conn

__all__ = ["day", "SIGMA_LENGTHS"]

# The simulator's pooled finishing noise (sim_tables.noise.sd_lengths x 0.95, ~3.8 lengths on 25 Oct):
# what turns a coefficient on the winner's log odds into lengths at the post where a card has no
# tables of its own to give its trips' spreads.
SIGMA_LENGTHS = 3.8
_ENGINE_TRIM = 0.95          # the engine runs on 0.95 x the tables' spread (sim-core.js `context`)


def _first_call(rp: str | None) -> int | None:
    pos = [int(p) for p in (rp or "").split() if p.isdigit()]
    return pos[0] if pos else None


def _sigma(noise: dict | None, group: str) -> float:
    """A race's finishing spread in the engine's lengths: its trip group's, else the pooled one."""
    if not noise:
        return SIGMA_LENGTHS
    own = (noise.get("by_group") or {}).get(group)
    return _ENGINE_TRIM * float(own["sd_lengths"] if own else noise["sd_lengths"])


def _tally() -> dict:
    return dict(races=0, winner_front_third=0, expected_front_third=0.0, expected_races=0,
                placed_lanes={"rail": 0, "middle": 0, "outside": 0})


def day(date: str, *, conn: Connection | None = None) -> dict:
    conn = conn or get_conn()
    m = store.meeting(conn, date)
    reads = store.lanes(conn, date)
    sims = store.race_sims(conn, date)
    record = store.latest_record(conn)
    noise = shape_sim.noise(conn, date)
    field: dict[int, list[dict]] = {}
    for u in m["runners"]:
        field.setdefault(u["race_no"], []).append(u)
    races: list[dict] = []
    tallies: dict[str, dict] = {}
    rows: dict[str, list[dict]] = {}
    venue = m["races"][0]["venue"] if m["races"] else None
    for r in m["races"]:
        no, surface = r["race_no"], r["surface"] or "Turf"
        sigma = _sigma(noise, cost_group(r["venue"], surface, int(r["distance"] or 0)))
        tally = tallies.setdefault(surface, _tally())
        item = dict(no=no, off=r["off_time"], distance=r["distance"], surface=surface,
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
                    rows.setdefault(surface, []).append(dict(
                        race_id=no, won=float(u["horse_no"] == winner), p_mkt=(1 / u["win_odds"]) / inv,
                        front=1 - share[u["horse_no"]], inside=1 - ((u["draw"] or 1) - 1) / max(1, n - 1),
                        rail_obs=np.nan if lane is None else float(lane == "rail"), sigma=sigma))
        races.append(item)
    surfaces = []
    for surface, tally in tallies.items():
        tally["expected_front_third"] = round(tally["expected_front_third"], 2)
        surfaces.append(dict(surface=surface, races=sum(1 for x in races if x["surface"] == surface), tally=tally,
                             read=_read(rows.get(surface, [])), record=_record(record, surface),
                             usual=_usual(record, venue, surface)))
    return dict(date=date, venue=venue, races=races, surfaces=surfaces, record=record)


def _record(record: dict | None, surface: str) -> dict | None:
    """The record a surface's read is set against: the AWT's own where it has one; the archive's
    whole (its meetings are mostly turf) otherwise."""
    if not record:
        return None
    own = (record.get("by_surface") or {}).get(surface)
    if own:
        return dict(own, seasons=record.get("seasons"), surface=surface)
    return dict(persistence=record["persistence"], prediction=record["prediction"],
                detectable_lengths=record.get("detectable_lengths"), seasons=record.get("seasons"), surface="all")


def _usual(record: dict | None, venue: str | None, surface: str) -> dict | None:
    base = (record or {}).get("base_rates") or {}
    return base.get(f"{venue} {surface}") or base.get(venue or "")


def _read(rows: list[dict]) -> dict | None:
    """The day's bias on one surface's finished races, shrunk towards none (a handful of races is
    mostly noise), each in lengths of those races' own finishing spread: front = leading against
    last, inside = gate one against the widest, rail = on the rail against off it, where Brett has
    read the lanes."""
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
    sigma = float(d.groupby("race_id").sigma.first().mean())
    return dict(races=n_races, sigma=round(sigma, 2),
                **{c: dict(beta=round(float(b), 3), se=round(float(s), 3),
                           lengths=round(logit_to_lengths(b, sigma), 2),
                           lengths_se=round(logit_to_lengths(s, sigma), 2))
                   for c, b, s in zip(cols, beta, se)})

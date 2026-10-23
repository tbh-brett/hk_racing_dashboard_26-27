"""The race simulation's routes (racesim-SPEC §8). Reads only: every figure was computed by
jobs/sim_card, one race every fifteen minutes from cron. No request ever simulates -- Fly's CPU
quota is the reason, and the reason is in ops/crontab."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import shape_record as record_q
from hkrd.query import shape_sim as sim_q

router = APIRouter()


@router.get("/api/shape/sim/{date}")
def sim_card(date: str) -> dict:
    """A meeting's races: simulated or not, changed since or not, the pace mix, the likeliest leader."""
    out = sim_q.card(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out


@router.get("/api/shape/sim/{date}/{race_no}")
def sim_race(date: str, race_no: int) -> dict:
    """One race's newest simulation, with what the browser needs to draw a run of it."""
    out = sim_q.race(date, race_no)
    if out is None:
        raise HTTPException(404, f"{date} race {race_no} has not been simulated: jobs.sim_card simulates "
                                 "one race every fifteen minutes once its v2 and shape scores are in")
    return out


@router.get("/api/shape/record")
def sim_record() -> dict:
    """Each meeting's simulations as they stood at the off, scored once the race was run."""
    return record_q.record()

"""The race simulation's routes (racesim-SPEC §8). Reads only: every figure was computed by
jobs/sim_card, one race every fifteen minutes from cron. No request ever simulates -- Fly's CPU
quota is the reason, and the reason is in ops/crontab."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import shape_day as day_q
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


@router.get("/api/shape/day/{date}")
def day(date: str) -> dict:
    """A race day as it is being run: its finished races' first three, the day's read and the
    record that says how far to trust it."""
    out = day_q.day(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out


@router.post("/api/shape/day/lane")
def save_lane(body: dict = Body(...)) -> dict:
    """Brett's read of where a placed horse came in the straight: rail, middle or outside; an empty
    lane takes it back."""
    from hkrd.jobs import save_day_lanes
    try:
        return save_day_lanes.save(body["race_date"], int(body["race_no"]), int(body["horse_no"]),
                                   body.get("lane") or None)
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/shape/day/clear")
def clear_race(body: dict = Body(...)) -> dict:
    """Every lane read of one race, removed."""
    from hkrd.jobs import save_day_lanes
    try:
        return save_day_lanes.clear(body["race_date"], int(body["race_no"]))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc


@router.post("/api/shape/day/{date}/fetch")
def fetch_results(date: str) -> dict:
    """The meeting's results so far: one HKJC page for each race whose pool has shut and has no
    result stored, kept only when its runners are the declared field."""
    from hkrd.jobs import fetch_run_races
    r = fetch_run_races.fetch(date)
    return dict(stored=r.stored, waiting=r.waiting, errors=r.errors, summary=r.render())

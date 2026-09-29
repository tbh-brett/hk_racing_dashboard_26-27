"""The fundamental model's routes. Reads only: every figure was computed when
the card was scored (jobs/score_gbm) or the model was fitted (jobs/fit_gbm);
no request ever loads the model (gbm-SPEC §6)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import gbm as gbm_q

router = APIRouter()


@router.get("/api/model/gbm/record")
def record() -> dict:
    """The live model's walk-forward record, its per-meeting record, which fit
    is live and what the gate said about the newest one."""
    out = gbm_q.record()
    if out is None:
        raise HTTPException(404, "no model has been promoted yet: run jobs.fit_gbm")
    return out


@router.get("/api/model/gbm/{date}/{race_no}")
def race(date: str, race_no: int) -> dict:
    """Each runner's model chance beside the latest market, the gap between
    them (shown, never a recommendation), the factor groups and the flags."""
    out = gbm_q.race(date, race_no)
    if not out["runners"]:
        raise HTTPException(404, f"the model has not scored {date} race {race_no}")
    return out

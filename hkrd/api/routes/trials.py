"""Trials routes — the batches, the feed, and whether the rating holds.

One engine, two surfaces: `/api/trials/horses` is what the Form Guide's inline
band reads, and it returns the same rating the batch and feed views show.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import trials as trials_q

router = APIRouter()


@router.get("/api/trials")
def trials_feed(limit: int = 12, venue: str | None = None,
                date: str | None = None) -> dict:
    """Recent trial batches, each runner rated by the same engine the Form
    Guide's inline band uses. `date` pins the feed to one trial morning."""
    batches = trials_q.recent_batches(limit=limit, venue=venue, date=date)
    return {"batches": batches, "count": len(batches)}


@router.get("/api/trials/days")
def trials_days(limit: int = 60, venue: str | None = None) -> dict:
    """The trial calendar. Trials are held on mornings that are mostly not race
    days, so the meeting in the header cannot address them."""
    rows = trials_q.days(limit=limit, venue=venue)
    return {"days": rows, "count": len(rows)}


@router.get("/api/trials/standouts")
def trials_standouts(days: int = 21, limit: int = 40) -> dict:
    """The live feed: the same rating every trial gets, filtered. Not a list
    anyone maintains by hand."""
    return trials_q.standouts(days=days, limit=limit)


@router.get("/api/trials/calibration")
def trials_calibration() -> dict:
    """What each band actually went on to do at the races. The rating is only
    worth showing if the bands separate, so the page prints this beside them."""
    return trials_q.calibration()


@router.get("/api/trials/batch/{date}/{trial_no}")
def trials_batch(date: str, trial_no: int) -> dict:
    body = trials_q.batch(date, trial_no)
    if not body:
        raise HTTPException(404, f"no trial {date} T{trial_no}")
    return body


@router.get("/api/trials/horses")
def trials_for_horses(horses: str, before: str | None = None,
                      limit: int = 2) -> dict:
    """Each named horse's most recent trials — the Form Guide's inline band.

    `before` keeps it honest on a past race: a trial run after the race being
    reviewed was not available when the race was run.
    """
    names = [h.strip() for h in horses.split(",") if h.strip()]
    return {"trials": trials_q.for_horses(names, before=before, limit=limit)}

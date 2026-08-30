"""Results routes — a finished race, assembled once.

A race with no finishing positions comes back `run: false` and empty rather
than as a race in which nobody finished. The two are not the same thing and
the page must not render one as the other.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import results as results_q

router = APIRouter()


@router.get("/api/results/{date}")
def meeting_results(date: str) -> dict:
    """Every race on the card and whether it has been run."""
    body = results_q.meeting_results(date)
    if not body["races"]:
        raise HTTPException(404, f"no meeting on {date}")
    return body


@router.get("/api/results/{date}/{race_no}")
def race_result(date: str, race_no: int) -> dict:
    """One finished race, with everything the page renders about it.

    A race with no finishing positions comes back `run: false` and empty
    rather than as a race in which nobody finished.
    """
    try:
        return results_q.race_result(date, race_no)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

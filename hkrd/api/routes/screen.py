"""Screen route — who is a chance in each race, before there is a price.

The Briefing's first section. Reads only; see query/screen for what the
Screen weighs and model/screen for how each weight was measured.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import screen as screen_q

router = APIRouter()


@router.get("/api/screen/{date}")
def screen(date: str) -> dict:
    """Every race on one card: each runner's chance to win and place from the
    form rating, the rider and the measured circumstances, the reasons for and
    against, the shortlist of four, and the blackbook, notes and head-to-heads
    beside them."""
    out = screen_q.meeting(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out

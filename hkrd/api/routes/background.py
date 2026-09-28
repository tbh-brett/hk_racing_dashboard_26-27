"""Background routes — where each import came from, and how each kind ran.

The background of a horse on a card travels with it in `/api/briefing` and
`/api/raceday`; this is the record, which the Briefing draws below the
sources' record.
"""
from __future__ import annotations

from fastapi import APIRouter

from hkrd.query import background as background_q

router = APIRouter()


@router.get("/api/background/record")
def record() -> dict:
    """Per agent, trained-in country, import type, sale price and pre-import
    trial: horses, runs, wins, A/E against the closing tote, the median rating
    change and how many rose ten points — horses profiled from 2026/27 on (the
    test) apart from 2025/26 (where the idea came from). See query/background."""
    return background_q.record()

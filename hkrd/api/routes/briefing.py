"""The Briefing: one meeting's card, what was said about it and what it is
priced at, in one answer. See `query/briefing`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from hkrd.query import briefing as briefing_q

router = APIRouter()


@router.get("/api/briefing/{date}")
def briefing(date: str) -> dict[str, Any]:
    out = briefing_q.meeting(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out

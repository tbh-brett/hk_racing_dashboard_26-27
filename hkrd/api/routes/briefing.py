"""The Briefing: one meeting's card, what was said about it and what it is
priced at, in one answer. See `query/briefing`."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException

from hkrd.query import briefing as briefing_q

router = APIRouter()


@router.get("/api/briefing/{date}")
def briefing(date: str, as_of: str | None = None) -> dict[str, Any]:
    """`as_of` (Hong Kong time, YYYY-MM-DDTHH:MM) reads the stage, the clock
    and the minutes to each off as at that moment instead of now — for
    looking back at what the page said, and for checking each stage."""
    try:
        now = dt.datetime.fromisoformat(as_of) if as_of else None
    except ValueError:
        raise HTTPException(422, f"as_of must be YYYY-MM-DDTHH:MM, got {as_of!r}")
    out = briefing_q.meeting(date, now=now)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out

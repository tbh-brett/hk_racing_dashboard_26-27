"""Tips routes — what the connections said, and who tipped what.

The write, the roster the PC-side extractor resolves against, and the meeting
summary the front page reads. The two reads the race card needs
(`GET /api/tips/race` and `/api/tips/card`) arrive with the panels that draw
them; see docs/handover/tips-layer/SPEC.md §8.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import tips as tips_q, tips_summary as tips_summary_q

router = APIRouter()


@router.post("/api/tips/import")
def import_tips(body: dict = Body(...)) -> dict:
    """Store one meeting's payload, pushed from the PC.

    Through a job, because `api/ -> jobs/` is the only write path out of a
    router. 422 names every way the payload broke the contract; a payload
    that is well formed but names runners the card does not have is a 200,
    and those rows are counted under `quarantined` rather than refused —
    that is the resolver reporting on itself, not the request failing.
    """
    from hkrd.jobs import import_tips as job

    try:
        return job.run(body).as_dict()
    except job.PayloadError as exc:
        raise HTTPException(422, f"tips payload rejected — {exc}") from exc


@router.get("/api/tips/summary/{date}")
def summary(date: str) -> dict:
    """One meeting summarised: per race, the horses the most sources back —
    each source's own words under them, jockey interviews first — with the
    HKJC tote and Ladbrokes fixed prices side by side, and the card-wide list
    of runners where one market's price beats the other's fair value.
    See query/tips_summary for what counts as support and how."""
    out = tips_summary_q.summary(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out


@router.get("/api/tips/roster/{date}")
def roster(date: str) -> dict:
    """The declared field for one meeting, with Chinese names — the closed
    list the extractor must pick every runner from."""
    out = tips_q.roster(date)
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out

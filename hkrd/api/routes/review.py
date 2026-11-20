"""Banker review routes -- every horse he backed to carry a ticket, what he said
about it, and what he says decided it (docs/banker-review.md).

Under /api/bets, so the Bets page's own password guards them: these are his
bets and his reasoning. Reads come from `query/banker_review` and
`query/review_record`; the one write goes through `jobs/review_banker`.

ORDER MATTERS: `/summary` is declared before `/{date}`, or a request for the
summary would be read as a meeting dated "summary".
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import banker_review, review_record

router = APIRouter()

# What a save may carry. A field left out is left as it is; an empty one
# clears it.
FIELDS = ("reasoning", "confidence", "picked_by", "verdict", "verdict_note",
          "circumstances")


def _day(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(422, f"date must be YYYY-MM-DD: {value!r}") from exc


@router.get("/api/bets/review")
def meetings(account: str | None = None) -> dict:
    """Every meeting with a horse a ticket could not pay without, newest first,
    with how far its review has got, and the words the form offers."""
    got = banker_review.meetings(account=account)
    if not got:
        raise HTTPException(404, f"no banker on record since {banker_review.SINCE}"
                                 + (f" for {account}" if account else ""))
    return {"meetings": got, "vocabulary": banker_review.vocabulary()}


@router.get("/api/bets/review/summary")
def summary(account: str | None = None) -> dict:
    """What the review adds up to: verdicts, how sure he was against how they
    ran, whose pick, and how often the record names what he blamed."""
    return review_record.summary(account=account)


@router.get("/api/bets/review/{date}")
def meeting(date: str, account: str | None = None) -> dict:
    """One meeting's key horses, each with his claim and verdict, the run, his
    notes from before it, what the stewards' report says, and his notes on
    its races. A card with no banker on it yet answers with an empty list."""
    out = banker_review.meeting(_day(date), account=account)
    if not out["card"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out


@router.post("/api/bets/review/{date}/note")
def note(date: str, body: dict = Body(...)) -> dict:
    """His note on one race (`race_no`), or on the night (`race_no` 0). An
    empty `note` removes it."""
    from hkrd.jobs import review_banker as job

    try:
        race_no = int(body.get("race_no", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "race_no must be a number, 0 for the night") from exc
    if "note" not in body:
        raise HTTPException(422, "missing field: note")
    try:
        got = job.reflect(_day(date), race_no, body["note"])
    except job.NotInRace as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"race_date": date, "race_no": race_no, "note": got}


@router.post("/api/bets/review/{date}/{race_no}/{horse_no}")
def save(date: str, race_no: int, horse_no: int, body: dict = Body(...)) -> dict:
    """Save any of `FIELDS` for one horse; returns its row as the meeting
    gives it. Writes no bet."""
    from hkrd.jobs import review_banker as job

    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        raise HTTPException(422, f"not a review field: {', '.join(unknown)} "
                                 f"(known: {', '.join(FIELDS)})")
    fields = {k: body[k] for k in FIELDS if k in body}
    if not fields:
        raise HTTPException(422, f"nothing to save: send one of {', '.join(FIELDS)}")
    if "circumstances" in fields and not isinstance(fields["circumstances"], list | None):
        raise HTTPException(422, "circumstances must be a list")
    day = _day(date)
    try:
        job.save(day, race_no, horse_no, **fields)
    except job.NotInRace as exc:
        raise HTTPException(404, str(exc)) from exc
    except (job.ClaimClosed, job.NoResult) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:            # ReviewError is a ValueError
        raise HTTPException(422, str(exc)) from exc
    return banker_review.horse(day, race_no, horse_no) or {}

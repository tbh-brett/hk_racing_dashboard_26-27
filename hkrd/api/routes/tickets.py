"""Ticket desk routes -- a banker ticket built around the owner's horse, what he
did with it, and how it turned out.

Reads come from `query/suggest`, `query/ticket_record` and `query/ticket_ledger`;
the one write goes through `jobs/decide_ticket`. Nothing here places a bet.

ORDER MATTERS: `/record`, `/bankers` and `/scorecard` are declared before `/{date}`, or a
request for the record would be read as a card dated "record".
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import banker_record, suggest
from hkrd.query import ticket_ledger, ticket_record

router = APIRouter()


def _day(value: str, name: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(422, f"{name} must be a date, YYYY-MM-DD: {value!r}") from exc


@router.get("/api/tickets/record")
def record(date_from: str | None = None, date_to: str | None = None,
           scope: str = "live", account: str | None = None) -> dict:
    """Every paper ticket and decision in a window, scored at the final
    dividends, and what they add up to. With no dates, from the day the
    engine's rules were written to today."""
    start = _day(date_from, "date_from") if date_from else suggest.ENGINE_FROM
    end = _day(date_to, "date_to") if date_to else max(start, dt.date.today().isoformat())
    if end < start:
        raise HTTPException(422, f"date_to {end} is before date_from {start}")
    try:
        return ticket_record.record(start, end, scope=scope, account=account)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/tickets/bankers")
def bankers(account: str | None = None, since: str | None = None) -> dict:
    """How the ledger's bankers have placed against the price, by the evidence
    the owner had on each: the measurement the desk rests on, read live."""
    if since:
        return ticket_ledger.bankers(account=account, since=_day(since, "since"))
    return ticket_ledger.bankers(account=account)


@router.get("/api/tickets/scorecard")
def scorecard(account: str | None = None) -> dict:
    """The owner's banker scorecard: for each fact knowable before the off,
    how much he leans towards it and how his bankers with it have placed
    against the price, split by whether he had his own evidence; and his
    reasons' record once he taps them on the desk."""
    return banker_record.record(account=account)


@router.get("/api/tickets/{date}")
def meeting(date: str) -> dict:
    """One card: each race's candidate bankers with the ticket the engine
    builds around each from the latest capture."""
    out = suggest.meeting(_day(date, "date"))
    if not out["races"]:
        raise HTTPException(404, f"no card stored for {date}")
    return out


@router.get("/api/tickets/{date}/{race_no}/{banker_no}")
def ticket(date: str, race_no: int, banker_no: int, partners: str | None = None,
           k: int | None = None, unit_stake: float | None = None) -> dict:
    """The ticket for any declared runner as banker. `partners` is a comma
    list of the owner's own; without it the engine takes `k`."""
    try:
        own = ([int(x) for x in partners.split(",") if x.strip()]
               if partners is not None else None)
        return suggest.ticket(_day(date, "date"), race_no, banker_no, k=k,
                              partners=own or None, unit_stake=unit_stake)
    except suggest.NotOnCard as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:                 # TicketError, or a partner not a number
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/tickets/{date}/{race_no}/{banker_no}/decision")
def decide(date: str, race_no: int, banker_no: int, body: dict = Body(...)) -> dict:
    """Record what the owner did with a ticket: `accept` (with `partners` of
    his own, it is stored as edited) or `reject`, with any `reasons` tapped,
    a line of his own in `reason` and how sure he is in `confidence` -- the
    last two are also the horse's claim on the banker review. Writes no bet."""
    from hkrd.jobs import decide_ticket as job

    try:
        partners = body.get("partners")
        return job.decide(
            _day(date, "date"), race_no, banker_no, decision=body["decision"],
            partners=[int(x) for x in partners] if partners else None,
            unit_stake=(float(body["unit_stake"])
                        if body.get("unit_stake") is not None else None),
            account=body.get("account"), reason=body.get("reason"),
            reasons=body.get("reasons") or [], confidence=body.get("confidence"))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except suggest.NotOnCard as exc:
        raise HTTPException(404, str(exc)) from exc
    except job.DecisionClosed as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc

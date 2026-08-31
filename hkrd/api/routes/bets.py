"""Bets routes — entry, the ledger, what it says, and how it reconciles.

Every slice the analysis returns carries n and a 95% interval. Design brief:
"EVERY FIGURE CARRIES n AND AN INTERVAL · A 12-BET SLICE IS NOT A FINDING."

Entry reads through `query/prebet` and writes through `jobs/place_bet` — the
router never reaches into `store/`, and never decides whether a bet may be
placed. A guardrail warns; the job records the override.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import bet_analysis as ba_q, bets as bets_q, prebet

router = APIRouter()


@router.get("/api/bets")
def bets_ledger(date: str | None = None, account: str | None = None,
                limit: int = 500) -> dict:
    rows = bets_q.ledger(date=date, account=account, limit=limit)
    return {"bets": rows, "count": len(rows)}


@router.get("/api/bets/summary")
def bets_summary(account: str | None = None) -> dict:
    return bets_q.summary(account=account)


@router.get("/api/bets/analysis")
def bets_analysis(account: str | None = None) -> dict:
    """Everything the analysis section renders, in one read.

    Every slice carries n and a 95% interval, because the design brief prints
    the rule across the whole section: a 12-bet slice is not a finding.
    """
    return ba_q.analysis(account=account)


@router.get("/api/bets/reconciliation")
def bets_reconciliation(account: str | None = None) -> dict:
    """Imported statement rows against logged bets. Nothing is silently
    merged, so a block the two disagree on is named."""
    return ba_q.reconciliation(account=account)


@router.get("/api/bets/accounts")
def bets_accounts() -> dict:
    """The two accounts and what has been staked through each.

    Design brief 07 §3.1: Brett and Kelvin. Client was removed there, along
    with its read-mostly variant.
    """
    return {"accounts": prebet.accounts()}


@router.get("/api/bets/types")
def bets_types() -> dict:
    """Bet types, and the accounts and stake presets entry offers."""
    return {"types": list(prebet.BET_TYPES),
            "single_race": list(prebet.SINGLE_RACE_TYPES)}


@router.get("/api/bets/raceday/{date}")
def bets_raceday(date: str, account: str | None = None) -> dict:
    """Running total for the meeting against the ceiling. A ceiling warns."""
    return prebet.raceday_total(date, account=account)


@router.get("/api/bets/card/{date}/{race_no}")
def bets_card(date: str, race_no: int) -> dict:
    """The selection table — win and place at equal weight, place never derived.

    Place odds cannot be computed from win odds: there is no fixed relationship
    between them, and the "one third of win" rule of thumb is structurally
    invalid. Every place price here is the scraped number.
    """
    card = prebet.entry_card(date, race_no)
    if not card.get("runners"):
        raise HTTPException(404, f"no runners for {date} race {race_no}")
    return card


@router.post("/api/bets/prebet")
def bets_prebet(body: dict = Body(...)) -> dict:
    """Price a ticket without placing it. Never refuses — says why not."""
    try:
        return prebet.evaluate(
            body["race_date"], bet_type=body["bet_type"],
            race_no=(int(body["race_no"]) if body.get("race_no") is not None
                     else None),
            selections=[int(x) for x in body.get("selections") or []],
            banker=(int(body["banker"]) if body.get("banker") is not None
                    else None),
            unit_stake=float(body.get("unit_stake") or 0),
            legs=body.get("legs") or [],
            legs_required=(int(body["legs_required"])
                           if body.get("legs_required") is not None else None),
            account=body.get("account"))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/bets")
def place_bet(body: dict = Body(...)) -> dict:
    """Write a manually entered bet.

    A fired guardrail never blocks this. `acknowledged` names the flags the user
    chose to go past, and each one is recorded against the bet — that record is
    what makes "which flags did I override, and how did those bets do" a
    question the ledger can answer later.
    """
    from hkrd.jobs import place_bet as job

    try:
        return job.place(
            body["race_date"], bet_type=body["bet_type"],
            account=body["account"],
            race_no=(int(body["race_no"]) if body.get("race_no") is not None
                     else None),
            selections=[int(x) for x in body.get("selections") or []],
            banker=(int(body["banker"]) if body.get("banker") is not None
                    else None),
            unit_stake=float(body.get("unit_stake") or 0),
            legs=body.get("legs") or [],
            legs_required=(int(body["legs_required"])
                           if body.get("legs_required") is not None else None),
            acknowledged=body.get("acknowledged") or [],
            blackbook_entry_id=body.get("blackbook_entry_id"),
            notes=body.get("notes"))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/bets/race/{date}/{race_no}")
def bets_for_race(date: str, race_no: int) -> dict:
    return {"race_date": date, "race_no": race_no,
            "bets": bets_q.bets_for_race(date, race_no)}


@router.get("/api/bets/horse/{name}")
def bets_for_horse(name: str, since: str | None = None) -> dict:
    return {"horse_name": name.upper(),
            "bets": bets_q.bets_for_horse(name, since=since)}

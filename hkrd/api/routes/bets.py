"""Bets routes — the ledger, what it says, and how it reconciles.

Every slice the analysis returns carries n and a 95% interval. Design brief:
"EVERY FIGURE CARRIES n AND AN INTERVAL · A 12-BET SLICE IS NOT A FINDING."
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hkrd.query import bets as bets_q, bet_analysis as ba_q

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


@router.get("/api/bets/race/{date}/{race_no}")
def bets_for_race(date: str, race_no: int) -> dict:
    return {"race_date": date, "race_no": race_no,
            "bets": bets_q.bets_for_race(date, race_no)}


@router.get("/api/bets/horse/{name}")
def bets_for_horse(name: str, since: str | None = None) -> dict:
    return {"horse_name": name.upper(),
            "bets": bets_q.bets_for_horse(name, since=since)}

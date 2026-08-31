"""Blackbook routes — the hypothesis tracker.

The route order matters: `/api/blackbook/tags`, `/summary`, `/backed-vs-missed`
and `/declared/{date}` are declared BEFORE `/{entry_id}`, or the path parameter
swallows them and `tags` is looked up as an entry id.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from hkrd.query import blackbook as bb_q, bets as bets_q

router = APIRouter()


@router.get("/api/blackbook")
def blackbook_list(status: str | None = None, tag: str | None = None) -> dict:
    """The list view. `runs_since` and `record since` are derived from the
    runners table, not from what anyone remembered to log."""
    entries = bb_q.list_entries(status=status, tag=tag)
    return {"entries": entries, "count": len(entries),
            "filters": {"status": status, "tag": tag}}


@router.get("/api/blackbook/tags")
def blackbook_tags() -> dict:
    """Per booking reason: strike, place, ROI and A/E with a 95% interval.

    A/E is the figure that says whether a tag beats the PRICE rather than
    merely wins sometimes, so `cleared` counts the tags whose interval excludes
    1.00 and `expected_by_chance` says how many would at 5%. Publishing both is
    what stops a tag that looks like it is working from reading as one.
    """
    tags = bb_q.tag_performance()
    scored = [t for t in tags if t["ae"] is not None]
    cleared = [t["tag"] for t in scored
               if t["ae_lo"] > 1.0 or t["ae_hi"] < 1.0]
    return {"tags": tags, "scored": len(scored), "cleared": cleared,
            "expected_by_chance": round(len(scored) * 0.05, 1)}


@router.get("/api/blackbook/summary")
def blackbook_summary(today: str | None = None) -> dict:
    """How big the book is, and whether it resolves."""
    return bb_q.book_summary(today=today)


@router.post("/api/blackbook/{entry_id}/status")
def set_blackbook_status(entry_id: str, body: dict = Body(...)) -> dict:
    """Resolve an entry. One call, because a book that only grows is unusable."""
    from hkrd.jobs import write_notes

    try:
        return write_notes.set_status(entry_id, body.get("status", ""))
    except KeyError as exc:
        raise HTTPException(404, f"no blackbook entry {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/blackbook/backed-vs-missed")
def blackbook_backed_vs_missed(entry_id: str | None = None,
                               account: str | None = None) -> dict:
    """What was backed, what was not, and how each did.

    Design brief 06 calls this "the single most important feature on the page":
    without it only the hits are visible. It is a join over the bets ledger, so
    nothing has to be logged by hand.
    """
    return bets_q.backed_and_missed(entry_id=entry_id, account=account)


@router.get("/api/blackbook/by-account")
def blackbook_by_account(entry_id: str | None = None) -> dict:
    """The same comparison per account, and combined.

    One book, two ledgers. The blackbook is shared — a horse is followed for
    what it did, not for whose money is on it — but "was this run backed" has a
    different answer per account, and the difference between them is a finding
    about each book's own discipline rather than about the horses.
    """
    return bets_q.backed_by_account(entry_id=entry_id)


@router.get("/api/blackbook/tags/backed-vs-missed")
def blackbook_tags_backed_vs_missed(account: str | None = None) -> dict:
    """BACKED vs MISSED for each booking reason.

    The artboard puts it beside every tag, and that is where the comparison is
    sharpest: "runs I booked for trip trouble and then did not back" names the
    reason the entry was made, which the whole-book number cannot.
    """
    return {"tags": bets_q.backed_and_missed_by_tag(account=account)}


@router.get("/api/blackbook/declared/{date}")
def blackbook_declared(date: str) -> dict:
    """Booked horses declared across one meeting."""
    rows = bb_q.declared_on(date)
    return {"race_date": date, "entries": rows, "count": len(rows)}


@router.get("/api/blackbook/{entry_id}")
def blackbook_entry(entry_id: str) -> dict:
    entry = bb_q.entry_detail(entry_id)
    if entry is None:
        raise HTTPException(404, f"no blackbook entry {entry_id}")
    entry.update(bb_q.entry_bets(entry_id))
    return entry

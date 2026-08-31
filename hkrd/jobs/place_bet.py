"""Placing a bet — the only write path for a manually entered ticket.

A router may not reach into `store/`, so this job owns the write and its own
defaults, including which database it lands in. It also owns the one rule that
makes the guardrails worth having:

    **A guardrail flags, it never blocks.**

Design brief 06 Part 2 is explicit that any rule the user sets should "warn, not
prevent", and that the override record is the useful part — reviewing which
flags were overridden and how those bets performed is a genuine analysis, and it
is only possible if the override is logged rather than the bet blocked. So a
ticket with flags still gets written; what changes is that each fired flag
writes a row alongside it.

The one thing that IS refused is an incoherent ticket — no selections, no stake,
an invalid formula. That is not a judgement about the bet, it is a ticket that
does not describe anything.
"""
from __future__ import annotations

from typing import Any

from hkrd.query import prebet
from hkrd.store import bets as bet_store
from hkrd.store.connect import get_conn

__all__ = ["place", "PlacedBet"]

PlacedBet = dict[str, Any]


def _venue_for(conn, date: str, race_no: int | None) -> str | None:
    row = conn.execute(
        "SELECT venue FROM races WHERE race_date = ?"
        + (" AND race_no = ?" if race_no else "") + " LIMIT 1",
        (date, race_no) if race_no else (date,)).fetchone()
    return row["venue"] if row else None


def place(date: str, *, bet_type: str, account: str,
          race_no: int | None = None, selections: list[int] | None = None,
          banker: int | None = None, unit_stake: float = 0.0,
          legs: list[dict] | None = None, legs_required: int | None = None,
          acknowledged: list[str] | None = None,
          blackbook_entry_id: str | None = None,
          notes: str | None = None, db: str | None = None) -> PlacedBet:
    """Write one bet. Returns the ticket as stored, with what it was flagged for.

    `acknowledged` names the guardrail flags the user chose to go past. A flag
    that fires and is NOT acknowledged is still written — the bet is never
    blocked — but it is reported back so the interface can say the record now
    carries it.
    """
    known = {a["key"] for a in prebet.ACCOUNTS}
    if account.lower() not in known:
        raise ValueError(
            f"unknown account {account!r}; known: {', '.join(sorted(known))}")

    conn = get_conn(db) if db else get_conn()
    try:
        ticket = prebet.evaluate(
            date, bet_type=bet_type, race_no=race_no, selections=selections,
            banker=banker, unit_stake=unit_stake, legs=legs,
            legs_required=legs_required, account=account, conn=conn)
        if not ticket["placeable"]:
            raise ValueError(ticket["reason"] or "ticket is not placeable")

        rows = _selection_rows(ticket, race_no=race_no, banker=banker)
        if not rows:
            raise ValueError("a bet must name at least one runner")

        ack = {a for a in (acknowledged or [])}
        overrides = [{"flag": f["flag"], "detail": f["detail"]}
                     for f in ticket["flags"] if f["flag"] in ack]

        bet_id = bet_store.insert_bet(
            conn,
            {"account": account.lower(), "race_date": date,
             "venue": _venue_for(conn, date, race_no),
             "race_no": race_no if ticket["bet_type"] != "ALLUP" else None,
             "bet_type": _stored_type(ticket, banker),
             "all_up_formula": ticket.get("all_up_formula"),
             "stake": ticket["total_outlay"], "notes": notes},
            rows, overrides)

        if blackbook_entry_id:
            _link_blackbook(conn, bet_id, blackbook_entry_id)

        return {
            "bet_id": bet_id, "race_date": date, "account": account.lower(),
            "bet_type": _stored_type(ticket, banker),
            "combinations": ticket["combinations"],
            "unit_stake": ticket["unit_stake"],
            "stake": ticket["total_outlay"],
            "selections": len(rows),
            "flags_fired": [f["flag"] for f in ticket["flags"]],
            "overrides_logged": [o["flag"] for o in overrides],
            "flags_unacknowledged": [f["flag"] for f in ticket["flags"]
                                     if f["flag"] not in ack],
        }
    finally:
        conn.close()


def _stored_type(ticket: dict, banker: int | None) -> str:
    """The type as the ledger records it, banker and formula included.

    `bets.bet_type` already carries `ALLUP_*` and `_BANKER` suffixes from the
    legacy log, and the ledger renders them. Writing the same vocabulary keeps a
    manual bet and an imported one on the same row grammar.
    """
    kind = ticket["bet_type"]
    if kind == "ALLUP":
        return f"ALLUP_{ticket.get('all_up_formula') or 'X'}"
    return f"{kind}_BANKER" if banker is not None else kind


def _selection_rows(ticket: dict, *, race_no: int | None,
                    banker: int | None) -> list[dict[str, Any]]:
    if ticket["bet_type"] == "ALLUP":
        rows = []
        for i, leg in enumerate(ticket["legs"], start=1):
            leg_banker = leg.get("banker")
            if leg_banker is not None:
                rows.append({"race_no": leg["race_no"], "horse_no": leg_banker,
                             "leg_no": i, "is_banker": True})
            for h in leg["selections"]:
                if h != leg_banker:
                    rows.append({"race_no": leg["race_no"], "horse_no": h,
                                 "leg_no": i, "is_banker": False})
        return rows

    rows = []
    if banker is not None:
        rows.append({"race_no": race_no, "horse_no": banker, "leg_no": 0,
                     "is_banker": True})
    for h in ticket["selections"]:
        rows.append({"race_no": race_no, "horse_no": h, "leg_no": 0,
                     "is_banker": False})
    return rows


def _link_blackbook(conn, bet_id: str, entry_id: str) -> None:
    """Attach the bet to the thesis it was placed on.

    This is not the same fact as backed-versus-missed, which the blackbook page
    already derives from the selection join — that one answers "was this booked
    horse backed". This answers "was this bet placed BECAUSE of that entry", and
    only the user knows it, so it is captured here or not at all.

    An unknown entry id raises rather than being dropped: a link the user asked
    for and did not get is the silent-failure class this rebuild removes.
    """
    row = conn.execute("SELECT id FROM blackbook WHERE id = ?",
                       (entry_id,)).fetchone()
    if row is None:
        raise ValueError(f"no blackbook entry {entry_id!r}")
    conn.execute(
        "INSERT INTO bet_blackbook_links (bet_id, entry_id) VALUES (?, ?) "
        "ON CONFLICT(bet_id, entry_id) DO NOTHING", (bet_id, entry_id))

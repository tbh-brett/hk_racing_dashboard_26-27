"""Deciding on a ticket -- the only write path for the owner's accept or reject.

A router may not reach into `store/`, so this job owns the write. What it
records is what the owner did with a ticket the desk built (`query/suggest`):

    accepted   taken with the engine's partners
    edited     taken with partners of his own
    rejected   passed

IT DOES NOT PLACE A BET AND DOES NOT WRITE ONE. Every bet this season reached
the ledger from an HKJC statement; a row written here as well would be the
duplicate `import_statement` already had to learn to remove. The ledger is
matched to the decision when the record is read (`query/ticket_record`).

THE TICKET HE SAW IS KEPT WITH THE DECISION: the engine's ticket at that moment,
with as many partners as he took, so "his partners against the engine's, at the
same cost" can be asked afterwards without rebuilding what the page showed.

A DECISION IS HONEST ONLY BEFORE THE OFF, the rule `trial_notes.written_at` and
a tipster's pick are held to. One made after the pool shut is stored `late` and
left out of every figure. One made in time is never replaced once the pool has
shut -- that would let a result rewrite the record -- and is refused. Before the
off he may change his mind as often as he likes; the last one stands.

WHY, IN TAPS. `reasons` are keys from `query/banker_record.REASONS` -- what he
saw that the database cannot (a trial, a run, the pace, the rider ...) -- and
`reason` is a line of his own. Both are optional, on a pass as well as a take,
and both are replaced with the decision. The facts the database can see are not
asked for: they are in the brief, which is kept with the ticket he saw.

A flag never blocks a decision. What is refused is a ticket that describes
nothing -- a banker not in the race, no partner, a partner named twice -- and a
reason that is not in the vocabulary, which would be a tap nothing can score.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from hkrd.query import banker_record, suggest
from hkrd.store import tickets
from hkrd.store.connect import get_conn, transaction

__all__ = ["decide", "DECISIONS", "DecisionClosed"]

DECISIONS = ("accept", "reject")


class DecisionClosed(RuntimeError):
    """The pool has shut and a decision made in time is already on record."""


def decide(date: str, race_no: int, banker_no: int, *, decision: str,
           partners: list[int] | None = None, unit_stake: float | None = None,
           account: str | None = None, reason: str | None = None,
           reasons: list[str] | None = None,
           db: Path | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Record one decision. Returns the decision as stored and the ticket it
    was made on. Raises `suggest.TicketError` for a ticket that describes
    nothing (`suggest.NotOnCard` when the race or runner is not stored) and
    `DecisionClosed` for a change after the pool shut."""
    if decision not in DECISIONS:
        raise suggest.TicketError(
            f"decision must be one of {', '.join(DECISIONS)}: {decision!r}")
    taps = sorted({str(x).strip() for x in reasons or []})
    unknown = [x for x in taps if x not in banker_record.REASON_LABEL]
    if unknown:
        raise suggest.TicketError(
            f"not a reason the desk knows: {', '.join(unknown)} (known: "
            f"{', '.join(banker_record.REASON_LABEL)})")
    conn = get_conn(db) if db else get_conn()
    try:
        # The engine's ticket as it stands now, at the size he is taking.
        k = len(partners) if partners else None
        seen = suggest.ticket(date, race_no, banker_no, k=k, unit_stake=unit_stake,
                              conn=conn)
        prior = seen.pop("decision")
        late = bool(seen["gone"])
        if late and prior and not prior["late"]:
            raise DecisionClosed(
                f"the pool has shut on {date} race {race_no}: the decision made at "
                f"{prior['decided_at']} ({prior['decision']}) stands")

        mine = seen
        if decision == "reject":
            kind, chosen = "rejected", []
        elif partners:
            mine = suggest.ticket(date, race_no, banker_no, partners=partners,
                                  unit_stake=unit_stake, conn=conn)
            mine.pop("decision")
            kind = "edited" if mine["edited"] else "accepted"
            chosen = [p["horse_no"] for p in mine["partners"]]
        else:
            chosen = list(seen["engine_partners"])
            if not chosen:
                raise suggest.TicketError(
                    "no price has been captured for this race, so the engine has no "
                    "partners to accept: name your own")
            kind = "accepted"

        stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
            dt.timezone.utc).isoformat(timespec="seconds")
        with transaction(conn, immediate=True):
            tickets.save_decision(conn, {
                "race_date": date, "race_no": race_no, "banker_no": banker_no,
                "decision": kind, "account": account,
                "unit_stake": mine["unit_stake"] if kind != "rejected" else None,
                "reason": (reason or "").strip() or None, "late": late,
                "seen_json": json.dumps(seen, sort_keys=True),
                "engine": suggest.ENGINE, "decided_at": stamp, "partners": chosen,
                "reasons": taps})
        stored = tickets.decision(conn, date, race_no, banker_no)
        stored.pop("seen_json")
        stored["late"] = bool(stored["late"])
        return {"decision": stored, "ticket": {**mine, "decision": stored}}
    finally:
        conn.close()

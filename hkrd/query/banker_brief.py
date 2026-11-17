"""A candidate banker's facts, each beside the owner's own record for it.

The reasoning aid asked for on 2026-11-17: he picks bankers on intuition and
deliberation and seldom writes down why. This does not pick for him. It holds
his own history up beside each fact about the horse in front of him -- "you
have banked horses that had already placed at this course and trip 165 times;
with nothing of yours on them they placed at 0.91 of the price" -- so the
deliberation has his record in it.

EVERYTHING IS READ ON ONE SIDE of the divide that held in both halves of the
season (17 Nov 2026): whether he had his OWN evidence on the horse. Each side
has a usual -- his bankers with his evidence placed at 1.30, without it at 1.07
-- and every horse he has written about inherits the first. Marked against the
break-even line, nearly every fact about such a horse read FOR (the first
build did exactly that, ten facts of ten on a real card), which says nothing
about which of his horses to choose. So each fact is read two ways:

    the mark    against HIS USUAL on that side: is this fact one his bankers
                have done better or worse with than his bankers in general?
                stronger    1.15x his usual or more
                weaker      1/1.15 of it or less
                usual       between
                thin        under `banker_record.THIN` bankers on the side, and
                            under it over all his bankers too
                unknown     not kept, or not known, for this race
    the line    the A/E itself, which the page colours against the break-even
                line and the market: whether bankers like this have PAID.

The evidence fact itself is read against the other side's usual.

A mark is a description of his record, not a forecast. Fourteen facts are
read, so some marks will be luck; the scorecard (`query/banker_record`) says
how many bankers each line stands on.
"""
from __future__ import annotations

from typing import Any

from hkrd.query.banker_facts import FACTS, facts_for
from hkrd.query.banker_record import BREAKEVEN, THIN
from hkrd.store.connect import Connection

__all__ = ["brief", "attach", "STRONGER"]

STRONGER = 1.15        # a fact this far from his usual, either way, is marked


def _short(ln: dict[str, Any] | None) -> dict[str, Any] | None:
    if ln is None:
        return None
    return {k: ln[k] for k in ("bankers", "placed", "expected", "ae", "ae_lo", "ae_hi", "thin")}


def _paid(ln: dict[str, Any]) -> str | None:
    """Where the line stands against the money: the page's colour."""
    if ln["ae"] is None:
        return None
    return "breakeven" if ln["ae"] >= BREAKEVEN else "market" if ln["ae"] >= 1.0 else "below"


def _mark(ln: dict[str, Any], usual: dict[str, Any] | None) -> str:
    if ln["bankers"] < THIN or ln["ae"] is None:
        return "thin"
    if not usual or not usual.get("ae"):
        return "usual"
    ratio = ln["ae"] / usual["ae"]
    return "stronger" if ratio >= STRONGER else "weaker" if ratio <= 1 / STRONGER else "usual"


def brief(facts: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """One candidate's brief from its facts and the scorecard."""
    own = facts.get("own") == "yours"
    side = "with_own" if own else "without_own"
    sides = record.get("sides", {})
    usual = sides.get(side)
    by = {f["key"]: {v["value"]: v for v in f["values"]} for f in record["facts"]}
    rows = []
    for key, label, _ in FACTS:
        value = facts.get(key)
        entry = by.get(key, {}).get(value) if value is not None else None
        if entry is None:
            rows.append({"key": key, "label": label, "value": value, "mark": "unknown",
                         "basis": None, "line": None, "paid": None, "yours": None,
                         "lean": None, "field_ae": None})
            continue
        if key == "own":
            use, basis = entry, "your evidence against the other side"
            against = sides.get("without_own" if own else "with_own")
        else:
            same = entry[side]
            if same["bankers"] >= THIN:
                use, basis, against = same, "same side", usual
            else:
                use, basis, against = entry, "all your bankers", record.get("overall")
        rows.append({"key": key, "label": label, "value": value,
                     "mark": _mark(use, against), "basis": basis,
                     "line": _short(use), "paid": _paid(use), "yours": _short(entry),
                     "usual": _short(against),
                     "lean": entry["lean"], "field_ae": entry["field"]["ae"]})
    return {"own": own, "tier": facts.get("tier"), "breakeven": BREAKEVEN, "thin": THIN,
            "usual": _short(usual), "usual_paid": _paid(usual) if usual else None,
            "stronger": sum(r["mark"] == "stronger" for r in rows),
            "weaker": sum(r["mark"] == "weaker" for r in rows),
            "rows": rows}


def attach(conn: Connection, tickets: list[dict[str, Any]],
           market: dict[tuple, int], record: dict[str, Any]) -> None:
    """Give each ticket its banker's brief: one facts read for all of them,
    with each runner's place in the market as the ticket was priced."""
    runs = [{"race_date": t["race_date"], "race_no": t["race_no"],
             "horse_no": t["banker"]["horse_no"], "horse_name": t["banker"]["horse_name"]}
            for t in tickets]
    facts = facts_for(conn, runs, market=market)
    for t, r in zip(tickets, runs):
        t["brief"] = brief(facts.get((r["race_date"], r["race_no"], r["horse_no"]), {}),
                           record)

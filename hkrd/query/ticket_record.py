"""The desk's record: every ticket scored once the result is in.

A RECORD, NOT A RANKING (`query/tips_record`'s rule). Every figure carries how
many it was measured on, and at the start that is a handful of races.

WHAT IS SCORED, on races with a result and a quinella dividend:

  paper      the ticket the engine built for every banker the owner had a tier
             on, kept by `jobs/record_tickets` from prices at least five minutes
             before the pool shut. Scored whether he took it, passed it or never
             looked, so what he passed is on the record beside what he took.
  taken      a ticket he accepted or edited, with HIS partners at HIS stake.
  the swap   on an edited ticket, his partners against the engine's at the
             moment he decided, same banker and same cost. This is the one
             comparison the desk exists to settle: at closing prices the market's
             order was 14 points ahead of his partners over 281 tickets, and at
             prices he could have seen it was 2.5 and not distinguishable from
             nothing (docs/ticket-desk.md).
  the banker placed against the place chance stored with the paper ticket, by
             the evidence he had on the horse and by what he did with it.

Every return is the FINAL dividend, which is what a tote bet is paid
(AGENTS.md); nothing here is priced at the moment the ticket was built.

WHAT IT LEAVES OUT. A decision made after the pool shut is `late`: listed,
counted, and in no figure. A paper ticket for a race before the engine's rules
were written (`suggest.ENGINE_FROM`) is `backfilled`: the engine was written
after looking at those races, so they are aggregated only when asked for.

A TICKET'S OUTCOME is one of: `banker_out` (it missed the places the ticket
needed), `partner_out` (the banker was there and no partner was), `paid_qpl`
(the quinella place alone) or `paid` (the quinella too). `under_cost` marks a
ticket that paid less than it cost.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

import numpy as np

from hkrd.derive.probability import actual_over_expected
from hkrd.query import ticket_ledger
from hkrd.query.bet_analysis import BOOTSTRAP_DRAWS, THIN_BETS, slice_stats
from hkrd.query.book_tier import LABEL as TIER_LABEL, TIERS
from hkrd.query.pools import PLACE_PAYING_FIELD
from hkrd.query.suggest import ENGINE, ENGINE_FROM
from hkrd.store.connect import Connection, get_conn

__all__ = ["record", "settle", "outcome", "SCOPES"]

SCOPES = ("live", "backfilled", "all")
_SEED = 20261117


def settle(banker: int, partners: list[int], unit: float, pools: list[str],
           dividends: dict[str, dict[frozenset, float]]) -> dict[str, float]:
    """What each pool pays this ticket at the final dividends: per $10, so a
    line returns unit / 10 x its dividend. A dead heat pays more than one pair
    in a pool and every one the ticket holds is counted."""
    return {pool: round(sum(unit / 10.0 * dividends.get(pool, {}).get(
        frozenset((banker, p)), 0.0) for p in partners), 2) for pool in pools}


def outcome(banker: int, pools: list[str], top2: set[int], top3: set[int],
            paid: dict[str, float]) -> str:
    need = top3 if "QPL" in pools else top2
    if banker not in need:
        return "banker_out"
    if not any(paid.values()):
        return "partner_out"
    return "paid" if paid.get("QIN") else "paid_qpl"


def _pools(field_size: int | None) -> list[str]:
    return ["QIN"] + (["QPL"] if (field_size or 0) >= PLACE_PAYING_FIELD else [])


def _scored(banker: int, partners: list[int], unit: float, pools: list[str],
            race: dict[str, Any]) -> dict[str, Any]:
    by_pool = settle(banker, partners, unit, pools, race["dividends"])
    cost = round(unit * len(partners) * len(pools), 2)
    returned = round(sum(by_pool.values()), 2)
    return {"partners": partners, "unit_stake": unit, "pools": pools, "cost": cost,
            "returned": returned, "by_pool": by_pool,
            "outcome": outcome(banker, pools, race["top2"], race["top3"], by_pool),
            "under_cost": 0 < returned < cost}


def _races(conn: Connection, date_from: str, date_to: str) -> dict[tuple, dict[str, Any]]:
    """Per race in the window: names, placings, the frame and the pair
    dividends. Two queries, whatever the window holds."""
    races: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"names": {}, "places": {}, "dividends": {}, "top2": set(), "top3": set()})
    for r in conn.execute(
            "SELECT race_date, race_no, horse_no, horse_name, place FROM runners "
            "WHERE race_date BETWEEN ? AND ? AND coalesce(place_code, '') NOT LIKE 'W%'",
            (date_from, date_to)):
        race = races[(r["race_date"], r["race_no"])]
        race["names"][r["horse_no"]] = r["horse_name"]
        if r["place"] is not None:
            race["places"][r["horse_no"]] = r["place"]
            if r["place"] <= 3:
                race["top3"].add(r["horse_no"])
            if r["place"] <= 2:
                race["top2"].add(r["horse_no"])
    for r in conn.execute(
            "SELECT race_date, race_no, pool, combination, dividend_per_10 FROM dividends "
            "WHERE race_date BETWEEN ? AND ? AND pool IN ('QIN', 'QPL') "
            "AND dividend_per_10 IS NOT NULL", (date_from, date_to)):
        pair = frozenset(int(x) for x in re.findall(r"\d+", r["combination"] or ""))
        if len(pair) == 2:
            races[(r["race_date"], r["race_no"])]["dividends"].setdefault(
                r["pool"], {})[pair] = r["dividend_per_10"]
    return races


def _awaiting(race: dict[str, Any]) -> str | None:
    if not race["places"]:
        return "result"
    return None if race["dividends"].get("QIN") else "dividends"


def _paired(rows: list[tuple[float, float, float]]) -> dict[str, Any]:
    """His partners against the engine's on the same tickets: (cost, his
    return, the engine's return). The difference in points of return on what
    was staked, with a percentile bootstrap over tickets -- a quinella's
    return is far too skewed for a symmetric interval (`bet_analysis`)."""
    n = len(rows)
    staked = sum(r[0] for r in rows)
    out = {"tickets": n, "staked": round(staked, 2),
           "yours": round(sum(r[1] for r in rows), 2),
           "engines": round(sum(r[2] for r in rows), 2),
           "difference_pts": None, "ci": None, "thin": n < THIN_BETS}
    if n and staked:
        out["difference_pts"] = round(100.0 * (out["yours"] - out["engines"]) / staked, 1)
    if n >= 2 and staked:
        a = np.array(rows, dtype=float)
        idx = np.random.default_rng(_SEED).integers(0, n, size=(BOOTSTRAP_DRAWS, n))
        s = a[idx, 0].sum(axis=1)
        d = 100.0 * (a[idx, 1].sum(axis=1) - a[idx, 2].sum(axis=1)) / s
        out["ci"] = [round(float(x), 1) for x in np.percentile(d, [2.5, 97.5])]
    return out


def _banker_line(key: str, label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Bankers placed against the place chance stored with their paper ticket.
    A paper ticket built with no price has no chance to be measured against and
    is counted apart."""
    priced = [r for r in rows if r["banker"]["place_pct"] is not None]
    placed = sum(1 for r in priced if r["banker_placed"])
    ae = actual_over_expected(sum(r["banker"]["place_pct"] for r in priced) / 100.0,
                              placed, len(priced))
    return {"key": key, "label": label, "bankers": len(priced), "placed": placed,
            "expected": ae["expected_wins"], "ae": ae["ae"], "ae_lo": ae["ae_lo"],
            "ae_hi": ae["ae_hi"], "unpriced": len(rows) - len(priced)}


def _slice(label: str, tickets: list[dict[str, Any]]) -> dict[str, Any]:
    return slice_stats([{"stake": t["cost"], "returned": t["returned"],
                         "hit": 1 if t["returned"] > 0 else 0} for t in tickets], label)


def record(date_from: str, date_to: str | None = None, *, scope: str = "live",
           account: str | None = None, conn: Connection | None = None) -> dict[str, Any]:
    """Every paper ticket and decision in the window, scored, and what they add
    up to. `scope` is which races are read: `live` (on or after the day the
    engine's rules were written), `backfilled` (before it) or `all`."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}: {scope!r}")
    date_to = date_to or date_from
    own = conn is None
    conn = conn or get_conn()
    try:
        span = (date_from, date_to)
        paper = {(r["race_date"], r["race_no"], r["banker_no"]): dict(r) for r in conn.execute(
            "SELECT * FROM ticket_suggestion WHERE race_date BETWEEN ? AND ?", span)}
        paper_partners: dict[tuple, list[int]] = defaultdict(list)
        for r in conn.execute(
                "SELECT race_date, race_no, banker_no, horse_no FROM ticket_suggestion_partner "
                "WHERE race_date BETWEEN ? AND ? ORDER BY rank", span):
            paper_partners[(r[0], r[1], r[2])].append(r[3])
        decided = {(r["race_date"], r["race_no"], r["banker_no"]): dict(r) for r in conn.execute(
            "SELECT * FROM ticket_decision WHERE race_date BETWEEN ? AND ?", span)}
        decided_partners: dict[tuple, list[int]] = defaultdict(list)
        for r in conn.execute(
                "SELECT race_date, race_no, banker_no, horse_no FROM ticket_decision_partner "
                "WHERE race_date BETWEEN ? AND ? ORDER BY horse_no", span):
            decided_partners[(r[0], r[1], r[2])].append(r[3])
        races = _races(conn, *span)
        ledger = ticket_ledger.banker_bets(conn, *span, account=account)
    finally:
        if own:
            conn.close()

    rows = []
    for key in sorted(set(paper) | set(decided)):
        date, race_no, banker = key
        backfilled = date < ENGINE_FROM
        if scope != "all" and backfilled != (scope == "backfilled"):
            continue
        race, p, d = races[(date, race_no)], paper.get(key), decided.get(key)
        awaiting = _awaiting(race)
        row: dict[str, Any] = {
            "race_date": date, "race_no": race_no, "backfilled": backfilled,
            "settled": awaiting is None, "awaiting": awaiting,
            "frame": sorted(race["top3"], key=lambda h: race["places"][h]),
            "banker": {"horse_no": banker, "horse_name": race["names"].get(banker),
                       "place": race["places"].get(banker),
                       "tier": p["tier"] if p else None,
                       "own": bool(p["own"]) if p else None,
                       "market_rank": p["market_rank"] if p else None,
                       "win_odds": p["win_odds"] if p else None,
                       "place_pct": p["place_pct"] if p else None,
                       "place_source": p["place_source"] if p else None},
            "banker_placed": None, "paper": None, "decision": None,
            "ledger": None}
        if p:
            pools = _pools(p["field_size"])
            row["paper"] = {"partners": paper_partners[key], "unit_stake": p["unit_stake"],
                            "pools": pools, "partner_source": p["partner_source"],
                            "lead_minutes": p["lead_minutes"], "engine": p["engine"]}
            if awaiting is None:
                row["banker_placed"] = banker in (race["top3"] if "QPL" in pools
                                                  else race["top2"])
                if paper_partners[key]:
                    row["paper"].update(_scored(banker, paper_partners[key],
                                                p["unit_stake"], pools, race))
        if d:
            seen = json.loads(d["seen_json"])
            mine = decided_partners[key]
            row["decision"] = {"decision": d["decision"], "late": bool(d["late"]),
                               "decided_at": d["decided_at"], "account": d["account"],
                               "reason": d["reason"], "partners": mine,
                               "engine_partners": seen["engine_partners"]}
            if awaiting is None:
                unit = d["unit_stake"] or seen["unit_stake"]
                if mine:
                    row["decision"].update(_scored(banker, mine, unit, seen["pools"], race))
                if seen["engine_partners"]:
                    row["decision"]["engine"] = _scored(
                        banker, seen["engine_partners"], unit, seen["pools"], race)
            row["ledger"] = ticket_ledger.match(ledger.get(key, []), mine)
        rows.append(row)

    done = [r for r in rows if r["settled"]]
    on_time = lambda r: r["decision"] and not r["decision"]["late"]          # noqa: E731
    took = lambda r: on_time(r) and r["decision"]["decision"] != "rejected"  # noqa: E731
    passed = lambda r: on_time(r) and r["decision"]["decision"] == "rejected"  # noqa: E731
    with_paper = [r for r in done if r["paper"]]
    priced = [r for r in with_paper if "returned" in r["paper"]]
    struck = [r for r in done if took(r) and "returned" in r["decision"]]
    edits = [(r["decision"]["cost"], r["decision"]["returned"],
              r["decision"]["engine"]["returned"]) for r in struck
             if r["decision"]["decision"] == "edited" and "engine" in r["decision"]]

    by_evidence = [
        _banker_line("own", "Your own evidence before race day",
                     [r for r in with_paper if r["banker"]["own"]]),
        *(_banker_line(f"tier:{t}", TIER_LABEL[t],
                       [r for r in with_paper if r["banker"]["own"]
                        and r["banker"]["tier"] == t]) for t in TIERS),
        _banker_line("system", "In the system's book only",
                     [r for r in with_paper if not r["banker"]["own"] and r["banker"]["tier"]]),
        _banker_line("none", "Nominated with nothing of yours on record",
                     [r for r in with_paper if not r["banker"]["tier"]])]
    by_decision = [
        _banker_line("taken", "Bankers you took", [r for r in with_paper if took(r)]),
        _banker_line("passed", "Bankers you passed", [r for r in with_paper if passed(r)]),
        _banker_line("undecided", "Bankers you did not decide on",
                     [r for r in with_paper if not on_time(r)])]
    return {
        "from": date_from, "to": date_to, "scope": scope, "account": account,
        "engine": ENGINE, "engine_from": ENGINE_FROM,
        "counts": {
            "tickets": len(rows), "settled": len(done),
            "awaiting": len(rows) - len(done),
            "decided": sum(1 for r in rows if on_time(r)),
            "late": sum(1 for r in rows if r["decision"] and r["decision"]["late"]),
            "struck_without_the_desk": sum(1 for k in ledger if k not in decided)},
        "bankers": {"by_evidence": [g for g in by_evidence if g["bankers"] or g["unpriced"]],
                    "by_decision": by_decision},
        "tickets": [
            _slice("PAPER · EVERY CANDIDATE", [r["paper"] for r in priced]),
            _slice("PAPER · THE ONES YOU TOOK", [r["paper"] for r in priced if took(r)]),
            _slice("PAPER · THE ONES YOU PASSED", [r["paper"] for r in priced if passed(r)]),
            _slice("PAPER · NOT DECIDED", [r["paper"] for r in priced if not on_time(r)]),
            _slice("TAKEN · AS YOU STRUCK THEM", [r["decision"] for r in struck])],
        "swaps": _paired(edits),
        "rows": rows}

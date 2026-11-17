"""What the ledger holds on banker tickets.

Two reads, both of bets that reached the ledger from an HKJC statement:

`banker_bets` -- the banker tickets struck in a window, keyed by race and
banker, so a decision on the desk can be shown beside the bet that was (or was
not) struck on it. A decision writes no bet (`jobs/decide_ticket`); this is the
join that replaces it.

`bankers` -- how the owner's bankers have PLACED against what the price
expected, split by whether he had written about the horse before race day. It
is the measurement the desk rests on (docs/ticket-desk.md), asked of the live
ledger every time and never typed into a page: on production's copy of 17 Nov
2026, this season's 83 bankers with his own evidence placed 37 where the place
pool expected 28.3 (1.31, and by the interval below not yet clear of 1.00), and
the 55 with nothing of his on record 21 against 20.3.

    expected   the PLACE POOL's own chance from the last capture, de-vigged to
               the places paid -- the pool that prices the question. Where no
               place pool was captured (every season before September 2026),
               Harville-Henery on the starting prices, which runs a little
               generous to favourites (0.97 over 4,307 of them). Each group
               says how many of its bankers were read each way.
    placed     in the first three, or the first two in a field under seven.
    evidence   `query/book_tier` as at the race: a trial note or run note of
               his, or an entry he booked or adopted, is his own; an entry only
               the system wrote is not.

One observation per banker per race, however many tickets were struck on it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from hkrd.derive.probability import (actual_over_expected, devig,
                                     market_place_probability, place_from_win)
from hkrd.query import book_tier
from hkrd.query.market import NO_PRICE
from hkrd.query.pools import places_paid
from hkrd.query.banker_facts import evidence
from hkrd.store.connect import Connection, get_conn

__all__ = ["BANKER_TYPES", "banker_bets", "match", "bankers", "place_chances"]

BANKER_TYPES = ("QIN_BANKER", "QPL_BANKER", "QQP_BANKER")
_TYPES = ", ".join(f"'{t}'" for t in BANKER_TYPES)


def banker_bets(conn: Connection, date_from: str, date_to: str, *,
                account: str | None = None
                ) -> dict[tuple[str, int, int], list[dict[str, Any]]]:
    """{(race_date, race_no, banker_no): bets} for every one-banker ticket in
    the window. One query; a ticket with two bankers is not this bet and is
    left out."""
    sql = (f"SELECT b.bet_id, b.race_date, b.race_no, b.bet_type, b.account, b.stake, "
           f"       b.returned, b.status, s.horse_no, s.is_banker "
           f"FROM bets b JOIN bet_selections s ON s.bet_id = b.bet_id "
           f"WHERE b.bet_type IN ({_TYPES}) AND b.race_no IS NOT NULL "
           f"  AND b.race_date BETWEEN ? AND ?")
    args: list[Any] = [date_from, date_to]
    if account:
        sql += " AND lower(b.account) = ?"
        args.append(account.lower())
    bets: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, args):
        b = bets.setdefault(r["bet_id"], {
            "bet_id": r["bet_id"], "race_date": r["race_date"], "race_no": r["race_no"],
            "bet_type": r["bet_type"], "account": r["account"], "stake": r["stake"],
            "returned": r["returned"], "status": r["status"],
            "bankers": [], "partners": []})
        b["bankers" if r["is_banker"] else "partners"].append(r["horse_no"])
    out: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for b in bets.values():
        if len(b["bankers"]) != 1:
            continue
        banker = b.pop("bankers")[0]
        b["partners"].sort()
        out[(b["race_date"], b["race_no"], banker)].append(b)
    return out


def match(bets: list[dict[str, Any]], partners: list[int]) -> dict[str, Any]:
    """What was struck on a banker the desk holds a decision for: whether any
    ticket was, whether one carried exactly the partners decided, and what the
    ledger says they cost and returned."""
    settled = [b for b in bets if b["status"] == "settled"]
    return {"struck": bool(bets),
            "same_partners": any(b["partners"] == sorted(partners) for b in bets),
            "bets": len(bets),
            "staked": round(sum(b["stake"] or 0.0 for b in bets), 2),
            "returned": (round(sum(b["returned"] or 0.0 for b in settled), 2)
                         if settled else None),
            "bet_ids": sorted(b["bet_id"] for b in bets)}


_BANKERS_SQL = f"""
    SELECT DISTINCT b.race_date, b.race_no, s.horse_no, u.horse_name, u.place,
           a.off_time
    FROM bets b
    JOIN bet_selections s ON s.bet_id = b.bet_id AND s.is_banker = 1
    JOIN runners u ON u.race_date = b.race_date AND u.race_no = b.race_no
                  AND u.horse_no = s.horse_no
    JOIN races a ON a.race_date = b.race_date AND a.race_no = b.race_no
    WHERE b.bet_type IN ({_TYPES}) AND b.status = 'settled' AND b.race_date >= ?
      AND coalesce(u.place_code, '') NOT LIKE 'W%'
      AND (SELECT count(*) FROM bet_selections x
            WHERE x.bet_id = b.bet_id AND x.is_banker = 1) = 1
"""
# The last capture of each race that holds a place price, and that capture's
# rows: grouped once, never asked per race (AGENTS.md, Performance).
_POOL_SQL = """
    WITH last AS (
      SELECT race_date, race_no, max(captured_at) AS cap FROM odds_snapshots
       WHERE race_date >= ? AND place_odds IS NOT NULL AND place_odds < ?
       GROUP BY race_date, race_no)
    SELECT o.race_date, o.race_no, o.horse_no, o.place_odds
      FROM odds_snapshots o JOIN last l ON l.race_date = o.race_date
       AND l.race_no = o.race_no AND l.cap = o.captured_at
     WHERE o.place_odds IS NOT NULL AND o.place_odds < ?
"""
# Every runner that started, placed or not: a banker that fell did not place,
# and leaving it out would flatter every figure here.
_FIELD_SQL = """
    SELECT race_date, race_no, horse_no, win_odds FROM runners
     WHERE race_date >= ? AND win_odds IS NOT NULL
       AND coalesce(place_code, '') NOT LIKE 'W%'
"""


def place_chances(conn: Connection, since: str, races: set[tuple[str, int]]
              ) -> dict[tuple[str, int], tuple[str, dict[int, float], int]]:
    """{race: (source, {horse_no: chance of placing}, places paid)}."""
    sp: dict[tuple[str, int], dict[int, float]] = defaultdict(dict)
    for r in conn.execute(_FIELD_SQL, (since,)):
        key = (r["race_date"], r["race_no"])
        if key in races and r["win_odds"] < NO_PRICE:
            sp[key][r["horse_no"]] = r["win_odds"]
    pool: dict[tuple[str, int], dict[int, float]] = defaultdict(dict)
    for r in conn.execute(_POOL_SQL, (since, NO_PRICE, NO_PRICE)):
        key = (r["race_date"], r["race_no"])
        if key in races:
            pool[key][r["horse_no"]] = r["place_odds"]
    out = {}
    for key in races:
        ran = sp.get(key, {})
        if len(ran) < 2:
            continue
        places = places_paid(len(ran))
        nos = sorted(ran)
        # The pool is read over the runners that started: a late scratching
        # still quoted in the last capture would take a share it never had.
        quoted = [pool.get(key, {}).get(h) for h in nos]
        if sum(1 for q in quoted if q) == len(nos):
            p = market_place_probability(quoted, places=places)
            out[key] = ("place pool", dict(zip(nos, map(float, p))), places)
        else:
            # The array form: the walk over orderings is ~7 ms a race and this
            # reads a season of them (817 ms over 255 bankers, measured).
            p = place_from_win(devig([ran[h] for h in nos]), places=places)
            out[key] = ("model", dict(zip(nos, map(float, p))), places)
    return out


def bankers(*, account: str | None = None, since: str = book_tier.RECORD_FROM,
            conn: Connection | None = None) -> dict[str, Any]:
    """How the ledger's bankers have placed against the price, by the evidence
    the owner had on each. See the module docstring for every term."""
    own = conn is None
    conn = conn or get_conn()
    try:
        sql, args = _BANKERS_SQL, [since]
        if account:
            sql += " AND lower(b.account) = ?"
            args.append(account.lower())
        runs = [dict(r) for r in conn.execute(sql, args)]
        tiers = book_tier.tiers_for(runs, conn=conn) if runs else {}
        chances = place_chances(conn, since, {(r["race_date"], r["race_no"]) for r in runs})
    finally:
        if own:
            conn.close()

    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "placed": 0, "expected": 0.0, "pool": 0, "model": 0})
    last = None
    for r in runs:
        got = chances.get((r["race_date"], r["race_no"]))
        if not got or r["horse_no"] not in got[1]:
            continue
        source, chance, places = got
        ev = evidence(tiers.get((r["race_date"], r["race_no"], r["horse_no"])))
        keys = ["all", "own" if ev and ev["own"] else "system" if ev else "none"]
        if ev and ev["own"]:
            keys.append(f"tier:{ev['tier']}")
        for k in keys:
            a = acc[k]
            a["n"] += 1
            a["placed"] += r["place"] is not None and r["place"] <= places
            a["expected"] += chance[r["horse_no"]]
            a["pool" if source == "place pool" else "model"] += 1
        last = max(last or r["race_date"], r["race_date"])

    def line(key: str, label: str) -> dict[str, Any]:
        a = acc[key]
        ae = actual_over_expected(a["expected"], a["placed"], a["n"])
        return {"key": key, "label": label, "bankers": a["n"], "placed": a["placed"],
                "expected": ae["expected_wins"], "ae": ae["ae"], "ae_lo": ae["ae_lo"],
                "ae_hi": ae["ae_hi"], "from_pool": a["pool"], "from_model": a["model"]}
    groups = [line("all", "Every banker"),
              line("own", "Your own evidence before race day"),
              line("system", "In the system's book only"),
              line("none", "Nothing of yours on record")]
    by_tier = [line(f"tier:{t}", book_tier.LABEL[t]) for t in book_tier.TIERS
               if acc.get(f"tier:{t}")]
    return {"account": account, "since": since, "through": last,
            "groups": groups, "by_tier": by_tier}

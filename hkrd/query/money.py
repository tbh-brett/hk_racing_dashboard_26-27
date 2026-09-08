"""How much money is in each pool, and how much of it is on each runner.

`query/pools.py` answers what the pools BELIEVE. This answers how much is
behind the belief, which the prices alone cannot say: 4.0 in a $91,000 double
leg and 4.0 in a $4,300,000 win pool are the same number describing amounts
forty times apart, and a 20% drift means something different in each.

THE CONVERSION IS ARITHMETIC, NOT A MODEL. In a pari-mutuel pool the dividend
is `pool x (1 - takeout) / stake`, so a runner's de-vigged share IS its share of
the money — the same identity that makes the reciprocals sum to one over the
takeout. De-vigging a bookmaker's board is a claim about what it believes;
de-vigging a tote is division. So `pool x share` is the actual number of
dollars, not an estimate of it.

TURNOVER IS A DENOMINATOR, NOT A TIP. On 2026-09-06 race 3 held $4,288,122 of
win money against roughly $500,000 in every other race on the card, because
KA YING RISING was 1.0 in a six-horse field. Following the money there means
backing an odds-on shot into a 17.5% takeout. Raw turnover tracks field size,
favourite shortness and race profile — nothing in this module ranks a runner by
the money on it.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.probability import devig
from hkrd.query.market import latest_prices
from hkrd.store.connect import Connection, get_conn

__all__ = ["pool_turnover", "money_flow", "meeting_money", "TOTAL_POOL",
           "MEETING_TOTAL_RACE_NO"]

# What `ingest/turnover` files the meeting-wide figure under. Race 0 is not a
# race: given a real number it would double-count in any sum over the card.
TOTAL_POOL = "MEETING_TOTAL"
MEETING_TOTAL_RACE_NO = 0


def pool_turnover(date: str, race_no: int, *, at: str = "latest",
                  conn: Connection | None = None) -> dict[str, Any]:
    """Money in each pool for one race, and how much has arrived since it opened.

    `growth` is measured against the EARLIEST capture rather than the previous
    one: two captures a minute apart say nothing, and the question worth asking
    is how much money has come in since the market opened.

    A pool with a NULL amount is reported as not open. That is different from
    absent — a field of six has no quinella place pool at all — and different
    again from zero, which is an open pool nobody has bet into.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        captured = (conn.execute(
            "SELECT max(captured_at) FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()[0]
            if at == "latest" else at)
        if not captured:
            return {"race_date": date, "race_no": race_no, "captured_at": None,
                    "pools": {}, "open": [],
                    "note": "no turnover captured for this race"}

        now = {r["pool"]: r["turnover"] for r in conn.execute(
            "SELECT pool, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ?",
            (date, race_no, captured))}
        # The first capture that actually held a number, per pool -- not the
        # first capture of the race. A card scraped the night before records
        # nulls for hours, and measuring growth from one of those would report
        # the whole pool as having arrived the instant it opened.
        first = {r["pool"]: (r["at"], r["turnover"]) for r in conn.execute(
            "SELECT pool, min(captured_at) at, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND turnover IS NOT NULL "
            "GROUP BY pool", (date, race_no))}

        pools: dict[str, Any] = {}
        for name, amount in sorted(now.items()):
            was = (first.get(name) or (None, None))[1]
            pools[name] = {
                "turnover": amount,
                "open": amount is not None,
                "since_first": (round(amount - was) if amount is not None
                                and was is not None else None),
                "growth_pct": (round(100 * (amount - was) / was, 1)
                               if amount is not None and was else None),
            }
        return {"race_date": date, "race_no": race_no, "captured_at": captured,
                "pools": pools,
                # Which pools this race is actually taking money on. A pool
                # with no row was never offered; one with a null amount is
                # offered but not yet selling.
                "open": sorted(k for k, v in pools.items() if v["open"])}
    finally:
        if own:
            conn.close()


def money_flow(date: str, race_no: int, *, at: str = "latest",
               conn: Connection | None = None) -> dict[str, Any]:
    """Dollars on each runner in the win pool, biggest first.

    This is what turnover is for. A price says a runner has a tenth of the
    money; the pool says whether that tenth is $9,000 or $430,000, and only the
    second tells you whether a move is worth looking at.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        prices = latest_prices(date, race_no, at=at, conn=conn)
        live = [p for p in prices if p["win_odds"]]
        if len(live) < 2:
            return {"race_date": date, "race_no": race_no, "runners": [],
                    "win_pool": None, "captured_at": None,
                    "note": "fewer than two priced runners"}

        turn = pool_turnover(date, race_no, conn=conn)
        pool = (turn["pools"].get("WIN") or {}).get("turnover")

        shares = devig([p["win_odds"] for p in live])
        runners = [{
            "horse_no": p["horse_no"], "win_odds": p["win_odds"],
            "share_pct": round(100 * float(s), 1),
            "dollars": round(pool * float(s)) if pool else None,
        } for p, s in zip(live, shares)]
        out = {"race_date": date, "race_no": race_no,
               "captured_at": live[0]["captured_at"], "win_pool": pool,
               "turnover_captured_at": turn["captured_at"],
               "runners": sorted(runners, key=lambda r: -r["share_pct"])}
        if pool is None:
            # The shares are still true; only the scale is missing. Saying
            # which of the two is absent is the difference between "no data"
            # and "no denominator".
            out["note"] = ("no win turnover captured — shares are shown, "
                           "dollars cannot be")
        return out
    finally:
        if own:
            conn.close()


def meeting_money(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """Money across the whole card, race by race.

    Deliberately flat and unranked. The biggest win pool on a card is usually
    an odds-on favourite in a small field, so this is context for reading a
    move, never an ordering of races by interest.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        races = [r["race_no"] for r in conn.execute(
            "SELECT DISTINCT race_no FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no != ? ORDER BY race_no",
            (date, MEETING_TOTAL_RACE_NO))]
        out = []
        for race_no in races:
            t = pool_turnover(date, race_no, conn=conn)
            win = t["pools"].get("WIN") or {}
            out.append({
                "race_no": race_no,
                "win_pool": win.get("turnover"),
                "growth_pct": win.get("growth_pct"),
                "open": t["open"],
            })
        total = conn.execute(
            "SELECT turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND pool = ? ORDER BY captured_at DESC LIMIT 1",
            (date, TOTAL_POOL)).fetchone()
        return {"race_date": date, "races": out,
                "meeting_total": total["turnover"] if total else None,
                # True once any pool on the card has taken money. Distinct from
                # "no rows": a card captured the night before has rows for every
                # pool and money in none of them.
                "observed": any(r["win_pool"] is not None for r in out)}
    finally:
        if own:
            conn.close()

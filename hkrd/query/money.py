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

from hkrd.derive.probability import devig_to
from hkrd.query.market import day_start, latest_prices
from hkrd.query.pools import latest_pair_odds
from hkrd.store.connect import Connection, get_conn

__all__ = ["pool_turnover", "money_flow", "meeting_money", "runner_money",
           "pair_money", "TOTAL_POOL", "MEETING_TOTAL_RACE_NO", "PAIR_POOLS",
           "RUNNER_POOLS"]

# THE SAME DIVISION ANSWERS TWO QUESTIONS, and only the normalisation differs.
#
# In every one of these pools the dividend is `pool x (1 - takeout) / stake`, so
# a runner's stake is proportional to the reciprocal of its price — in the win
# pool, in the place pool (which is split into three equal parts, one per placed
# horse, so the identity survives), and in both pair pools. Normalise those
# reciprocals to ONE and you have each runner's share of the MONEY. Normalise
# them to three and you have a place PROBABILITY, which is what
# `derive/probability.market_place_probability` does and is a different
# quantity for a different purpose.
#
# Money share is always to one. A "share of the pool" that sums to three is not
# a share of anything.
MONEY_SHARE = 1.0

# Pools priced per runner, and the price column each reads.
RUNNER_POOLS: dict[str, str] = {"WIN": "win_odds", "PLA": "place_odds"}

# Pools priced per PAIR. QQP is not a pool — it is one ticket struck into both
# of these — so the money on a QQP pair is the money in each of them added, and
# `pair_money` returns them separately as well as combined.
PAIR_POOLS: tuple[str, ...] = ("QIN", "QPL")

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
            # The SAME shape as the answer below, `race_total` included. A
            # caller that has to know which branch it got is a caller that will
            # one day read the wrong one.
            return {"race_date": date, "race_no": race_no, "captured_at": None,
                    "pools": {}, "open": [], "race_total": None,
                    "note": "no turnover captured for this race"}

        now = {r["pool"]: (r["turnover"], r["merged_into"]) for r in conn.execute(
            "SELECT pool, turnover, merged_into FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ?",
            (date, race_no, captured))}
        # The first capture that actually held a number, per pool -- not the
        # first capture of the race. A card scraped the night before records
        # nulls for hours, and measuring growth from one of those would report
        # the whole pool as having arrived the instant it opened.
        #
        # And from MIDNIGHT on the race day, like every other change on this
        # card: the pool opens the day before and takes almost nothing until
        # the morning. `growth` is "how much has come in today", which is the
        # question, rather than "how much has ever come in", which is just the
        # size of the pool with an extra step.
        first = {r["pool"]: (r["at"], r["turnover"]) for r in conn.execute(
            "SELECT pool, min(captured_at) at, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND turnover IS NOT NULL "
            "  AND captured_at >= ? GROUP BY pool",
            (date, race_no, day_start(date)))}
        if not first:
            first = {r["pool"]: (r["at"], r["turnover"]) for r in conn.execute(
                "SELECT pool, min(captured_at) at, turnover "
                "FROM odds_pool_turnover WHERE race_date = ? AND race_no = ? "
                "  AND turnover IS NOT NULL GROUP BY pool", (date, race_no))}

        # MERGED POOLS ARE ONE POOL REPORTED TWICE. HKJC merges Quartet into
        # First 4 and reports the same money under both ids, with
        # `mergedPoolId` naming the one it was merged into. Measured on
        # 2026-09-09 HV race 1: FF and QTT both read $28,006, so a sum over
        # every pool overstates the race by exactly that. The first pool of a
        # merged group carries the money and the rest are marked, which is what
        # lets a total add up without anyone having to know the rule.
        seen_merges: set[str] = set()
        pools: dict[str, Any] = {}
        for name, (amount, merged_into) in sorted(now.items()):
            was = (first.get(name) or (None, None))[1]
            duplicate = False
            if merged_into:
                duplicate = merged_into in seen_merges
                seen_merges.add(merged_into)
            pools[name] = {
                "turnover": amount,
                "open": amount is not None,
                # True where this row's money is already counted under another
                # pool's name. Its own figure is still correct and still shown.
                "merged_into": merged_into,
                "counted_elsewhere": duplicate,
                "since_first": (round(amount - was) if amount is not None
                                and was is not None else None),
                "growth_pct": (round(100 * (amount - was) / was, 1)
                               if amount is not None and was else None),
            }
        return {"race_date": date, "race_no": race_no, "captured_at": captured,
                "pools": pools,
                # What the race holds in total, with merged pools counted once.
                # Provided rather than left to the caller because the naive sum
                # is both obvious and wrong.
                "race_total": round(sum(
                    v["turnover"] for v in pools.values()
                    if v["turnover"] is not None
                    and not v["counted_elsewhere"])) or None,
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

        shares = devig_to([p["win_odds"] for p in live], MONEY_SHARE)
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


# ── the distribution turnover makes readable ─────────────────────────────────
#
# `money_flow` above answers the win pool, which is the one the card is sorted
# by. These answer the rest of the question: how the money is spread across
# every pool a race takes, including the pair pools that a QQP ticket is
# actually struck into.
#
# It is worth having because the pools disagree about scale as well as about
# price. On 2026-09-09 HV race 1, captured the day before racing, the quinella
# place pool held $244,136 against the win pool's $168,086 — the pair pool was
# the biggest single-race market on the race, and a win-only reading of "where
# the money is" would have missed the larger half of it.


def runner_money(date: str, race_no: int, *, at: str = "latest",
                 conn: Connection | None = None) -> dict[str, Any]:
    """Dollars on each runner, in every pool priced per runner.

    One row per runner carrying its share and its dollars in each of WIN and
    PLA, because they are different opinions with different money behind them:
    a horse can hold a tenth of the win pool and a twentieth of the place pool,
    and the gap is a fact about how the crowd expects it to run rather than
    whether it wins.

    A pool with no turnover captured contributes a share and no dollars. The
    shares are still true — only the scale is missing — and saying which of the
    two is absent is the difference between "no data" and "no denominator".
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        prices = latest_prices(date, race_no, at=at, conn=conn)
        if len(prices) < 2:
            # The SAME shape as the answer below. A caller that has to know
            # which branch it got is a caller that will one day read the wrong
            # one — the card asks for `turnover_captured_at` either way.
            return {"race_date": date, "race_no": race_no, "runners": [],
                    "pools": {}, "captured_at": None,
                    "turnover_captured_at": None,
                    "note": "fewer than two priced runners"}

        turn = pool_turnover(date, race_no, conn=conn)
        rows: dict[int, dict[str, Any]] = {
            p["horse_no"]: {"horse_no": p["horse_no"],
                            "win_odds": p["win_odds"],
                            "place_odds": p["place_odds"], "pools": {}}
            for p in prices}

        sized: dict[str, float | None] = {}
        for pool, column in RUNNER_POOLS.items():
            priced = [p for p in prices if p[column]]
            if len(priced) < 2:
                continue
            amount = (turn["pools"].get(pool) or {}).get("turnover")
            sized[pool] = amount
            shares = devig_to([p[column] for p in priced], MONEY_SHARE)
            for p, share in zip(priced, shares):
                rows[p["horse_no"]]["pools"][pool] = {
                    "odds": p[column],
                    "share_pct": round(100 * float(share), 1),
                    "dollars": (round(amount * float(share))
                                if amount else None),
                }
        return {
            "race_date": date, "race_no": race_no,
            "captured_at": prices[0]["captured_at"],
            "turnover_captured_at": turn["captured_at"],
            "pools": sized,
            # Ordered by the win pool, which is how the card is read. A runner
            # with no win price sorts last rather than being dropped: it is
            # still declared, and its absence from the market is information.
            "runners": sorted(
                rows.values(),
                key=lambda r: -((r["pools"].get("WIN") or {}).get("share_pct") or -1)),
        }
    finally:
        if own:
            conn.close()


def pair_money(date: str, race_no: int, *, top: int = 10,
               conn: Connection | None = None) -> dict[str, Any]:
    """Dollars on each PAIR, in the quinella and quinella-place pools.

    This is the one a QQP ticket needs, and it is not derivable from the win
    market: the pair pools have their own money in them and their own opinion,
    and on a real race there is more of it than in the win pool.

    QQP is a ticket rather than a pool — one selection struck into both — so
    `combined` is the two added, which is what a QQP bet is actually competing
    with. The two are also returned apart, because they answer different
    questions: QIN is "these two, first and second", QPL is "both in the first
    three", and a pair short in one and long in the other is not a
    disagreement.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        turn = pool_turnover(date, race_no, conn=conn)
        by_pair: dict[tuple[int, int], dict[str, Any]] = {}
        sized: dict[str, float | None] = {}

        for pool in PAIR_POOLS:
            quoted, captured = latest_pair_odds(date, race_no, pool, conn=conn)
            if len(quoted) < 2:
                continue
            amount = (turn["pools"].get(pool) or {}).get("turnover")
            sized[pool] = amount
            keys = sorted(quoted)
            shares = devig_to([quoted[k] for k in keys], MONEY_SHARE)
            for key, share in zip(keys, shares):
                slot = by_pair.setdefault(key, {
                    "horse_nos": list(key), "pools": {}, "combined": None})
                slot["pools"][pool] = {
                    "odds": quoted[key],
                    "share_pct": round(100 * float(share), 1),
                    "dollars": round(amount * float(share)) if amount else None,
                    "captured_at": captured,
                }

        for slot in by_pair.values():
            dollars = [p["dollars"] for p in slot["pools"].values()
                       if p["dollars"] is not None]
            # Only where BOTH halves priced. A combined figure missing one pool
            # is not the money on a QQP ticket, it is the money on half of one,
            # and shown beside complete figures it would rank a pair last for
            # having been measured differently.
            slot["combined"] = (round(sum(dollars))
                                if len(dollars) == len(slot["pools"])
                                and len(slot["pools"]) == len(sized)
                                and dollars else None)

        ranked = sorted(
            by_pair.values(),
            key=lambda s: -(s["combined"]
                            if s["combined"] is not None
                            else max((p["share_pct"]
                                      for p in s["pools"].values()), default=0)))
        return {"race_date": date, "race_no": race_no, "pools": sized,
                "pairs": ranked[:top], "priced": len(by_pair),
                "turnover_captured_at": turn["captured_at"],
                "note": ("QQP is one ticket into both pools, so `combined` is "
                         "the money a QQP selection actually competes with")}
    finally:
        if own:
            conn.close()


# ── the figure turnover exists for ───────────────────────────────────────────
#
# A price move says the RATIO changed. It cannot say whether that happened
# because money came for this horse or because money left the others, and those
# are different events: a runner can shorten from 9.0 to 7.5 without a dollar
# being bet on it, purely because the favourite was backed.
#
# Money arriving is the version of that with the ambiguity taken out. Dollars
# now, minus dollars at the first capture that had both a price and a pool —
# and because the pool only grows, a runner losing dollars is arithmetically
# impossible, so a NEGATIVE figure here is not a horse being laid, it is money
# arriving on it more slowly than the race filled up. Both are reported: the
# amount, and the share of everything new that went to this runner.


def money_arrived(date: str, race_no: int, *, pool: str = "WIN",
                  conn: Connection | None = None) -> dict[str, Any]:
    """How many dollars have come for each runner since the market opened.

    The pool at two moments times each runner's share at those same two
    moments. Both halves have to move together — using today's pool with the
    morning's share, or the reverse, invents money that never arrived.

    Returns nothing rather than a guess where either end is missing. One
    capture is a size; two are a rate, and this is the rate.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        column = RUNNER_POOLS.get(pool.upper())
        if column is None:
            raise ValueError(f"not a per-runner pool: {pool!r}")

        # From MIDNIGHT on the race day, for the same reason every other
        # change on this card is: the pool opens the day before and takes
        # almost nothing until the morning, so money measured from there is
        # mostly the first stranger to bet on the race.
        bounds = conn.execute(
            "SELECT min(captured_at) f, max(captured_at) l "
            "FROM odds_pool_turnover WHERE race_date = ? AND race_no = ? "
            "  AND pool = ? AND turnover IS NOT NULL AND captured_at >= ?",
            (date, race_no, pool.upper(), day_start(date))).fetchone()
        if not bounds or not bounds["f"]:
            # No turnover at or after midnight — an archived meeting captured
            # only the day before. Its whole series is the honest baseline
            # there, and saying so beats reporting nothing.
            bounds = conn.execute(
                "SELECT min(captured_at) f, max(captured_at) l "
                "FROM odds_pool_turnover WHERE race_date = ? AND race_no = ? "
                "  AND pool = ? AND turnover IS NOT NULL",
                (date, race_no, pool.upper())).fetchone()
        if not bounds or not bounds["f"] or bounds["f"] == bounds["l"]:
            return {"race_date": date, "race_no": race_no, "pool": pool,
                    "observed": False, "runners": [], "opened": None,
                    "latest": None, "pool_then": None, "pool_now": None,
                    "arrived": None,
                    "note": ("one turnover capture is a size, not a rate — "
                             "two are needed to say what arrived")}

        amounts = {r["captured_at"]: r["turnover"] for r in conn.execute(
            "SELECT captured_at, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND pool = ? "
            "  AND captured_at IN (?, ?)",
            (date, race_no, pool.upper(), bounds["f"], bounds["l"]))}
        then_pool, now_pool = amounts.get(bounds["f"]), amounts.get(bounds["l"])

        # The price capture nearest each turnover capture, on its own clock.
        # The two are separate requests a second or two apart, so pairing them
        # by exact timestamp would find nothing.
        then_prices = _prices_at(conn, date, race_no, bounds["f"], column)
        now_prices = _prices_at(conn, date, race_no, bounds["l"], column)
        if not then_prices or not now_prices or not then_pool or not now_pool:
            return {"race_date": date, "race_no": race_no, "pool": pool,
                    "observed": False, "runners": [], "opened": bounds["f"],
                    "latest": bounds["l"], "pool_then": then_pool,
                    "pool_now": now_pool, "arrived": None,
                    "note": "no priced capture to pair with the turnover"}

        total_new = now_pool - then_pool
        rows = []
        for horse_no, now_share in now_prices.items():
            was = then_prices.get(horse_no)
            now_dollars = now_pool * now_share
            then_dollars = then_pool * was if was is not None else None
            gained = (now_dollars - then_dollars
                      if then_dollars is not None else None)
            rows.append({
                "horse_no": horse_no,
                "dollars": round(now_dollars),
                "dollars_then": (round(then_dollars)
                                 if then_dollars is not None else None),
                "arrived": round(gained) if gained is not None else None,
                # What share of everything NEW went to this runner, which is
                # the like-for-like figure across runners of very different
                # sizes. Only where the race actually took money in between.
                "of_new_pct": (round(100 * gained / total_new, 1)
                               if gained is not None and total_new > 0 else None),
                "share_pct": round(100 * now_share, 1),
                "share_then_pct": (round(100 * was, 1)
                                   if was is not None else None),
            })
        return {
            "race_date": date, "race_no": race_no, "pool": pool.upper(),
            "observed": True, "opened": bounds["f"], "latest": bounds["l"],
            "pool_then": then_pool, "pool_now": now_pool,
            "arrived": round(total_new),
            "runners": sorted(rows, key=lambda r: -(r["arrived"] or 0)),
        }
    finally:
        if own:
            conn.close()


def _prices_at(conn: Connection, date: str, race_no: int, near: str,
               column: str) -> dict[int, float]:
    """Each runner's share of one pool, at the capture nearest `near`.

    Nearest rather than exact: turnover and prices are two requests a second or
    two apart, so an exact join finds nothing. Nearest AT OR BEFORE, so a share
    is never read from money that had not arrived when the pool was measured.
    """
    captured = conn.execute(
        "SELECT max(captured_at) FROM odds_snapshots "
        "WHERE race_date = ? AND race_no = ? AND captured_at <= ? "
        f"  AND {column} IS NOT NULL AND {column} < 999",
        (date, race_no, near)).fetchone()[0]
    if not captured:
        # Nothing at or before it — the first turnover capture can land a
        # moment ahead of the first price. Take the earliest price instead of
        # reporting the runner as unpriced.
        captured = conn.execute(
            "SELECT min(captured_at) FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? "
            f"  AND {column} IS NOT NULL AND {column} < 999",
            (date, race_no)).fetchone()[0]
    if not captured:
        return {}
    rows = conn.execute(
        f"SELECT horse_no, {column} AS price FROM odds_snapshots "
        "WHERE race_date = ? AND race_no = ? AND captured_at = ? "
        f"  AND {column} IS NOT NULL AND {column} < 999",
        (date, race_no, captured)).fetchall()
    if len(rows) < 2:
        return {}
    shares = devig_to([r["price"] for r in rows], MONEY_SHARE)
    return {r["horse_no"]: float(s) for r, s in zip(rows, shares)}

"""The other pools — quinella, quinella place, doubles, and the money in each.

`query/market.py` reads the win and place snapshots. This module reads the
three tables nothing was reading: `odds_pairs`, `odds_doubles` and
`odds_pool_turnover`.

WHY THE MARKET'S OWN PAIR PRICES MATTER. `market.ranked_pairs` derives quinella
pairs from win odds through Harville, which is a model of what the pair market
should say. The pair market is right there and says it directly, funded by
different money. Showing the two side by side is the same choice the pre-bet
panel already makes for Harville against the 3x rule of thumb: a figure the
reader can see disagreeing is worth more than one quietly picked for them.

WHAT THE ARCHIVE ACTUALLY SUPPORTS. Measured over 69 races with complete
matrices, ranking a horse by how much of the quinella pool it carries relative
to what its win price implies is MONOTONIC in outcome:

    ratio 0.38-0.74   A/E 0.48      ratio 0.94-1.13   A/E 1.04
    ratio 0.74-0.94   A/E 0.64      ratio 1.13-3.09   A/E 1.52

The bottom of that range is the half worth acting on: 7 winners against 18.0
expected below 0.85, which is a Poisson p of 0.003. The top is NOT a betting
signal -- its +14.6% ROI is one 20/1 winner, and drops to -2.1% with the two
biggest removed. So `pair_divergence` flags COLD and says nothing louder than
"no objection" about hot. Sixty-nine races is also eight meetings; this is a
hypothesis with a promising first read, not a rule to size bets on.

TURNOVER IS A DENOMINATOR, NOT A TIP. Race 3 of 2026-09-06 held $4.29m of win
money, ten times the rest of the card, because KA YING RISING was 1.0 in a
six-horse field. Nothing here ranks a horse by the money on it.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.probability import devig, pair_probability
from hkrd.query.market import STALE_AFTER_HOURS, snapshot_age_hours
from hkrd.store.connect import Connection, get_conn

__all__ = ["market_pairs", "pair_divergence", "pool_turnover", "money_flow",
           "doubles_conditional", "pool_changes", "COLD_PAIR_RATIO",
           "DOUBLES_DISPLAY_CAP", "MIN_FIELD_FOR_DIVERGENCE"]

# Below this, the pair market is carrying materially less of a horse than its
# win price implies. Set at the boundary the archive measures, not chosen for
# roundness: A/E is 0.48 and 0.64 in the two quartiles beneath it.
COLD_PAIR_RATIO = 0.85

# The doubles page cannot print more than three digits, so anything at or past
# 999 renders as exactly 999. Inverting it into a probability would treat a
# combination worth several thousand as though it were worth 999.
DOUBLES_DISPLAY_CAP = 999.0

# Below six priced runners the pair pool has too few combinations for the
# marginal to mean much, and the ratio swings on a single price.
MIN_FIELD_FOR_DIVERGENCE = 6


def _latest(conn: Connection, table: str, where: str,
            args: tuple) -> str | None:
    row = conn.execute(
        f"SELECT max(captured_at) FROM {table} WHERE {where}", args).fetchone()
    return row[0] if row else None


def market_pairs(date: str, race_no: int, *, pool: str = "QIN",
                 top: int = 10, at: str = "latest",
                 conn: Connection | None = None) -> dict[str, Any]:
    """What the quinella market itself is paying, shortest first.

    The counterpart to `market.ranked_pairs`, which infers the same ordering
    from win odds. This is the pool's own answer.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        pool = pool.upper()
        captured = (_latest(conn, "odds_pairs",
                            "race_date = ? AND race_no = ? AND pool = ?",
                            (date, race_no, pool)) if at == "latest" else at)
        if not captured:
            return {"race_date": date, "race_no": race_no, "pool": pool,
                    "captured_at": None, "pairs": [],
                    "note": f"no {pool} prices captured for this race"}
        rows = conn.execute(
            "SELECT horse_a, horse_b, odds FROM odds_pairs "
            "WHERE race_date = ? AND race_no = ? AND pool = ? "
            "  AND captured_at = ? AND odds IS NOT NULL "
            "ORDER BY odds", (date, race_no, pool, captured)).fetchall()
        total = sum(1.0 / r["odds"] for r in rows)
        pairs = [{
            "rank": i + 1, "horse_nos": [r["horse_a"], r["horse_b"]],
            "odds": r["odds"],
            # De-vigged across the pool, so it is a share of the money rather
            # than a raw reciprocal that sums past one by the takeout.
            "prob": round(100 * (1.0 / r["odds"]) / total, 1) if total else None,
        } for i, r in enumerate(rows[:top])]
        return {"race_date": date, "race_no": race_no, "pool": pool,
                "captured_at": captured, "combinations": len(rows),
                "pairs": pairs}
    finally:
        if own:
            conn.close()


def _pair_marginals(conn: Connection, date: str, race_no: int, pool: str,
                    at: str) -> tuple[dict[int, float], str | None, int]:
    """Each horse's share of the pair pool, and when it was captured.

    A horse's marginal is the sum of its pairs: under a quinella that is the
    pool's implied probability of it finishing in the first two.
    """
    captured = (_latest(conn, "odds_pairs",
                        "race_date = ? AND race_no = ? AND pool = ?",
                        (date, race_no, pool)) if at == "latest" else at)
    if not captured:
        return {}, None, 0
    rows = conn.execute(
        "SELECT horse_a, horse_b, odds FROM odds_pairs "
        "WHERE race_date = ? AND race_no = ? AND pool = ? AND captured_at = ? "
        "  AND odds IS NOT NULL", (date, race_no, pool, captured)).fetchall()
    total = sum(1.0 / r["odds"] for r in rows)
    if not total:
        return {}, captured, 0
    marginal: dict[int, float] = {}
    for r in rows:
        share = (1.0 / r["odds"]) / total
        marginal[r["horse_a"]] = marginal.get(r["horse_a"], 0.0) + share
        marginal[r["horse_b"]] = marginal.get(r["horse_b"], 0.0) + share
    return marginal, captured, len(rows)


def pair_divergence(date: str, race_no: int, *, pool: str = "QIN",
                    at: str = "latest",
                    conn: Connection | None = None) -> dict[str, Any]:
    """Per runner: what the pair pool carries against what the win price implies.

    A ratio under 1 means the pair market is combining this horse LESS than its
    win price says it should — money is on it to win and not on it to be in the
    first two, which the archive says is the informative direction.

    Only pairs whose BOTH horses are priced in the win market are used, and the
    Harville comparison is renormalised over that same set, so the two sides are
    always measuring the same collection of combinations.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        pool = pool.upper()
        wcap = _latest(conn, "odds_snapshots",
                       "race_date = ? AND race_no = ?", (date, race_no))
        win_rows = conn.execute(
            "SELECT horse_no, win_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ? "
            "  AND win_odds IS NOT NULL ORDER BY horse_no",
            (date, race_no, wcap)).fetchall() if wcap else []
        blank = {"race_date": date, "race_no": race_no, "pool": pool,
                 "runners": [], "captured_at": None, "cold": []}
        if len(win_rows) < MIN_FIELD_FOR_DIVERGENCE:
            return {**blank, "note": (
                f"fewer than {MIN_FIELD_FOR_DIVERGENCE} priced runners — the "
                "pair marginal turns on a single price at this field size")}

        marginal, pcap, n_pairs = _pair_marginals(conn, date, race_no, pool, at)
        if not marginal:
            return {**blank, "note": f"no {pool} prices captured for this race"}

        nos = [r["horse_no"] for r in win_rows]
        odds = [r["win_odds"] for r in win_rows]
        priced = set(nos)

        # The market is missing pairs for some priced runner: the matrix was
        # captured mid-render, or a runner came out between the two pages. The
        # ratio is still computable, but it is measured against a different
        # set of combinations, so it is reported as partial rather than clean.
        expected_pairs = len(nos) * (len(nos) - 1) // 2
        complete = n_pairs == expected_pairs

        hv = pair_probability(odds)
        hv_total = sum(hv.values())
        hv_marginal: dict[int, float] = {}
        for (i, j), v in hv.items():
            hv_marginal[nos[i]] = hv_marginal.get(nos[i], 0.0) + v / hv_total
            hv_marginal[nos[j]] = hv_marginal.get(nos[j], 0.0) + v / hv_total

        win_p = devig(odds)
        runners: list[dict[str, Any]] = []
        for no, o, wp in zip(nos, odds, win_p):
            mk, hvm = marginal.get(no), hv_marginal.get(no)
            if not mk or not hvm:
                continue
            ratio = mk / hvm
            runners.append({
                "horse_no": no, "win_odds": o,
                "win_pct": round(100 * float(wp), 1),
                "pool_top2_pct": round(100 * mk, 1),
                "implied_top2_pct": round(100 * hvm, 1),
                "ratio": round(ratio, 2),
                "cold": ratio < COLD_PAIR_RATIO,
            })
        runners.sort(key=lambda r: r["ratio"])

        # How long before racing this was captured. The pair pool matures much
        # later than the win pool: hours out it holds a fraction of the money
        # and its marginals swing on single bets, so the ratio is wide for
        # everybody and the flag fires on half the field. Measured on a live
        # card at eleven hours out it called 6 of 14 runners cold, which is not
        # a signal, it is an immature market.
        #
        # The archive the 0.85 boundary was measured on is captures taken AT
        # racing, so an early read is not the thing the number was fitted to.
        # Same threshold and the same reasoning as `market.concentration`,
        # imported rather than restated so the two cannot drift apart.
        age = snapshot_age_hours(date, pcap)
        early = age is not None and age > STALE_AFTER_HOURS

        out = {
            "race_date": date, "race_no": race_no, "pool": pool,
            "captured_at": pcap, "win_captured_at": wcap,
            "combinations": n_pairs, "complete": complete,
            "age_hours": age, "early": early,
            "runners": runners,
            # Named separately so a caller can show the flag without deciding
            # which end of the list is the interesting one. Empty while the
            # pool is still immature: the per-runner ratios stay visible for
            # anyone who wants to look, but nothing is asserted about them.
            "cold": [] if early else [r["horse_no"] for r in runners if r["cold"]],
        }
        notes = []
        if not complete:
            notes.append(
                f"{n_pairs} of {expected_pairs} combinations captured — the "
                "ratio is measured over a partial matrix")
        if early:
            notes.append(
                f"captured {age:.0f}h before racing — the pair pool is still "
                f"thin this early, so no runner is flagged until it is within "
                f"{STALE_AFTER_HOURS:.0f}h")
        if notes:
            out["note"] = "; ".join(notes)
        return out
    finally:
        if own:
            conn.close()


def pool_turnover(date: str, race_no: int, *, at: str = "latest",
                  conn: Connection | None = None) -> dict[str, Any]:
    """Money in each pool for one race, and how fast it is arriving.

    `growth` is against the EARLIEST capture, not the previous one: two
    captures a minute apart say nothing, and the question this answers is how
    much money has come in since the market opened.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        captured = (_latest(conn, "odds_pool_turnover",
                            "race_date = ? AND race_no = ?", (date, race_no))
                    if at == "latest" else at)
        if not captured:
            return {"race_date": date, "race_no": race_no, "captured_at": None,
                    "pools": {}, "note": "no turnover captured for this race"}
        now = {r["pool"]: r["turnover"] for r in conn.execute(
            "SELECT pool, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ?",
            (date, race_no, captured))}
        first_at = conn.execute(
            "SELECT min(captured_at) FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()[0]
        first = {r["pool"]: r["turnover"] for r in conn.execute(
            "SELECT pool, turnover FROM odds_pool_turnover "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ?",
            (date, race_no, first_at))} if first_at != captured else {}

        pools: dict[str, Any] = {}
        for name, amount in sorted(now.items()):
            was = first.get(name)
            pools[name] = {
                "turnover": amount,
                "since_first": round(amount - was, 0) if was is not None else None,
                "growth_pct": (round(100 * (amount - was) / was, 1)
                               if was else None),
            }
        return {"race_date": date, "race_no": race_no, "captured_at": captured,
                "first_captured_at": first_at, "pools": pools,
                # Which pools this race actually runs. A pool with no row was
                # not offered -- fewer than seven starters means no quinella
                # place pool at all -- and that is a fact, not a gap.
                "operated": sorted(k for k in now if not k.startswith("RACE_TOTAL"))}
    finally:
        if own:
            conn.close()


def money_flow(date: str, race_no: int, *, at: str = "latest",
               conn: Connection | None = None) -> dict[str, Any]:
    """Dollars on each runner in the win pool, and how many arrived.

    In a pari-mutuel pool this is not an estimate. The dividend is
    `pool x (1 - takeout) / stake`, so a runner's stake is exactly the pool
    times its de-vigged share — the same arithmetic that makes the reciprocals
    sum to one over the takeout. De-vigging a bookmaker's board is an
    assumption; de-vigging a tote is division.

    Which is the point of capturing turnover at all: a drift from 4.0 to 5.0 is
    the same number in a $91,000 pool and a $4,300,000 one, and means something
    different in each.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        wcap = (_latest(conn, "odds_snapshots",
                        "race_date = ? AND race_no = ?", (date, race_no))
                if at == "latest" else at)
        rows = conn.execute(
            "SELECT horse_no, win_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ? "
            "  AND win_odds IS NOT NULL ORDER BY horse_no",
            (date, race_no, wcap)).fetchall() if wcap else []
        if len(rows) < 2:
            return {"race_date": date, "race_no": race_no, "runners": [],
                    "win_pool": None, "captured_at": wcap,
                    "note": "fewer than two priced runners"}

        turn = pool_turnover(date, race_no, conn=conn)
        pool = turn["pools"].get("WIN", {}).get("turnover")

        probs = devig([r["win_odds"] for r in rows])
        runners = [{
            "horse_no": r["horse_no"], "win_odds": r["win_odds"],
            "share_pct": round(100 * float(p), 1),
            "dollars": round(pool * float(p)) if pool else None,
        } for r, p in zip(rows, probs)]
        out = {"race_date": date, "race_no": race_no, "captured_at": wcap,
               "win_pool": pool, "turnover_captured_at": turn["captured_at"],
               "runners": sorted(runners, key=lambda r: -r["share_pct"])}
        if pool is None:
            out["note"] = ("no win turnover captured — shares are shown, "
                           "dollars cannot be")
        return out
    finally:
        if own:
            conn.close()


def doubles_conditional(date: str, leg_no: int, *, at: str = "latest",
                        conn: Connection | None = None) -> dict[str, Any]:
    """What the doubles pool implies about the SECOND leg's runners.

    Dividing a double by its first-leg win odds leaves the price the pool is
    effectively offering on the second-leg runner, conditional on the first leg
    landing. Averaged over the first-leg field — weighted by each first-leg
    runner's own share of the money, so a 2.7 favourite counts for more than a
    62/1 outsider — that is an estimate of the second race's win market built
    entirely from money bet before that race's own pool had matured.

    This is the one genuinely forward-looking figure in the system. It is also
    the least tested: no archive of it exists, because nothing captured doubles
    until now.

    COMPARE `implied_pct`, NOT `implied_odds`. A double is one pool and one
    takeout; backing the two legs separately pays the product of two prices
    that have each been taken out of. So `double / first-leg price` is
    systematically short of the second leg's own win price, and reading the two
    numbers side by side would show a phantom overlay on every runner in the
    race. `implied_pct` is normalised across the second-leg field, which is the
    like-for-like quantity to hold against a de-vigged win share.

    Measured on the live card at 2026-09-06 leg 1, the ratio of the two ran
    0.94-0.95 on the short runners and 1.09-1.34 on the outsiders, which is the
    takeout plus the usual favourite-longshot bias, not eight overlays.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        race_first, race_second = int(leg_no), int(leg_no) + 1
        captured = (_latest(conn, "odds_doubles",
                            "race_date = ? AND leg_no = ?", (date, leg_no))
                    if at == "latest" else at)
        blank = {"race_date": date, "leg_no": leg_no,
                 "race_first": race_first, "race_second": race_second,
                 "captured_at": captured, "runners": []}
        if not captured:
            return {**blank, "note": "no doubles captured for this leg"}

        wcap = _latest(conn, "odds_snapshots",
                       "race_date = ? AND race_no = ?", (date, race_first))
        first_odds = {r["horse_no"]: r["win_odds"] for r in conn.execute(
            "SELECT horse_no, win_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ? "
            "  AND win_odds IS NOT NULL", (date, race_first, wcap))} if wcap else {}
        if len(first_odds) < 2:
            return {**blank, "note": (
                f"race {race_first} has no win prices to divide out")}

        nos = sorted(first_odds)
        weight = dict(zip(nos, devig([first_odds[n] for n in nos])))

        rows = conn.execute(
            "SELECT horse_first, horse_second, odds FROM odds_doubles "
            "WHERE race_date = ? AND leg_no = ? AND captured_at = ? "
            "  AND odds IS NOT NULL", (date, leg_no, captured)).fetchall()

        # Weighted mean of the conditional probability, per second-leg runner.
        num: dict[int, float] = {}
        den: dict[int, float] = {}
        capped = 0
        for r in rows:
            if r["odds"] >= DOUBLES_DISPLAY_CAP:
                capped += 1
                continue
            first, second = r["horse_first"], r["horse_second"]
            w = weight.get(first)
            if w is None or not first_odds.get(first):
                continue
            # double = first x second, so the implied second-leg price is the
            # double divided by the first leg's own price.
            implied = r["odds"] / first_odds[first]
            if implied <= 0:
                continue
            num[second] = num.get(second, 0.0) + w * (1.0 / implied)
            den[second] = den.get(second, 0.0) + w

        if not num:
            return {**blank, "note": "no usable combinations under the display cap"}

        raw = {h: num[h] / den[h] for h in num if den[h]}
        total = sum(raw.values())
        runners = [{
            "horse_no": h,
            # Kept because it is the number the page shows next to a price, and
            # dropping it would hide the raw reading. The docstring says which
            # of the two is comparable to the win market.
            "implied_odds": round(1.0 / v, 1) if v else None,
            "implied_pct": round(100 * v / total, 1) if total else None,
        } for h, v in sorted(raw.items(), key=lambda kv: -kv[1])]
        out = {**blank, "runners": runners, "combinations": len(rows)}
        if capped:
            out["note"] = (f"{capped} combinations at the "
                           f"{DOUBLES_DISPLAY_CAP:.0f} display cap were excluded")
        return out
    finally:
        if own:
            conn.close()


def pool_changes(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """What the other pools say across the whole meeting, race by race.

    Written for the Race Day strip, which already reports drifts, firmers and
    favourite swaps out of the win pool. This is the same question asked of the
    money and of the pair market: where is the crowd's pair betting refusing to
    follow its own win betting, and how much money is behind the disagreement.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        races = [r["race_no"] for r in conn.execute(
            "SELECT DISTINCT race_no FROM odds_snapshots "
            "WHERE race_date = ? ORDER BY race_no", (date,))]
        out: list[dict[str, Any]] = []
        for race_no in races:
            div = pair_divergence(date, race_no, conn=conn)
            turn = pool_turnover(date, race_no, conn=conn)
            win_pool = turn["pools"].get("WIN", {})
            flow = money_flow(date, race_no, conn=conn)
            dollars = {r["horse_no"]: r["dollars"] for r in flow["runners"]}
            # `div["cold"]` rather than the per-runner flag: the list is the
            # one that respects the maturity guard, and reading the boolean
            # instead reports flags on a pool the guard has already judged too
            # thin to read.
            flagged = set(div["cold"])
            cold = [{
                "horse_no": r["horse_no"], "win_odds": r["win_odds"],
                "ratio": r["ratio"], "dollars": dollars.get(r["horse_no"]),
            } for r in div["runners"] if r["horse_no"] in flagged]
            out.append({
                "race_no": race_no,
                "win_pool": win_pool.get("turnover"),
                "pool_growth_pct": win_pool.get("growth_pct"),
                "cold_in_pairs": cold,
                "pair_matrix_complete": div.get("complete"),
                "pairs_early": div.get("early"),
                "operated": turn.get("operated", []),
            })
        return {"race_date": date, "races": out,
                "races_with_cold": sum(1 for r in out if r["cold_in_pairs"]),
                # True while every pair pool that COULD be read is still too
                # early to read. Races with no pair prices, or a field too
                # small to judge, carry None and are not counted either way --
                # left in, one unreadable race turned the whole meeting's
                # answer to False and the strip claimed a maturity it did not
                # have. Distinct from "nothing diverged": the strip should say
                # "too early" rather than imply the market agrees with itself.
                "too_early": (lambda judged: bool(judged) and all(judged))(
                    [r["pairs_early"] for r in out
                     if r["pairs_early"] is not None]),
                # Is there anything here to show at all. Said plainly rather
                # than implied by an empty list, because "nothing diverged" and
                # "nothing was captured" are different answers and only one of
                # them is interesting.
                #
                # Turnover is NOT the test. Keyed on the win pool alone it
                # reported False on a database holding a full quinella archive
                # and no turnover -- which is exactly the state every meeting
                # scraped before this release is in -- and the strip, which
                # returns early on it, hid seven races of real cold flags.
                "observed": any(r["win_pool"] is not None or r["cold_in_pairs"]
                                for r in out)}
    finally:
        if own:
            conn.close()

"""What the place and pair pools themselves say, and what the model says.

Until the capture moved off Playwright this project had win odds and nothing
else, so every place and pair figure on the screen was INFERRED from them by
Harville-Henery. That was the right thing to do with the data that existed, and
`derive/probability` still documents why the linear alternative is not a
transform at all.

It is no longer the only thing available. One GraphQL call now brings back WIN,
PLA, QIN and QPL together, and three of those price directly what the model was
estimating:

    the place pool          P(this runner finishes in the first three)
    the quinella pool       P(these two finish first and second)
    the quinella place pool P(these two both finish in the first three)

So the order is reversed here. The pool's own price is the answer wherever one
was captured; the model fills in where one was not — a market that has not
opened, a field too small for HKJC to run a quinella place pool at all, and the
seasons of archive that hold win odds only. Every figure carries which of the
two it came from, because a number sourced two different ways and labelled once
is how two pages end up disagreeing with nothing to tell them apart.

WHAT THE MODEL IS FOR NOW. Not the answer — the benchmark. On 2026-09-06 R1 the
two agree to about 2.6 points a runner, and disagree most on the favourite,
where Harville says 62.4% and the place pool says 54.2%. The pool also
separates two horses the model cannot: #6 and #13 were both 15.0 to win, and
the market placed them at 3.9 and 3.4 — the same win price, a different opinion
about placing. That gap is the only check on the model that does not have to
wait for a result, and it is now measurable on every race.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.probability import (
    devig, market_pair_probability, market_place_probability, pair_hits,
    pair_probability, place_probability,
)
from hkrd.query.market import latest_prices
from hkrd.store.connect import Connection, get_conn

__all__ = ["place_probabilities", "ranked_pairs", "pair_probabilities",
           "latest_pair_odds", "places_paid", "PLACE_PAYING_FIELD",
           "PAIR_POOLS", "doubles_conditional", "doubles_after_leg"]

# HKJC pays three places in fields of seven or more, two below that, and runs
# no quinella place pool at all under seven. One definition, because the
# transform depends on it and a second guess at the call site is how a figure
# ends up scaled by 3 in a race that pays 2.
PLACE_PAYING_FIELD = 7

PAIR_POOLS = ("QPL", "QIN")


def places_paid(field_size: int) -> int:
    return 3 if field_size >= PLACE_PAYING_FIELD else 2


def latest_pair_odds(date: str, race_no: int, pool: str = "QPL", *,
                     conn: Connection | None = None
                     ) -> tuple[dict[tuple[int, int], float], str | None]:
    """One capture of a pair pool, keyed by (lower horse, higher horse).

    The LATEST capture, and only that one: a pair pool is a matrix, and mixing
    two captures would price half the combinations at one moment and half at
    another. Returns an empty map where the pool was never captured — a field
    of six has no quinella place pool to capture, which is a fact about the
    race rather than a failure.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        captured = conn.execute(
            "SELECT max(captured_at) FROM odds_pairs "
            "WHERE race_date = ? AND race_no = ? AND pool = ?",
            (date, race_no, pool)).fetchone()[0]
        if not captured:
            return {}, None
        rows = conn.execute(
            "SELECT horse_a, horse_b, odds FROM odds_pairs "
            "WHERE race_date = ? AND race_no = ? AND pool = ? "
            "  AND captured_at = ? AND odds IS NOT NULL",
            (date, race_no, pool, captured)).fetchall()
        return ({(r["horse_a"], r["horse_b"]): r["odds"] for r in rows},
                captured)
    finally:
        if own:
            conn.close()


def place_probabilities(date: str, race_no: int, *, at: str = "latest",
                        conn: Connection | None = None) -> dict[str, Any]:
    """P(top three) per runner, from the place pool where it was captured.

    Both figures come back on every runner. `place_pct` is the one to size a
    bet on — the pool's own price where there is one, the model where there is
    not — and `place_source` says which it was. `model_pct` is Harville-Henery
    on the win pool, kept beside it so the two can be compared on the race in
    front of you rather than in aggregate afterwards.
    """
    prices = latest_prices(date, race_no, at=at, conn=conn)
    live = [p for p in prices if p["win_odds"]]
    if len(live) < 2:
        return {"race_date": date, "race_no": race_no, "runners": [],
                "captured_at": None, "places": None, "priced_pool": False,
                "note": "fewer than two priced runners"}

    odds = [p["win_odds"] for p in live]
    places = places_paid(len(live))
    win = devig(odds)
    model = place_probability(odds, places=places)

    # The place pool is quoted for the same runners or not at all; a runner
    # missing from it comes back NaN and falls through to the model.
    quoted = [p["place_odds"] for p in live]
    market = (market_place_probability(quoted, places=places)
              if any(o for o in quoted) else [float("nan")] * len(live))

    runners, from_pool = [], 0
    for p, w, mdl, mkt in zip(live, win, model, market):
        priced = mkt == mkt          # NaN is the only value not equal to itself
        if priced:
            from_pool += 1
        runners.append({
            "horse_no": p["horse_no"],
            "win_odds": p["win_odds"], "place_odds": p["place_odds"],
            "win_pct": round(100 * float(w), 1),
            "place_pct": round(100 * float(mkt if priced else mdl), 1),
            "place_source": "place pool" if priced else "model",
            "market_pct": round(100 * float(mkt), 1) if priced else None,
            "model_pct": round(100 * float(mdl), 1),
            "gap_points": (round(100 * float(mkt - mdl), 1) if priced else None),
        })
    return {"race_date": date, "race_no": race_no,
            "captured_at": live[0]["captured_at"], "places": places,
            "field_priced": len(live), "priced_pool": from_pool > 0,
            "from_pool": from_pool, "runners": runners}


def pair_probabilities(date: str, race_no: int, *, pool: str = "QPL",
                       conn: Connection | None = None) -> dict[str, Any]:
    """P(this pair collects), from the pair pool where it was captured.

    QPL is "both in the first three", QIN is "these two, first and second".
    They are different questions and are never mixed: a pair that is 4.0 in one
    and 22.0 in the other is not a disagreement.
    """
    if pool not in PAIR_POOLS:
        raise ValueError("pool must be one of " + ", ".join(PAIR_POOLS))
    own = conn is None
    conn = conn or get_conn()
    try:
        prices = latest_prices(date, race_no, conn=conn)
        live = [p for p in prices if p["win_odds"]]
        if len(live) < 2:
            return {"race_date": date, "race_no": race_no, "pool": pool,
                    "source": None, "captured_at": None, "pairs": {},
                    "note": "fewer than two priced runners"}

        quoted, captured = latest_pair_odds(date, race_no, pool, conn=conn)
        places = places_paid(len(live))
        if quoted:
            hits = pair_hits(places) if pool == "QPL" else 1
            return {"race_date": date, "race_no": race_no, "pool": pool,
                    "source": pool, "captured_at": captured,
                    "pairs": market_pair_probability(quoted, hits=hits)}

        # No pair pool captured. Harville answers the QUINELLA question — both
        # in the first two — so it stands in for QIN exactly and for QPL only
        # as a ranking. Said rather than silently substituted.
        model = pair_probability([p["win_odds"] for p in live])
        by_pair = {}
        for (i, j), prob in model.items():
            a, b = live[i]["horse_no"], live[j]["horse_no"]
            by_pair[(a, b) if a < b else (b, a)] = prob
        return {"race_date": date, "race_no": race_no, "pool": pool,
                "source": "model", "captured_at": live[0]["captured_at"],
                "pairs": by_pair,
                "note": ("no pair pool captured; ranked on Harville-Henery, "
                         "which prices the quinella question")}
    finally:
        if own:
            conn.close()


def ranked_pairs(date: str, race_no: int, *, top: int = 5, pool: str = "QPL",
                 conn: Connection | None = None) -> list[dict[str, Any]]:
    """The most likely pairs, best first, priced by the pool that pays them.

    Ranking pairs is worth about +25 ROI points over taking them at random
    within the pool. It does not clear the ~17.5% takeout — nothing here does —
    but it is the right way to choose which combinations to take, and the
    design shows it next to the ticket so the chosen set can be compared
    against the ranking rather than assumed to match it.

    Ranked on the pool's own prices now that they are captured, which removes
    the circularity in ranking quinella-place combinations by a number derived
    from the win market: what the ticket pays and what it is ranked by are the
    same pool.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        found = pair_probabilities(date, race_no, pool=pool, conn=conn)
        pairs = found["pairs"]
        if not pairs:
            return []
        quoted, _ = latest_pair_odds(date, race_no, pool, conn=conn)
        ordered = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return [{"rank": i + 1, "horse_nos": [a, b],
                 "prob": round(100 * prob, 1),
                 "odds": quoted.get((a, b)),
                 "pool": found["source"]}
                for i, ((a, b), prob) in enumerate(ordered)]
    finally:
        if own:
            conn.close()


def doubles_conditional(date: str, leg_no: int, *,
                        conn: Connection | None = None) -> dict[str, Any]:
    """What the doubles pool implies about the leg's SECOND race.

    A double is the two legs multiplied, so dividing out the first leg's own
    win price leaves what the pool is effectively offering on the second-leg
    runner. Averaged over the first-leg field — weighted by each first-leg
    runner's share of the money, so a 2.7 favourite counts for more than a 62/1
    outsider — that is an estimate of the second race's win market built
    entirely from money bet into a different pool.

    WHY IT IS WORTH HAVING. It is the only forward-looking figure here. Doubles
    money on a second leg arrives hours before that race's own win pool
    matures, so early in a card this is a read on a race the win market has
    barely looked at, and where the two disagree, one of them is stale.

    COMPARE `implied_pct`, NOT `implied_odds`. A double is one pool and one
    takeout; backing the two legs separately pays the product of two prices
    that have each been taken out of. So `double / first-leg price` comes in
    systematically SHORT of the second leg's own win price, and reading the two
    side by side would show a phantom overlay on every runner in the race.
    `implied_pct` is normalised across the second-leg field, which is the
    like-for-like quantity to hold against a de-vigged win share.

    Untested against a result: nothing captured this pool before now, so there
    is no archive to measure it on. The arithmetic is checked; the edge is not.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        captured = conn.execute(
            "SELECT max(captured_at) FROM odds_doubles "
            "WHERE race_date = ? AND leg_no = ?", (date, leg_no)).fetchone()[0]
        legs = conn.execute(
            "SELECT race_first, race_second FROM odds_doubles "
            "WHERE race_date = ? AND leg_no = ? LIMIT 1",
            (date, leg_no)).fetchone()
        first_race = legs["race_first"] if legs else None
        second_race = legs["race_second"] if legs else None
        blank = {"race_date": date, "leg_no": leg_no,
                 "race_first": first_race, "race_second": second_race,
                 "captured_at": captured, "runners": []}
        if not captured:
            return {**blank, "note": "no doubles captured for this leg"}

        prices = latest_prices(date, first_race, conn=conn)
        first_odds = {p["horse_no"]: p["win_odds"] for p in prices
                      if p["win_odds"]}
        if len(first_odds) < 2:
            return {**blank, "note": (
                f"race {first_race} has no win prices to divide out")}

        nos = sorted(first_odds)
        weight = dict(zip(nos, devig([first_odds[n] for n in nos])))

        rows = conn.execute(
            "SELECT horse_first, horse_second, odds FROM odds_doubles "
            "WHERE race_date = ? AND leg_no = ? AND captured_at = ? "
            "  AND odds IS NOT NULL", (date, leg_no, captured)).fetchall()

        num: dict[int, float] = {}
        den: dict[int, float] = {}
        for r in rows:
            w = weight.get(r["horse_first"])
            leg1 = first_odds.get(r["horse_first"])
            if w is None or not leg1:
                continue
            implied = r["odds"] / leg1
            if implied <= 0:
                continue
            num[r["horse_second"]] = num.get(r["horse_second"], 0.0) + w * (1.0 / implied)
            den[r["horse_second"]] = den.get(r["horse_second"], 0.0) + w
        if not num:
            return {**blank, "note": "no combinations priced against a live first leg"}

        raw = {h: num[h] / den[h] for h in num if den[h]}
        total = sum(raw.values())
        runners = [{
            "horse_no": h,
            # The raw reading, kept because it is what sits next to a price on
            # the page. The docstring says which of the two is comparable.
            "implied_odds": round(1.0 / v, 1) if v else None,
            "implied_pct": round(100 * v / total, 1) if total else None,
        } for h, v in sorted(raw.items(), key=lambda kv: -kv[1])]
        return {**blank, "runners": runners, "combinations": len(rows)}
    finally:
        if own:
            conn.close()


def doubles_after_leg(date: str, leg_no: int, *,
                      conn: Connection | None = None) -> dict[str, Any]:
    """The second leg's market, as the doubles pool left it when betting shut.

    THE PROPERTY THIS USES. A double's betting closes when its FIRST race goes
    off — you cannot back leg 3 of a double once race 3 has run — so from that
    moment the grid is frozen. Once the first race is decided, only the row
    belonging to its winner can still pay, and that row is a complete book on
    the second race: one price per runner in it.

    AND THE FIRST LEG DIVIDES OUT EXACTLY. The price of (winner, X) is the two
    legs multiplied, so across X it is `P(winner) x P(X)` with `P(winner)`
    constant. Normalising the reciprocals removes the constant, so no estimate
    of the first leg is needed and none is made — unlike `doubles_conditional`,
    which has to weight over a first-leg field that has not run yet. This is
    arithmetic on a settled fact.

    WHY IT IS WORTH LOOKING AT. It is an independent read on the race you are
    about to bet, formed from different money, and FROZEN at the previous
    race's off — typically half an hour earlier. The second leg's own win pool
    has kept taking money since. Where the two disagree, one of them has heard
    something the other has not, and the frozen one cannot have heard it.

    Nothing here says which is right. The doubles pool is smaller and its
    opinion is older; both facts are on the answer.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        head = conn.execute(
            "SELECT race_first, race_second, max(captured_at) at "
            "FROM odds_doubles WHERE race_date = ? AND leg_no = ?",
            (date, leg_no)).fetchone()
        first_race = head["race_first"] if head else None
        second_race = head["race_second"] if head else None
        blank = {"race_date": date, "leg_no": leg_no,
                 "race_first": first_race, "race_second": second_race,
                 "settled": False, "winners": [], "runners": [],
                 "captured_at": head["at"] if head else None}
        if not head or not head["at"]:
            return {**blank, "note": "no doubles captured for this leg"}

        winners = [r["horse_no"] for r in conn.execute(
            "SELECT horse_no FROM runners "
            "WHERE race_date = ? AND race_no = ? AND place = 1",
            (date, first_race))]
        if not winners:
            return {**blank, "note": (
                f"race {first_race} has not been decided — until it is, the "
                f"whole grid is still live and `doubles_conditional` is the "
                f"reading that applies")}

        rows = conn.execute(
            "SELECT horse_first, horse_second, odds FROM odds_doubles "
            "WHERE race_date = ? AND leg_no = ? AND captured_at = ? "
            "  AND odds IS NOT NULL", (date, leg_no, head["at"])).fetchall()
        # A dead heat pays both first legs, so both rows are real books on the
        # same race. Averaged with equal weight rather than one picked: HKJC
        # settles a dead-heated leg on both, and choosing one would be a claim
        # about which half of a shared result to believe.
        live = [r for r in rows if r["horse_first"] in winners]
        if not live:
            return {**blank, "settled": True, "winners": winners,
                    "note": "no priced combination on the winning first leg"}

        raw: dict[int, list[float]] = {}
        for r in live:
            if r["odds"] > 0:
                raw.setdefault(r["horse_second"], []).append(1.0 / r["odds"])
        implied = {h: sum(v) / len(v) for h, v in raw.items() if v}
        total = sum(implied.values())
        if not total:
            return {**blank, "settled": True, "winners": winners,
                    "note": "no positive prices on the winning first leg"}

        # The second leg's OWN market, for the comparison this exists to make.
        live_prices = {p["horse_no"]: p["win_odds"]
                       for p in latest_prices(date, second_race, conn=conn)
                       if p["win_odds"]}
        win_share: dict[int, float] = {}
        if len(live_prices) >= 2:
            nos = sorted(live_prices)
            win_share = dict(zip(nos, devig([live_prices[n] for n in nos])))

        runners = []
        for horse_no, value in sorted(implied.items(), key=lambda kv: -kv[1]):
            pct = 100 * value / total
            now = win_share.get(horse_no)
            runners.append({
                "horse_no": horse_no,
                # Normalised across the second-leg field, which is the
                # like-for-like quantity to hold against a de-vigged win share.
                # The raw price is not: a double carries one takeout where two
                # win bets carry two.
                "implied_pct": round(pct, 1),
                "win_pct": round(100 * now, 1) if now is not None else None,
                "win_odds": live_prices.get(horse_no),
                # Positive means the doubles crowd liked it MORE than the win
                # market does now — which is to say the win market has let it
                # go since the previous race went off.
                "gap_points": (round(pct - 100 * now, 1)
                               if now is not None else None),
            })
        return {**blank, "settled": True, "winners": winners,
                "combinations": len(live), "runners": runners,
                "note": ("frozen when race "
                         f"{first_race} went off; the second leg's own market "
                         f"has kept taking money since")}
    finally:
        if own:
            conn.close()

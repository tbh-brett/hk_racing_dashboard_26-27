"""A banker ticket from one moment's prices: pure arithmetic, no database.

`query/suggest` reads the prices and the owner's evidence and says why the desk
is built the way it is; this is the ticket itself. `book` turns one race's
prices into chances once, `from_book` cuts a ticket for one banker from them,
and `build` is the two together.

Each chance comes from the pool that prices the question (AGENTS.md): the place
pool for the banker placing, the quinella pool for the first two and for each
quinella line, the quinella place pool for each of its lines. A line's price
and chance come from the same pool, so every ticket's expected return is that
pool's payout and nothing here says otherwise.

A FLAG WARNS AND NEVER BLOCKS, as `jobs/place_bet` has it. The one thing refused
is a ticket that describes nothing: a banker not in the race, a partner twice.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.probability import (devig, market_pair_probability,
                                     market_place_probability, pair_hits,
                                     place_from_win)
from hkrd.query.pools import PLACE_PAYING_FIELD, places_paid

__all__ = ["ENGINE", "ENGINE_FROM", "UNIT_STAKE", "PARTNERS", "FLAGS", "TicketError",
           "NotOnCard", "book", "from_book", "build"]

ENGINE = "ticket-1.0"
# The day these rules were written. They were written after looking at every
# race before it, so a paper ticket for an earlier race is the engine marking
# its own homework (`jobs/record_screen` skips a meeting inside the model's fit
# for the same reason); the record keeps those apart.
ENGINE_FROM = "2026-11-17"
UNIT_STAKE = 10.0          # HKJC's minimum a line
PARTNERS = 4               # the owner's median this season; nothing measured prefers another

# What each flag has been measured to mean, dated: a figure here is the day's
# measurement and not a live one (docs/ticket-desk.md has the working).
FLAGS = {
    "no_own_evidence": "bankers with no book entry or note of the owner's placed 21 "
                       "where the place pool expected 20.3 (55 bankers, Sep-Nov 2026): "
                       "at the price",
    "small_field": "the favourite with the market's next three returned -23.7% in "
                   "fields of 7 to 9 against -12.9% overall, in both halves of 4,730 "
                   "races (2019 to Nov 2026)",
    "line_under_cost": "16 of this season's 58 paying banker tickets returned less "
                       "than they cost (Sep-Nov 2026)",
    "qin_only": "HKJC runs no quinella place pool in a field under seven",
    "no_market": "no price has been captured for this race yet, so nothing is ranked",
    "banker_unpriced": "the market is open and this runner has no price",
}


class TicketError(ValueError):
    """A ticket that does not describe a bet."""


class NotOnCard(TicketError):
    """The race, or the runner in it, is not stored."""



def _pair(odds: dict[tuple[int, int], float], a: int, b: int) -> float | None:
    return odds.get((a, b) if a < b else (b, a))


def _pct(x: float | None) -> float | None:
    return None if x is None or x != x else round(100.0 * float(x), 1)


def book(field: list[dict[str, Any]], win: dict[int, float],
         place: dict[int, float] | None = None,
         qin: dict[tuple[int, int], float] | None = None,
         qpl: dict[tuple[int, int], float] | None = None) -> dict[str, Any]:
    """One race's prices turned into chances, ONCE: everything every ticket in
    the race shares. A race is the same race whichever banker is asked about,
    and working its chances out again for each candidate was most of a card's
    cost (AGENTS.md, Performance: never compute a per-race constant per row).

    `field` is the declared runners still in the race, `win` and `place` the
    prices of one capture by horse number, `qin` and `qpl` one capture of each
    pair pool keyed (lower, higher). Each chance comes from the pool that
    prices the question; the model is `place_from_win`, the array form of
    Harville-Henery, which is the same arithmetic as the walk over orderings.
    """
    place, qin, qpl = place or {}, qin or {}, qpl or {}
    by_no = {r["horse_no"]: r for r in field}
    priced = sorted((h for h in by_no if win.get(h)), key=lambda h: (win[h], h))
    pools = ["QIN"] + (["QPL"] if len(by_no) >= PLACE_PAYING_FIELD else [])
    places = places_paid(len(priced)) if priced else None
    many = len(priced) >= 2
    win_p = dict(zip(priced, devig([win[h] for h in priced]))) if many else {}
    model = (dict(zip(priced, place_from_win(list(win_p.values()), places=places)))
             if many else {})
    pool_place = (dict(zip(priced, market_place_probability(
        [place.get(h) for h in priced], places=places)))
        if many and any(place.get(h) for h in priced) else {})
    qin_p = market_pair_probability(qin, hits=1) if qin else {}
    qpl_p = (market_pair_probability(qpl, hits=pair_hits(places))
             if qpl and places and "QPL" in pools else {})
    # The quinella pool's own chance of each runner finishing in the first two:
    # the pairs holding it cannot both land, so their chances add.
    top2: dict[int, float] = {}
    for (a, b), v in qin_p.items():
        top2[a] = top2.get(a, 0.0) + v
        top2[b] = top2.get(b, 0.0) + v
    return {"by_no": by_no, "win": win, "place": place, "qin": qin, "qpl": qpl,
            "priced": priced, "market_rank": {h: i + 1 for i, h in enumerate(priced)},
            "pools": pools, "places": places, "win_p": win_p, "model": model,
            "pool_place": pool_place, "qin_p": qin_p, "qpl_p": qpl_p, "top2": top2}


def build(banker_no: int, field: list[dict[str, Any]], win: dict[int, float],
          place: dict[int, float] | None = None,
          qin: dict[tuple[int, int], float] | None = None,
          qpl: dict[tuple[int, int], float] | None = None, *,
          k: int | None = None, unit_stake: float | None = None,
          partners: list[int] | None = None) -> dict[str, Any]:
    """One banker ticket from one moment's prices (`book` has what each is).
    `partners` replaces the engine's choice with the owner's; `k` is how many
    the engine takes when he has not said."""
    return from_book(banker_no, book(field, win, place, qin, qpl), k=k,
                   unit_stake=unit_stake, partners=partners)


def from_book(banker_no: int, bk: dict[str, Any], *, k: int | None,
            unit_stake: float | None, partners: list[int] | None) -> dict[str, Any]:
    by_no, win, place, qin, qpl = bk["by_no"], bk["win"], bk["place"], bk["qin"], bk["qpl"]
    priced, market_rank, pools, places = (bk["priced"], bk["market_rank"], bk["pools"],
                                          bk["places"])
    win_p, model, pool_place, qin_p, qpl_p = (bk["win_p"], bk["model"], bk["pool_place"],
                                              bk["qin_p"], bk["qpl_p"])
    unit = UNIT_STAKE if unit_stake is None else float(unit_stake)
    if unit <= 0:
        raise TicketError("the unit stake must be more than nothing")
    if banker_no not in by_no:
        raise NotOnCard(f"#{banker_no} is not a declared runner in this race")
    others = [h for h in by_no if h != banker_no]

    # The engine's order: the pool that pays the pair, then the win market.
    def quoted(pool: dict) -> int:
        return sum(1 for h in others if _pair(pool, banker_no, h))
    if "QPL" in pools and quoted(qpl):
        source, pairs = "QPL pool", qpl
    elif quoted(qin):
        source, pairs = "QIN pool", qin
    else:
        source, pairs = ("win market" if priced else None), {}
    def order(h: int) -> tuple:
        p = _pair(pairs, banker_no, h)
        return (p is None, p or 0.0, win.get(h) is None, win.get(h) or 0.0, h)
    ranked = sorted(others, key=order) if source else []
    engine_rank = {h: i + 1 for i, h in enumerate(ranked)}

    want = PARTNERS if k is None else int(k)
    if want < 1:
        raise TicketError("a banker ticket needs at least one partner")
    engine_partners = ranked[:want]
    if partners is None:
        chosen = engine_partners
    else:
        chosen = [int(h) for h in partners]
        if len(set(chosen)) != len(chosen):
            raise TicketError("a partner is named twice")
        if banker_no in chosen:
            raise TicketError("the banker cannot be its own partner")
        missing = [h for h in chosen if h not in by_no]
        if missing:
            raise NotOnCard("not a declared runner in this race: "
                            + ", ".join(f"#{h}" for h in missing))
        if not chosen:
            raise TicketError("a banker ticket needs at least one partner")
        engine_partners = ranked[:len(chosen)]
    lines = len(chosen) * len(pools)
    cost = round(unit * lines, 2)

    def runner(h: int) -> dict[str, Any]:
        r = by_no[h]
        return {"horse_no": h, "horse_name": r.get("horse_name"), "draw": r.get("draw"),
                "jockey": r.get("jockey"), "win_odds": win.get(h),
                "win_pct": _pct(win_p.get(h)), "market_rank": market_rank.get(h)}

    under = 0
    out_partners = []
    for i, h in enumerate(chosen):
        q_in, q_pl = _pair(qin, banker_no, h), _pair(qpl, banker_no, h)
        line = {"QIN": round(unit * q_in, 2) if q_in else None,
                "QPL": round(unit * q_pl, 2) if q_pl and "QPL" in pools else None}
        covers = {p: (v >= cost if v is not None else None) for p, v in line.items()}
        under += sum(1 for v in covers.values() if v is False)
        out_partners.append({
            **runner(h), "rank": i + 1, "engine_rank": engine_rank.get(h),
            "qin_odds": q_in, "qpl_odds": q_pl if "QPL" in pools else None,
            "qin_pct": _pct(_pair(qin_p, banker_no, h)),
            "qpl_pct": _pct(_pair(qpl_p, banker_no, h)),
            "line_returns": line, "covers_cost": covers})

    b_pool = pool_place.get(banker_no)
    b_pool = None if b_pool is None or b_pool != b_pool else float(b_pool)
    b_model = model.get(banker_no)
    top2 = bk["top2"].get(banker_no)
    banker = {
        **runner(banker_no), "place_odds": place.get(banker_no),
        "place_pct": _pct(b_pool if b_pool is not None else b_model),
        "place_source": ("place pool" if b_pool is not None
                         else "model" if b_model is not None else None),
        "model_pct": _pct(b_model),
        "gap_points": (round(100 * (b_pool - float(b_model)), 1)
                       if b_pool is not None and b_model is not None else None),
        "top2_pct": _pct(top2), "top2_source": "QIN pool" if top2 is not None else None}

    qin_hit = [p["qin_pct"] for p in out_partners if p["qin_pct"] is not None]
    qpl_hit = [p["qpl_pct"] for p in out_partners if p["qpl_pct"] is not None]
    flags = []
    if not priced:
        flags.append({"code": "no_market", "detail": "no price captured"})
    elif banker_no not in market_rank:
        flags.append({"code": "banker_unpriced",
                      "detail": f"#{banker_no} has no win price in this capture"})
    if "QPL" not in pools:
        flags.append({"code": "qin_only", "detail": f"field of {len(by_no)}"})
    elif len(by_no) <= 9:
        flags.append({"code": "small_field", "detail": f"field of {len(by_no)}"})
    if under:
        flags.append({"code": "line_under_cost",
                      "detail": f"{under} of {lines} lines would return less than the "
                                f"${cost:,.0f} the ticket costs if it were the only "
                                "one to land"})
    for f in flags:
        f["basis"] = FLAGS[f["code"]]
    return {
        "banker": banker, "partners": out_partners,
        "engine_partners": engine_partners,
        "edited": partners is not None and set(chosen) != set(engine_partners),
        "partner_source": source, "pools": pools, "unit_stake": unit,
        "lines": lines, "cost": cost,
        # The quinella's lines cannot land together, so their chances add to the
        # chance the leg pays. The quinella place's can, so theirs add to how
        # many lines are expected to land -- not to a chance.
        "hit": {"qin_pct": round(sum(qin_hit), 1) if qin_hit else None,
                "qpl_lines": round(sum(qpl_hit) / 100.0, 2) if qpl_hit else None},
        "field_size": len(by_no), "priced": len(priced), "places": places,
        "flags": flags, "engine": ENGINE}

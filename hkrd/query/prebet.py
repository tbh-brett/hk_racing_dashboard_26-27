"""The pre-bet panel — what a ticket costs and what it is worth knowing about.

Design brief 06 Part 2 puts four figures in front of a ticket before it is
confirmed, and each is here because a measurement said the intuitive version is
wrong:

  * **Banker place probability** must be Harville-Henery. The linear rule
    overstates a short banker by ~34 points — 94.5% where the truth is 60.3%.
  * **Market concentration** must come from the LATEST snapshot. The morning
    price misclassifies the band in 60% of races, always downward, which
    under-covers exactly the races a top-3 box performs best in.
  * **Combination count**, because "betlines multiply faster than intuition
    tracks" and it is the number that turns an intended small bet into a large
    one.
  * **Pair ranking**, because ranking pairs beats boxing a set at every ticket
    size.

Everything here reads. Nothing writes, and no guardrail here can stop a bet —
`jobs/place_bet.py` owns the write, and a flag is a warning that gets recorded,
never a block. That distinction is deliberate: "reviewing which flags were
overridden and how those bets performed is a genuine analysis, and it's only
possible if the override is logged rather than the bet blocked."
"""
from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any

from hkrd.query import market as market_q
from hkrd.query import raceday as raceday_q
from hkrd.store.bets import DEFAULT_SETTINGS, settings
from hkrd.store.connect import Connection, get_conn

__all__ = ["entry_card", "accounts", "raceday_total", "evaluate", "formulas",
           "combination_count", "ACCOUNTS", "SINGLE_RACE_TYPES", "BET_TYPES"]

# Design brief 07 §3.1: two accounts. The Client account specified in the
# earlier brief was removed there, along with its read-mostly variant.
ACCOUNTS: tuple[dict[str, str], ...] = (
    {"key": "brett", "name": "Brett"},
    {"key": "kelvin", "name": "Kelvin"},
)

SINGLE_RACE_TYPES: tuple[str, ...] = ("WIN", "PLACE", "QIN", "QPL")
BET_TYPES: tuple[str, ...] = SINGLE_RACE_TYPES + ("ALLUP",)

# A pair pool takes two runners per line; WIN and PLACE take one.
_PICKS_PER_LINE = {"WIN": 1, "PLACE": 1, "QIN": 2, "QPL": 2}


def combination_count(bet_type: str, n_selected: int, *,
                      has_banker: bool = False) -> int:
    """Lines on a single-race ticket.

    Design brief 07 §3.3 gives the table this must reproduce: four picks with no
    banker is C(4,2) = 6, five is 10, six is 15; a banker plus four legs is 4.
    A banker appears in every combination, so it multiplies rather than
    combines.
    """
    per_line = _PICKS_PER_LINE.get(bet_type.upper())
    if per_line is None:
        raise ValueError(f"not a single-race bet type: {bet_type!r}")
    if n_selected < 0:
        raise ValueError("selection count cannot be negative")
    if per_line == 1:
        return n_selected
    if has_banker:
        # The banker is the anchor; each remaining selection forms one line
        # with it. n_selected counts the legs, not the banker.
        return n_selected
    return comb(n_selected, 2) if n_selected >= 2 else 0


def formulas(n_races: int) -> list[dict[str, Any]]:
    """Every valid All-Up formula for this many legs, generated not memorised.

    Design brief 07 §4 replaces HKJC's dropdown of codes with one question —
    *how many of my legs must win* — because the combination count is simply
    C(n, r) and a generated picker cannot produce an invalid formula.
    """
    if n_races < 2:
        return []
    out = []
    for legs in range(n_races, 1, -1):
        combos = comb(n_races, legs)
        out.append({"legs": legs, "combinations": combos,
                    "label": f"{legs}x{combos}"})
    return out


def entry_card(date: str, race_no: int, *,
               conn: Connection | None = None) -> dict[str, Any]:
    """The selection table: one row per runner, priced both ways.

    Built on `raceday.build_card` rather than beside it. The card is already the
    one assembly of a race — blackbook flags, styles, draws, market rank — and a
    second one here would be the exact duplication this package exists to
    remove. What entry adds is the place side: the scraped place odd at equal
    weight to win, and the place probability that the ticket is sized on.

    Place odds are never derived from win odds. There is no fixed relationship
    between them; it depends on how concentrated the market is, and the common
    "one third of win" rule is structurally invalid.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        card = raceday_q.build_card(date, race_no, conn=conn)
        if not card.get("runners"):
            return {**card, "runners": [], "place_probabilities": None}

        probs = market_q.place_probabilities(date, race_no, conn=conn)
        by_no = {r["horse_no"]: r for r in probs.get("runners", [])}

        rows = []
        for r in card["runners"]:
            p = by_no.get(r["horse_no"])
            rows.append({
                "horse_no": r["horse_no"], "horse_name": r["horse_name"],
                "draw": r.get("draw"), "jockey": r.get("jockey"),
                "trainer": r.get("trainer"),
                "pace_style": r.get("pace_style"),
                "style_ordinal": r.get("style_ordinal"),
                "win_odds": r.get("win_odds"),
                # From the snapshot, not the runners row: the design puts win
                # and place at equal weight here and both must be the same
                # capture, or the ticket is sized on two different moments.
                "place_odds": (p["place_odds"] if p and p["place_odds"] is not None
                               else r.get("place_odds")),
                "win_pct": r.get("win_pct"),
                "place_pct": p["place_pct"] if p else None,
                "linear_pct": p["linear_pct"] if p else None,
                "gap_points": p["gap_points"] if p else None,
                "scratched": r.get("win_odds") is None,
                "blackbook": r.get("blackbook"),
                "market_rank": r.get("market_rank"),
            })
        favourite = next((r["horse_no"] for r in rows if r["market_rank"] == 1),
                         None)
        return {
            "race_date": date, "race_no": race_no, "venue": card.get("venue"),
            "course": card.get("course"), "surface": card.get("surface"),
            "going": card.get("going"), "distance": card.get("distance"),
            "race_class": card.get("race_class"),
            "field_size": card.get("field_size"),
            "concentration": card.get("concentration"),
            "place_ratio_range": card.get("place_ratio_range"),
            "places_paid": probs.get("places"),
            "captured_at": probs.get("captured_at"),
            "favourite": favourite,
            "runners": rows,
        }
    finally:
        if own:
            conn.close()


def accounts(*, conn: Connection | None = None) -> list[dict[str, Any]]:
    """The two accounts, each with what has been staked through it.

    Design brief 07 §3.1 keeps a persistent colour band per account because
    "logging a bet to the wrong account is a real and costly error". The count
    and P/L travel with the name so the switcher itself says which book is open.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        stats = {}
        for r in conn.execute(
                """SELECT account, count(*) n, sum(stake) staked,
                          sum(COALESCE(pnl, 0)) pnl
                     FROM bets GROUP BY account"""):
            stats[(r["account"] or "").lower()] = {
                "bets": r["n"], "staked": round(r["staked"] or 0.0, 2),
                "pnl": round(r["pnl"] or 0.0, 2)}
        out = []
        for a in ACCOUNTS:
            s = stats.get(a["key"], {"bets": 0, "staked": 0.0, "pnl": 0.0})
            out.append({**a, **s})
        return out
    finally:
        if own:
            conn.close()


def raceday_total(date: str, *, account: str | None = None,
                  conn: Connection | None = None) -> dict[str, Any]:
    """What is already staked on this meeting, against the ceiling.

    Shown persistently during entry, per design brief 06 Part 2: "a running
    raceday total against the ceiling, so it's always visible without
    navigating." The ceiling is a threshold to warn at, never a limit enforced.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = "SELECT COALESCE(sum(stake), 0) t, count(*) n FROM bets WHERE race_date = ?"
        params: list[Any] = [date]
        if account:
            sql += " AND lower(account) = ?"
            params.append(account.lower())
        row = conn.execute(sql, params).fetchone()
        cfg = settings(conn)
        total = round(float(row["t"]), 2)
        ceiling = cfg["raceday_ceiling"]
        return {"race_date": date, "account": account, "staked": total,
                "bets": row["n"], "ceiling": ceiling,
                "remaining": round(ceiling - total, 2),
                "over": total > ceiling,
                "configured": ceiling != DEFAULT_SETTINGS["raceday_ceiling"]}
    finally:
        if own:
            conn.close()


def _banker_panel(card: dict, banker_no: int | None) -> dict[str, Any] | None:
    """The banker's place chance, and the rule of thumb it corrects."""
    if banker_no is None:
        return None
    row = next((r for r in card["runners"] if r["horse_no"] == banker_no), None)
    if row is None or row["place_pct"] is None:
        return {"horse_no": banker_no,
                "horse_name": row["horse_name"] if row else None,
                "place_pct": None,
                "note": "no priced snapshot for this runner"}
    return {
        "horse_no": banker_no, "horse_name": row["horse_name"],
        "win_odds": row["win_odds"], "place_odds": row["place_odds"],
        "win_pct": row["win_pct"], "place_pct": row["place_pct"],
        "linear_pct": row["linear_pct"], "gap_points": row["gap_points"],
        "overstated": (row["gap_points"] or 0) > 0,
    }


def _flags(card: dict, *, bet_type: str, selections: list[int],
           banker_no: int | None, combos: int, total: float,
           day: dict, cfg: dict) -> list[dict[str, Any]]:
    """Guardrails. Every one of these warns; none of them blocks."""
    out: list[dict[str, Any]] = []
    if day["staked"] + total > day["ceiling"]:
        out.append({
            "flag": "raceday_ceiling",
            "title": "RACEDAY CEILING",
            "detail": (f"${day['staked']:,.0f} already staked; this ticket "
                       f"takes the meeting to ${day['staked'] + total:,.0f} "
                       f"against a ${day['ceiling']:,.0f} ceiling"),
        })
    if combos > cfg["max_combinations"]:
        out.append({
            "flag": "max_combinations",
            "title": "COMBINATION COUNT",
            "detail": (f"{combos} combinations, past the "
                       f"{cfg['max_combinations']:.0f} you set"),
        })
    fav = card.get("favourite")
    if fav is not None and selections and fav not in selections \
            and banker_no != fav:
        out.append({
            "flag": "favourite_excluded",
            "title": "FAVOURITE EXCLUDED",
            "detail": ("favourite-excluded tickets have carried a "
                       "disproportionate share of net losses relative to stake"),
        })
    if bet_type.upper() == "QIN" and banker_no is not None:
        row = next((r for r in card["runners"] if r["horse_no"] == banker_no), None)
        if row and row.get("market_rank") not in (None, 1):
            out.append({
                "flag": "non_fav_banker_qin",
                "title": "NON-FAVOURITE BANKER IN QIN",
                "detail": ("a non-favourite banker has run -35.1% in QIN "
                           "against -11.3% in QPL; when the banker will place "
                           "but need not win, QPL is the structurally correct "
                           "pool"),
            })
    conc = card.get("concentration") or {}
    if conc.get("stale"):
        out.append({
            "flag": "stale_snapshot",
            "title": "PRICE IS NOT POST-TIME",
            "detail": conc.get("note") or "concentration read from a stale snapshot",
        })
    return out


def evaluate(date: str, *, bet_type: str, race_no: int | None = None,
             selections: list[int] | None = None, banker: int | None = None,
             unit_stake: float = 0.0, legs: list[dict] | None = None,
             legs_required: int | None = None, account: str | None = None,
             conn: Connection | None = None) -> dict[str, Any]:
    """Price a ticket and say everything worth knowing before it is confirmed.

    Returns the combination count, the outlay, the banker panel, the
    concentration band, the ranked pairs and any guardrail that fired. It never
    refuses: an impossible ticket comes back with `placeable: false` and a
    reason, which the interface shows rather than silently disabling a button.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        kind = bet_type.upper()
        if kind not in BET_TYPES:
            raise ValueError(f"unknown bet type {kind!r}")
        cfg = settings(conn)
        day = raceday_total(date, account=account, conn=conn)

        if kind == "ALLUP":
            return _evaluate_allup(date, legs or [], legs_required,
                                   unit_stake, day, cfg, conn)

        if race_no is None:
            raise ValueError("a single-race bet needs a race number")
        picks = sorted(set(selections or []))
        card = entry_card(date, race_no, conn=conn)
        if banker is not None and banker in picks:
            # The banker is the anchor, never also a leg -- counting it twice
            # is how a combination count silently doubles.
            picks = [p for p in picks if p != banker]

        combos = combination_count(kind, len(picks), has_banker=banker is not None)
        total = round(combos * float(unit_stake or 0), 2)
        pairs = (market_q.ranked_pairs(date, race_no, conn=conn)
                 if kind in ("QIN", "QPL") else [])
        chosen = {tuple(sorted(c)) for c in _lines(kind, picks, banker)}
        for p in pairs:
            p["in_ticket"] = tuple(sorted(p["horse_nos"])) in chosen

        reason = None
        if not picks and banker is None:
            reason = "no selections"
        elif combos == 0:
            reason = ("a pair pool needs two selections, or a banker and one leg"
                      if kind in ("QIN", "QPL") else "no selections")
        elif not unit_stake:
            reason = "no stake"

        return {
            "race_date": date, "race_no": race_no, "bet_type": kind,
            "selections": picks, "banker": banker,
            "combinations": combos, "unit_stake": round(float(unit_stake or 0), 2),
            "total_outlay": total,
            "combination_formula": _formula_text(kind, len(picks), banker),
            "lines": [list(line) for line in _lines(kind, picks, banker)],
            "banker_panel": _banker_panel(card, banker),
            "concentration": card.get("concentration"),
            "pairs": pairs,
            "places_paid": card.get("places_paid"),
            "raceday": day,
            "flags": _flags(card, bet_type=kind, selections=picks,
                            banker_no=banker, combos=combos, total=total,
                            day=day, cfg=cfg),
            "placeable": reason is None,
            "reason": reason,
        }
    finally:
        if own:
            conn.close()


def _lines(kind: str, picks: list[int], banker: int | None) -> list[tuple[int, ...]]:
    """The actual combinations a ticket buys, so the count can be checked."""
    if kind in ("WIN", "PLACE"):
        base = ([banker] if banker is not None else []) + picks
        return [(p,) for p in base]
    if banker is not None:
        return [(banker, p) for p in picks]
    return [tuple(c) for c in combinations(picks, 2)]


def _formula_text(kind: str, n: int, banker: int | None) -> str | None:
    if kind in ("WIN", "PLACE"):
        return None
    if banker is not None:
        return f"banker + {n} leg{'s' if n != 1 else ''}"
    return f"C({n},2)" if n >= 2 else None


def _evaluate_allup(date: str, legs: list[dict], legs_required: int | None,
                    unit_stake: float, day: dict, cfg: dict,
                    conn: Connection) -> dict[str, Any]:
    """An All-Up spans races, so its count is C(n, r) over the legs, not a pool."""
    priced = [l for l in legs if l.get("selections")]
    n = len(priced)
    available = formulas(n)
    required = legs_required if legs_required is not None else (n if n >= 2 else None)
    match = next((f for f in available if f["legs"] == required), None)
    combos = match["combinations"] if match else 0
    total = round(combos * float(unit_stake or 0), 2)

    reason = None
    if n < 2:
        reason = "an all-up needs at least two legs"
    elif match is None:
        reason = f"{required} of {n} is not a valid formula"
    elif not unit_stake:
        reason = "no stake"

    return {
        "race_date": date, "race_no": None, "bet_type": "ALLUP",
        "legs": [{"race_no": l.get("race_no"), "bet_type": l.get("bet_type"),
                  "selections": sorted(set(l.get("selections") or [])),
                  "banker": l.get("banker")} for l in priced],
        "legs_required": required, "formulas": available,
        "combinations": combos, "unit_stake": round(float(unit_stake or 0), 2),
        "total_outlay": total,
        "combination_formula": (f"C({n},{required})" if match else None),
        "all_up_formula": match["label"] if match else None,
        "raceday": day,
        "flags": [f for f in (
            {"flag": "raceday_ceiling", "title": "RACEDAY CEILING",
             "detail": (f"${day['staked']:,.0f} already staked; this ticket "
                        f"takes the meeting to ${day['staked'] + total:,.0f} "
                        f"against a ${day['ceiling']:,.0f} ceiling")}
            if day["staked"] + total > day["ceiling"] else None,
            {"flag": "max_combinations", "title": "COMBINATION COUNT",
             "detail": (f"{combos} combinations, past the "
                        f"{cfg['max_combinations']:.0f} you set")}
            if combos > cfg["max_combinations"] else None,
        ) if f],
        "placeable": reason is None,
        "reason": reason,
    }

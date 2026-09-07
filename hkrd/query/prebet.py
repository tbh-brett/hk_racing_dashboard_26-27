"""The pre-bet panel — what a ticket costs and what it is worth knowing about.

Design brief 06 Part 2 puts four figures in front of a ticket before it is
confirmed, and each is here because a measurement said the intuitive version is
wrong:

  * **Banker place probability** comes from the place pool, which is captured
    on every race since the move to the JSON endpoint and prices the question
    directly. Harville-Henery is shown beside it as the check, and stands in
    where no pool was captured; the linear `p × 3` rule is not a transform at
    all and is not offered — it overstates a short banker by ~34 points.
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

from typing import Any

from hkrd.query import market as market_q
from hkrd.query.tickets import (
    BET_TYPES, MAX_ALLUP_LEGS, PAIR_TYPES, SINGLE_RACE_TYPES, allup_formula,
    allup_formulas, allup_lines, combination_count, formula_text, lines_for,
    pools_per_ticket,
)
from hkrd.query import pools as pools_q
from hkrd.query import raceday as raceday_q
from hkrd.store.bets import DEFAULT_SETTINGS, settings
from hkrd.store.connect import Connection, get_conn

__all__ = ["entry_card", "accounts", "raceday_total", "evaluate",
           "allup_formulas", "combination_count", "pools_per_ticket",
           "ACCOUNTS", "SINGLE_RACE_TYPES", "BET_TYPES"]

# Design brief 07 §3.1: two accounts. The Client account specified in the
# earlier brief was removed there, along with its read-mostly variant.
ACCOUNTS: tuple[dict[str, str], ...] = (
    {"key": "brett", "name": "Brett"},
    {"key": "kelvin", "name": "Kelvin"},
)

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

        probs = pools_q.place_probabilities(date, race_no, conn=conn)
        by_no = {r["horse_no"]: r for r in probs.get("runners", [])}

        rows = []
        # A horse is scratched when it is WITHDRAWN, not when nobody has
        # priced it yet. `win_odds is None` alone badged every runner on the
        # card SCR until the market opened at 13:00 the day before racing —
        # a full field of twelve reading as a full field of withdrawals.
        #
        # The market is open when anyone in the race has a price. Then, and
        # only then, is a runner without one actually out.
        market_open = any(r.get("win_odds") is not None
                          for r in card["runners"])
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
                # Which of the two answered, and by how much they differ. The
                # comparison used to be against the 3× rule of thumb, which was
                # worth showing while the place pool was not captured and there
                # was nothing better to disagree with. There is now.
                "place_source": p["place_source"] if p else None,
                "model_pct": p["model_pct"] if p else None,
                "gap_points": p["gap_points"] if p else None,
                "scratched": market_open and r.get("win_odds") is None,
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
    """The banker's place chance, from the pool that pays it.

    The place pool prices this question directly and is captured on every
    race now, so it is the figure; Harville-Henery comes back beside it as the
    check. Where the two disagree the pool wins — it is several hundred
    thousand dollars of opinion against ten lines of arithmetic — but the size
    of the disagreement is worth seeing, because it is the only reading on the
    model that does not have to wait for a result.
    """
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
        "place_source": row["place_source"], "model_pct": row["model_pct"],
        "gap_points": row["gap_points"],
        # Signed from the MODEL's point of view: positive means Harville is
        # under the market, negative means it is over — which is the direction
        # it errs on a short-priced favourite.
        "model_overstates": (row["gap_points"] or 0) < 0,
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
             legs_required: int | None = None, formula: str | None = None,
             account: str | None = None,
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
                                   unit_stake, day, cfg, conn, formula)

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
        # Ranked in the pool the ticket is actually struck into: QPL pays for
        # both in the first three, QIN for the first two, and a QQP is both.
        # Ranking a quinella-place ticket by a quinella number was the old
        # shape of this and it recommended a different set of pairs.
        pairs = (pools_q.ranked_pairs(date, race_no,
                                      pool="QIN" if kind == "QIN" else "QPL",
                                      conn=conn)
                 if kind in ("QIN", "QPL", "QQP") else [])
        chosen = {tuple(sorted(c)) for c in lines_for(kind, picks, banker)}
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
            "combination_formula": formula_text(kind, len(picks), banker),
            "lines": [list(line) for line in lines_for(kind, picks, banker)],
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


def _leg_lines(leg: dict) -> tuple[dict[str, Any], int]:
    """One leg, normalised, and how many combinations it holds.

    A leg is a whole ticket in its own race — a QQP banker with four others is
    eight combinations, not one — and the chain multiplies those. Counting a
    leg as one line is what under-quoted an all-up by a factor of eight.
    """
    kind = str(leg.get("bet_type") or "WIN").upper()
    if kind not in SINGLE_RACE_TYPES:
        kind = "WIN"
    banker = leg.get("banker")
    picks = sorted({int(x) for x in (leg.get("selections") or [])})
    if banker is not None:
        banker = int(banker)
        picks = [p for p in picks if p != banker]
    return ({"race_no": leg.get("race_no"), "bet_type": kind,
             "selections": picks, "banker": banker,
             "combinations": combination_count(kind, len(picks),
                                               has_banker=banker is not None)},
            combination_count(kind, len(picks), has_banker=banker is not None))


def _evaluate_allup(date: str, legs: list[dict], legs_required: int | None,
                    unit_stake: float, day: dict, cfg: dict,
                    conn: Connection, formula: str | None = None
                    ) -> dict[str, Any]:
    """An All Up spans races, so it multiplies twice.

    HKJC's formula code names which MULTIPLES the ticket buys — 4x11 is every
    double, every treble and the quadruple — and each of those multiples costs
    the product of its legs' own combination counts. Both halves matter and
    the second one used to be missing: a 2X1 over a QQP banker-with-four and a
    single place is eight lines, and was quoted as one.
    """
    shaped = [_leg_lines(l) for l in legs
              if l.get("selections") or l.get("banker") is not None]
    priced = [row for row, count in shaped if count]
    per_leg = [count for _row, count in shaped if count]
    n = len(priced)
    available = allup_formulas(n)

    match = allup_formula(n, formula)
    if match is None and legs_required is not None and 2 <= n:
        # The older shape of this question — "how many of my legs must win" —
        # is exactly one multiple size, so it names a formula rather than
        # replacing the idea of one.
        match = next((f for f in available if f["sizes"] == [int(legs_required)]),
                     None)
    if match is None and formula is None and legs_required is None and n >= 2:
        # Nothing chosen yet: every leg must win, which is the shortest ticket
        # on the list and the one people mean by "an all-up".
        match = next((f for f in available if f["sizes"] == [n]), None)

    combos = allup_lines(match["sizes"], per_leg) if match else 0
    total = round(combos * float(unit_stake or 0), 2)

    reason = None
    if n < 2:
        reason = "an all-up needs at least two legs"
    elif n > MAX_ALLUP_LEGS:
        reason = f"HKJC sells at most {MAX_ALLUP_LEGS} legs; this has {n}"
    elif match is None:
        reason = (f"{formula or legs_required} is not one of the "
                  f"{len(available)} formulas HKJC offers over {n} legs")
    elif not unit_stake:
        reason = "no stake"

    return {
        "race_date": date, "race_no": None, "bet_type": "ALLUP",
        "legs": priced,
        "legs_required": match["sizes"][0] if match else legs_required,
        "formulas": available,
        "formula": match["code"] if match else None,
        "formula_sizes": match["sizes"] if match else None,
        "formula_breakdown": match["breakdown"] if match else None,
        "combinations": combos,
        # What the formula alone costs, before the legs multiply it. Shown
        # beside the real number so a ticket that has quietly become eight
        # times its formula is visible as that rather than as a big total.
        "formula_combinations": match["combinations"] if match else 0,
        "leg_combinations": per_leg,
        "unit_stake": round(float(unit_stake or 0), 2),
        "total_outlay": total,
        "combination_formula": (match["label"] if match else None),
        "all_up_formula": match["code"] if match else None,
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

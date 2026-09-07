"""Probability transforms. Pure functions — no database, no I/O.

The transform this module exists to replace is `p / sum(p) * 3`, which was used
to turn win probability into place probability. It is not a valid transform: it
scales linearly, so it hands a short-priced banker a place probability of 94.5%
where the true figure is 60.3% — a 34-point overstatement, applied directly to
bet sizing.

Harville with the Henery discount is accurate to about 0.9 points on the same
data, and costs about ten lines of arithmetic.

Why the discount is needed: plain Harville assumes a horse's chance of running
second, given another won, is proportional to its win probability. That
overstates favourites, because a favourite's win probability already embeds the
chance it dominates outright. Henery corrects it by damping the probabilities
with an exponent (lambda) at each subsequent finishing position.
"""
from __future__ import annotations

from collections.abc import Sequence
from itertools import permutations

import numpy as np

__all__ = ["devig", "devig_to", "place_probability", "pair_probability",
           "exacta_probability", "market_place_probability",
           "market_pair_probability", "pair_hits", "actual_over_expected",
           "HENERY_LAMBDA", "ProbabilityError"]

# Fitted on HK data; the literature's usual range is 0.76-0.88.
HENERY_LAMBDA = 0.81


class ProbabilityError(ValueError):
    """Inputs that cannot produce a valid probability distribution."""


def devig(win_odds: Sequence[float]) -> np.ndarray:
    """Decimal odds -> normalised implied win probabilities.

    The raw reciprocals sum to more than 1 by the takeout (~17.5% in the HK
    pools). Normalising distributes that proportionally, which is the standard
    assumption and good enough for sizing.
    """
    odds = np.asarray(win_odds, dtype=float)
    if odds.ndim != 1 or odds.size == 0:
        raise ProbabilityError("win_odds must be a non-empty 1-D sequence")
    if np.any(~np.isfinite(odds)) or np.any(odds <= 0):
        raise ProbabilityError(f"win_odds must all be finite and positive: {win_odds!r}")
    raw = 1.0 / odds
    return raw / raw.sum()


def _validate(p: np.ndarray) -> np.ndarray:
    if np.any(p < 0):
        raise ProbabilityError("probabilities must be non-negative")
    total = p.sum()
    if total <= 0:
        raise ProbabilityError("probabilities must sum to a positive value")
    return p / total


def _position_probabilities(p: np.ndarray, lam: float, depth: int) -> np.ndarray:
    """P(runner i finishes in position k) for k < depth, summed over positions.

    Enumerates orderings of the leading `depth` places. Fields here are 8-14
    runners and depth is at most 3, so the exact enumeration is cheap and avoids
    any approximation error.
    """
    n = p.size
    depth = min(depth, n)
    out = np.zeros(n)
    damped = np.power(p, lam)

    for order in permutations(range(n), depth):
        # First place uses the undamped probabilities: it is the quantity the
        # market actually prices. Subsequent places use the Henery damping.
        prob = p[order[0]]
        if prob <= 0:
            continue
        used = [order[0]]
        for pos in order[1:]:
            remaining = damped.copy()
            remaining[used] = 0.0
            denom = remaining.sum()
            if denom <= 0:
                prob = 0.0
                break
            prob *= damped[pos] / denom
            used.append(pos)
        for idx in order:
            out[idx] += prob
    return out


def place_probability(
    win_odds: Sequence[float], lam: float = HENERY_LAMBDA, *, places: int = 3
) -> np.ndarray:
    """P(each runner finishes in the top `places`).

    NEVER compute this as p / sum(p) * places. See the module docstring.

    HK pays three places in fields of seven or more, two in smaller fields; the
    caller passes `places` accordingly.
    """
    if not 0 < lam <= 1:
        raise ProbabilityError(f"lambda must be in (0, 1]: {lam}")
    p = _validate(devig(win_odds))
    if places >= p.size:
        return np.ones_like(p)
    out = _position_probabilities(p, lam, places)
    return np.clip(out, 0.0, 1.0)


def pair_probability(
    win_odds: Sequence[float], lam: float = HENERY_LAMBDA
) -> dict[tuple[int, int], float]:
    """P(both runners finish in the top 2), for every unordered pair.

    This is the quinella structure. Ranking pairs by this figure is worth about
    +25 ROI points over taking them at random within the pool -- it does not
    clear the ~17.5% takeout, but it is the right way to choose which
    combinations to take, and it beats boxing a set of favourites.
    """
    p = _validate(devig(win_odds))
    damped = np.power(p, lam)
    n = p.size
    out: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            total = 0.0
            for first, second in ((i, j), (j, i)):
                denom = damped.sum() - damped[first]
                if denom > 0:
                    total += p[first] * damped[second] / denom
            out[(i, j)] = float(min(total, 1.0))
    return out


def exacta_probability(
    win_odds: Sequence[float], lam: float = HENERY_LAMBDA
) -> dict[tuple[int, int], float]:
    """P(i first AND j second), ordered. The forecast pool."""
    p = _validate(devig(win_odds))
    damped = np.power(p, lam)
    out: dict[tuple[int, int], float] = {}
    for i in range(p.size):
        denom = damped.sum() - damped[i]
        if denom <= 0:
            continue
        for j in range(p.size):
            if i == j:
                continue
            out[(i, j)] = float(p[i] * damped[j] / denom)
    return out


# ── the market's own answer, where it offers one ────────────────────────────
#
# Everything above INFERS a probability from the win pool, because that is all
# the old dashboard captured. Since the move to the JSON endpoint the capture
# also carries PLA, QIN and QPL, and those pools price the same questions
# directly: the place pool IS a place probability, the quinella-place pool IS a
# "both in the first three" probability, each with a few hundred thousand
# dollars of opinion behind it.
#
# So the transforms above stop being the answer and become the benchmark. Where
# a pool is captured, its own price is used; where it is not — a market that
# has not opened, or the seasons of archive that hold win odds only — Harville
# fills in, and every figure says which of the two it is. The gap between them
# is worth watching in its own right: it is the only check on the model that
# does not have to wait for a result.


def devig_to(odds: Sequence[float | None], target: float) -> np.ndarray:
    """Reciprocals of one pool's prices, normalised to sum to `target`.

    `target` is how many of the pool's outcomes come true in a race: 1 for win
    and for quinella, 3 for the place pool in a field of seven or more (three
    horses place), and C(3,2) = 3 for quinella place (three of the pairs among
    the first three horses collect). Getting it wrong scales every probability
    in the pool by a constant, which is invisible in a ranking and wrong
    everywhere a figure is read as a percentage.

    A missing price is not a zero probability — it is a runner or a
    combination this pool did not quote — so it comes back as NaN rather than
    contributing to the normalisation.
    """
    raw = np.array([1.0 / o if o and o > 0 else np.nan for o in odds],
                   dtype=float)
    total = np.nansum(raw)
    if not np.isfinite(total) or total <= 0:
        raise ProbabilityError("no positive prices in the pool")
    return raw * (target / total)


def market_place_probability(place_odds: Sequence[float | None], *,
                             places: int = 3) -> np.ndarray:
    """P(top `places`) straight from the place pool, one entry per runner.

    Never derived from the win price. There is no fixed relationship between
    the two — it depends on how concentrated the market is, and the familiar
    "a third of the win odds" rule is structurally invalid.

    Clipped at 1.0. Proportional de-vigging assumes the takeout is spread
    evenly across the pool, and in a place pool it is not: the short prices
    carry less of it, so a heavy odds-on favourite can normalise past certainty.
    Clipping costs a fraction of a point on exactly those runners and keeps
    every figure readable as a percentage.
    """
    return np.clip(devig_to(place_odds, float(places)), 0.0, 1.0)


def pair_hits(places: int) -> int:
    """How many of a race's PAIRS collect in the quinella-place pool.

    Three horses place, so three of the pairs among them are in the first
    three: (1,2), (1,3) and (2,3). In a small field paying two places only the
    one pair collects, which is the quinella — and HKJC runs no quinella place
    pool at all below seven declared starters.
    """
    return 3 if places >= 3 else 1


def market_pair_probability(pair_odds: dict[tuple[int, int], float | None], *,
                            hits: int = 3) -> dict[tuple[int, int], float]:
    """P(this pair collects) from a pair pool's own prices.

    `hits` is 1 for the quinella (one pair wins) and 3 for quinella place. The
    keys come back unchanged, and a pair the pool did not quote is absent
    rather than zero.
    """
    keys = list(pair_odds)
    probs = np.clip(devig_to([pair_odds[k] for k in keys], float(hits)),
                    0.0, 1.0)
    return {k: float(v) for k, v in zip(keys, probs) if np.isfinite(v)}


# ──────────────────────────────────────────────────────────────────────────

def actual_over_expected(expected: float | None, wins: int, runs: int) -> dict:
    """A/E — actual wins over the wins the market implied, with an interval.

    The one figure on this page that says whether a tag beats the price rather
    than merely wins sometimes. A tag can have a fine strike rate purely by
    booking short-priced horses; A/E divides that out. 1.00 IS the market.

    The interval is the Poisson one, A/E ± 1.96·sqrt(A)/E: wins are a count, and
    at the counts here (a dozen or two per tag) a normal interval on the RATE
    understates how wide the honest range is. With no wins at all the upper
    bound is the 95% Poisson bound of 3.0 events, not zero — a tag that has not
    won yet has not been shown to fail.
    """
    if not expected or runs == 0:
        return {"ae": None, "ae_lo": None, "ae_hi": None, "ae_runs": runs,
                "expected_wins": round(expected, 2) if expected else None}
    ae = wins / expected
    half = 1.96 * (wins ** 0.5) / expected
    return {
        "ae": round(ae, 2),
        "ae_lo": round(max(0.0, ae - half), 2),
        "ae_hi": round(ae + half if wins else 3.0 / expected, 2),
        "ae_runs": runs,
        "expected_wins": round(expected, 2),
    }

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

__all__ = ["devig", "place_probability", "pair_probability", "exacta_probability",
           "actual_over_expected", "HENERY_LAMBDA", "ProbabilityError"]

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

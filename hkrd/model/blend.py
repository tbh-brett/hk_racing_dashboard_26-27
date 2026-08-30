"""Blending a fundamental stream with the market — and what it is worth.

Design brief 05 §5 asks the Model Analysis page to show the components of the
blend rather than its output, because "putting fund, mkt and the raw market
side by side makes visible, without any further explanation, that the blended
column leans heavily on a number the market already provides".

Measured here, the finding is stronger than that. On a walk-forward split of
660 races with a complete scored field, a winner and complete odds (train: 394
races before 2026-01-07, test: 266 from it), mean negative log likelihood of
the winner:

    uniform                     2.4903
    SARR fundamental            2.3716
    market, de-vigged           2.0466
    blend at the fitted weight  2.0466   (w = 0.00)

The optimal weight on the fundamental stream is ZERO. Every positive weight
makes the blend worse: w=0.10 gives 2.0613, w=0.32 gives 2.1060, w=1.00 gives
2.3716. The old FUSE ran w=1.00 and the handoff's own grid search put it at
0.10; against this package's fundamental stream neither beats simply using the
price.

So the default blend weight here is 0.0, and the page shows the alternatives
beside it rather than asserting the conclusion. That is the point of the page.

NOTE ON WHAT `fund` IS. The retired FUSE `fund` head was LightGBM over 55
shifted features (AUC 0.727). It is not rebuilt in this package, and nothing
here claims to be it. The fundamental stream below is SARR mapped to a
probability, which is weaker — the numbers above are its numbers, not FUSE's.
Re-derive them all with `python -m hkrd.jobs.fit_blend`.
"""
from __future__ import annotations

import numpy as np

__all__ = ["BETA", "DEFAULT_BLEND_WEIGHT", "CALIBRATION", "fundamental_probability",
           "market_probability", "blend"]

# Softmax temperature mapping SARR (lower is better) to a win probability,
# fitted on the training split by minimising the winner's log loss. Not a
# chosen constant: 3.25 is the grid minimum, and the grid is in fit_blend.
BETA = 3.25

# See the module docstring. Zero is the fitted value, not a placeholder.
DEFAULT_BLEND_WEIGHT = 0.0

# Published so the page can render the comparison rather than restate it.
CALIBRATION = {
    "races": 660, "train_races": 394, "test_races": 266,
    "split_date": "2026-01-07",
    "beta": BETA,
    "fitted_weight": DEFAULT_BLEND_WEIGHT,
    "log_loss": {"uniform": 2.4903, "fundamental": 2.3716, "market": 2.0466},
    # Keyed by a fixed 2dp string, not a float: JSON turns 1.0 into "1.0"
    # and a reader looking up "1" finds nothing.
    "log_loss_by_weight": {"0.00": 2.0466, "0.10": 2.0613,
                           "0.32": 2.1060, "1.00": 2.3716},
}


def fundamental_probability(sarr: list[float], beta: float = BETA) -> np.ndarray:
    """SARR scores to win probabilities. Lower SARR is better, hence the sign.

    Centred before exponentiating so the result depends on the SPREAD of the
    field's scores and not on their level, which drifts with the reference
    window. Without the centring a whole meeting's probabilities move when the
    reference is rebuilt, and nothing on the page would explain why.
    """
    s = np.asarray(sarr, dtype=float)
    if s.size == 0:
        return s
    z = -beta * (s - s.mean())
    p = np.exp(z - z.max())
    return p / p.sum()


def market_probability(win_odds: list[float]) -> np.ndarray:
    """De-vigged implied probability. The overround is divided out
    proportionally, which is what makes the column sum to 100%."""
    o = np.asarray(win_odds, dtype=float)
    if o.size == 0:
        return o
    p = 1.0 / o
    return p / p.sum()


def blend(fund, market, weight: float = DEFAULT_BLEND_WEIGHT) -> np.ndarray:
    """A linear pool. `weight` is the share carried by the FUNDAMENTAL stream.

    Linear rather than the log-opinion pool the old FUSE used, because a
    geometric mean of two streams needs both to be non-zero and FUSE returned
    None for the whole race when any stream held a NaN. One missing odds field
    killed a race; that is a fault, not a design.

    Either stream may be empty — that is the whole point — so both are coerced
    rather than assumed to be arrays. A caller with nothing to pass will pass a
    list, and refusing it here would reintroduce the fault this avoids.
    """
    fund = np.asarray(fund, dtype=float)
    market = np.asarray(market, dtype=float)
    if fund.size == 0 or market.size == 0:
        return market if fund.size == 0 else fund
    out = weight * fund + (1.0 - weight) * market
    total = out.sum()
    return out / total if total else out

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
    SARR fundamental            2.3096
    market, de-vigged           2.0505
    blend at the fitted weight  2.0505   (w = 0.00)

The optimal weight on the fundamental stream is ZERO. Every positive weight
makes the blend worse: w=0.10 gives 2.0609, w=0.32 gives 2.0952, w=1.00 gives
2.3096. The old FUSE ran w=1.00 and the handoff's own grid search put it at
0.10; against this package's fundamental stream neither beats simply using the
price.

AND IT IS NOT AN ARTEFACT OF THAT POPULATION, which was the obvious objection
and went untested until the stream learned to carry a partial field. Requiring
every runner to be scored selects for races without debutants -- 660 of the
archive's 1,712 -- so the weight was being chosen on a third of the card and
applied to all of it. Over all 1,617 races with a complete book, rated field or
not, the fit returns 0.00 again: 2.0155 at w=0.00 against 2.0242 at 0.10,
2.0536 at 0.32 and 2.2395 at 1.00, on 535 test races rather than 266. Tripling
the evidence did not rescue the fundamental stream.

So the default blend weight here is 0.0, and the page shows the alternatives
beside it rather than asserting the conclusion. That is the point of the page.

EVERY FIGURE ABOVE IS MEASURED ON A `runner_sarr` REBUILT UNDER THE MODEL IN
THIS PACKAGE -- `sarr.DERIVE_VERSION`, sarr-1.1 as written. The fundamental
stream is only as good as the scores it reads and those have moved twice: the
same grid on sarr-1.0 rows gives 2.3360 at w=1.00 and on the pre-draw model
2.3716. The weight was 0.00 on all three. If the table holds an older
derive_version the numbers below describe a model the page is not showing, so
rebuild before re-deriving them.

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
    "log_loss": {"uniform": 2.4903, "fundamental": 2.3096, "market": 2.0505},
    # Keyed by a fixed 2dp string, not a float: JSON turns 1.0 into "1.0"
    # and a reader looking up "1" finds nothing.
    "log_loss_by_weight": {"0.00": 2.0505, "0.10": 2.0609,
                           "0.32": 2.0952, "1.00": 2.3096},
    # The same grid on every race with a complete book, which is the
    # population the page runs on rather than the subset it could be fitted on
    # before the fundamental stream could carry an unrated runner.
    "partial": {"races": 1617, "test_races": 535, "fitted_weight": 0.0,
                "log_loss_by_weight": {"0.00": 2.0155, "0.10": 2.0242,
                                       "0.32": 2.0536, "1.00": 2.2395}},
}


def fundamental_probability(sarr: list[float], beta: float = BETA, *,
                            mass: float = 1.0) -> np.ndarray:
    """SARR scores to win probabilities. Lower SARR is better, hence the sign.

    Centred before exponentiating so the result depends on the SPREAD of the
    field's scores and not on their level, which drifts with the reference
    window. Without the centring a whole meeting's probabilities move when the
    reference is rebuilt, and nothing on the page would explain why.

    A RUNNER SARR COULD NOT RATE ARRIVES AS None AND LEAVES AS NaN. It used to
    take the whole column with it: the caller blanked the stream unless every
    runner scored, on the argument that a softmax over part of a field is
    normalised against a denominator missing terms. The argument is right and
    the remedy was wrong, because the case is not rare. SARR needs two prior
    runs, every card carries debutants, and 65.2% of the 1,712 races in the
    archive hold at least one runner it will not score -- so the page built to
    show a model beside the market showed no model on two races in three.

    The denominator is fixable instead. `mass` is the share of the book these
    scores are allowed to hold between them, so a caller that knows what the
    market gives the rated group can hand that over and keep the two streams on
    one scale. The unrated slots stay NaN rather than being filled with a
    plausible number, because the model has no opinion about them and `blend`
    needs to be able to tell that apart from a low one.

    Centring uses only the scored runners, which is the same quantity it always
    was on a fully scored field: `mass` defaults to 1.0 and a field with
    nothing missing returns exactly what it returned before.
    """
    s = np.asarray(sarr, dtype=float)
    if s.size == 0:
        return s
    out = np.full(s.shape, np.nan)
    rated = ~np.isnan(s)
    if not rated.any():
        return out
    z = -beta * (s[rated] - s[rated].mean())
    p = np.exp(z - z.max())
    out[rated] = mass * p / p.sum()
    return out


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
    # A runner the fundamental stream has no opinion about falls through to its
    # market price, at every weight -- w*m + (1-w)*m is m. That is the honest
    # reading of "unrated": not a horse with no chance, which is what dropping
    # it would say, and not the field average, which is a number we invented.
    # It also keeps the column summing to 100% without the renormalisation
    # below having to rescue it, provided the caller gave `mass` the market's
    # own share of the rated group.
    fund = np.where(np.isnan(fund), market, fund)
    out = weight * fund + (1.0 - weight) * market
    total = out.sum()
    return out / total if total else out

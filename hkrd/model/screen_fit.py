"""Fitting the Screen's weights: an exploded logit over the first three home.

The Screen is asked "is this horse a chance", which is closer to "does it
place" than "does it win", so the fit reads the first THREE finishers of every
race rather than the winner alone: the winner chosen from the field, then the
second from those left, then the third. That is Harville's model of a finish,
the same one `derive/probability` prices places with, fitted rather than
assumed -- and it uses three times the information a win-only fit does.

A small ridge (`L2`) keeps a factor that fires a few hundred times from
taking an extreme weight on a lucky season. Standard errors come from the
Fisher information at the fitted weights.

Pure numpy and scipy. `jobs/fit_screen` builds the matrix and calls this.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import minimize

__all__ = ["Sets", "choice_sets", "fit", "win_probabilities", "L2"]

L2 = 1.0
DEPTH = 3


class Sets:
    """Every risk set in a stack of races: for each race, the whole field
    choosing the winner, the rest choosing second, the rest choosing third."""

    def __init__(self, rows: np.ndarray, sets: np.ndarray, chosen: np.ndarray):
        self.rows, self.sets, self.chosen = rows, sets, chosen
        self.starts = np.r_[0, np.flatnonzero(np.diff(sets)) + 1]
        self.lens = np.diff(np.r_[self.starts, len(rows)])


def choice_sets(race_ids: np.ndarray, places: np.ndarray,
                depth: int = DEPTH) -> Sets:
    """Build the risk sets. `race_ids` groups rows into races (contiguous or
    not); `places` is the finishing position, NaN for a non-finisher, who stays
    in every risk set and is never chosen."""
    rows: list[int] = []
    sets: list[int] = []
    chosen: list[float] = []
    sid = 0
    order = np.argsort(race_ids, kind="stable")
    bounds = np.flatnonzero(np.diff(race_ids[order])) + 1
    for group in np.split(order, bounds):
        pl = places[group]
        finished = ~np.isnan(pl)
        if not (pl == 1).any() or finished.sum() < 2:
            continue
        ranked = group[np.argsort(np.where(finished, pl, np.inf), kind="stable")]
        left = list(group)
        # A choice needs two to choose between: the last runner standing is
        # no choice, but a finisher left with a non-finisher still is one.
        for k in range(min(depth, int(finished.sum()), len(group) - 1)):
            who = ranked[k]
            rows.extend(left)
            sets.extend([sid] * len(left))
            chosen.extend(1.0 if x == who else 0.0 for x in left)
            left.remove(who)
            sid += 1
    return Sets(np.asarray(rows), np.asarray(sets), np.asarray(chosen))


def _probs(S: Sets, s: np.ndarray) -> np.ndarray:
    m = np.repeat(np.maximum.reduceat(s, S.starts), S.lens)
    e = np.exp(s - m)
    return e / np.repeat(np.add.reduceat(e, S.starts), S.lens)


def _objective(beta: np.ndarray, X: np.ndarray, S: Sets, l2: float
               ) -> tuple[float, np.ndarray]:
    Xr = X[S.rows]
    s = Xr @ beta
    m = np.maximum.reduceat(s, S.starts)
    z = np.add.reduceat(np.exp(s - np.repeat(m, S.lens)), S.starts)
    ll = (S.chosen * s).sum() - (np.log(z) + m).sum()
    grad = Xr.T @ (S.chosen - _probs(S, s))
    return -ll + l2 * beta @ beta, -grad + 2 * l2 * beta


def fit(X: np.ndarray, race_ids: np.ndarray, places: np.ndarray, *,
        l2: float = L2) -> tuple[np.ndarray, np.ndarray]:
    """Weights and their standard errors."""
    S = choice_sets(race_ids, places)
    res = minimize(_objective, np.zeros(X.shape[1]), args=(X, S, l2), jac=True,
                   method="L-BFGS-B")
    if not res.success:
        raise RuntimeError(f"screen fit did not converge: {res.message}")
    Xr = X[S.rows]
    p = _probs(S, Xr @ res.x)
    xbar = np.add.reduceat(Xr * p[:, None], S.starts)
    info = (Xr * p[:, None]).T @ Xr - xbar.T @ xbar
    se = np.sqrt(np.diag(np.linalg.inv(info + 2 * l2 * np.eye(len(info)))))
    return res.x, se


def win_probabilities(X: np.ndarray, race_ids: np.ndarray,
                      beta: Sequence[float]) -> np.ndarray:
    """Softmax of the strengths within each race."""
    s = X @ np.asarray(beta)
    order = np.argsort(race_ids, kind="stable")
    starts = np.r_[0, np.flatnonzero(np.diff(race_ids[order])) + 1]
    lens = np.diff(np.r_[starts, len(order)])
    so = s[order]
    e = np.exp(so - np.repeat(np.maximum.reduceat(so, starts), lens))
    out = np.empty_like(s)
    out[order] = e / np.repeat(np.add.reduceat(e, starts), lens)
    return out

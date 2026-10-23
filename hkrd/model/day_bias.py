"""A meeting's own bias: what its run races say, and whether that has ever carried over.

The question (Brett, 24 Oct): on a race day, do the races already run say anything about how the
rest will be run -- the leaders holding on, the rail being the place to be, the inside gates
doing well? Each is a conditional logit on the winner with the closing price as the offset, so a
coefficient is what that feature did BEYOND what the market already knew:

  front   1 - first-call position as a share of the field (1 led, 0 last)   HKJC running positions
  inside  1 - (draw - 1) / (field - 1) (1 gate one)                          the card
  rail    1 on the rail turning in, 0 wider; the race's mean where unknown  Brett's straight-lane
                                                                            reads on race day; the
                                                                            Comments on Running in
                                                                            the archive

THE RECORD (`record`), measured on the archive on 24 Oct over 614 meetings from 2019: a meeting's
first half does not predict its second half on any of the three (r -0.04, -0.04, +0.05) -- where
the same test, on synthetic meetings of the archive's size, reliably finds days that differ by
about 1.8 lengths between leading and racing last (a day sd of 0.6 on the log odds; z 4.2) and
can miss days that differ by 0.7 to 1.2 -- and carrying the day's read from earlier races into the
next race's chances made them WORSE than the price alone (log loss +0.0043 a race, in seven
seasons of eight). On rain-affected going (30 meetings) the rail reads r +0.24, z 1.3: a hint on
too few days to be a finding. So the day's read is shown as what happened, beside this record,
and never moves a simulation unless Brett sets it to.

Pure: frames in, numbers out. jobs/fit_day_bias stores the record; query/shape_day reads the day.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

__all__ = ["COLS", "covariates", "fit", "persistence", "prediction", "record", "logit_to_lengths"]

COLS = ("front", "inside", "rail")

# The smallest day effect the persistence test finds reliably at the archive's size: a day sd of
# 0.6 on the winner's log odds, leading against racing last, in lengths on the simulator's noise
# (measured 24 Oct on synthetic meetings, 614 of ten races; 0.4 gave z 2.5, 0.25 gave z 1.4).
DETECTABLE_LENGTHS = 1.8


def covariates(d: pd.DataFrame) -> pd.DataFrame:
    """front, inside and rail for runs with a first call; `d` has early_frac, draw, field and,
    where known, rail_obs (1 on the rail turning in, 0 wider)."""
    out = d.copy()
    out["front"] = 1 - out.early_frac
    out["inside"] = 1 - (out.draw - 1) / (out.field - 1).clip(lower=1)
    obs = out["rail_obs"] if "rail_obs" in out else pd.Series(np.nan, index=out.index)
    out["rail"] = obs.fillna(obs.groupby(out.race_id).transform("mean")).fillna(0.2)
    return out


def fit(rows: pd.DataFrame, cols: Sequence[str] = COLS, *, ridge: float = 0.0,
        iters: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Conditional logit on the winner, log p_mkt as offset (Newton). `rows`: one race's runners
    contiguous, columns race_id, won, p_mkt and `cols`. Returns (beta, standard errors); `ridge`
    shrinks towards no bias, which is what a handful of races needs."""
    rows = rows.sort_values("race_id", kind="stable")
    X = rows[list(cols)].to_numpy(float)
    off, y = np.log(rows.p_mkt.to_numpy(float)), rows.won.to_numpy(float)
    g = pd.factorize(rows.race_id)[0]
    starts = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    k = len(cols)
    b = np.zeros(k)
    H = -np.eye(k)
    for _ in range(iters):
        s = off + X @ b
        s = s - np.repeat(np.maximum.reduceat(s, starts), np.bincount(g))
        e = np.exp(s)
        p = e / np.bincount(g, e)[g]
        xbar = np.stack([np.bincount(g, p * X[:, j])[g] for j in range(k)], 1)
        dx = X - xbar
        grad = (y[:, None] * dx).sum(0) - ridge * b
        H = -(dx * p[:, None]).T @ dx - ridge * np.eye(k)
        step = np.linalg.solve(H, grad)
        b = b - step
        if np.abs(step).max() < 1e-9:
            break
    return b, np.sqrt(np.diag(np.linalg.inv(-H)))


def logit_to_lengths(beta: float, sigma: float) -> float:
    """A coefficient on the winner's log odds, in lengths at the post: with the finishing noise sd
    `sigma` (the simulator's, ~4 lengths) the winning margin's spread is about 0.78 sigma (the
    extreme-value scale), so a length is worth beta / (0.78 sigma) on the log odds."""
    return float(beta * 0.78 * sigma)


def _halves(d: pd.DataFrame, cols: Sequence[str], min_races: int) -> np.ndarray:
    rows = []
    for _, m in d.groupby("race_date"):
        nos = sorted(m.race_no.unique())
        if len(nos) < min_races:
            continue
        h = len(nos) // 2
        try:
            ba, sa = fit(m[m.race_no.isin(nos[:h])], cols, ridge=0.5)
            bb, sb = fit(m[m.race_no.isin(nos[h:])], cols, ridge=0.5)
        except np.linalg.LinAlgError:
            continue
        rows.append(np.r_[ba, bb, sa, sb])
    return np.array(rows)


def persistence(d: pd.DataFrame, cols: Sequence[str] = COLS, *, min_races: int = 8) -> dict:
    """Each meeting's first half against its second: the correlation across meetings, its z, and
    the spread of the day's own bias -- the COVARIANCE of the two halves, because a day's bias is
    shared by both halves and their sampling noise is not (0 = no day bias to find). On the halves'
    shrunk scale, so it is a test of whether there is one, not a size."""
    R = _halves(d, cols, min_races)
    k, n = len(cols), len(R)
    out = {"meetings": n}
    for i, c in enumerate(cols):
        a, b = R[:, i], R[:, k + i]
        r = float(np.corrcoef(a, b)[0, 1])
        cov = float(np.cov(a, b)[0, 1])
        out[c] = dict(r=round(r, 3), z=round(float(np.arctanh(r) * np.sqrt(n - 3)), 2),
                      day_sd=round(float(np.sqrt(max(cov, 0.0))), 3))
    return out


def prediction(d: pd.DataFrame, *, first: int = 4, ridge: float = 2.0) -> dict:
    """From race `first` on, the day's bias fitted on the races before it, used on the next race
    with what is known before it (the expected front from habitual early speed, front_exp; the
    draw): the winner's log loss against the price alone, clustered by meeting."""
    gains, dates, seasons = [], [], []
    for date, m in d.groupby("race_date"):
        nos = sorted(m.race_no.unique())
        for i, no in enumerate(nos):
            if i < first - 1:
                continue
            try:
                b, _ = fit(m[m.race_no.isin(nos[:i])], ("front", "inside"), ridge=ridge)
            except np.linalg.LinAlgError:
                continue
            now = m[m.race_no == no]
            x = now[["front_exp", "inside"]].to_numpy(float)
            s = np.log(now.p_mkt.to_numpy(float)) + x @ b
            p = np.exp(s - s.max())
            p /= p.sum()
            w = now.won.to_numpy() == 1
            price = now.p_mkt.to_numpy(float) / now.p_mkt.sum()
            gains.append(float(np.log(p[w][0]) - np.log(price[w][0])))
            dates.append(date)
            seasons.append(int(now.season.iloc[0]))
    g = pd.Series(gains)
    by_meeting = g.groupby(pd.Series(dates)).sum()
    G = len(by_meeting)
    se = float(np.sqrt(G / (G - 1) * (by_meeting ** 2).sum()) / len(g)) if G > 1 else None
    per_season = g.groupby(pd.Series(seasons)).mean()
    return dict(races=len(g), gain_per_race=round(float(g.mean()), 4), se=round(se, 4) if se else None,
                seasons_better=int((per_season > 0).sum()), seasons=int(len(per_season)))


def record(d: pd.DataFrame) -> dict:
    """The whole record: persistence on all going and on rain-affected going, the prediction test,
    and the base rates the day's read is set against (winners from the front third, placegetters
    on the rail turning in), by venue."""
    wet = d.going.fillna("G").str.upper().str.strip().isin(["Y", "GY", "YS", "S", "H", "WS", "SL"])
    win = d[d.won == 1]
    placed = d[d.place.between(1, 3) & d.rail_obs.notna()]
    base = {v: dict(winner_front_third=round(float((g.early_frac < 1 / 3).mean()), 3),
                    placed_on_rail=round(float(placed[placed.venue == v].rail_obs.mean()), 3))
            for v, g in win.groupby("venue")}
    return dict(seasons=[int(d.season.min()), int(d.season.max())], persistence=persistence(d),
                persistence_wet=persistence(d[wet], min_races=6), prediction=prediction(d), base_rates=base,
                detectable_lengths=DETECTABLE_LENGTHS)

"""The fundamental model: every declared runner's chance before any price exists.

One pooled LightGBM model with a race-softmax objective (a conditional logit):
a runner's chance is exp(score) over the sum of exp(score) across its field,
so the chances in a race always add to 1 and every runner is trained against
the horses it actually met. Ported from `claude/tools/model-lab/model.py`.

WHAT IT IS, measured (gbm-SPEC §8, walk-forward 2021-22 to 2025-26): R² 0.126
against the closing price's 0.192, top pick 25.9% against the favourite's
30.6%. A calibrated form model that sits clearly behind the price. Nothing
built on it may present the gap to the price as an edge (§10).

Pure: no database. LightGBM is imported here and by the jobs that train and
score; never in a request (§6) -- pages read `runner_gbm`.

Two LightGBM facts that bit the lab and will bite again (§5):
- a custom objective and metric receive the raw score INCLUDING `init_score`;
  `Booster.predict(raw_score=True)` returns it EXCLUDING it (moot here, the
  fundamental model has no init score -- it matters to phase 2's anchored one);
- categories: the booster records the training frame's categories and maps a
  scoring frame onto them, so a venue it never saw becomes NaN, not a new level.
"""
from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from hkrd.derive.features import CATEGORICAL, FEATURES, GROUPS
from hkrd.derive.probability import place_from_win

__all__ = ["KIND", "RECIPE", "INPUTS", "PARAMS", "FIRST_TRAIN", "Races", "prep", "fit",
           "choose_rounds", "predict", "contributions", "assume_body_weight", "place_chances",
           "evaluate",
           "recipe", "from_text"]

KIND = "fundamental"
RECIPE = "gbm-1.0"             # bump when anything below changes what a fit produces
INPUTS = FEATURES + CATEGORICAL
# The lab's settings, fixed: tuning them against recent meetings is fitting the
# test (§10). num_threads is part of the recipe -- LightGBM sums histograms per
# thread, and two thread counts are two slightly different models (§14.10).
PARAMS: dict[str, Any] = dict(learning_rate=0.03, num_leaves=15, min_data_in_leaf=200,
                              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1,
                              lambda_l2=10.0, verbose=-1, seed=7, num_threads=2)
FIRST_TRAIN = 2020             # 2019-20 is history only: every horse in it looks first-up
MAX_ROUNDS, PATIENCE, SLACK, MIN_ROUNDS = 3000, 150, 1.1, 50


class Races:
    """Contiguous race blocks in a frame sorted by race."""

    def __init__(self, race_id: np.ndarray) -> None:
        _, starts, counts = np.unique(race_id, return_index=True, return_counts=True)
        order = np.argsort(starts)
        self.starts, self.counts = starts[order], counts[order]
        self.idx = np.repeat(np.arange(len(self.starts)), self.counts)

    def softmax(self, s: np.ndarray) -> np.ndarray:
        m = np.maximum.reduceat(s, self.starts)
        e = np.exp(s - m[self.idx])
        return e / np.add.reduceat(e, self.starts)[self.idx]

    def nll(self, p: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Per race: minus the log of the chance given to the winner(s)."""
        return -np.add.reduceat(y * np.log(np.clip(p, 1e-12, 1)), self.starts)


def _objective(races: Races, y: np.ndarray):
    def fobj(preds: np.ndarray, data: lgb.Dataset) -> tuple[np.ndarray, np.ndarray]:
        p = races.softmax(preds)
        return p - y, np.maximum(p * (1 - p), 1e-6)
    return fobj


def _metric(races: Races, y: np.ndarray):
    def feval(preds: np.ndarray, data: lgb.Dataset) -> tuple[str, float, bool]:
        return "race_nll", float(races.nll(races.softmax(preds), y).mean()), False
    return feval


def prep(frame: pd.DataFrame, rows: np.ndarray | pd.Series | None = None) -> pd.DataFrame:
    """The model's input matrix: one copy of the input columns (of `rows`), with
    the categories typed as the lab typed them -- from the rows at hand."""
    X = frame.loc[rows, INPUTS] if rows is not None else frame[INPUTS].copy()
    for c in CATEGORICAL:
        X[c] = X[c].astype("category")
    return X


def _data(frame: pd.DataFrame, rows, reference: lgb.Dataset | None = None
          ) -> tuple[lgb.Dataset, Races, np.ndarray]:
    part = frame.loc[rows, ["race_id", "y"]] if rows is not None else frame[["race_id", "y"]]
    y = part["y"].to_numpy(dtype=float)
    races = Races(part["race_id"].to_numpy())
    return lgb.Dataset(prep(frame, rows), label=y, reference=reference), races, y


def fit(frame: pd.DataFrame, rows=None, *, rounds: int | None = None,
        valid_rows=None, params: dict[str, Any] | None = None) -> lgb.Booster:
    """Train on `rows` of `frame` (sorted by race). With `valid_rows`, stop
    early on them (the booster's `best_iteration` is the answer); otherwise
    grow exactly `rounds` trees. `params` replaces PARAMS (another recipe's)."""
    train, races, y = _data(frame, rows)
    params = {**(params or PARAMS), "objective": _objective(races, y)}
    kw: dict[str, Any] = {}
    if valid_rows is not None:
        valid, vraces, vy = _data(frame, valid_rows, reference=train)
        kw = dict(valid_sets=[valid], feval=_metric(vraces, vy),
                  callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
    return lgb.train(params, train, num_boost_round=rounds or MAX_ROUNDS, **kw)


def choose_rounds(frame: pd.DataFrame, train_rows, valid_rows) -> int:
    """How many trees: early stopping on the most recent season held out,
    plus a tenth for the refit on more data (the lab's rule)."""
    best = fit(frame, train_rows, valid_rows=valid_rows).best_iteration
    return max(MIN_ROUNDS, int(best * SLACK))


def predict(booster: lgb.Booster, frame: pd.DataFrame, rows=None) -> np.ndarray:
    """Win chances, each race's summing to 1 over the rows given."""
    ids = (frame.loc[rows, "race_id"] if rows is not None else frame["race_id"]).to_numpy()
    s = booster.predict(prep(frame, rows), raw_score=True)
    return Races(ids).softmax(s)


def contributions(booster: lgb.Booster, frame: pd.DataFrame) -> pd.DataFrame:
    """Per runner and factor group: its log-contribution against this field's
    mean, so exp(value) reads as "x vs this field" (§5). SHAP accounting: the
    groups add back to the score exactly; they are not causes."""
    raw = booster.predict(prep(frame), pred_contrib=True)[:, :-1]
    C = pd.DataFrame(raw, columns=booster.feature_name(), index=frame.index)
    G = pd.DataFrame({k: C[v].sum(axis=1) for k, v in GROUPS.items()})
    return G - G.groupby(frame["race_id"].to_numpy()).transform("mean")


def assume_body_weight(frame: pd.DataFrame) -> int:
    """A card runner with no body weight and no run to carry one from gets the
    median debutant's (gbm-SPEC §13.2): the model never met a missing body
    weight in training, and would read the gap as information. The median is
    of debutants' declared weights over the card's season and the two before.
    Changes `frame` in place; returns how many runners it filled."""
    card = frame["is_card"].to_numpy(dtype=bool)
    if not card.any():
        return 0
    season = frame.loc[card, "season"].max()
    deb = (~card) & (frame["n_prior"] == 0) & (frame["season"] >= season - 2)
    gap = card & frame["declared_weight"].isna().to_numpy()
    frame.loc[gap, "declared_weight"] = frame.loc[deb, "declared_weight"].median()
    return int(gap.sum())


def place_chances(p: np.ndarray, race_id: np.ndarray) -> np.ndarray:
    """Harville-Henery from the win chances: three places paid in a field of
    seven or more, two in a smaller one."""
    races = Races(race_id)
    return np.concatenate([place_from_win(p[s:s + c], places=3 if c >= 7 else 2)
                           for s, c in zip(races.starts, races.counts)])


def evaluate(frame: pd.DataFrame, col: str, base: str = "p_mkt") -> dict[str, float]:
    """Per-race log loss against `base`, and R² against a field of equal
    chances (0 = no better than a guess, 1 = certain of every winner)."""
    races = Races(frame["race_id"].to_numpy())
    y = frame["y"].to_numpy()
    nll, nll_b = races.nll(frame[col].to_numpy(), y), races.nll(frame[base].to_numpy(), y)
    unif = np.log(races.counts).sum()
    d = nll_b - nll
    top = frame.loc[frame.groupby("race_id")[col].idxmax(), "won"]
    fav = frame.loc[frame.groupby("race_id")[base].idxmax(), "won"]
    return dict(races=len(nll), nll=float(nll.mean()), nll_base=float(nll_b.mean()),
                gain=float(d.mean()), gain_se=float(d.std(ddof=1) / np.sqrt(len(d))),
                r2=float(1 - nll.sum() / unif), r2_base=float(1 - nll_b.sum() / unif),
                top_pick=float(top.mean()), top_pick_base=float(fav.mean()))


def recipe(rounds: int) -> dict[str, Any]:
    """Everything that decides what a fit produces, for `params_json`: two
    builds of the library are never compared as if they were one (§14.10)."""
    return {"recipe": RECIPE, "params": PARAMS, "rounds": rounds,
            "lightgbm": lgb.__version__, "first_train": FIRST_TRAIN}


def from_text(model_text: str) -> lgb.Booster:
    return lgb.Booster(model_str=model_text)

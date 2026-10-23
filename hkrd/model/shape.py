"""How a race will be run, before it is run: four shape models (racesim-SPEC §6).

  early    first-call position as a share of the field (0 in front)   every settled run
  lead     P(first at the first call), a softmax over the field          every settled run
  lane     the settling lane (1 rail .. 4 four wide or wider)            runs whose comment states it
  nocover  P(no cover before the straight)                               runs whose comment states a lane

A port of the 16 Oct study's Part C (lab race-shape-2d: exante.py's features, common2d.py's LightGBM
plumbing, score_card.py, score_lanes.py). Each is pooled across courses and averaged over five seeds;
each seed's rounds are found by early stopping on the last settled season, then the model is refitted
on every season from 2020/21 to `train_to`. The features use previous meetings only -- a horse's last
three runs, its rider's forwardness over the 365 days before the day -- and the race's declared facts.

The settle model beats the Speed Map on 26/27 (first-call position off by 2.17 places against 2.31 on
1,465 runners; its top pick led 39% of races against 28%) -- which is a separate decision, not this
model's to make (§13.10). The lane and cover models learn from the Comments on Running, so they inherit
its selection: a lane is written down when it was notable.

Pure: frames in, boosters and predictions out. LightGBM is imported here and by the jobs that fit and
score; never in a request.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

__all__ = ["VERSION", "KINDS", "SEEDS", "RUNNER", "features", "card_rows", "fit_rounds", "fit_final",
           "predict", "checks", "first_time_gear"]

VERSION = "shape-1.0"
KINDS = ("early", "lead", "lane", "nocover")
SEEDS = (7, 11, 23, 42, 99)
FIRST_SEASON = 2020
CATEGORICAL = ("vs", "going_g", "vsd", "course")
# common2d.PARAMS, with score_card's thread count: the lead model's race softmax
LEAD = dict(learning_rate=0.03, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.7,
            bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=2)
# score_card.REG / score_lanes.REG: early, lane, nocover
REG = dict(learning_rate=0.03, num_leaves=31, min_data_in_leaf=200, feature_fraction=0.8,
           bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=2)

RUNNER = ["hab_early", "hab_mid", "l1_early_frac", "l1_mid_frac", "hab_wide3_pre", "hab_nocover_pre",
          "hab_rail_pre", "hab_stated", "hab_lane", "hab_slow", "n_prior", "days_since", "dist_change",
          "gear_first", "gear_bv_first", "gear_removed", "rider_fwd", "j_n", "ndraw", "draw", "field",
          "early_rank", "n_leaders", "n_onpace", "best_rival_early", "quicker_inside", "race_hab_mean",
          "race_top2_early", "cls_num", "vsd", "course"]


# ── features: previous meetings only ──────────────────────────────────────────────────────────
def first_time_gear(g: object) -> tuple[int, int, int]:
    """(any first-time gear, first-time blinkers or visor, any gear removed): HKJC's own suffix."""
    if g is None or (isinstance(g, float) and np.isnan(g)):
        return 0, 0, 0
    toks = [t.strip() for t in str(g).split("/") if t.strip()]
    ft = [t for t in toks if re.fullmatch(r"[A-Z]+1", t)]
    bv = [t for t in ft if t in ("B1", "V1", "BO1")]
    rm = [t for t in toks if t.endswith("-")]
    return int(bool(ft)), int(bool(bv)), int(bool(rm))


def card_rows(card: pd.DataFrame) -> pd.DataFrame:
    """A stored card's declared runners as rows of the trip frame (`is_card` 1). `card`: runners
    joined to races for one date; the rider's claim ("(-7)") is not the rider."""
    c = card.copy()
    c["jockey_card"] = c.jockey
    c["jockey"] = c.jockey.str.replace(r"\s*\(.*\)$", "", regex=True)
    c["date"] = pd.to_datetime(c.race_date)
    c["season"] = np.where(c.date.dt.month >= 8, c.date.dt.year, c.date.dt.year - 1)
    c["race_id"] = c.race_date + "_" + c.race_no.astype(str).str.zfill(2)
    c["field"] = c.groupby("race_no").horse_no.transform("size")
    c["vs"] = c.venue + "_" + c.surface
    c["dist"] = c.distance.astype(float)
    c["is_card"] = 1
    return c


def features(trip: pd.DataFrame) -> pd.DataFrame:
    """Every row's pre-race inputs. `trip`: the trip frame (model/shape_frame), with any card rows
    appended (`is_card` 1, no result)."""
    d = trip.sort_values(["horse_name", "date", "race_no"]).copy()
    g = d.groupby("horse_name", sort=False)
    d["n_prior"] = g.cumcount()
    d["days_since"] = (d.date - g.date.shift(1)).dt.days
    d["dist_change"] = d.dist - g.dist.shift(1)
    for c in ("early_frac", "mid_frac"):
        sh = [g[c].shift(k) for k in (1, 2, 3)]
        d[f"hab_{c.split('_')[0]}"] = pd.concat(sh, axis=1).mean(axis=1)
        d[f"l1_{c}"] = sh[0]
    for c in ("wide3_pre", "nocover_pre", "rail_pre", "stated"):
        d[f"hab_{c}"] = pd.concat([g[c].shift(k) for k in (1, 2, 3)], axis=1).mean(axis=1)
    d["hab_lane"] = pd.concat([g["lane_max_pre"].shift(k).astype(float) for k in (1, 2, 3)], axis=1).mean(axis=1)
    d["hab_slow"] = pd.concat([g["slow_bad"].shift(k) for k in (1, 2, 3)], axis=1).mean(axis=1)
    gear = d.gear.map(first_time_gear)
    d["gear_first"] = gear.map(lambda t: t[0])
    d["gear_bv_first"] = gear.map(lambda t: t[1])
    d["gear_removed"] = gear.map(lambda t: t[2])

    # rider forwardness: the rider's mean (first-call position - the horse's habit), previous 365 days,
    # evaluated for every (rider, day) in the frame -- a card day included, which has no results yet
    d["fwd_resid"] = d.early_frac - d.hab_early
    daily = (d.dropna(subset=["fwd_resid"]).groupby(["jockey", "date"])
               .agg(n=("fwd_resid", "size"), s=("fwd_resid", "sum")).reset_index().sort_values(["jockey", "date"]))
    by_jockey = {jk: x for jk, x in daily.groupby("jockey", sort=False)}
    want = d[["jockey", "date"]].dropna().drop_duplicates().sort_values(["jockey", "date"])
    rows = []
    for jk, q in want.groupby("jockey", sort=False):
        qd = q.date.values
        x = by_jockey.get(jk)
        if x is None:
            rows.append(pd.DataFrame({"jockey": jk, "date": qd, "j_n": 0.0, "j_s": 0.0}))
            continue
        dates = x.date.values
        cs = np.vstack([np.zeros(2), x[["n", "s"]].cumsum().values])
        hi = np.searchsorted(dates, qd, side="left")                          # strictly before the day
        lo = np.searchsorted(dates, qd - np.timedelta64(365, "D"), side="left")
        w = cs[hi] - cs[lo]
        rows.append(pd.DataFrame({"jockey": jk, "date": qd, "j_n": w[:, 0], "j_s": w[:, 1]}))
    d = d.merge(pd.concat(rows), on=["jockey", "date"], how="left")
    d["rider_fwd"] = d.j_s.fillna(0) / (d.j_n.fillna(0) + 30.0)          # shrunk toward 0 with 30 rides

    key = ["race_date", "race_no"]
    d["ndraw"] = (d.draw - 1) / (d.field - 1).clip(lower=1)
    d["early_rank"] = d.groupby(key).hab_early.rank(method="average") / d.field
    d["n_leaders"] = d.groupby(key).hab_early.transform(lambda s: (s < 0.15).sum())
    d["n_onpace"] = d.groupby(key).hab_early.transform(lambda s: (s < 0.35).sum())
    srt = d.groupby(key).hab_early.transform(lambda s: s.nsmallest(1).max())
    d["best_rival_early"] = np.where(d.hab_early == srt,
                                     d.groupby(key).hab_early.transform(lambda s: s.nsmallest(2).max()), srt)

    def inside(gr: pd.DataFrame) -> pd.Series:          # quicker-habit horses drawn inside this one
        h, dr = gr.hab_early.values, gr.draw.values
        return pd.Series([np.nansum((dr < dr[i]) & (h < h[i])) for i in range(len(gr))], index=gr.index)
    d["quicker_inside"] = d.groupby(key, group_keys=False)[["hab_early", "draw"]].apply(inside)
    d["race_hab_mean"] = d.groupby(key).hab_early.transform("mean")
    d["race_top2_early"] = d.groupby(key).hab_early.transform(lambda s: s.nsmallest(2).mean())
    d["course"] = d.course.fillna("?").astype(str)
    d["vsd"] = (d.vs.astype(str) + "_" + d.distance.astype(int).astype(str)).astype(str)
    d["cls_num"] = d.race_class.map(lambda c: float(c) if str(c) in {"1", "2", "3", "4", "5"} else 0.0)
    d["going_g"] = d.going.fillna("G").astype(str).str.upper().str[:2]
    d["lead"] = (d.early_frac == 0).astype(float)
    d["y_lead"] = d.lead / d.groupby("race_id").lead.transform("sum").replace(0, 1)
    d["wide_or_nocover"] = ((d.wide3_pre == 1) | (d.nocover_pre == 1)).astype(float)
    d["lane_s4"] = d.lane_settle.clip(upper=4)
    return d.sort_values(["race_id", "horse_no"]).reset_index(drop=True)


# ── the LightGBM plumbing (common2d) ──────────────────────────────────────────────────────────
class _Groups:
    """Races as contiguous blocks of rows: the softmax over a field, and its log loss."""

    def __init__(self, race_id: np.ndarray) -> None:
        _, self.starts, self.counts = np.unique(race_id, return_index=True, return_counts=True)
        order = np.argsort(self.starts)
        self.starts, self.counts = self.starts[order], self.counts[order]
        self.idx = np.repeat(np.arange(len(self.starts)), self.counts)

    def softmax(self, s: np.ndarray) -> np.ndarray:
        m = np.maximum.reduceat(s, self.starts)
        e = np.exp(s - m[self.idx])
        return e / np.add.reduceat(e, self.starts)[self.idx]

    def race_nll(self, p: np.ndarray, y: np.ndarray) -> np.ndarray:
        return -np.add.reduceat(y * np.log(np.clip(p, 1e-12, 1)), self.starts)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    X = df[RUNNER].copy()
    for c in CATEGORICAL:
        if c in X:
            X[c] = X[c].astype("category")
    return X


def _softmax_objective(g: _Groups, y: np.ndarray) -> Callable:
    def f(preds: np.ndarray, data: lgb.Dataset) -> tuple[np.ndarray, np.ndarray]:
        p = g.softmax(preds)
        return p - y, np.maximum(p * (1 - p), 1e-6)
    return f


def _race_nll(g: _Groups, y: np.ndarray) -> Callable:
    def f(preds: np.ndarray, data: lgb.Dataset) -> tuple[str, float, bool]:
        return "race_nll", float(g.race_nll(g.softmax(preds), y).mean()), False
    return f


_SPEC = {  # kind: (target, objective, which runs it learns from)
    "early": ("early_frac", "regression", lambda x: x.early_frac.notna() & x.hab_early.notna()),
    "lead": ("y_lead", None, lambda x: np.ones(len(x), bool)),
    "lane": ("lane_s4", "regression", lambda x: x.lane_s4.notna()),
    "nocover": ("nocover_pre", "binary", lambda x: x.stated == 1),
}


def _rows(hist: pd.DataFrame, kind: str) -> pd.DataFrame:
    return hist[_SPEC[kind][2](hist)]


def _train(kind: str, seed: int, tr: pd.DataFrame, rounds: int, va: pd.DataFrame | None = None) -> lgb.Booster:
    target, objective, _ = _SPEC[kind]
    dtr = lgb.Dataset(_prep(tr), label=tr[target].values, free_raw_data=False)
    kw: dict = {}
    if kind == "lead":
        params = {**LEAD, "seed": seed, "objective": _softmax_objective(_Groups(tr.race_id.values), tr[target].values)}
        if va is not None:
            kw = dict(valid_sets=[lgb.Dataset(_prep(va), label=va[target].values, reference=dtr)],
                      feval=_race_nll(_Groups(va.race_id.values), va[target].values),
                      callbacks=[lgb.early_stopping(150, verbose=False)])
    else:
        params = {**REG, "objective": objective, "seed": seed}
        if va is not None:
            kw = dict(valid_sets=[lgb.Dataset(_prep(va), label=va[target].values)],
                      callbacks=[lgb.early_stopping(150, verbose=False)])
    return lgb.train(params, dtr, rounds, **kw)


def fit_rounds(hist: pd.DataFrame, kind: str, *, season: int) -> list[int]:
    """Each seed's rounds: early stopping on season - 1, trained on 2020/21 to season - 2, x1.1."""
    d = _rows(hist, kind)
    itr = d[(d.season >= FIRST_SEASON) & (d.season <= season - 2)]
    iva = d[d.season == season - 1]
    return [max(50, int(_train(kind, sd, itr, 3000, iva).best_iteration * 1.1)) for sd in SEEDS]


def fit_final(hist: pd.DataFrame, kind: str, rounds: Sequence[int], *, train_to: int) -> list[lgb.Booster]:
    """One booster per seed on every season from 2020/21 to `train_to`."""
    d = _rows(hist, kind)
    tr = d[(d.season >= FIRST_SEASON) & (d.season <= train_to)]
    return [_train(kind, sd, tr, n) for sd, n in zip(SEEDS, rounds)]


def predict(boosters: dict[str, list[lgb.Booster]], rows: pd.DataFrame) -> pd.DataFrame:
    """pred_early, p_lead, exp_lane, p_nocover for `rows` (sorted by race, then horse)."""
    D = rows.sort_values(["race_id", "horse_no"])
    X = _prep(D)
    out = D[["race_date", "race_no", "horse_no", "race_id"]].copy()
    out["pred_early"] = np.mean([m.predict(X) for m in boosters["early"]], axis=0).clip(0, 1)
    g = _Groups(D.race_id.values)
    out["p_lead"] = np.mean([g.softmax(m.predict(X, raw_score=True)) for m in boosters["lead"]], axis=0)
    out["p_lead"] = out.p_lead / out.groupby("race_id").p_lead.transform("sum")
    out["exp_lane"] = np.mean([m.predict(X) for m in boosters["lane"]], axis=0).clip(1, 4)
    out["p_nocover"] = np.mean([m.predict(X) for m in boosters["nocover"]], axis=0)
    return out


# ── the record: §6's checks on the season so far ──────────────────────────────────────────────
def _auc(y: np.ndarray, p: np.ndarray) -> float:
    """Area under the ROC curve by ranks (Mann-Whitney), ties at the average rank."""
    r = pd.Series(p).rank(method="average").to_numpy()
    pos = y == 1
    n1, n0 = int(pos.sum()), int((~pos).sum())
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _clustered(d_race: pd.Series) -> tuple[float, float]:
    """Mean of a per-race difference and its SE clustered by meeting."""
    meet = pd.Series(d_race.index.str[:10], index=d_race.index)
    e = (d_race - d_race.mean()).groupby(meet.values).sum()
    G = len(e)
    return float(d_race.mean()), float(np.sqrt(G / (G - 1) * (e ** 2).sum()) / len(d_race))


def checks(season_rows: pd.DataFrame, pred: pd.DataFrame, speed_map: pd.DataFrame,
           lane_train: pd.DataFrame) -> dict:
    """The season so far, scored with models that stop at the season before: `season_rows` are its
    feature rows, `pred` the models' predictions for them, `speed_map` production's projection
    (runner_projection: settle, field_size), `lane_train` the lane model's training rows."""
    N = season_rows.merge(pred[["race_id", "horse_no", "pred_early", "p_lead", "exp_lane", "p_nocover"]],
                          on=["race_id", "horse_no"]).merge(speed_map, on=["race_date", "race_no", "horse_no"], how="left")
    H = N[N.early_frac.notna() & N.pred_early.notna() & N.prod_settle.notna()].copy()
    H["err_v2"] = (H.early_frac * (H.field - 1) + 1 - (H.pred_early * (H.field - 1) + 1)).abs()
    H["err_prod"] = (H.early_frac * (H.field - 1) + 1 - (H.prod_settle * (H.prod_field - 1) + 1)).abs()
    gain, se = _clustered((H.err_prod - H.err_v2).groupby(H.race_id).mean()) if len(H) else (np.nan, np.nan)
    L = N[N.early_frac.notna()]

    def led(col: str, how: str) -> float:
        x = L.dropna(subset=[col]).groupby("race_id")[col]
        idx = x.idxmax() if how == "max" else x.idxmin()
        return round(float(L.loc[idx.values].lead.mean()), 3)

    early = dict(runners=int(len(H)), races=int(H.race_id.nunique()), mae_model=round(float(H.err_v2.mean()), 3),
                 mae_speed_map=round(float(H.err_prod.mean()), 3), gain_per_runner=round(gain, 3), se=round(se, 3),
                 z=round(gain / se, 2) if se else None,
                 top_pick_led=dict(model=led("p_lead", "max"), speed_map=led("prod_settle", "min")),
                 mae_frac=round(float((H.early_frac - H.pred_early).abs().mean()), 4))
    lead = [dict(band=f"{lo:.2f}-{hi:.2f}", n=int(len(x)), shown=round(float(x.p_lead.mean()), 3),
                 actual=round(float(x.lead.mean()), 3))
            for lo, hi in ((0, .1), (.1, .3), (.3, .5), (.5, 1.01))
            for x in [L[(L.p_lead >= lo) & (L.p_lead < hi)]] if len(x)]
    g = lane_train.dropna(subset=["ndraw"])
    slope, icept = np.polyfit(g.ndraw.values, g.lane_s4.values, 1)
    Tl = N[N.lane_s4.notna()]
    gate = icept + slope * Tl.ndraw.fillna(0.5).values
    lane = dict(runs=int(len(Tl)), mae_model=round(float((Tl.lane_s4 - Tl.exp_lane).abs().mean()), 3),
                mae_gate_only=round(float((Tl.lane_s4 - gate).abs().mean()), 3),
                mae_constant=round(float((Tl.lane_s4 - g.lane_s4.mean()).abs().mean()), 3))
    Tn = N[N.stated == 1]
    nocover = dict(runs=int(len(Tn)), base_rate=round(float(Tn.nocover_pre.mean()), 3),
                   shown=round(float(Tn.p_nocover.mean()), 3),
                   auc=round(_auc(Tn.nocover_pre.to_numpy(), Tn.p_nocover.to_numpy()), 3) if Tn.nocover_pre.nunique() > 1 else None)
    return dict(early=early, lead=lead, lane=lane, nocover=nocover)

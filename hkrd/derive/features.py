"""The fundamental model's inputs: one ex-ante row per starter.

Ported from `claude/tools/model-lab/features.py`, the build behind every number
in gbm-SPEC §8 and §14.2, and held to equal it value for value
(`tests/test_gbm.py`). What makes a row ex-ante (gbm-SPEC §4):

1. A horse's history is its runs at EARLIER MEETINGS. Every lag, weighted
   average, rolling and expanding figure is shifted one run back.
2. Riders and trainers are read over the 365 days to YESTERDAY. A rider's win
   in race 2 never moves his figures for race 7 on the same card.
3. The race's own declared facts are allowed: draw, weight carried, body
   weight, class, trip, venue, going, the field after scratchings.
4. Today's price is never an input. The market's PAST opinion of the horse
   (`l1_logp_run`, `ew_logp_run`) is, and it is among the strongest.
5. Missing is NaN. The one exception is the card's body weight (§13.2): a card
   published days ahead has none, so it carries the last run's, rather than
   hand the model a gap it almost never met in training.
6. History joins on `horse_name`, never `horse_id` (AGENTS.md).

A CARD is a meeting not yet run. Every declared runner on it is kept except the
scratched (`W` place codes), and every result column on it is blanked here,
whatever the table holds -- so a price or a placing written early can never
reach the model. Everything else is history, and history is starters only.

Vectorised where the lab looped per horse: the same pandas window functions,
per group, so the port equals the lab bit for bit rather than nearly.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

__all__ = ["DERIVE_VERSION", "FEATURES", "CATEGORICAL", "GROUPS", "TIME", "FACTS",
           "build", "season_of", "class_num"]

DERIVE_VERSION = "feat-1.0"

# The lab's order, kept: feature_fraction samples columns by position, so a
# reordering is a different model.
FEATURES = [
    "field", "cls", "dist", "draw", "draw_frac", "actual_weight", "declared_weight",
    "rel_wt", "rating", "rel_rating", "race_no",
    "n_prior", "debut", "days_since", "days_since2", "prep_run",
    "l1_pos_frac", "l1_lbw", "l1_fmrp", "l1_early_frac", "l1_late_gain", "l1_beat_mkt",
    "l1_logp_run", "l1_early_dev", "l1_late_dev", "l1_n_trouble", "l1_field",
    "l1_n_wide", "l1_n_vet", "l1_eased", "l1_weakened", "l1_keen",
    "l2_pos_frac", "l2_lbw", "l2_fmrp", "l2_beat_mkt", "l2_logp_run",
    "l3_pos_frac", "l3_lbw", "l3_fmrp",
    "ew_pos_frac", "ew_fmrp", "ew_beat_mkt", "ew_lbw", "ew_early_frac", "ew_late_dev",
    "ew_early_dev", "ew_top3", "ew_won", "ew_logp_run",
    "best5_pos_frac", "best5_fmrp", "best5_lbw",
    "career_win_rate", "career_top3_rate",
    "n_vs", "top3_vs", "posf_vs", "n_dist", "top3_dist", "posf_dist",
    "cls_change", "dist_change", "wt_change", "bw_change", "rating_change",
    "jockey_change", "vs_change", "draw_vs_l3",
    "jockey_rides", "jockey_ae", "jockey_sr", "trainer_rides", "trainer_ae", "trainer_sr",
    "hab_early", "n_leaders", "early_rank",
    "rel_ew_fmrp", "rel_ew_pos_frac", "rel_ew_logp_run", "rel_jockey_ae",
    "rel_career_top3_rate",
]
CATEGORICAL = ["vs", "going_g"]

# The twelve factor groups the page speaks in (gbm-SPEC §14.1), from the lab's
# groups.py. Every model input is in exactly one (tests/test_gbm.py).
GROUPS = {
    "form": ["l1_pos_frac", "l1_lbw", "l1_fmrp", "l2_pos_frac", "l2_lbw", "l2_fmrp", "l3_pos_frac",
             "l3_lbw", "l3_fmrp", "ew_pos_frac", "ew_fmrp", "ew_lbw", "ew_top3", "ew_won",
             "best5_pos_frac", "best5_fmrp", "best5_lbw", "career_win_rate", "career_top3_rate",
             "rel_ew_fmrp", "rel_ew_pos_frac", "rel_career_top3_rate", "l1_beat_mkt", "l2_beat_mkt",
             "ew_beat_mkt", "l1_field"],
    "market history": ["l1_logp_run", "l2_logp_run", "ew_logp_run", "rel_ew_logp_run"],
    "rider": ["jockey_rides", "jockey_ae", "jockey_sr", "rel_jockey_ae", "jockey_change"],
    "stable": ["trainer_rides", "trainer_ae", "trainer_sr"],
    "draw": ["draw", "draw_frac", "draw_vs_l3"],
    "weight": ["actual_weight", "rel_wt", "wt_change", "declared_weight", "bw_change"],
    "rating & class": ["rating", "rel_rating", "rating_change", "cls", "cls_change"],
    "campaign": ["n_prior", "debut", "days_since", "days_since2", "prep_run"],
    "trip & track": ["dist", "dist_change", "vs_change", "n_vs", "top3_vs", "posf_vs", "n_dist",
                     "top3_dist", "posf_dist", "vs", "going_g"],
    "pace & sectionals": ["l1_early_frac", "l1_late_gain", "l1_early_dev", "l1_late_dev",
                          "ew_early_frac", "ew_late_dev", "ew_early_dev", "hab_early",
                          "n_leaders", "early_rank"],
    "trouble & vet": ["l1_n_trouble", "l1_n_wide", "l1_n_vet", "l1_eased", "l1_weakened", "l1_keen"],
    "race": ["field", "race_no"],
}
# The inputs made from a race time (the MODEL view's "where time enters").
TIME = ["l1_fmrp", "l2_fmrp", "l3_fmrp", "ew_fmrp", "best5_fmrp", "rel_ew_fmrp",
        "l1_early_dev", "l1_late_dev", "ew_late_dev", "ew_early_dev"]
# Not inputs: what the Race Day flags read beside them (gbm-SPEC §14.4).
FACTS = ["l1_place", "l1_vs", "l2_vs", "l3_vs"]

_KEEP = ["race_date", "race_no", "horse_no", "horse_name", "race_id", "season", "is_card",
         "y", "won", "top3", "p_mkt"]
# Blanked on a card: nothing about a race that has not been run is an outcome.
_RESULT = ["place", "place_code", "finish_time", "lengths_behind", "win_odds",
           "running_positions", "early_dev", "late_dev",
           "n_trouble", "n_wide", "n_vet", "eased", "weakened", "keen"]
# Only the lags something reads (the lab built 78 and used 39).
_LAGS = {
    1: ["pos_frac", "lbw", "fmrp", "early_frac", "late_gain", "beat_mkt", "logp_run",
        "early_dev", "late_dev", "n_trouble", "n_wide", "n_vet", "eased", "weakened", "keen",
        "field", "cls", "dist", "actual_weight", "declared_weight", "rating", "draw_frac",
        "jockey", "vs", "place"],
    2: ["pos_frac", "lbw", "fmrp", "beat_mkt", "logp_run", "early_frac", "draw_frac", "vs"],
    3: ["pos_frac", "lbw", "fmrp", "early_frac", "draw_frac", "vs"],
}
_EW_HALFLIFE = [("pos_frac", 3), ("fmrp", 3), ("beat_mkt", 3), ("lbw", 3), ("early_frac", 3),
                ("late_dev", 3), ("early_dev", 3), ("top3", 4), ("won", 6), ("logp_run", 3)]
_CONNECTION_DAYS = 365
_AE_PRIOR = 20.0      # shrink rider/trainer A/E towards 1 by 20 expected wins
_LEADER = 0.15        # a habitual leader: mean first-call share in the front 15%
_NEW_PREP_DAYS = 60   # a break this long starts a new preparation


def season_of(dates: pd.Series) -> np.ndarray:
    """HK seasons start in September; August belongs to the season ahead."""
    y = dates.dt.year
    return np.where(dates.dt.month >= 8, y, y - 1)


def class_num(c: object) -> float:
    """1-5 as numbers, Griffin as 6, everything else (Group, Listed, 4YO,
    unknown) as 0 -- the lab's coding, which the model was fitted on."""
    s = str(c).strip()
    if s in {"1", "2", "3", "4", "5"}:
        return float(s)
    if s.lower().startswith("griffin"):
        return 6.0
    return 0.0


def _first_call(positions: pd.Series) -> pd.Series:
    tok = positions.str.replace(";", " ", regex=False).str.split().str[0]
    return pd.to_numeric(tok, errors="coerce").astype(float)


def _per(s: pd.Series, keys: list[pd.Series]):
    return s.groupby(keys, sort=False)


def _back(res: pd.Series, n_keys: int) -> pd.Series:
    """A grouped window result, re-indexed to the frame's rows."""
    return res.droplevel(list(range(n_keys)))


def _add(r: pd.DataFrame, cols: dict[str, object]) -> pd.DataFrame:
    """Columns added in one concat, each aligned to the frame's rows: a hundred
    single inserts fragment the frame, and packing them into one block first
    copies every column once more than needed."""
    parts = [pd.Series(v, index=r.index, name=k) if isinstance(v, np.ndarray)
             else v.reindex(r.index).rename(k) for k, v in cols.items()]
    return pd.concat([r, *parts], axis=1)


def _connections(r: pd.DataFrame, who: str) -> pd.DataFrame:
    """Rides, wins and market-expected wins over [date - 365d, date)."""
    daily = (r.groupby([who, "date"])
               .agg(rides=("won", "size"), wins=("won", "sum"), exp=("p_mkt", "sum"))
               .reset_index())
    out = []
    for name, d in daily.groupby(who, sort=False):
        dates = d["date"].to_numpy()
        # cs[i] is the total over the days before day i: today is never in it
        cs = np.vstack([np.zeros(3), d[["rides", "wins", "exp"]].cumsum().to_numpy()])
        lo = np.searchsorted(dates, dates - np.timedelta64(_CONNECTION_DAYS, "D"), side="left")
        w = cs[:-1] - cs[lo]
        out.append(pd.DataFrame({who: name, "date": dates, "rides": w[:, 0],
                                 "wins": w[:, 1], "exp": w[:, 2]}))
    st = pd.concat(out, ignore_index=True)
    return r[[who, "date"]].merge(st, on=[who, "date"], how="left")


def build(runs: pd.DataFrame, card_dates: Iterable[str] = ()) -> pd.DataFrame:
    """One row per starter (and per declared runner on `card_dates`), in key
    order, with `FEATURES`, `CATEGORICAL`, `FACTS`, the outcome and the closing
    market's chance (`p_mkt`, the benchmark -- never an input)."""
    cards = set(card_dates)
    card = runs["race_date"].isin(cards)
    scratched = runs["place_code"].fillna("").str.startswith("W")
    r = runs.loc[(card & ~scratched) | (~card & runs["win_odds"].gt(0))].reset_index(drop=True)
    del runs, card, scratched     # the caller's frame is not needed again; let it go
    r["is_card"] = r["race_date"].isin(cards)
    r.loc[r["is_card"], _RESULT] = np.nan
    r["date"] = pd.to_datetime(r["race_date"])
    r["season"] = season_of(r["date"])
    r["race_id"] = r["race_date"] + "_" + r["race_no"].astype(str).str.zfill(2)
    key = [r["race_date"], r["race_no"]]

    # ---- the race's own declared facts
    r["field"] = _per(r["horse_no"], key).transform("size")
    span = (r["field"] - 1).clip(lower=1)
    r["cls"] = r["race_class"].map(class_num)
    r["vs"] = r["venue"].fillna("?") + "_" + r["surface"].fillna("?")
    r["dist"] = r["distance"].astype(float)
    r["draw_frac"] = (r["draw"] - 1) / span
    r["going_g"] = r["going"].fillna("G").str.upper().str[:2]
    # Raw text is most of the frame's memory; each column goes once it is read.
    r = r.drop(columns=["race_class", "venue", "surface", "distance", "going", "place_code"])

    # ---- the closing market: the benchmark, and an input only once lagged
    inv = 1.0 / r["win_odds"]
    r["p_mkt"] = inv / _per(inv, key).transform("sum")
    mkt_rank = _per(r["p_mkt"], key).rank(ascending=False, method="first")

    # ---- the outcome (dead heats split the win)
    r["won"] = (r["place"] == 1).astype(float).where(~r["is_card"])
    nwin = _per(r["won"], key).transform("sum")
    r["y"] = np.where(nwin > 0, r["won"] / nwin.replace(0, 1), 0.0)
    r["top3"] = (r["place"] <= 3).astype(float).where(~r["is_card"])

    # ---- what each run was (only ever read one run later)
    fin = _per(r["place"], key).transform("max")
    r["pos_frac"] = (r["place"] - 1) / (fin - 1).clip(lower=1)
    r["lbw"] = r["lengths_behind"].where(r["place"] != 1, 0.0).clip(upper=30)
    med = _per(r["finish_time"], key).transform("median")
    r["fmrp"] = (r["finish_time"] - med) / med * 100
    r["early_frac"] = (_first_call(r["running_positions"]) - 1) / span
    r["late_gain"] = r["early_frac"] - r["pos_frac"]
    r["beat_mkt"] = (mkt_rank - 1) / span - r["pos_frac"]
    r["logp_run"] = np.log(r["p_mkt"])
    r = r.drop(columns=["finish_time", "lengths_behind", "running_positions", "win_odds"])

    # ---- the horse's history, earlier runs only
    r = r.sort_values(["horse_name", "race_date", "race_no"], kind="stable").reset_index(drop=True)
    horse = r["horse_name"]
    g = r.groupby("horse_name", sort=False)
    r["n_prior"] = g.cumcount()
    prev_date = g["date"].shift(1)
    r["days_since"] = (r["date"] - prev_date).dt.days
    r["days_since2"] = (prev_date - g["date"].shift(2)).dt.days
    new_prep = r["days_since"].isna() | (r["days_since"] >= _NEW_PREP_DAYS)
    prep_id = new_prep.astype(int).groupby(horse).cumsum()
    r["prep_run"] = r.groupby([horse, prep_id]).cumcount() + 1
    r["debut"] = (r["n_prior"] == 0).astype(float)
    hist: dict[str, object] = {}
    for k, cols in _LAGS.items():
        sh = g[cols].shift(k)
        hist.update({f"l{k}_{c}": sh[c].to_numpy() for c in cols})
    for c, hl in _EW_HALFLIFE:
        prior = g[c].shift(1)
        hist[f"ew_{c}"] = _back(_per(prior, [horse]).ewm(halflife=hl, ignore_na=True).mean(), 1)
    for c in ("pos_frac", "fmrp", "lbw"):
        prior = g[c].shift(1)
        hist[f"best5_{c}"] = _back(_per(prior, [horse]).rolling(5, min_periods=1).min(), 1)
    won, top3 = (_back(_per(g[c].shift(1), [horse]).expanding().sum(), 1) for c in ("won", "top3"))
    hist["career_win_rate"] = (won + 0.1) / (r["n_prior"] + 1)
    hist["career_top3_rate"] = (top3 + 0.3) / (r["n_prior"] + 1)

    # the same venue and surface, and the same trip, earlier runs only
    for col in ("vs", "dist"):
        keys = [horse, r[col]]
        gg = r.groupby(keys, sort=False)
        hist[f"n_{col}"] = gg.cumcount()
        # a run with no recorded trip is in no group and counts 0 placings
        top3 = _back(_per(gg["top3"].shift(1), keys).expanding().sum(), 2)
        hist[f"top3_{col}"] = top3.reindex(r.index).fillna(0)
        hist[f"posf_{col}"] = _back(_per(gg["pos_frac"].shift(1), keys).expanding().mean(), 2)
    r = _add(r, hist)
    del hist, sh

    card_bw = r["is_card"] & r["declared_weight"].isna()
    r.loc[card_bw, "declared_weight"] = r.loc[card_bw, "l1_declared_weight"]
    raced = r["n_prior"] > 0
    r = _add(r, {
        "cls_change": r["cls"] - r["l1_cls"],
        "dist_change": r["dist"] - r["l1_dist"],
        "wt_change": r["actual_weight"] - r["l1_actual_weight"],
        "bw_change": r["declared_weight"] - r["l1_declared_weight"],
        "rating_change": r["rating"] - r["l1_rating"],
        "jockey_change": (r["jockey"] != r["l1_jockey"]).astype(float).where(raced),
        "vs_change": (r["vs"] != r["l1_vs"]).astype(float).where(raced),
        "draw_vs_l3": r["draw_frac"] - r[["l1_draw_frac", "l2_draw_frac", "l3_draw_frac"]].mean(axis=1),
    })

    # ---- connections: the 365 days to yesterday
    conn: dict[str, object] = {}
    for who in ("jockey", "trainer"):
        st = _connections(r, who)
        wins, exp, rides = (st[c].to_numpy() for c in ("wins", "exp", "rides"))
        conn[f"{who}_rides"] = rides
        conn[f"{who}_ae"] = (wins + _AE_PRIOR) / (exp + _AE_PRIOR)
        conn[f"{who}_sr"] = (wins + 1) / (rides + 10)
    r = _add(r, conn)

    # ---- today's field, from its runners' ex-ante figures
    key = [r["race_date"], r["race_no"]]
    hab = r[["l1_early_frac", "l2_early_frac", "l3_early_frac"]].mean(axis=1)
    ctx = {"hab_early": hab,
           "n_leaders": _per(hab < _LEADER, key).transform("sum"),
           "early_rank": _per(hab, key).rank(method="average") / r["field"]}
    for c in ("ew_fmrp", "ew_pos_frac", "ew_logp_run", "rating", "jockey_ae", "career_top3_rate"):
        ctx[f"rel_{c}"] = r[c] - _per(r[c], key).transform("mean")
    ctx["rel_wt"] = r["actual_weight"] - _per(r["actual_weight"], key).transform("mean")
    r = _add(r, ctx)

    out = r[list(dict.fromkeys(_KEEP + FEATURES + CATEGORICAL + FACTS))]
    del r    # the working columns go before the sort copies what is kept
    return out.sort_values(["race_date", "race_no", "horse_no"], kind="stable").reset_index(drop=True)

"""What the fundamental model has earned, and whether a new fit may replace it.

THE RECORD is the walk-forward test (gbm-SPEC §7, §8): every season scored by a
model trained only on seasons before it. The MODEL view, the GAP tooltip and the
FLAGS tooltips read their numbers from it and from nowhere else -- a figure
typed into a page is a figure nobody re-measures.

THE GATE decides whether a new fit replaces the one on the page (§5). It fits
the new recipe on races before the last eight settled meetings and asks, race
by race over those meetings, whether it is worse than the benchmark: what the
page actually showed for them, or -- until eight meetings have been shown --
the current recipe fitted the same way. Worse means worse by more than chance
(one-sided, 10%). Calibration is a sanity check only, at 3 points AND 3
standard errors: over 426 eight-meeting windows of the five test seasons the
literal "within 3 points" failed an honest model 65% of the time and "3 points
and 2 SE" 8%, while this never did (29 Sep). A retrain that does not promote
is a normal night, not an error; the page keeps the model it has.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from hkrd.derive.probability import actual_over_expected
from hkrd.model import gbm
from hkrd.model.gbm_flags import FLAGS, flags
from hkrd.model.gbm_unseen import UNSEEN

__all__ = ["FLAGS", "flags", "walk_forward", "record", "calibration_check", "gate",
           "align_shown", "meeting_row", "GATE_MEETINGS", "DRAW_COURSES"]

CAL_BANDS = [0, .02, .05, .08, .12, .18, .25, .35, .5, 1.0]
GATE_BANDS = [0, .05, .10, .20, .35]
GATE_POINTS, GATE_SE, GATE_T, GATE_MEETINGS = 0.03, 3.0, 1.2816, 8
# The draw courses the model gets wrong (§14.4, §14.6): the inside under-rated
# at HV 1200, and the wrong way round on the ST 1000 straight.
DRAW_COURSES = {"HV 1200": ("HV_Turf", 1200.0), "ST 1000 straight": ("ST_Turf", 1000.0)}


_KEEP = ["race_id", "race_date", "horse_no", "season", "y", "won", "p_mkt", "vs", "dist", "draw",
         "prep_run", "l1_place", "l1_vs", "l2_vs", "l3_vs", "hab_early", "n_leaders", "n_prior"]


def walk_forward(frame: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """Season S scored by a model that never saw it: trees chosen by early
    stopping on S-1 (trained to S-2), then refitted on 2020-21 to S-1."""
    out = []
    season = frame["season"].to_numpy()
    for s in seasons:
        inner = (season >= (gbm.FIRST_TRAIN if s - 2 >= gbm.FIRST_TRAIN else 2019)) & (season <= s - 2)
        rounds = gbm.choose_rounds(frame, inner, season == s - 1)
        booster = gbm.fit(frame, (season >= gbm.FIRST_TRAIN) & (season < s), rounds=rounds)
        # The facts the model cannot see ride along when the fit marked them
        # (jobs/fit_gbm), so the record can say what each is worth against it.
        te = frame.loc[season == s, _KEEP + [k for k in UNSEEN if k in frame]].copy()
        te["p_model"] = gbm.predict(booster, frame, season == s)
        te["rounds"] = rounds
        out.append(te)
    return pd.concat(out, ignore_index=True)


def _ae(x: pd.DataFrame, col: str) -> dict[str, Any]:
    return actual_over_expected(float(x[col].sum()), int(x["won"].sum()), len(x))


def _row(x: pd.DataFrame, **label: Any) -> dict[str, Any]:
    return {**label, "runs": len(x), "won": int(x["won"].sum()),
            "model": _ae(x, "p_model"), "price": _ae(x, "p_mkt")}


def record(preds: pd.DataFrame) -> dict[str, Any]:
    """The walk-forward tables: by season, calibration, segments, gap deciles,
    the two gap slices the GAP tooltip quotes, and the flags' records."""
    seasons = [{"season": f"{s}-{(s + 1) % 100:02d}", "rounds": int(x["rounds"].iloc[0]),
                **gbm.evaluate(x, "p_model")} for s, x in preds.groupby("season")]
    cal = []
    for col, who in (("p_model", "model"), ("p_mkt", "price")):
        band = pd.cut(preds[col], CAL_BANDS)
        for b, x in preds.groupby(band, observed=True):
            cal.append({"who": who, "band": f"{b.left:.0%}-{b.right:.0%}", "runs": len(x),
                        "said": float(x[col].mean()), "won": float(x["won"].mean())})
    dband = pd.cut(preds["dist"], [0, 1200, 1650, 9999], labels=["sprint", "mid", "long"])
    segs = [{"segment": f"{vs} {d}", **{k: v for k, v in gbm.evaluate(x, "p_model").items()
                                         if k in ("races", "nll", "nll_base", "r2", "r2_base")}}
            for (vs, d), x in preds.groupby([preds["vs"], dband], observed=True)]
    gap = preds["p_model"] - preds["p_mkt"]
    dec = pd.qcut(gap, 10, labels=False)
    deciles = [_row(x, decile=int(d) + 1, gap_from=float(gap[x.index].min()),
                    gap_to=float(gap[x.index].max())) for d, x in preds.groupby(dec)]
    ratio = preds["p_model"] / preds["p_mkt"]
    named = {"model_1_5x_price": _row(preds[ratio >= 1.5]),
             "market_15_model_two_thirds": _row(preds[(preds["p_mkt"] >= 0.15) & (ratio < 2 / 3)])}
    fl = flags(preds)
    flag_rows = [_row(preds[fl[k]], flag=k, **FLAGS[k]) for k in FLAGS]
    # What the model cannot see (model/gbm_unseen): wins against its chance and
    # the price's. A trial row holds only the seasons that have trials.
    unseen = [{**_row(preds[preds[k]], fact=k, **UNSEEN[k]),
               "seasons": sorted({f"{s}-{(s + 1) % 100:02d}" for s in preds.loc[preds[k], "season"]})}
              for k in UNSEEN if k in preds]
    draw = []
    for course, (vs, dist) in DRAW_COURSES.items():
        on = (preds["vs"] == vs) & (preds["dist"] == dist)
        for gates, lo, hi in (("1-2", 1, 2), ("11-14", 11, 14)):
            draw.append(_row(preds[on & preds["draw"].between(lo, hi)], course=course, gates=gates))
    ids, won = preds["race_id"], preds["won"] == 1
    top4 = {}                       # races whose winner was in each side's first four
    for who, col in (("model", "p_model"), ("market", "p_mkt")):
        rank = preds.groupby("race_id")[col].rank(ascending=False, method="first")
        top4[who] = float(((rank <= 4) & won).groupby(ids).any().mean())
    whole = gbm.evaluate(preds, "p_model")
    return {"seasons": seasons, "pooled": whole, "calibration": cal, "segments": segs,
            "gap_deciles": deciles, "gap_named": named, "flags": flag_rows, "unseen": unseen,
            "draw_courses": draw, "top4_has_winner": top4, "test_races": int(ids.nunique()),
            "test_seasons": [s["season"] for s in seasons]}


def calibration_check(p: np.ndarray, won: np.ndarray) -> list[dict[str, Any]]:
    """Bands below 35% where the chances said and the wins disagree by more
    than 3 points and more than 3 standard errors. Empty is a pass."""
    bad = []
    for lo, hi in zip(GATE_BANDS, GATE_BANDS[1:]):
        m = (p >= lo) & (p < hi)
        if not m.any():
            continue
        said, got = float(p[m].mean()), float(won[m].mean())
        se = (said * (1 - said) / m.sum()) ** 0.5
        if abs(got - said) > GATE_POINTS and abs(got - said) > GATE_SE * se:
            bad.append({"band": f"{lo:.0%}-{hi:.0%}", "runs": int(m.sum()),
                        "said": round(said, 4), "won": round(got, 4)})
    return bad


def gate(candidate_nll: np.ndarray, benchmark_nll: np.ndarray, p: np.ndarray,
         won: np.ndarray, *, benchmark: str) -> dict[str, Any]:
    """Promote unless the candidate is worse race by race beyond chance, or
    plainly miscalibrated. Per race, positive `diff` means the candidate is worse."""
    d = candidate_nll - benchmark_nll
    se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan")
    t = float(d.mean() / se) if se and se == se and se > 0 else 0.0
    cal = calibration_check(p, won)
    worse = t > GATE_T
    reason = ("worse than " + benchmark + f" by {d.mean():.4f} a race (t {t:.2f})" if worse
              else "calibration off in " + ", ".join(b["band"] for b in cal) if cal
              else f"not worse than {benchmark} ({d.mean():+.4f} a race, t {t:.2f})")
    return {"promote": not worse and not cal, "reason": reason, "benchmark": benchmark,
            "races": len(d), "diff": float(d.mean()), "se": se, "t": t, "calibration": cal}


def align_shown(results: pd.DataFrame, shown: pd.DataFrame) -> np.ndarray | None:
    """The chances the page showed, for the runners that started, renormalised
    over them (a horse scratched after the last score held some). None unless
    every starter has one: a benchmark with holes is not a benchmark."""
    key = ["race_date", "race_no", "horse_no"]
    m = results[key + ["race_id"]].merge(shown[key + ["p_win"]], on=key, how="left")
    if m.empty or m["p_win"].isna().any():
        return None
    return (m["p_win"] / m.groupby("race_id")["p_win"].transform("sum")).to_numpy()


def meeting_row(results: pd.DataFrame, latest: np.ndarray,
                card: np.ndarray | None) -> dict[str, Any]:
    """One settled meeting against its result (gbm-SPEC §13.4): what the page
    showed at the off, the first read when the card landed, and the price."""
    races = gbm.Races(results["race_id"].to_numpy())
    y, unif = results["y"].to_numpy(), np.log(races.counts).sum()
    ids, won = results["race_id"].to_numpy(), results["won"].to_numpy() == 1

    def rank(p: np.ndarray) -> np.ndarray:
        return pd.Series(p).groupby(ids).rank(ascending=False, method="first").to_numpy()
    r_model, r_mkt = rank(latest), rank(results["p_mkt"].to_numpy())
    return {"date": str(results["race_date"].iloc[0]), "races": len(races.starts),
            "r2_model": float(1 - races.nll(latest, y).sum() / unif),
            "r2_card": None if card is None else float(1 - races.nll(card, y).sum() / unif),
            "r2_market": float(1 - races.nll(results["p_mkt"].to_numpy(), y).sum() / unif),
            "top_pick_won": int((won & (r_model == 1)).sum()),
            "favourite_won": int((won & (r_mkt == 1)).sum()),
            "winner_top3_model": int((won & (r_model <= 3)).sum()),
            "winner_top3_market": int((won & (r_mkt <= 3)).sum())}

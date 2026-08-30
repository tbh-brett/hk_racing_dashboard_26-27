"""
Expected Time (ET) — par times and the descriptive speed figure derived from them.

This is a rewrite of the v4 reference-table model. Three things changed:

1.  Winner-take-all tier selection is replaced by SHRINKAGE. The old lookup
    walked ClassFine -> Fine -> Coarse -> Ultra and took the first tier whose
    sample size cleared a threshold. Measured out of sample, that made the
    model *worse*: the finest tier (median n=4) scored MAE 0.755 while plain
    distance x track scored 0.722. We now blend fine toward coarse in
    proportion to sample size, which scored 0.691 on the same rows.

2.  Draw offsets and the weight adjustment are GONE. Out of sample the draw
    offsets explained R^2 = 0.005 of residual variance, and the within-horse
    weight slope came to -0.0021 s/lb (0.021 s per 10 lb, wrong sign,
    ~13% of cells significant at p<0.10 against 10% expected by chance).
    Both added variance without information.

3.  The output is framed as a DESCRIPTIVE figure, not a model input. ET
    figures carry real standalone signal (AUC 0.658 predicting the next run)
    but add nothing on top of the market price (p=0.746 with log-odds
    controlled). They belong on a race card, not in a selection rule.

Everything here is a pure function: DataFrames in, DataFrames out. No sqlite3,
no Streamlit, no file paths. Failures raise rather than returning None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from hkrd.store import coerce

DERIVE_VERSION = "et-v5.0"

# Fitted from 21,045 runs rather than assumed. See fit_seconds_per_length().
DEFAULT_SEC_PER_LENGTH = 0.139

# Physical sanity bounds. A fit outside this means the input is wrong.
PLAUSIBLE_SEC_PER_LENGTH = (0.10, 0.22)

# Shrinkage constant. Fitted by out-of-sample sweep on a 2,678-run test set;
# MAE was flat across k=5..10 and minimised at 8.
DEFAULT_SHRINKAGE_K = 8.0

class ETError(ValueError):
    """Raised when ET cannot be computed. Never returns a silent NaN instead."""


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
#
# These delegate to store.coerce. The module originally carried its own copies,
# which is the duplication this rebuild exists to remove: a beaten margin now
# has one definition, used by the scraper, the migration and the model alike.
#
# The only adaptation is the missing-value convention. store.coerce returns None
# (a winner has no margin); pandas needs NaN to keep a float column float.


def parse_lbw(token) -> float:
    """Beaten lengths as a float, NaN where there is no margin.

    See store.coerce.parse_lbw. Raises rather than silently returning NaN on an
    unrecognised token, which is what the module docstring always claimed and
    the original implementation did not do.
    """
    value = coerce.parse_lbw(token)
    return np.nan if value is None else value


def parse_finish_time(token) -> float:
    """Finish time in seconds, NaN where absent. See store.coerce."""
    value = coerce.parse_finish_time(token)
    return np.nan if value is None else value


def weight_band(w) -> str:
    """3-lb bands with wider tails, matching the v4 convention."""
    if pd.isna(w):
        return "unknown"
    w = int(w)
    if w <= 112:
        return "<=112"
    if w >= 137:
        return ">=137"
    lo = 113 + 3 * ((w - 113) // 3)
    return f"{lo}-{lo + 2}"


def class_band(c) -> str:
    return {"1": "C1-C2", "2": "C1-C2", "3": "C3", "4": "C4", "5": "C5"}.get(
        str(c).strip(), "Other"
    )


def normalise_surface(race_course, track_type, going) -> tuple[str, str]:
    """Fix the v4 mislabelling where AWT going groups carried track_type 'Turf'.

    Sha Tin's all-weather track is treated as its own course category, never
    pooled with ST turf.
    """
    course = str(race_course).strip().upper() if pd.notna(race_course) else ""
    going_s = str(going).strip().upper() if pd.notna(going) else ""
    is_awt = course == "AWT" or going_s.startswith("AWT") or "ALL WEATHER" in str(track_type).upper()
    return ("AWT", "AWT") if is_awt else (course or "unknown", "Turf")


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def fit_seconds_per_length(runs: pd.DataFrame) -> float:
    """Fit seconds-per-length from margins and finish times, through the origin.

    Requires correctly parsed lbw — fitting on pd.to_numeric output gives
    roughly 0.11 s/length, which is wrong, because it keeps only the
    whole-number margins.

    Raises if the fit lands outside a physically plausible range. A degenerate
    input (every margin identical, or margins uncorrelated with time) can
    otherwise return an arbitrary number that silently rescales every figure
    downstream.
    """
    d = runs.dropna(subset=["finish_time", "lengths_behind"]).copy()
    d["t_win"] = d.groupby(["race_date", "race_no"])["finish_time"].transform("min")
    d["dt"] = d["finish_time"] - d["t_win"]
    d = d[(d.dt > 0) & (d.dt < 15) & (d.lengths_behind > 0) & (d.lengths_behind < 40)]
    if len(d) < 200:
        raise ETError(f"only {len(d)} usable rows to fit seconds-per-length")
    if d["lengths_behind"].nunique() < 5:
        raise ETError(
            "margins are near-constant — cannot fit seconds-per-length "
            f"({d['lengths_behind'].nunique()} distinct values)"
        )

    fitted = float(np.sum(d.lengths_behind * d.dt) / np.sum(d.lengths_behind ** 2))
    if not (PLAUSIBLE_SEC_PER_LENGTH[0] <= fitted <= PLAUSIBLE_SEC_PER_LENGTH[1]):
        raise ETError(
            f"fitted seconds-per-length {fitted:.4f} is outside the plausible "
            f"range {PLAUSIBLE_SEC_PER_LENGTH}. Check lbw parsing — using "
            f"pd.to_numeric on lbw drops the 66% of values that are fractions "
            f"and biases this fit low."
        )
    return fitted


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------

# Coarse to fine. Each level is a fallback for the one after it.
#
# weight_band is deliberately NOT a key. v4 used it, which had two costs:
#
#   - It fragmented the finest tier into 2,405 cells with median n=3. Dropping
#     it gives 348 cells with median n=26, and equal or better accuracy.
#   - It gave 5-8 DIFFERENT par times to horses in the same race, purely from
#     carrying different weights. A par time is a property of the race, not of
#     what each runner carries.
#
# The justification for keying on it was a weight effect measured at
# -0.0021 s/lb — 0.021s over a 10 lb swing, with the sign pointing the wrong
# way, against a residual sd of 0.68s. It was buying noise.
_LEVELS: list[tuple[str, list[str]]] = [
    ("dist", ["distance", "surface"]),
    ("ultra", ["distance", "surface", "going_group"]),
    ("coarse", ["distance", "surface", "going_group", "course"]),
    ("fine", ["distance", "surface", "going_group", "course", "class_band"]),
]


@dataclass
class ETReferences:
    """Par times at every granularity, plus the metadata to audit them."""

    tables: dict[str, pd.DataFrame]
    sec_per_length: float
    shrinkage_k: float
    built_from: tuple[pd.Timestamp, pd.Timestamp]
    n_runs: int
    version: str = DERIVE_VERSION

    def describe(self) -> pd.DataFrame:
        rows = []
        for name, _ in _LEVELS:
            t = self.tables[name]
            rows.append({
                "level": name,
                "cells": len(t),
                "median_n": int(t["n"].median()),
                "cells_n_lt_5": int((t["n"] < 5).sum()),
                "runs_covered": int(t["n"].sum()),
            })
        return pd.DataFrame(rows)


def prepare_runs(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw runner table into the columns ET needs.

    Expects: race_date, race_no, distance, going, race_course, track_type,
             actual_weight, race_class, finish_time (raw), lbw (raw).
    """
    required = {"race_date", "race_no", "distance", "going", "finish_time", "lbw"}
    missing = required - set(raw.columns)
    if missing:
        raise ETError(f"prepare_runs missing required columns: {sorted(missing)}")

    d = raw.copy()
    d["finish_time"] = d["finish_time"].map(parse_finish_time)
    d["lengths_behind"] = d["lbw"].map(parse_lbw)
    d["distance"] = pd.to_numeric(d["distance"], errors="coerce")
    d["race_date"] = pd.to_datetime(d["race_date"], errors="coerce")
    d["weight_band"] = d.get("actual_weight", pd.Series(index=d.index)).map(weight_band)
    d["class_band"] = d.get("race_class", pd.Series(index=d.index)).map(class_band)

    surf = d.apply(
        lambda r: normalise_surface(
            r.get("race_course"), r.get("track_type"), r.get("going")
        ),
        axis=1,
    )
    d["course"] = [s[0] for s in surf]
    d["surface"] = [s[1] for s in surf]
    d["going_group"] = d["going"].astype(str).str.strip().str.upper()

    d = d.dropna(subset=["finish_time", "distance", "race_date"])
    d = d[(d.finish_time > 40) & (d.finish_time < 300)]
    if d.empty:
        raise ETError("no usable runs after cleaning")
    return d


def build_references(
    runs: pd.DataFrame,
    window_months: int | None = 24,
    as_of: pd.Timestamp | None = None,
    shrinkage_k: float = DEFAULT_SHRINKAGE_K,
    min_runs: int = 500,
) -> ETReferences:
    """Build par-time tables on a rolling window.

    window_months=None uses everything. The v4 table was built once and never
    rebuilt, so by August it was four months stale; call this after every
    meeting instead.
    """
    d = prepare_runs(runs)
    as_of = pd.Timestamp(as_of) if as_of is not None else d["race_date"].max()
    if window_months:
        d = d[d["race_date"] > as_of - pd.DateOffset(months=window_months)]
    if len(d) < min_runs:
        raise ETError(
            f"only {len(d)} runs in window — too few to build references "
            f"(min_runs={min_runs})"
        )

    tables = {}
    for name, keys in _LEVELS:
        g = (
            d.groupby(keys, dropna=False)["finish_time"]
            .agg(par="median", mean="mean", sd="std", n="size")
            .reset_index()
        )
        tables[name] = g

    try:
        spl = fit_seconds_per_length(d)
    except ETError:
        spl = DEFAULT_SEC_PER_LENGTH

    return ETReferences(
        tables=tables,
        sec_per_length=spl,
        shrinkage_k=shrinkage_k,
        built_from=(d["race_date"].min(), d["race_date"].max()),
        n_runs=len(d),
    )


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------

def expected_time(runs: pd.DataFrame, refs: ETReferences) -> pd.DataFrame:
    """Attach a shrunk par time to every run.

    Walks coarse -> fine, blending each level toward the running estimate:

        w  = n / (n + k)
        et = w * par_this_level + (1 - w) * et_so_far

    A cell with 2 observations contributes about 20% of its own value at k=8,
    rather than being either trusted outright or discarded. This removes the
    MIN_N question entirely.

    Adds: et, et_n (sample size at the finest level that matched),
          et_level (finest level reached), et_shrunk (whether blending applied).
    """
    d = prepare_runs(runs) if "surface" not in runs.columns else runs.copy()

    base_name, base_keys = _LEVELS[0]
    base = refs.tables[base_name]
    m = d.merge(base[base_keys + ["par", "n"]], on=base_keys, how="left")
    m = m.rename(columns={"par": "_et", "n": "_n"})
    if m["_et"].isna().all():
        raise ETError(
            f"no {base_name}-level par matched any run — check surface normalisation"
        )

    m["et_level"] = base_name
    m["et_cell_n"] = m["_n"].fillna(1.0)
    m["et_shrunk"] = False

    # Effective sample size of the blended estimate. The variance of a weighted
    # mean of independent cell means is sum(w_i^2 * sigma^2 / n_i); setting that
    # equal to sigma^2 / n_eff gives n_eff = 1 / sum(w_i^2 / n_i).
    #
    # This matters for the confidence label. Reporting the finest cell's n makes
    # every figure look unreliable, when in practice a shrunk estimate leans
    # mostly on the coarse levels and is far better supported than n=4 suggests.
    m["_var_acc"] = 1.0 / m["_n"].fillna(1.0).clip(lower=1.0)

    k_map = refs.shrinkage_k
    if not isinstance(k_map, dict):
        k_map = {name: float(k_map) for name, _ in _LEVELS[1:]}

    for name, keys in _LEVELS[1:]:
        k = float(k_map.get(name, DEFAULT_SHRINKAGE_K))
        t = refs.tables[name][keys + ["par", "n"]].rename(
            columns={"par": f"par_{name}", "n": f"n_{name}"}
        )
        m = m.merge(t, on=keys, how="left")
        has = m[f"par_{name}"].notna() & m["_et"].notna()
        n = m.loc[has, f"n_{name}"].astype(float)
        w = n / (n + k)
        m.loc[has, "_et"] = w * m.loc[has, f"par_{name}"] + (1 - w) * m.loc[has, "_et"]
        m.loc[has, "_var_acc"] = (
            w ** 2 / n.clip(lower=1.0) + (1 - w) ** 2 * m.loc[has, "_var_acc"]
        )
        m.loc[has, "et_level"] = name
        m.loc[has, "et_cell_n"] = n.values
        m.loc[has & (w < 0.95), "et_shrunk"] = True

    m["et_n"] = (1.0 / m["_var_acc"]).round(0)

    m = m.rename(columns={"_et": "et"}).drop(columns=["_n"])
    if m["et"].isna().any():
        n_bad = int(m["et"].isna().sum())
        raise ETError(
            f"{n_bad} runs got no expected time. Distance/surface combinations "
            f"absent from references: "
            f"{m.loc[m.et.isna(), ['distance', 'surface']].drop_duplicates().to_dict('records')[:5]}"
        )
    return m


# --------------------------------------------------------------------------
# the descriptive figure
# --------------------------------------------------------------------------

def speed_figure(runs: pd.DataFrame, refs: ETReferences) -> pd.DataFrame:
    """Turn par times into a figure meant to be read, not optimised against.

    Columns added:
      sec_vs_par   seconds faster (+) or slower (-) than par for the conditions
      len_vs_par   the same, converted to lengths
      sec_vs_race  race-relative: par-adjusted, then centred on the race median.
                   Removes the day's track speed, isolating the horse.
      len_vs_race  the same, in lengths
      figure       standardised: 100 = par, +10 = one distance-sd faster
      confidence   'high' / 'medium' / 'low', from the sample behind the par

    sec_vs_par answers "was this a fast time?". sec_vs_race answers "did this
    horse outrun the rest of its field?". They are different questions and the
    old model conflated them.
    """
    m = expected_time(runs, refs)

    m["sec_vs_par"] = m["et"] - m["finish_time"]
    m["len_vs_par"] = m["sec_vs_par"] / refs.sec_per_length

    race_med = m.groupby(["race_date", "race_no"])["sec_vs_par"].transform("median")
    m["sec_vs_race"] = m["sec_vs_par"] - race_med
    m["len_vs_race"] = m["sec_vs_race"] / refs.sec_per_length

    base_name, base_keys = _LEVELS[0]
    sd = refs.tables[base_name].set_index(base_keys)["sd"]
    idx = pd.MultiIndex.from_arrays([m[k] for k in base_keys])
    m["_sd"] = sd.reindex(idx).to_numpy()
    m["_sd"] = m["_sd"].fillna(m["_sd"].median())
    m["figure"] = (100 + 10 * m["sec_vs_par"] / m["_sd"]).round(1)

    m["confidence"] = pd.cut(
        m["et_n"], bins=[-1, 25, 100, np.inf], labels=["low", "medium", "high"]
    )
    m["derive_version"] = refs.version

    return m.drop(columns=[c for c in m.columns if c.startswith(("par_", "n_", "_"))])


def format_figure(row) -> str:
    """One-line render for a race card. Never a bare number."""
    d = row["len_vs_par"]
    direction = "faster" if d >= 0 else "slower"
    conf = {"high": "", "medium": " ~", "low": " ?"}.get(str(row["confidence"]), " ?")
    return (f"{row['figure']:.0f}{conf}  ({abs(d):.1f}L {direction} than par, "
            f"n_eff={int(row['et_n'])})")

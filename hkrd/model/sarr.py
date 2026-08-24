"""SARR — Sectional-Anchored Relative Rating.

Faithful reimplementation of sarr_raceday.py for walk-forward backtesting.
Constants and weights are copied verbatim from the original; the weights were
fitted on ~15,500 runners / 145 meetings before 29 Mar 2026, so anything from
June onward is genuinely out of sample for them.

Mechanism, and how it differs from ET: every component is measured against the
MEDIAN OF THE HORSE'S OWN RACE, not against an absolute par time. That makes
the rating immune to how fast the track was on the day, which is the main
weakness of a par-time model. It also means SARR can only ever say "this horse
was strong relative to the field it met", never "this was a fast time".

Lower SARR is better. Rank 1 = top-rated.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from hkrd.derive.pace import SECTION_LENGTHS, classify_style
from hkrd.store.coerce import parse_section_times
from scipy import stats

RECENCY_LAMBDA = 0.85
MAX_PRIOR_RUNS = 15
GOING_BAND_MIN_N = 3
LAST_STYLE_BOOST = 3.0

# SECTION_LENGTHS and classify_style now come from derive/pace, which is the
# canonical definition after decision A2. Verified identical to the copies
# this module carried before they were removed -- same nine distances, same
# splits, and the same field-size-scaled style cutoffs, which matters because
# SARR was built and backtested against exactly this classifier.

STYLE_ADV = {
    1000: {"Leader": 2.034, "On-Pace": 1.162, "Midfield": 0.573, "Closer": 0.418},
    1200: {"Leader": 1.708, "On-Pace": 1.204, "Midfield": 0.765, "Closer": 0.428},
    1400: {"Leader": 1.370, "On-Pace": 1.395, "Midfield": 0.954, "Closer": 0.424},
    1600: {"Leader": 1.455, "On-Pace": 1.439, "Midfield": 0.829, "Closer": 0.448},
    1650: {"Leader": 1.287, "On-Pace": 1.398, "Midfield": 0.861, "Closer": 0.542},
    1800: {"Leader": 1.951, "On-Pace": 0.627, "Midfield": 1.184, "Closer": 0.451},
    2000: {"Leader": 1.771, "On-Pace": 1.165, "Midfield": 0.866, "Closer": 0.450},
    2200: {"Leader": 1.142, "On-Pace": 1.004, "Midfield": 0.690, "Closer": 1.380},
}
HV_STYLE_MOD = {"Leader": 1.15, "On-Pace": 1.10, "Midfield": 0.95, "Closer": 0.80}

IDEAL_SSI = {
    1000: 0.0, 1200: -0.10, 1400: -0.20, 1600: -0.30,
    1650: -0.30, 1800: -0.35, 2000: -0.40, 2200: -0.45, 2400: -0.50,
}

WEIGHTS = {
    "f_fmrp": 0.2901, "f_lsa": 0.0912, "f_esz": 0.0688, "f_style": -0.0267,
    "f_rating": 0.0137, "f_traj": -0.0711, "f_wpr": 0.0540, "f_dist": -0.0147,
}


def parse_sections(s) -> list[float]:
    """Sectional splits as a list. Delegates to store.coerce.

    The module carried its own copy; a split is now parsed in one place for the
    scraper, the migration, pace and this model alike.
    """
    return list(parse_section_times(s))



def going_band(g) -> str:
    if g is None:
        return "unknown"
    g = str(g).strip().upper()
    if g in ("SE", "SEALED", "WF", "WS", "AWT"):
        return "awt"
    if g in ("FM", "F", "HD", "GF"):
        return "firm"
    if g == "G":
        return "good"
    if g in ("GY", "Y", "S", "SOFT", "HEAVY", "H"):
        return "soft"
    return "unknown"


def dist_weight(delta_m: float) -> float:
    d = abs(delta_m)
    if d == 0:
        return 1.0
    if d <= 100:
        return 0.75
    if d <= 200:
        return 0.50
    if d <= 400:
        return 0.25
    return 0.10


def get_style_fit(style, distance, venue) -> float:
    closest = min(STYLE_ADV, key=lambda d: abs(d - distance))
    mult = STYLE_ADV[closest].get(style, 1.0)
    if venue == "HV":
        mult *= HV_STYLE_MOD.get(style, 1.0)
    return -math.log(max(mult, 0.05))


def annotate_runs(db: pd.DataFrame) -> pd.DataFrame:
    """Attach the field-relative components to every historical run."""
    d = db.copy()
    key = ["race_date", "race_no"]
    d["field_size"] = d.groupby(key)["place"].transform("size")

    med_ft = d.groupby(key)["finish_time"].transform("median")
    d["fmrp"] = (d["finish_time"] - med_ft) / med_ft * 100

    def zones(row):
        secs = parse_sections(row["sectiontimes"])
        lengths = SECTION_LENGTHS.get(int(row["distance"]) if pd.notna(row["distance"]) else 0)
        if not secs or lengths is None or len(secs) != len(lengths):
            return (np.nan, np.nan)
        p4 = [t * 400.0 / l for t, l in zip(secs, lengths)]
        return (p4[0], p4[-1])

    z = d.apply(zones, axis=1, result_type="expand")
    d["_early"], d["_late"] = z[0], z[1]
    d["early_dev"] = d["_early"] - d.groupby(key)["_early"].transform("median")
    d["late_dev"] = d["_late"] - d.groupby(key)["_late"].transform("median")
    d["ssi"] = d["late_dev"] - d["early_dev"]
    d["style"] = d.apply(
        lambda r: classify_style(r["running_positions"], r["field_size"]), axis=1
    )
    return d


def build_profile(runs: list[dict], today_dist, today_venue,
                  today_surface, today_going=None) -> dict | None:
    """runs must be ordered MOST RECENT FIRST."""
    if not runs:
        return None
    runs = runs[:MAX_PRIOR_RUNS]

    weights = []
    for i, run in enumerate(runs):
        w = RECENCY_LAMBDA ** i
        rd = run.get("distance", np.nan)
        if not pd.isna(rd) and not pd.isna(today_dist):
            w *= dist_weight(rd - today_dist)
        if run.get("venue") and today_venue and run["venue"] != today_venue:
            w *= 0.60
        if run.get("surface") and today_surface and run["surface"] != today_surface:
            w *= 0.50
        weights.append(max(w, 0.01))
    weights = np.array(weights)

    def wmean(key):
        vals = np.array([r.get(key, np.nan) for r in runs], dtype=float)
        m = ~np.isnan(vals)
        if not m.any():
            return np.nan
        return np.average(vals[m], weights=weights[m])

    styles = [r["style"] for r in runs if r.get("style", "Unknown") != "Unknown"]
    if styles:
        sc = defaultdict(float)
        last_style = next((r["style"] for r in runs
                           if r.get("style", "Unknown") != "Unknown"), None)
        for s in styles:
            sc[s] += 1.0
        if last_style:
            sc[last_style] += LAST_STYLE_BOOST - 1.0
        style = max(sc, key=sc.get)
    else:
        style = "Midfield"

    rating = next((r["rating"] for r in runs
                   if not pd.isna(r.get("rating", np.nan))), np.nan)

    fr = [r.get("fmrp", np.nan) for r in runs[:5]]
    fr = [v for v in fr if not pd.isna(v)]
    slope = stats.linregress(np.arange(len(fr)), fr).slope if len(fr) >= 3 else 0.0

    places = [r.get("place", 99) for r in runs]
    place_rate = sum(1 for p in places if p <= 3) / max(len(places), 1)

    place_rate_band = None
    if today_going:
        band = going_band(today_going)
        if band != "unknown":
            bp = [r.get("place", 99) for r in runs
                  if going_band(r.get("going", "")) == band]
            if len(bp) >= GOING_BAND_MIN_N:
                place_rate_band = sum(1 for p in bp if p <= 3) / len(bp)

    return {
        "fmrp": wmean("fmrp"), "lsa": wmean("late_dev"), "esz": wmean("early_dev"),
        "avg_ssi": wmean("ssi"), "style": style, "rating": rating,
        "traj": slope, "place_rate": place_rate,
        "place_rate_band": place_rate_band, "n_runs": len(runs),
    }


def score(profile: dict, distance, venue, med_rating, draw_score=0.0) -> float:
    """The SARR composite. Lower is better.

    The rating term degrades to 0 when rating is unavailable rather than
    poisoning the whole score with NaN. This matters: `rating` is 100% null
    from July 2026 and ~46% null in June (same scraper regression that killed
    `horse_id`), so a NaN-propagating version returns no score at all for any
    recent meeting. f_rating carries the second-smallest weight (0.0137), so
    dropping it costs little.
    """
    nan0 = lambda v: 0.0 if (v is None or (isinstance(v, float) and np.isnan(v))) else v
    rat = profile["rating"]
    if pd.isna(rat):
        rat = med_rating
    f_rating = 0.0 if (pd.isna(rat) or pd.isna(med_rating)) else -(rat - med_rating) / 10.0

    pr = profile.get("place_rate_band")
    if pr is None:
        pr = profile["place_rate"]

    return (
        WEIGHTS["f_fmrp"] * nan0(profile["fmrp"])
        + WEIGHTS["f_lsa"] * nan0(profile["lsa"])
        + WEIGHTS["f_esz"] * nan0(profile["esz"])
        + WEIGHTS["f_style"] * get_style_fit(profile["style"], distance, venue)
        + WEIGHTS["f_rating"] * f_rating
        + WEIGHTS["f_traj"] * nan0(profile["traj"])
        + WEIGHTS["f_wpr"] * (-pr * 5)
        + WEIGHTS["f_dist"] * abs(nan0(profile["avg_ssi"]) - IDEAL_SSI.get(int(distance), -0.20))
        + nan0(draw_score) * 0.3
    )

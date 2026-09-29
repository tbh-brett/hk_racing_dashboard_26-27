"""Where five seasons say the fundamental model is off (gbm-SPEC §14.4).

Each flag is a rule on the same ex-ante row the model reads, and each carries
its measured record in the walk-forward record (`model/gbm_record`), never a
typed number. A flag says "the model's number is weakest here"; it is not a
bet either way.

Kept apart from `model/gbm` because the pages read these rules and must never
load LightGBM (§6): this module imports pandas and nothing of the model.
"""
from __future__ import annotations

import pandas as pd

__all__ = ["FLAGS", "flags", "TURF"]

TURF = ("ST_Turf", "HV_Turf")

# In the order a runner's chips are drawn; at most two are shown (§14.4).
FLAGS: dict[str, dict[str, str]] = {
    "second_up_bad": {"label": "2nd-up after 7th+",
                      "note": "second run of a preparation after finishing 7th or worse first-up"},
    "awt_form": {"label": "AWT form", "note": "racing on turf with 2 of its last 3 runs on the AWT"},
    "lone_leader": {"label": "lone leader",
                    "note": "the only habitual leader in the field -- a lead, not an edge"},
    "first_up": {"label": "first-up", "note": "the model cannot see its preparation"},
    "no_hk": {"label": "no HK record", "note": "a placeholder, built from the card alone"},
}


def flags(frame: pd.DataFrame) -> pd.DataFrame:
    """One boolean column per flag, from `prep_run`, `n_prior`, `l1_place`,
    `vs`, `l1_vs`..`l3_vs`, `hab_early` and `n_leaders`."""
    awt = sum((frame[f"l{k}_vs"] == "ST_AWT").astype(int) for k in (1, 2, 3))
    return pd.DataFrame({
        "second_up_bad": (frame["prep_run"] == 2) & (frame["l1_place"] >= 7),
        "awt_form": frame["vs"].isin(TURF) & (awt >= 2),
        "lone_leader": (frame["hab_early"] < 0.15) & (frame["n_leaders"] == 1),
        "first_up": (frame["prep_run"] == 1) & (frame["n_prior"] > 0),
        "no_hk": frame["n_prior"] == 0,
    }, index=frame.index)

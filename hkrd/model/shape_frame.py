"""One row per run, the frame every race-shape model and table is fitted on (racesim-SPEC §6, §7).

The lab's trip table (`trip2d.py`) without the study's next-run columns, and without the parse:
the trip columns -- lane, cover, a slow start, traffic -- come in already read, from
`runner_trip` (derive/trip). What is computed here is along the track, from that race alone:
each horse's position at the first call, the 800 m and the 400 m as a share of the field, and how
far behind the race's median time it finished, in lengths. Nothing here is a pre-race feature
(model/shape builds those, from earlier runs only).

Pure: a frame in, a frame out. `store/shape.load_runs` reads the runs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hkrd.derive.pace import section_lengths

__all__ = ["LENGTH_M", "TRIP_INTS", "season_of", "floats", "build"]

LENGTH_M = 2.4
# Trip columns that are counts or flags: a run with no trip row (a card still to run) reads 0,
# as the lab's parse of no comment did. Lanes stay NaN: not stated is not the rail.
TRIP_INTS = ("has_comment", "nocover_settle", "nocover_mid", "nocover_turn", "nocover_straight",
             "stated", "wide3_pre", "nocover_pre", "rail_pre", "wide_straight",
             "slow_bad", "slow_mild", "traffic_pre", "traffic_straight")


def season_of(d: pd.Series) -> np.ndarray:
    """The HKJC season a date belongs to, by the year it starts (September 2026 -> 2026)."""
    return np.where(d.dt.month >= 8, d.dt.year, d.dt.year - 1)


def floats(s: object) -> list[float] | None:
    """"23.45;22.10" or "4 4 3 1" as floats; None for nothing or a "---" call."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    try:
        v = [float(x) for x in str(s).replace(";", " ").split()]
    except ValueError:
        return None
    return v or None


def _call_index(tg: list[float] | None, target: float) -> int | None:
    if tg is None:
        return None
    for i, t in enumerate(tg):
        if abs(t - target) < 1:
            return i
    return None


def build(runs: pd.DataFrame) -> pd.DataFrame:
    """The trip frame from `store/shape.load_runs`' rows, sorted by race and horse."""
    r = runs.copy()
    for c in TRIP_INTS:
        r[c] = r[c].fillna(0).astype(int)
    r["date"] = pd.to_datetime(r.race_date)
    r["season"] = season_of(r.date)
    r["race_id"] = r.race_date + "_" + r.race_no.astype(str).str.zfill(2)
    key = ["race_date", "race_no"]
    r["field"] = r.groupby(key).horse_no.transform("size")
    r["vs"] = r.venue.fillna("?") + "_" + r.surface.fillna("?")
    r["dist"] = r.distance.astype(float)
    inv = 1.0 / r.win_odds
    r["p_mkt"] = inv / inv.groupby([r.race_date, r.race_no]).transform("sum")
    r["won"] = (r.place == 1).astype(float)
    nwin = r.groupby(key).won.transform("sum")
    r["y"] = np.where(nwin > 0, r.won / nwin.replace(0, 1), 0.0)
    r["top3"] = (r.place <= 3).astype(float)

    # along the track: positions at the first call, the 800 m and the 400 m
    pos = r.running_positions.map(floats)
    seclen = r.distance.map(lambda d: section_lengths(int(d)) if pd.notna(d) else None)
    togo = seclen.map(lambda L: None if L is None else list(np.sum(L) - np.cumsum(L)))
    early_i = [0 if tg else None for tg in togo]
    mid_i = [_call_index(tg, 800) if tg else None for tg in togo]
    mid_i = [m if m is not None else e for m, e in zip(mid_i, early_i)]    # 1000/1200: MID is the first call
    turn_i = [_call_index(tg, 400) if tg else None for tg in togo]
    fn = (r.field - 1).clip(lower=1)
    for name, idx in (("early", early_i), ("mid", mid_i), ("turn", turn_i)):
        frac = [(p[i] - 1) if (p is not None and i is not None and i < len(p)) else np.nan
                for p, i in zip(pos, idx)]
        r[f"{name}_frac"] = pd.Series(frac, index=r.index, dtype=float) / fn
    med = r.groupby(key).finish_time.transform("median")
    r["lens_vs_med"] = (r.finish_time - med) * (r.dist / med) / LENGTH_M
    return r.sort_values(["race_id", "horse_no"]).reset_index(drop=True)

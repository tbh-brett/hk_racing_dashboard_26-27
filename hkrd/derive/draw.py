"""The barrier draw — how much an outside gate costs, per venue and distance.

SARR carried a `draw` component from the day it was written: the parameter, the
multiplier, the database column and a column on the Model Analysis page. Nothing
ever supplied a value, so the term contributed exactly 0.0 to all 17,376 scored
runners. This module supplies it.

WHY NOT SIMPLY RESTORE THE LEGACY TERM. The old dashboard did score the draw
(`sarr_raceday.py`, `get_draw_score(...) * 0.3`), so the rebuild dropped a live
term rather than declining to add one. Restoring it verbatim would have made the
model worse, for three reasons that are measured rather than argued:

1.  A FIXED MID-PLACE. Legacy scored `mean(place) - 6.5` for a gate. 6.5 is the
    median place of a 14-runner field, but gates 13 and 14 only exist in
    14-runner fields, so every wide gate collected a field-size artefact instead
    of an effect. Normalising both axes by field size is the whole correction.

2.  VENUE-ONLY KEYING. The effect is strongly distance-specific and at ST 1000
    it REVERSES -- an outside gate is an advantage there, because the chute
    means the run to the first turn is short and inside runners get crowded. A
    venue-only table averages that away.

3.  AN UNFITTED MULTIPLIER. Legacy's 0.3 was hand-chosen and carried a comment
    reading "conservative draw weight". The eight `WEIGHTS` in model/sarr came
    from one OLS that never saw a draw term; this multiplier was fitted
    separately, by out-of-sample sweep, and is named apart from them for that
    reason.

WHAT IS FITTED. One slope of normalised finishing position on normalised draw,
per (venue, distance), shrunk toward the global slope in proportion to sample
size:

    npos  = (place - 1) / (field_size - 1)      0 = won,       1 = last
    ndraw = (draw  - 1) / (field_size - 1)      0 = innermost, 1 = widest
    slope = regress(npos ~ ndraw), shrunk toward the global slope by n/(n+K)
    score = slope * (ndraw - 0.5)

A slope rather than fourteen per-gate cells because the cells are thin, the
effect is monotone almost everywhere, and a slope can carry the one place it is
not. Centring on 0.5 makes the score REORDER a field rather than shift it: the
mean contribution across a full field is ~0, so the draw term cannot move a
race's whole rating up or down against another race's.

WHAT THIS IS NOT. It does not create betting edge. Draw is insignificant once
the market's log-odds are controlled, and the corrected model still sits well
short of the market. It makes SARR a better DESCRIPTIVE rating of who ran well
relative to the field they met. `model/backtest.py` documents why the other
road is closed; nothing here reopens it.

Pure functions. DataFrames in, plain data out. No sqlite3, no paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["DERIVE_VERSION", "DrawError", "DrawTable", "SHRINK_K",
           "normalised_draw", "draw_table", "draw_score"]

DERIVE_VERSION = "draw-1.0"

# Shrinkage constant: a (venue, distance) cell carries its own slope with weight
# n/(n+K) and the global slope with the rest. K=200 means a cell needs 200 runs
# to be believed half on its own evidence. Chosen against the cell sizes rather
# than swept: the thinnest cells here hold a few dozen runs and the fattest tens
# of thousands, so K sits an order of magnitude below the cells that should
# speak for themselves and above the ones that should not.
SHRINK_K = 200.0

# A cell needs at least this many runs before its own slope is estimated at all.
# Below it the cell takes the global slope outright -- a two-point regression
# has a slope, and it means nothing.
MIN_CELL_N = 30


class DrawError(ValueError):
    """The draw table could not be fitted. Never returns a silent empty table."""


@dataclass(frozen=True)
class DrawTable:
    """Fitted slopes, keyed by (venue, distance_in_metres).

    `global_slope` is the fallback for a key never seen in training -- a new
    distance, or a venue the fit did not cover. It is not a neutral 0.0 on
    purpose: an unseen cell is far more likely to behave like Hong Kong racing
    in general than like no draw effect at all.
    """

    slopes: dict[tuple[str, int], float]
    global_slope: float
    counts: dict[tuple[str, int], int] = field(default_factory=dict)
    raw_slopes: dict[tuple[str, int], float] = field(default_factory=dict)
    n_runs: int = 0
    version: str = DERIVE_VERSION

    def slope_for(self, venue, distance) -> float:
        """The shrunk slope for a cell, falling back to the global slope."""
        try:
            key = (str(venue), int(distance))
        except (TypeError, ValueError):
            return self.global_slope
        return self.slopes.get(key, self.global_slope)


def normalised_draw(draw, field_size) -> float | None:
    """Gate as a fraction of the field: 0.0 innermost, 1.0 widest.

    Returns None when the draw is unknown or the field is too small to have an
    inside and an outside -- a one-runner race has no draw effect to measure and
    dividing by zero to say so would be worse.

    Clipped to [0, 1] because a draw can exceed the field size after
    scratchings: a horse drawn 14 in a race that 12 contest is still the widest
    of them, and that is exactly 1.0. 103 of 21,100 archived runs are in this
    position.
    """
    if draw is None or field_size is None:
        return None
    try:
        d, n = float(draw), float(field_size)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(d) or not np.isfinite(n) or n < 2 or d < 1:
        return None
    return float(np.clip((d - 1.0) / (n - 1.0), 0.0, 1.0))


def _slope(ndraw: np.ndarray, npos: np.ndarray) -> float | None:
    """OLS slope of npos on ndraw. None when the draws carry no spread."""
    if len(ndraw) < 2:
        return None
    var = float(np.var(ndraw))
    if var <= 1e-12:
        return None
    cov = float(np.mean((ndraw - ndraw.mean()) * (npos - npos.mean())))
    return cov / var


def draw_table(hist: pd.DataFrame) -> DrawTable:
    """Fit the slopes from historical runs.

    `hist` needs `place`, `draw`, `venue`, `distance` and either `field_size` or
    the `race_date`/`race_no` pair to derive it from. Rows missing a place, a
    draw or a distance are dropped -- the 15 July 2026 card carries results for
    all 107 runners and no gates at all, and a table fitted as though those were
    gate 0 would be poisoned by it.

    Walk-forward is the CALLER's responsibility: pass only runs strictly before
    the meeting being scored. `jobs/rebuild_sarr` does this per meeting. A table
    fitted on the race it then scores is the single easiest way to manufacture a
    finding in this package, and `model/backtest.py` says so at length.
    """
    if hist is None or len(hist) == 0:
        raise DrawError("no history supplied; cannot fit a draw table")

    d = hist.copy()
    if "field_size" not in d.columns:
        if not {"race_date", "race_no"} <= set(d.columns):
            raise DrawError(
                "need a field_size column, or race_date and race_no to derive one")
        d["field_size"] = d.groupby(["race_date", "race_no"])["place"].transform("size")

    for col in ("place", "draw", "distance", "field_size"):
        if col not in d.columns:
            raise DrawError(f"missing required column {col!r}")
        d[col] = pd.to_numeric(d[col], errors="coerce")

    d = d.dropna(subset=["place", "draw", "distance", "field_size", "venue"])
    d = d[d["field_size"] >= 2]
    if d.empty:
        raise DrawError("no runs carry a place, a draw and a distance together")

    span = d["field_size"] - 1.0
    d["npos"] = ((d["place"] - 1.0) / span).clip(0.0, 1.0)
    d["ndraw"] = ((d["draw"] - 1.0) / span).clip(0.0, 1.0)

    g = _slope(d["ndraw"].to_numpy(float), d["npos"].to_numpy(float))
    if g is None:
        raise DrawError("no spread in normalised draw across the whole history")

    slopes: dict[tuple[str, int], float] = {}
    counts: dict[tuple[str, int], int] = {}
    raw: dict[tuple[str, int], float] = {}
    for (venue, distance), cell in d.groupby(["venue", "distance"]):
        key = (str(venue), int(distance))
        n = len(cell)
        counts[key] = n
        s = (_slope(cell["ndraw"].to_numpy(float), cell["npos"].to_numpy(float))
             if n >= MIN_CELL_N else None)
        if s is None:
            slopes[key] = g
            continue
        raw[key] = s
        w = n / (n + SHRINK_K)
        slopes[key] = w * s + (1.0 - w) * g

    return DrawTable(slopes=slopes, global_slope=float(g), counts=counts,
                     raw_slopes=raw, n_runs=int(len(d)))


def draw_score(draw, field_size, venue, distance, table: DrawTable) -> float:
    """The centred draw score for one runner. Higher is worse.

    Sign convention matches SARR: LOWER is better, every component is a penalty,
    and this returns a positive number for a gate the table says is a
    disadvantage at that venue and distance. At ST 1000 the fitted slope is
    negative, so a wide gate correctly scores BELOW zero there.

    Returns 0.0 when the gate is unknown. That is a deliberate degradation, not
    a claim: the horse then ranks on the other eight terms rather than dropping
    out of the race entirely, which is what a NaN here would do. `query/model`
    reports the component's realised influence, so a meeting scored without
    gates shows as one -- it does not quietly look like a meeting where the draw
    did not matter.
    """
    nd = normalised_draw(draw, field_size)
    if nd is None:
        return 0.0
    return float(table.slope_for(venue, distance) * (nd - 0.5))

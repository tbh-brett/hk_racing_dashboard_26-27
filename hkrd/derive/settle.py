"""Where a horse will settle in the early stages — before the race is run.

"How quickly does it break" and "where will it end up in the run" are two
different questions, and only the first is a trait. A quick beginner drawn wide
still ends up behind a slow one drawn on the fence, because it has to cross to
get there. This module answers the second, from the first plus the gate.

WHAT IS PREDICTED. The normalised FIRST-CALL position — the field's order at the
first sectional, which is the earliest thing `running_positions` records:

    settle = (first_call - 1) / (field_size - 1)     0 = leading, 1 = last

WHAT IT IS PREDICTED FROM. Three inputs, all of which exist before the off:

    esz_rank   within-race percentile of the habitual ESZ trait, 0 = quickest
    ndraw      (draw - 1) / (field_size - 1),                    0 = innermost
    style      the habitual running style from the SARR profile

ESZ ENTERS AS A WITHIN-RACE RANK, NOT A RAW VALUE, and that is not a detail.
Early sectionals are dominated by distance and grade — a 1000m sprint's first
400m and a 2000m staying race's are different events — so a raw ESZ is not
comparable across races. `query/pace.py:early_speed_z` standardises within the
race for the same reason.

THE TWO ESZ ARE NOT THE SAME FIELD. `query/pace.py:early_speed_z` is ONE run's
jump, standardised inside that race. The SARR `esz` component is a
recency-weighted mean across a horse's history — a trait. This module wants the
TRAIT, because the race has not been run and there is no jump to measure yet.
`tests/test_esz.py:test_it_is_not_the_sarr_component` exists to stop the two
being conflated.

WHAT WAS MEASURED. Fitted on 17,251 walk-forward runs, split by date at
2026-02-19, reported on the 4,723 held-out rows that follow it:

    model                 test r    in-race rho     MAE
    ESZ rank only          0.557          0.567   0.214
    gate only              0.234          0.231   0.260
    style only             0.482          0.503   0.229
    ESZ + gate             0.605          0.601   0.202
    ESZ + gate + style     0.620          0.622   0.198

MAE 0.198 is roughly +/- 2.4 positions in a 13-horse field, and the page must
say so. A speed map that implies more precision than that is lying.

FIELD SIZE IS THE DECLARED FIELD, not the count of finishers. Measured across
the archive, `max(draw)` exceeds the finisher count in 107 races and the
declared count in none, so normalising by finishers puts `ndraw` above 1 for a
horse that was never wide. Both inputs and the target share the denominator.

WHAT THIS IS NOT. It is not a selection rule and it creates no edge. It says
where the field will be at the first call, which is a description of the shape
of a race, not a claim about who wins it. `model/backtest.py` documents why that
road is closed; nothing here reopens it.

Pure functions. Plain data in, plain data out. No sqlite3, no paths, no pandas.
"""
from __future__ import annotations

__all__ = ["DERIVE_VERSION", "COEFFICIENTS", "BANDS", "BAND_NAMES",
           "normalised_draw", "settle_score", "settle_band", "esz_ranks"]

DERIVE_VERSION = "settle-1.0"

# Ordinary least squares on all 17,251 rows. Named apart from SARR's WEIGHTS
# because they are a different fit answering a different question, and folding
# them together would misrepresent how either was obtained.
#
# The card being projected is never in this fit -- it has no results yet, so it
# cannot be. The walk-forward rule is satisfied by construction rather than by
# configuration.
COEFFICIENTS: dict[str, float] = {
    "intercept": 0.1650,
    "esz_rank": 0.4232,
    "ndraw": 0.2522,
    "Leader": -0.1172,
    "On-Pace": -0.0408,
    "Closer": 0.0224,
    # Midfield is the reference level and is deliberately absent: adding a
    # fourth dummy alongside an intercept makes the design matrix singular.
}

# Cutoffs on the PREDICTED value, chosen from its own distribution rather than
# from round numbers. Across the archive they split 18.8 / 22.6 / 27.3 / 31.3
# percent, and the actual mean first-call position inside each band comes out
# 0.215 / 0.364 / 0.520 / 0.708 -- monotone, and spread widely enough that the
# four labels mean visibly different things.
BANDS: tuple[tuple[float, str], ...] = (
    (0.30, "LEAD"),
    (0.45, "PACE"),
    (0.60, "MID"),
)
BAND_NAMES = ("LEAD", "PACE", "MID", "BACK")


def normalised_draw(draw, field_size) -> float | None:
    """(draw - 1) / (field_size - 1), or None when the gate is not known.

    None rather than a default, because a horse with no gate cannot be placed
    on a gate ladder at all. `derive/draw.py` can fall back to 0.0 and let the
    other eight terms rank the horse; a speed map has nowhere to draw it.
    """
    if draw is None or field_size is None:
        return None
    try:
        d, n = float(draw), int(field_size)
    except (TypeError, ValueError):
        return None
    if d != d or n < 2 or d < 1:          # d != d catches NaN
        return None
    return (d - 1.0) / (n - 1.0)


def esz_ranks(values: list[float | None]) -> list[float | None]:
    """Within-race percentile of the habitual ESZ, 0 = quickest away.

    Ties share the average rank, the same convention the fit used. A runner
    with no ESZ keeps its place in the list as None and is not ranked -- it is
    excluded from the denominator too, so one unrated horse does not shift
    everybody else's percentile.
    """
    known = [(i, v) for i, v in enumerate(values)
             if v is not None and v == v]
    out: list[float | None] = [None] * len(values)
    n = len(known)
    if n == 0:
        return out
    if n == 1:
        out[known[0][0]] = 0.0
        return out
    order = sorted(known, key=lambda p: p[1])
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][1] == order[i][1]:
            j += 1
        shared = (i + j) / 2.0 / (n - 1.0)      # average rank, scaled to [0,1]
        for k in range(i, j + 1):
            out[order[k][0]] = shared
        i = j + 1
    return out


def settle_score(esz_rank: float | None, ndraw: float | None,
                 style: str | None) -> float | None:
    """Projected normalised first-call position, or None if it cannot be said.

    Returns None rather than a number whenever either continuous input is
    missing. A zero would read as "breaks at field average", which is a claim
    about a horse we know nothing about -- the failure `query/model.py:_unscored`
    exists to avoid. An unknown STYLE is different: Midfield is the reference
    level of the fit, so an unclassifiable style is genuinely the neutral case
    and contributes nothing.
    """
    if esz_rank is None or ndraw is None:
        return None
    if esz_rank != esz_rank or ndraw != ndraw:
        return None
    value = (COEFFICIENTS["intercept"]
             + COEFFICIENTS["esz_rank"] * float(esz_rank)
             + COEFFICIENTS["ndraw"] * float(ndraw)
             + COEFFICIENTS.get(str(style), 0.0))
    # The target lives on [0, 1] by construction and a linear fit does not know
    # that. Clamping keeps a projection off the end of the ladder it is drawn on.
    return min(1.0, max(0.0, value))


def settle_band(settle: float | None) -> str | None:
    """LEAD / PACE / MID / BACK, or None when there is no projection."""
    if settle is None or settle != settle:
        return None
    for cutoff, name in BANDS:
        if settle < cutoff:
            return name
    return "BACK"

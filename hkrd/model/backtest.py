"""Is the model calibrated, and is there anything to bet on.

Two questions, and they are not the same one. The legacy
`calibration_harness.py` framed the distinction and it is the right frame:

    "Where backtest_combined_edge.py asks 'did filter X make money?', this
     harness asks two more fundamental questions: 1. Calibration: when our
     model says P(win)=p, does the horse really win at p?"

A model can be profitable and badly calibrated, or perfectly calibrated and
unprofitable. Reporting only the second is how a filter that got lucky over
forty races becomes a rule.

Everything here is WALK-FORWARD. A calibration curve fitted on the same races
it is scored against will look excellent and mean nothing, which is the single
easiest way to manufacture a finding in this whole package. The split is by
DATE, never by row: two runners in the same race are not independent
observations, and splitting between them leaks the result across the boundary.

WHAT THIS FOUND. The headline is negative and is reported as such rather than
buried. Walk-forward over 596 usable races (train: 327 before 2025-12-14,
test: 269 from it, 3,321 test runners):

THE MARKET IS WELL CALIBRATED. Every bin's predicted rate falls inside the
interval its outcomes support -- nine bins, none off:

      band        n     predicted   actual   95% CI
      0-2%      662         1.2%     1.4%   [0.7%, 2.6%]
      2-5%      817         3.4%     2.5%   [1.6%, 3.8%]
      5-8%      566         6.2%     6.4%   [4.6%, 8.7%]
      8-12%     553         9.5%    10.7%   [8.4%, 13.5%]
     12-18%     369        14.5%    14.9%   [11.6%, 18.9%]
     18-25%     213        21.1%    19.2%   [14.5%, 25.1%]
     25-35%     102        29.1%    31.4%   [23.2%, 40.9%]
     35-50%      33        41.3%    42.4%   [27.2%, 59.2%]  thin
     50-100%      6        66.6%    66.7%   [30.0%, 90.3%]  thin

    Brier 0.06788, log loss 2.0983.

THERE IS NOTHING TO BET ON. At the fitted weight of zero the model IS the
market, so there are no value bets by construction. At every positive weight
and every edge threshold the answer is a loss, and it gets WORSE as more
disagreement is required -- which is the signature of no signal rather than of
a signal too small to exploit:

      weight   edge    bets   strike      ROI
        0.05    10%     555     1.3%   -42.2%
        0.10    10%    1007     1.4%   -50.1%
        0.10    25%     411     1.0%   -59.4%
        0.25    10%    1563     2.4%   -36.5%
        1.00    10%    1952     3.4%   -30.2%

    Demanding a bigger disagreement selects the horses the market is most
    confident the model is wrong about, and the market is right about them.

That is why `model/staking.py` is not in this package. Kelly sizing on an edge
that has not been shown to exist compounds the error rather than exploiting
it, and the question "is there an edge" has to be answered before "how much".
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from hkrd.model import blend as blend_m
from hkrd.store.connect import Connection, get_conn

__all__ = ["races_for_backtest", "calibration", "value_bets", "walk_forward",
           "BINS", "MIN_BIN", "MEASURED"]

# Published so the page renders the finding rather than restating it, and so a
# rerun that disagrees with it is visible. Recomputed live by `walk_forward`;
# this is what it returned when the module was written.
MEASURED = {
    "races": 596, "train_races": 327, "test_races": 269,
    "split_date": "2025-12-14", "test_runners": 3321,
    "brier": 0.06788, "log_loss": 2.0983, "off_bins": 0,
    "value_at_fitted_weight": {"bets": 0, "roi": None},
    "value_by_weight": [
        {"weight": 0.05, "edge": 0.10, "bets": 555, "strike": 0.013, "roi": -0.422},
        {"weight": 0.10, "edge": 0.10, "bets": 1007, "strike": 0.014, "roi": -0.501},
        {"weight": 0.10, "edge": 0.25, "bets": 411, "strike": 0.010, "roi": -0.594},
        {"weight": 0.25, "edge": 0.10, "bets": 1563, "strike": 0.024, "roi": -0.365},
        {"weight": 1.00, "edge": 0.10, "bets": 1952, "strike": 0.034, "roi": -0.302},
    ],
    "reading": ("Requiring more disagreement with the market makes the return "
                "worse, not better. That is the signature of no signal, not of "
                "a signal too small to exploit."),
}

# Probability bins for the reliability curve. Uneven on purpose: most runners
# sit under 20% and a uniform grid would put nine tenths of the field in two
# buckets and then read their noise as structure.
BINS = (0.0, 0.02, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.50, 1.0)

# Under this a bin's rate is not a rate. Shown dimmed rather than dropped --
# removing it hides how much of the curve is unsupported.
MIN_BIN = 50

# Smaller than any difference that could be a real disagreement between two
# probability streams, and larger than the float error of computing the same
# number twice.
_EPS = 1e-9


def races_for_backtest(*, conn: Connection | None = None,
                       since: str | None = None) -> list[dict[str, Any]]:
    """Every race with a complete scored field, a winner, and complete odds.

    All three are required together. A race missing one runner's odds cannot be
    de-vigged, a race missing one runner's SARR cannot be scored as a field,
    and a race with no recorded winner cannot be evaluated -- and letting any
    of them through with a gap silently changes what the probabilities mean.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where = "AND r.race_date >= ?" if since else ""
        params = [since] if since else []
        rows = conn.execute(f"""
            SELECT r.race_date, r.race_no, r.horse_no, r.horse_name,
                   r.place, r.win_odds, s.sarr
            FROM runners r
            LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            WHERE 1 = 1 {where}
            ORDER BY r.race_date, r.race_no, r.horse_no""", params).fetchall()

        races: dict[tuple[str, int], list[dict]] = {}
        for row in rows:
            races.setdefault((row["race_date"], row["race_no"]), []).append(
                dict(row))

        out = []
        for (date, race_no), field in races.items():
            if any(f["sarr"] is None for f in field):
                continue
            if any(not f["win_odds"] or f["win_odds"] <= 0 for f in field):
                continue
            if not any(f["place"] == 1 for f in field):
                continue
            out.append({"race_date": date, "race_no": race_no, "field": field})
        return out
    finally:
        if own:
            conn.close()


def _probabilities(field: list[dict], weight: float) -> np.ndarray:
    fund = blend_m.fundamental_probability([f["sarr"] for f in field])
    market = blend_m.market_probability([f["win_odds"] for f in field])
    return blend_m.blend(fund, market, weight)


def _wilson(hits: int, n: int) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z = 1.96
    phat = hits / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def calibration(races: list[dict[str, Any]], *,
                weight: float = blend_m.DEFAULT_BLEND_WEIGHT) -> dict[str, Any]:
    """When the model says P(win)=p, does the horse win at p?

    Returns the reliability curve with an interval on every bin, plus the two
    summary numbers that mean something across the whole set: the Brier score,
    and the log loss of the winner. Neither replaces the curve -- a model can
    have a good Brier score and be systematically wrong at the short end,
    which is exactly where the money is.
    """
    predicted: list[float] = []
    won: list[int] = []
    log_loss = 0.0
    for race in races:
        probs = _probabilities(race["field"], weight)
        for runner, p in zip(race["field"], probs):
            predicted.append(float(p))
            hit = 1 if runner["place"] == 1 else 0
            won.append(hit)
            if hit:
                log_loss -= math.log(max(float(p), 1e-12))

    if not predicted:
        return {"races": 0, "runners": 0, "bins": [], "brier": None,
                "log_loss": None, "weight": weight}

    p_arr = np.asarray(predicted)
    y_arr = np.asarray(won, dtype=float)
    bins = []
    for lo, hi in zip(BINS, BINS[1:]):
        mask = (p_arr >= lo) & (p_arr < hi)
        n = int(mask.sum())
        if not n:
            continue
        hits = int(y_arr[mask].sum())
        interval = _wilson(hits, n)
        mean_p = float(p_arr[mask].mean())
        actual = hits / n
        bins.append({
            "lo": lo, "hi": hi, "runners": n, "wins": hits,
            "predicted": round(mean_p, 4),
            "actual": round(actual, 4),
            "ci": list(interval) if interval else None,
            # True when the model's own prediction falls outside the interval
            # the outcome supports. That is a miscalibration, not a near miss.
            "off": bool(interval and not (interval[0] <= mean_p <= interval[1])),
            "thin": n < MIN_BIN,
        })

    return {
        "races": len(races),
        "runners": len(predicted),
        "weight": weight,
        "bins": bins,
        "brier": round(float(((p_arr - y_arr) ** 2).mean()), 5),
        "log_loss": round(log_loss / len(races), 4),
        "off_bins": sum(1 for b in bins if b["off"] and not b["thin"]),
        "min_bin": MIN_BIN,
    }


def value_bets(races: list[dict[str, Any]], *,
               weight: float = blend_m.DEFAULT_BLEND_WEIGHT,
               edge: float = 0.0, stake: float = 100.0) -> dict[str, Any]:
    """Flat-stake every runner the model prices shorter than the market.

    `edge` is the margin required before backing: 0.10 means the model's
    probability must exceed the market's implied probability by a tenth of
    itself. A flat stake rather than Kelly on purpose -- Kelly sizing on an
    edge that has not been shown to exist compounds the error, so the question
    "is there an edge" is answered before the question "how much".

    The de-vigged market is the comparison, not the raw price. Comparing
    against the raw 1/odds makes every runner look overpriced by the overround
    and would call the whole field a value bet.
    """
    picks = []
    staked = returned = 0.0
    for race in races:
        field = race["field"]
        probs = _probabilities(field, weight)
        market = blend_m.market_probability([f["win_odds"] for f in field])
        for runner, p, m in zip(field, probs, market):
            # At weight 0 the blend IS the market, so p and m are the same
            # number reached by two code paths and differ only in the last
            # bits. Without this floor, half the field reads as value and the
            # harness returns the ROI of picking at random -- which it did:
            # 882 "value bets" at a weight where by construction there are
            # none.
            if m <= 0 or (p - m) <= max(edge * m, _EPS):
                continue
            hit = runner["place"] == 1
            got = stake * runner["win_odds"] if hit else 0.0
            staked += stake
            returned += got
            picks.append({
                "race_date": race["race_date"], "race_no": race["race_no"],
                "horse_name": runner["horse_name"],
                "win_odds": runner["win_odds"],
                "model_p": round(float(p), 4), "market_p": round(float(m), 4),
                "edge": round(float(p / m - 1.0), 4),
                "won": hit, "returned": round(got, 2),
            })

    wins = sum(1 for p in picks if p["won"])
    interval = _wilson(wins, len(picks))
    return {
        "races": len(races),
        "bets": len(picks),
        "wins": wins,
        "strike_rate": round(wins / len(picks), 4) if picks else None,
        "strike_ci": list(interval) if interval else None,
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "pnl": round(returned - staked, 2),
        "roi": round((returned - staked) / staked, 4) if staked else None,
        "edge_required": edge,
        "weight": weight,
        # A flat bet on every runner returns minus the overround by
        # construction. Stating it makes the ROI above readable as a
        # comparison rather than as a number on its own.
        "note": ("A flat bet on every runner loses the overround by "
                 "construction. An ROI near that is the model finding "
                 "nothing, not the model failing."),
        "picks": picks[:200],
        "truncated": len(picks) > 200,
    }


def walk_forward(*, split_date: str | None = None, edge: float = 0.0,
                 weight: float | None = None,
                 conn: Connection | None = None) -> dict[str, Any]:
    """Fit nothing on the test set. Split by DATE, never by row.

    Two runners in the same race are not independent observations: if one wins
    the others did not, so splitting between them leaks the answer across the
    boundary. The split is therefore between MEETINGS, and the returned
    `split_date` says where.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        races = races_for_backtest(conn=conn)
        if not races:
            return {"races": 0, "train": None, "test": None,
                    "split_date": None, "usable": False}
        dates = sorted({r["race_date"] for r in races})
        if split_date is None:
            split_date = dates[int(len(dates) * 0.6)]
        train = [r for r in races if r["race_date"] < split_date]
        test = [r for r in races if r["race_date"] >= split_date]
        w = blend_m.DEFAULT_BLEND_WEIGHT if weight is None else weight

        return {
            "races": len(races),
            "split_date": split_date,
            "train_races": len(train),
            "test_races": len(test),
            "weight": w,
            "calibration": calibration(test, weight=w),
            "value": value_bets(test, weight=w, edge=edge),
            # The market alone, on the same test set, as the thing to beat.
            "market_calibration": calibration(test, weight=0.0),
            "usable": bool(test),
        }
    finally:
        if own:
            conn.close()

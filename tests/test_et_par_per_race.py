"""Regression test: a par time is a property of the race, not of the runner.

v4 keyed ET on weight_band, so horses in the same race received different par
times purely from carrying different weights — up to 1.98s apart in a single
1600m race. This test exists so that no future change reintroduces it.
"""

import pandas as pd
import pytest

from hkrd.derive import et


@pytest.fixture
def one_race_mixed_weights():
    """Twelve runners, same race, weights spread across five v4 weight bands."""
    weights = [110, 114, 117, 120, 123, 126, 129, 132, 115, 121, 127, 133]
    return pd.DataFrame([
        {
            "race_date": pd.Timestamp("2026-07-12"),
            "race_no": 7,
            "distance": 1600,
            "going": "GF",
            "race_course": "A",
            "track_type": "Turf",
            "actual_weight": w,
            "race_class": "4",
            "finish_time": 94.0 + i * 0.1,
            "lbw": "---" if i == 0 else "1-1/2",
            "horse_name": f"HORSE {i}",
        }
        for i, w in enumerate(weights)
    ])


@pytest.fixture
def history():
    """Enough prior racing to build references over."""
    rng = pd.date_range("2025-01-01", periods=120, freq="3D")
    rows = []
    for i, day in enumerate(rng):
        for r in range(1, 11):
            for h in range(12):
                rows.append({
                    "race_date": day,
                    "race_no": r,
                    "distance": 1600,
                    "going": "GF",
                    "race_course": "A",
                    "track_type": "Turf",
                    "actual_weight": 110 + (h * 2),
                    "race_class": "4",
                    "finish_time": 94.5 + h * 0.139 * 1.5,
                    "lbw": "---" if h == 0 else f"{h}-1/2",
                })
    return pd.DataFrame(rows)


def test_one_par_per_race(history, one_race_mixed_weights):
    refs = et.build_references(history, window_months=None)
    out = et.expected_time(one_race_mixed_weights, refs)
    assert out["et"].nunique() == 1, (
        f"expected a single par for the race, got {out['et'].nunique()}: "
        f"{sorted(out['et'].unique())}"
    )


def test_weight_band_not_an_et_key():
    keys = {k for _, ks in et._LEVELS for k in ks}
    assert "weight_band" not in keys, (
        "weight_band was reintroduced as an ET key. It fragments cells "
        "(median n 26 -> 3) and splits one race across several par times, "
        "to capture an effect measured at 0.021s per 10lb."
    )


def test_figures_still_separate_runners(history, one_race_mixed_weights):
    """Shrinkage must not flatten the field into one figure."""
    refs = et.build_references(history, window_months=None)
    out = et.speed_figure(one_race_mixed_weights, refs)
    spread = out["len_vs_par"].max() - out["len_vs_par"].min()
    assert spread > 3.0, f"within-race spread collapsed to {spread:.2f}L"

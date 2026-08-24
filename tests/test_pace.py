"""derive/pace — running style (decision A2) and sectional deviations."""
from __future__ import annotations

import pytest

from hkrd.derive import pace


# ── A2: the field-size-scaled classifier is canonical ────────────────────────

@pytest.mark.parametrize("first,field,expected", [
    (1, 12, "Leader"), (2, 12, "Leader"),
    (3, 12, "On-Pace"), (4, 12, "On-Pace"),
    (5, 12, "Midfield"), (7, 12, "Midfield"),
    (9, 12, "Closer"), (12, 12, "Closer"),
])
def test_style_boundaries(first, field, expected):
    assert pace.classify_style([first, first, first], field) == expected


@pytest.mark.parametrize("field,expected", [
    (9, "Closer"), (10, "Closer"), (11, "Closer"), (12, "Closer"),
    (13, "Midfield"), (14, "Midfield"),
])
def test_the_a2_disagreement_case(field, expected):
    """First-call position 8 -- the ONLY case the four legacy implementations
    disagreed on, across all 21,130 runs.

    A fixed "> 7 is a Closer" fires at every field size. The scaled cutoff,
    max(8, field * 0.7), reaches 9 at 13+ runners, so position 8 becomes
    mid-division -- which is correct: 8th of 14 is not a closer, 8th of 9 is.
    """
    assert pace.classify_style([8, 8, 8], field) == expected


def test_style_sorts_positionally_not_alphabetically():
    """Alphabetical gives Closer, Leader, Midfield, On-Pace -- meaningless."""
    styles = ["Closer", "Leader", "Midfield", "On-Pace"]
    assert sorted(styles, key=pace.style_ordinal) == [
        "Leader", "On-Pace", "Midfield", "Closer"]
    assert sorted(styles) != sorted(styles, key=pace.style_ordinal)


def test_unknown_style_sorts_last_and_never_raises():
    assert pace.style_ordinal("Nonsense") == len(pace.STYLE_ORDER)
    assert pace.classify_style(None, 12) == "Unknown"
    assert pace.classify_style([], 12) == "Unknown"
    assert pace.classify_style([4], 0) == "Unknown"


def test_style_accepts_both_legacy_position_shapes():
    assert pace.classify_style("10 9 6 3 2", 12) == "Closer"
    assert pace.classify_style("4; 3; 3; 1", 12) == "On-Pace"


# ── sectionals ───────────────────────────────────────────────────────────────

def test_sections_are_not_equal_lengths():
    """An 1800m race opens with a 200m section; treating it as 400m is wrong."""
    assert pace.section_lengths(1800) == (200, 400, 400, 400, 400)
    assert pace.section_lengths(1200) == (400, 400, 400)


def test_per_400_normalises_a_short_opening_section():
    """A 200m split of 12s is a 24s 400m pace, not a fast 12s one."""
    norm = pace.per_400([12.0, 24.0, 24.0, 24.0, 24.0], 1800)
    assert norm[0] == pytest.approx(24.0)
    assert all(v == pytest.approx(24.0) for v in norm)


def test_unknown_distance_raises_naming_it():
    with pytest.raises(pace.PaceError, match="1234"):
        pace.section_lengths(1234)


def test_wrong_section_count_raises_rather_than_guessing():
    with pytest.raises(pace.PaceError, match="expects 5 sections, got 3"):
        pace.per_400([24.0, 24.0, 24.0], 1800)


# ── deviations are from the RACE MEDIAN, not a par ───────────────────────────

def _race(splits_per_runner, positions=None):
    return [{"race_date": "2026-07-15", "race_no": 3, "horse_no": i + 1,
             "section_times": "; ".join(str(s) for s in splits),
             "running_positions": (positions or ["1 1 1"] * len(splits_per_runner))[i]}
            for i, splits in enumerate(splits_per_runner)]


def test_deviations_are_centred_on_the_field():
    runners = _race([
        [12.0, 23.0, 23.0, 23.0, 23.0],
        [12.0, 24.0, 24.0, 24.0, 24.0],
        [12.0, 25.0, 25.0, 25.0, 25.0],
    ])
    out = pace.race_pace_rows(runners, 1800)
    assert sorted(r["late_dev"] for r in out)[1] == pytest.approx(0.0)
    assert out[0]["late_dev"] < 0 < out[2]["late_dev"]


def test_faster_than_the_field_reads_negative():
    """The intuitive direction: below the median is quicker."""
    out = pace.race_pace_rows(_race([
        [12.0, 22.0, 22.0, 22.0, 22.0],
        [12.0, 24.0, 24.0, 24.0, 24.0],
        [12.0, 24.0, 24.0, 24.0, 24.0],
    ]), 1800)
    assert out[0]["late_dev"] < 0


def test_every_row_carries_a_derive_version():
    """A formula change must not silently overwrite figures computed by the old one."""
    out = pace.race_pace_rows(_race([[12.0, 24.0, 24.0, 24.0, 24.0]]), 1800)
    assert out[0]["derive_version"] == pace.DERIVE_VERSION


def test_malformed_sectionals_raise_with_the_identifying_key():
    """Never return None -- a blank column three days later is the failure mode."""
    bad = [{"race_date": "2026-07-15", "race_no": 3, "horse_no": 7,
            "section_times": "24.97; oops; 23.6", "running_positions": "1 1 1"}]
    with pytest.raises(pace.PaceError, match="R3 #7"):
        pace.race_pace_rows(bad, 1800)


def test_runners_without_sectionals_degrade_rather_than_void_the_race():
    """A missing minor input must not void the whole result."""
    runners = _race([[12.0, 24.0, 24.0, 24.0, 24.0], [], [12.0, 23.0, 23.0, 23.0, 23.0]])
    out = pace.race_pace_rows(runners, 1800)
    assert len(out) == 3
    assert out[1]["late_dev"] is None
    assert out[0]["late_dev"] is not None    # the others still compute


def test_empty_race_returns_empty():
    assert pace.race_pace_rows([], 1800) == []

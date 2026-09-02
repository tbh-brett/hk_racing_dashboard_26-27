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


# ── one horse that did not finish must not void the field ────────────────────
#
# This was live. A runner that pulled up carries a short section list — HKJC
# records what it completed and pads the rest — and per_400's length check
# raised out of the per-runner loop in race_pace_rows, taking the whole race
# with it. Measured on the real database: 30 races, 377 runners with no pace
# figure at all, every one of them a full field killed by a single non-finisher.
# AGENTS.md, Error handling: a missing minor input must never void a whole
# result.

def test_one_non_finisher_does_not_remove_the_fields_pace():
    runners = _race([
        [24.0, 23.0, 23.0],
        [24.5, 23.2, 22.8],
        [24.2, 23.1, 23.4],
        [24.1],                      # pulled up after the first section
    ])
    rows = pace.race_pace_rows(runners, 1200)

    assert len(rows) == 4, "every runner keeps a row"
    finished = [r for r in rows if not r["incomplete"]]
    assert len(finished) == 3
    assert all(r["early_pace"] is not None for r in finished)


def test_the_non_finisher_gets_no_figure_but_keeps_its_style():
    """It has no pace and never will. Its running positions are still real."""
    runners = _race([
        [24.0, 23.0, 23.0],
        [24.5, 23.2, 22.8],
        [24.1],
    ], positions=["1 1 1", "3 3 2", "8 9 9"])
    rows = pace.race_pace_rows(runners, 1200)

    short = [r for r in rows if r["incomplete"]]
    assert len(short) == 1
    assert short[0]["early_pace"] is None
    assert short[0]["late_pace"] is None
    assert short[0]["sec_400"] is None
    assert short[0]["pace_style"] is not None


def test_the_median_ignores_the_non_finisher():
    """Otherwise one short runner drags the field's centre and every deviation
    beside it is wrong — a quieter failure than the one this replaced."""
    full = _race([[24.0, 23.0, 23.0], [26.0, 23.0, 23.0], [28.0, 23.0, 23.0]])
    with_dnf = full + _race([[24.1]])[:1]
    with_dnf[-1]["horse_no"] = 4

    a = {r["horse_no"]: r["early_dev"] for r in pace.race_pace_rows(full, 1200)}
    b = {r["horse_no"]: r["early_dev"] for r in pace.race_pace_rows(with_dnf, 1200)}
    for horse in (1, 2, 3):
        assert a[horse] == pytest.approx(b[horse])


def test_too_many_sections_still_raises_because_that_is_our_bug():
    """Short means a horse stopped. LONG means the section layout for this
    distance is wrong, which is a fault in the table and must stay loud."""
    runners = _race([[24.0, 23.0, 23.0], [24.0, 23.0, 23.0, 23.0, 23.0]])
    with pytest.raises(pace.PaceError, match="section layout is wrong"):
        pace.race_pace_rows(runners, 1200)

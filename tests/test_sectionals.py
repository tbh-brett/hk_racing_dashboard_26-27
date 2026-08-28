"""Where a race was won — section by section.

`derive/pace` normalises the splits and reads the first and last. What this
adds is the middle, and the pairing of a section's time with the places
changed over it:

    A horse that ran the fastest closing 400m and gained six places did
    something. A horse that ran the fastest closing 400m from last and gained
    nothing was simply left behind, and the two are indistinguishable from the
    time alone.
"""
from __future__ import annotations

import pytest

from hkrd.derive import pace, sectionals as sx


# ── the segmentation ─────────────────────────────────────────────────────────

def test_the_section_table_is_the_one_pace_already_uses():
    """One table. A second copy would drift from the one SARR and the pace
    bands are built on."""
    assert sx.section_lengths is pace.section_lengths
    assert pace.section_lengths(1650) == (450, 400, 400, 400)


@pytest.mark.parametrize("distance,lengths", [
    (1000, (200, 400, 400)),
    (1200, (400, 400, 400)),
    (1650, (450, 400, 400, 400)),
    (2200, (200, 400, 400, 400, 400, 400)),
])
def test_sections_run_from_the_finish_backwards(distance, lengths):
    """HKJC splits in 400m from the FINISH, and the OPENING section carries
    the remainder. Checked against the archive: every opening section is
    slower per metre than the rest, which a standing start requires and the
    reverse assignment would contradict."""
    assert pace.section_lengths(distance) == lengths
    assert sum(lengths) == distance


def test_a_split_count_that_does_not_match_the_distance_raises():
    """Three splits for an 1800m race is a parse that went wrong, not a race
    with three sections."""
    with pytest.raises(sx.SectionError, match="expects 5 sections, got 3"):
        sx.decompose([14.0, 23.0, 24.0], [3, 2, 1], 1800)


# ── one runner ───────────────────────────────────────────────────────────────

def test_the_opening_section_has_no_places_gained():
    """Every horse starts level, so there is no earlier position to have
    gained on. Reporting a gain from the draw would confuse a wide barrier
    with a bad beginning."""
    out = sx.decompose([25.0, 23.0, 24.0, 23.5], [10, 10, 7, 1], 1650)
    assert out[0]["places_gained"] is None
    assert [s["places_gained"] for s in out[1:]] == [0, 3, 6]


def test_a_section_is_counted_from_the_finish_not_from_the_start():
    """"The last 400" is how a sectional is spoken about."""
    out = sx.decompose([25.0, 23.0, 24.0, 23.5], [10, 10, 7, 1], 1650)
    assert [s["from_finish"] for s in out] == [4, 3, 2, 1]
    assert out[-1]["length_m"] == 400
    assert out[0]["length_m"] == 450


def test_speed_uses_the_section_s_own_length():
    """A 450m opening and a 400m closing are not comparable as raw times, and
    that is exactly the mistake the normalisation exists to stop."""
    out = sx.decompose([25.0, 23.0, 24.0, 23.5], [10, 10, 7, 1], 1650)
    assert out[0]["speed_ms"] == round(450 / 25.0, 3)
    assert out[-1]["speed_ms"] == round(400 / 23.5, 3)
    # per_400 puts them on one scale: 25s for 450m is faster per 400m than 25
    # would be for 400m.
    assert out[0]["per_400"] < 25.0


# ── against the field ────────────────────────────────────────────────────────

def _race(*runs):
    """runs: (horse_no, splits, positions)."""
    return [{"race_date": "2026-05-01", "race_no": 1, "horse_no": no,
             "horse_name": f"H{no}",
             "section_times": "; ".join(f"{s:.2f}" for s in splits),
             "running_positions": " ".join(str(p) for p in positions)}
            for no, splits, positions in runs]


def test_a_section_is_ranked_within_its_own_field():
    out = sx.race_sections(_race(
        (1, [25.0, 23.0, 24.0, 22.5], [4, 4, 3, 1]),
        (2, [25.0, 23.0, 24.0, 23.0], [1, 1, 1, 2]),
        (3, [25.0, 23.0, 24.0, 23.5], [2, 2, 2, 3]),
    ), 1650)
    closing = {p["horse_no"]: p["sections"][-1]["rank"] for p in out}
    assert closing == {1: 1, 2: 2, 3: 3}


def test_ties_share_a_rank_rather_than_inventing_an_order():
    """Two runners on the same split are both equal-second. Ranking them 2 and
    3 claims an order the clock did not measure."""
    out = sx.race_sections(_race(
        (1, [25.0, 23.0, 24.0, 22.5], [3, 3, 3, 1]),
        (2, [25.0, 23.0, 24.0, 23.0], [1, 1, 1, 2]),
        (3, [25.0, 23.0, 24.0, 23.0], [2, 2, 2, 3]),
    ), 1650)
    closing = {p["horse_no"]: p["sections"][-1]["rank"] for p in out}
    assert closing == {1: 1, 2: 2, 3: 2}


def test_the_comparison_is_the_race_median_not_a_par():
    """It removes the day's track speed, so the figure answers "did this horse
    go faster than its own field" — which is not the question the ET par
    figure already answers."""
    slow = sx.race_sections(_race(
        (1, [26.0, 24.0, 25.0, 24.0], [2, 2, 2, 1]),
        (2, [26.0, 24.0, 25.0, 25.0], [1, 1, 1, 2]),
    ), 1650)
    fast = sx.race_sections(_race(
        (1, [24.0, 22.0, 23.0, 22.0], [2, 2, 2, 1]),
        (2, [24.0, 22.0, 23.0, 23.0], [1, 1, 1, 2]),
    ), 1650)
    # The same one-second gap to the field reads the same on a slow day and a
    # fast one.
    assert slow[0]["closing_dev"] == fast[0]["closing_dev"]


def test_negative_deviation_means_faster_than_the_field():
    """The direction every other deviation in this package uses."""
    out = sx.race_sections(_race(
        (1, [25.0, 23.0, 24.0, 22.0], [2, 2, 2, 1]),
        (2, [25.0, 23.0, 24.0, 24.0], [1, 1, 1, 2]),
    ), 1650)
    quick = next(p for p in out if p["horse_no"] == 1)
    assert quick["closing_dev"] < 0


# ── the read ─────────────────────────────────────────────────────────────────

def test_a_fast_section_with_places_gained_is_a_run():
    out = sx.race_sections(_race(
        (1, [26.0, 24.0, 25.0, 21.0], [8, 8, 6, 1]),
        (2, [25.0, 23.0, 24.0, 24.0], [1, 1, 1, 2]),
        (3, [25.0, 23.0, 24.0, 24.0], [2, 2, 2, 3]),
    ), 1650)
    made = next(p for p in out if p["horse_no"] == 1)
    assert "made its run over the last 400m" in made["read"]
    assert "5 places" in made["read"]


def test_a_fast_section_with_nothing_gained_says_so():
    """Being quickest from the back of a field that had already gone is not a
    run at the race, and a closing rank alone would imply it was."""
    out = sx.race_sections(_race(
        (1, [27.0, 25.0, 26.0, 21.0], [3, 3, 3, 3]),
        (2, [25.0, 23.0, 24.0, 24.0], [1, 1, 1, 1]),
        (3, [25.0, 23.0, 24.0, 24.0], [2, 2, 2, 2]),
    ), 1650)
    stayed = next(p for p in out if p["horse_no"] == 1)
    assert stayed["sections"][-1]["rank"] == 1
    assert "gained nothing" in stayed["read"]


def test_a_section_inside_the_noise_is_not_called_a_move():
    """Below 0.25 s/400m the band stops separating from the field: measured
    over the archive, -0.25 to 0.00 wins at 8.6% with an interval containing
    the 8.1% baseline."""
    out = sx.race_sections(_race(
        (1, [25.0, 23.0, 24.0, 23.9], [3, 3, 2, 1]),
        (2, [25.0, 23.0, 24.0, 24.0], [1, 1, 1, 2]),
        (3, [25.0, 23.0, 24.0, 24.1], [2, 2, 3, 3]),
    ), 1650)
    winner = next(p for p in out if p["horse_no"] == 1)
    assert all(not s["notable"] for s in winner["sections"])
    assert "made its run" not in (winner["read"] or "")


def test_a_move_is_named_by_the_marker_it_began_from():
    """Hong Kong markers count distance REMAINING. The opening section of an
    1650m race ends at the 1200m, and naming it by its position from the
    finish would put it 450 metres from where it was run."""
    out = sx.race_sections(_race(
        (1, [25.0, 21.0, 24.0, 24.0], [8, 3, 2, 1]),
        (2, [25.0, 23.5, 24.0, 24.0], [1, 1, 1, 2]),
        (3, [25.0, 23.5, 24.0, 24.0], [2, 2, 3, 3]),
    ), 1650)
    mover = next(p for p in out if p["horse_no"] == 1)
    assert mover["best_section_label"] == "the 800m"
    assert "from the 800m" in mover["read"]


def test_a_runner_with_no_splits_is_left_out_rather_than_zeroed(  ):
    out = sx.race_sections([
        {"race_date": "2026-05-01", "race_no": 1, "horse_no": 1,
         "horse_name": "A", "section_times": "25.0; 23.0; 24.0; 23.0",
         "running_positions": "2 2 2 1"},
        {"race_date": "2026-05-01", "race_no": 1, "horse_no": 2,
         "horse_name": "B", "section_times": "", "running_positions": ""},
    ], 1650)
    assert [p["horse_no"] for p in out] == [1]


def test_an_empty_race_returns_nothing_rather_than_raising():
    assert sx.race_sections([], 1650) == []

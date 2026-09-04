"""Where a horse will settle — the fitted projection, as pure arithmetic.

The tests that matter here are the ones about what the module REFUSES to say.
A speed map's failure mode is not a wrong number, it is a confident number for
a horse nobody knows anything about: a zero bar reads as "breaks at field
average", and a first-starter has no field average.
"""
from __future__ import annotations

import pytest

from hkrd.derive import settle as st


# ── the normalised gate ──────────────────────────────────────────────────────

def test_the_gate_is_normalised_to_the_field_it_is_drawn_in():
    """Gate 12 of 12 is the widest possible; gate 12 of 14 is not."""
    assert st.normalised_draw(1, 14) == 0.0
    assert st.normalised_draw(14, 14) == 1.0
    assert st.normalised_draw(12, 12) == 1.0
    assert st.normalised_draw(12, 14) == pytest.approx(11 / 13)


@pytest.mark.parametrize("draw, field", [
    (None, 14), (float("nan"), 14), (5, None), (5, 1), (5, 0), ("", 14),
])
def test_a_gate_that_cannot_be_normalised_is_none_not_a_default(draw, field):
    """`derive/draw` can fall back to 0.0 and let the other eight terms rank
    the horse. A gate ladder has nowhere to draw a horse with no gate, so this
    says so instead of picking the middle."""
    assert st.normalised_draw(draw, field) is None


# ── the within-race rank ─────────────────────────────────────────────────────

def test_the_quickest_ranks_zero_and_the_slowest_one():
    """Lower ESZ is a quicker first section, so ascending order is the field
    from fastest away to slowest."""
    assert st.esz_ranks([0.5, -0.5, 0.0]) == [1.0, 0.0, 0.5]


def test_ties_share_the_average_rank():
    """The convention the coefficients were fitted under. Breaking ties by
    position would make the projection depend on saddlecloth order."""
    assert st.esz_ranks([1.0, 1.0, 0.0]) == [0.75, 0.75, 0.0]


def test_a_runner_without_an_esz_is_left_out_of_the_denominator():
    """One unrated horse must not shift everybody else's percentile — the rank
    is a statement about the field that will actually race."""
    assert st.esz_ranks([-1.0, None, 1.0]) == [0.0, None, 1.0]


def test_a_single_known_runner_ranks_zero_rather_than_dividing_by_zero():
    assert st.esz_ranks([0.4, None]) == [0.0, None]


def test_an_empty_field_ranks_nothing():
    assert st.esz_ranks([None, None]) == [None, None]


# ── the projection ───────────────────────────────────────────────────────────

def test_the_fit_is_applied_as_written():
    """One arithmetic check against the coefficients, so a typo in a constant
    fails here rather than showing up as a plausible map."""
    c = st.COEFFICIENTS
    expected = c["intercept"] + c["esz_rank"] * 0.25 + c["ndraw"] * 0.5 + c["Leader"]
    assert st.settle_score(0.25, 0.5, "Leader") == pytest.approx(expected)


def test_midfield_is_the_reference_level_and_adds_nothing():
    """A fourth dummy beside an intercept makes the design matrix singular, so
    Midfield is absent from COEFFICIENTS by construction, not by oversight."""
    assert "Midfield" not in st.COEFFICIENTS
    base = st.settle_score(0.5, 0.5, "Midfield")
    assert base == pytest.approx(st.settle_score(0.5, 0.5, None))


def test_an_unknown_style_is_the_neutral_case_not_a_refusal():
    """A style we cannot classify genuinely is the reference level. A missing
    GATE is different, and the next test is the one that matters."""
    assert st.settle_score(0.5, 0.5, "Nonsense") is not None


@pytest.mark.parametrize("rank, ndraw", [
    (None, 0.5), (0.5, None), (float("nan"), 0.5), (0.5, float("nan")),
])
def test_a_missing_input_yields_no_projection_rather_than_a_zero(rank, ndraw):
    """The whole point of the module. A zero would read as "breaks at field
    average", which is a claim about a horse we know nothing about."""
    assert st.settle_score(rank, ndraw, "Leader") is None


def test_quick_away_and_inside_projects_in_front_of_slow_and_wide():
    assert st.settle_score(0.0, 0.0, "Leader") < st.settle_score(1.0, 1.0, "Closer")


def test_the_gate_can_be_outweighed_by_early_speed():
    """The reason the bar and the gate are drawn as separate axes: the quickest
    beginner in the race from gate 14 still projects in front of the slowest
    from gate 1."""
    quick_wide = st.settle_score(0.0, 1.0, "Leader")
    slow_inside = st.settle_score(1.0, 0.0, "Closer")
    assert quick_wide < slow_inside


def test_a_projection_stays_on_the_ladder_it_is_drawn_on():
    """The target lives on [0, 1] by construction and a linear fit does not
    know that."""
    assert st.settle_score(0.0, 0.0, "Leader") >= 0.0
    assert st.settle_score(1.0, 1.0, "Closer") <= 1.0


# ── the bands ────────────────────────────────────────────────────────────────

def test_the_bands_run_front_to_back_in_order():
    assert st.settle_band(0.10) == "LEAD"
    assert st.settle_band(0.40) == "PACE"
    assert st.settle_band(0.50) == "MID"
    assert st.settle_band(0.90) == "BACK"


def test_the_band_cutoffs_are_the_documented_ones():
    """They were chosen from the prediction's own distribution. A round-number
    edit here would quietly change what every label on the page means."""
    assert st.BANDS == ((0.30, "LEAD"), (0.45, "PACE"), (0.60, "MID"))
    assert st.BAND_NAMES == ("LEAD", "PACE", "MID", "BACK")


def test_no_projection_means_no_band():
    assert st.settle_band(None) is None
    assert st.settle_band(float("nan")) is None


def test_every_band_boundary_belongs_to_the_faster_side():
    """Half-open intervals, so a value exactly on a cutoff lands in one band
    and not both."""
    assert st.settle_band(0.30) == "PACE"
    assert st.settle_band(0.45) == "MID"
    assert st.settle_band(0.60) == "BACK"

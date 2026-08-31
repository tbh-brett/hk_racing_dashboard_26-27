"""Multi-select filters, and the pace band that must mean one thing."""
from __future__ import annotations

import pytest

from hkrd.query import lookup as lq, slices as sq


def test_a_multi_select_becomes_IN_not_a_chain_of_ANDs():
    """Two tracks ANDed as equalities match nothing at all."""
    where, params = lq._where({"venue": ["HV", "ST"]})
    assert "IN (?, ?)" in where
    assert params == ["HV", "ST"]


def test_a_single_value_still_uses_equality():
    where, params = lq._where({"venue": "HV"})
    assert "a.venue = ?" in where and params == ["HV"]


def test_an_empty_list_filters_nothing():
    where, params = lq._where({"venue": []})
    assert params == [] and where.strip() == "1 = 1"


def test_a_range_bound_has_no_plural_reading():
    """distance_min is a bound; two of them ANDed would contradict."""
    where, params = lq._where({"distance_min": [1200, 1600]})
    assert params == [1200]


def test_the_pace_band_has_exactly_one_definition():
    """A race must not read Fast in the grid and Neutral in a breakdown
    computed over the very same rows."""
    assert sq.DIMENSIONS["pace"] is lq._PACE_BAND


def test_rating_is_not_a_filter():
    """`rating` stopped populating in April 2026 alongside horse_id, so a
    rating band would exclude every recent run rather than narrow anything.
    Class is the categorisation this page offers instead."""
    names = {k for group in lq.FILTERS.values() for k in group}
    assert not {n for n in names if "rating" in n}
    assert "race_class" in names

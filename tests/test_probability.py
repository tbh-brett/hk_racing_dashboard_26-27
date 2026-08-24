"""derive/probability — Harville-Henery, and the transform it replaces."""
from __future__ import annotations

import numpy as np
import pytest

from hkrd.derive.probability import (
    ProbabilityError, devig, exacta_probability, pair_probability, place_probability,
)

# The real 15 Jul 2026, Happy Valley R3 card.
CARD = [3.0, 4.9, 6.1, 7.5, 12, 15, 16, 18, 26, 32, 33, 51]


def test_devig_normalises_to_one():
    p = devig(CARD)
    assert p.sum() == pytest.approx(1.0)
    assert np.all(p > 0)


def test_devig_preserves_the_market_ordering():
    p = devig(CARD)
    assert np.all(np.diff(p) < 0)   # CARD is sorted shortest-price first


@pytest.mark.parametrize("bad", [[], [0], [-1, 2], [float("nan"), 2], [float("inf")]])
def test_devig_rejects_impossible_input(bad):
    with pytest.raises(ProbabilityError):
        devig(bad)


# ── the properties any place probability must satisfy ────────────────────────

def test_place_probabilities_are_probabilities():
    pp = place_probability(CARD)
    assert np.all(pp >= 0) and np.all(pp <= 1)


def test_place_probabilities_sum_to_the_number_of_places():
    """Three places are filled in every race, so the sum across runners is 3."""
    assert place_probability(CARD, places=3).sum() == pytest.approx(3.0, abs=1e-6)
    assert place_probability(CARD, places=2).sum() == pytest.approx(2.0, abs=1e-6)


def test_place_probability_exceeds_win_probability_for_everyone():
    p, pp = devig(CARD), place_probability(CARD)
    assert np.all(pp > p)


def test_place_probability_preserves_ordering():
    pp = place_probability(CARD)
    assert np.all(np.diff(pp) < 0)


def test_a_forty_percent_chance_places_between_sixty_and_ninety():
    """The sanity bound from the build spec: plausible, and never above 1."""
    odds = [2.2, 5.0, 7.0, 9.0, 12, 16, 20, 25]
    p, pp = devig(odds), place_probability(odds)
    assert p[0] == pytest.approx(0.40, abs=0.03)
    assert 0.6 < pp[0] < 0.9


def test_every_runner_places_when_the_field_is_no_bigger_than_the_places():
    assert np.all(place_probability([2.0, 3.0, 4.0], places=3) == 1.0)


# ── the bug this module exists to prevent ────────────────────────────────────

def test_naive_linear_transform_massively_overstates_the_banker():
    """`p / sum(p) * 3` applies a fixed 3x to every runner.

    The true ratio is not constant: it is ~2.2x for the favourite and ~4.5x for
    a longshot. A fixed multiplier is therefore wrong at both ends, and wrong in
    the direction that inflates a banker -- the number that drives bet sizing.
    """
    p = devig(CARD)
    naive = p / p.sum() * 3
    hh = place_probability(CARD)
    assert naive[0] - hh[0] > 0.15          # overstated by 20+ points here
    assert hh[-1] > naive[-1]               # and understates the longshot


def test_naive_transform_can_exceed_certainty_which_proves_it_invalid():
    """At short prices `p/sum*3` returns more than 1.0 -- not a probability."""
    short = [2.0, 5.0, 7.0, 9.0, 12, 16, 20, 25, 33, 40, 50, 66]
    p = devig(short)
    assert (p / p.sum() * 3)[0] > 1.0
    assert place_probability(short)[0] <= 1.0


def test_place_ratio_falls_as_price_shortens():
    """The structural reason the linear transform cannot work."""
    p, pp = devig(CARD), place_probability(CARD)
    ratios = pp / p
    assert np.all(np.diff(ratios) > 0)      # longshots have the higher multiple
    assert ratios[0] < 3.0 < ratios[-1]     # the fixed 3x sits inside the range


# ── pairs and exactas ────────────────────────────────────────────────────────

def test_pair_probabilities_are_valid_and_ranked_sensibly():
    pairs = pair_probability(CARD)
    assert len(pairs) == 12 * 11 // 2
    assert all(0.0 <= v <= 1.0 for v in pairs.values())
    # The two shortest prices are the likeliest pair.
    assert max(pairs, key=pairs.get) == (0, 1)


def test_pair_probabilities_sum_to_one_across_all_pairs():
    """Exactly one unordered pair fills the top two."""
    assert sum(pair_probability(CARD).values()) == pytest.approx(1.0, abs=1e-6)


def test_exacta_probabilities_sum_to_one():
    assert sum(exacta_probability(CARD).values()) == pytest.approx(1.0, abs=1e-6)


def test_exacta_is_ordered_and_pair_is_not():
    ex = exacta_probability(CARD)
    assert ex[(0, 1)] != ex[(1, 0)]
    assert pair_probability(CARD)[(0, 1)] == pytest.approx(ex[(0, 1)] + ex[(1, 0)])


def test_lambda_must_be_a_valid_discount():
    with pytest.raises(ProbabilityError):
        place_probability(CARD, lam=1.5)
    with pytest.raises(ProbabilityError):
        place_probability(CARD, lam=0.0)


def test_no_discount_recovers_plain_harville():
    """lam=1 is Harville with no Henery correction: favourites read higher."""
    assert place_probability(CARD, lam=1.0)[0] > place_probability(CARD, lam=0.81)[0]

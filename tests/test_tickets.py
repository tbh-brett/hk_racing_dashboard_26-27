"""What a ticket costs — checked against HKJC's own table and two real bets.

The arithmetic here is the number in front of the confirm button, and it was
wrong for all-ups in a way that under-quoted by a whole factor: the formula's
own line count assumes one combination per leg, and a leg is a whole ticket in
its own race. A 2X1 whose first leg is a quinella-place banker with four others
is eight lines, not one.

Two independent checks on the same arithmetic, and neither is a number this
project made up:

  the published table   HKJC's All Up Combinations, supplied as a screenshot.
                        Every formula it offers over two, three, four and five
                        legs, and nothing it does not.
  two real tickets      2026-09-06 ref 3593 from the account statement ($160)
                        and bet 1 from the bet sheet ($4,000). Both are money
                        that actually moved, and both come out to the cent.
"""
from __future__ import annotations

import pytest

from hkrd.query.tickets import (
    allup_formula, allup_formulas, allup_lines, combination_count,
    pools_per_ticket,
)

# The owner's screenshot of HKJC's All Up Combinations table, transcribed as
# codes. The generator has to reproduce this exactly — including which of two
# ranges that come to the same total HKJC chose to list.
PUBLISHED = {
    2: ["2x1", "2x3"],
    3: ["3x1", "3x3", "3x4", "3x6", "3x7"],
    4: ["4x1", "4x4", "4x5", "4x6", "4x10", "4x11", "4x14", "4x15"],
    5: ["5x1", "5x5", "5x6", "5x10", "5x15", "5x16", "5x20", "5x25", "5x26",
        "5x30", "5x31"],
}


@pytest.mark.parametrize("legs, codes", sorted(PUBLISHED.items()))
def test_the_generated_table_is_hkjcs_published_one(legs, codes):
    assert [f["code"] for f in allup_formulas(legs)] == codes


def test_the_code_is_the_line_count_and_the_breakdown_says_why():
    """4x11 is "6 doubles, 4 trebles and 1 quadruple" — and 6 + 4 + 1 = 11.

    That identity is the whole of HKJC's numbering, and it is the thing nobody
    remembers, so it is on screen rather than in a lookup table.
    """
    f = allup_formula(4, "4x11")
    assert f["label"] == "6 doubles + 4 trebles + 1 quadruple"
    assert [b["count"] for b in f["breakdown"]] == [6, 4, 1]
    assert sum(b["count"] for b in f["breakdown"]) == f["combinations"] == 11


def test_singles_alone_is_not_a_chain():
    """Four singles is four bets, not an all-up, so no formula offers it."""
    assert all(f["sizes"] != [1] for f in allup_formulas(4))


def test_two_ranges_with_one_total_are_listed_once():
    """Over five legs, doubles alone and trebles alone are both 10 lines.

    HKJC lists the lower one — 5x10 is "10 doubles" — so a code cannot mean
    two different tickets.
    """
    ten = allup_formula(5, "5x10")
    assert ten["sizes"] == [2] and ten["label"] == "10 doubles"


def test_a_formula_that_does_not_exist_is_none_not_a_guess():
    assert allup_formula(4, "4x7") is None
    assert allup_formula(4, None) is None
    assert allup_formulas(1) == [] and allup_formulas(7) == []


def test_a_bare_number_names_the_same_formula_as_its_code():
    """A statement writes "2X1"; a sheet writes "3x4"; a person types "11"."""
    assert allup_formula(4, "11")["code"] == "4x11"
    assert allup_formula(2, "2X1")["code"] == "2x1"


# ─── the second multiplication ────────────────────────────────────────────────

def test_a_leg_is_a_whole_ticket_and_the_chain_multiplies_those():
    """Statement 2026-09-06 ref 3593, which is money that actually moved.

    All Up 2X1, Quinella-Quinella Place into Place. Leg one is a QQP banker
    with four others: four pairs into two pools, eight combinations. Leg two is
    a single place, one. 8 x 1 = 8 lines at $20 = $160 debited — which is what
    the statement says, and eight times what an unmultiplied count would quote.
    """
    leg_one = combination_count("QQP", 4, has_banker=True)
    leg_two = combination_count("PLACE", 1)
    assert (leg_one, leg_two) == (8, 1)

    f = allup_formula(2, "2x1")
    lines = allup_lines(f["sizes"], [leg_one, leg_two])
    assert lines == 8
    assert lines * 20 == 160


def test_the_bet_sheets_own_total_is_reproduced():
    """Bet 1 of the sheet: 3x4 over two QQP legs of two horses and a WP leg.

    Each leg is two combinations. Three doubles at 2 x 2 and one treble at
    2 x 2 x 2 is 12 + 8 = 20 lines, at $200 = $4,000 — the figure in the
    sheet's own Total stakes column.
    """
    per_leg = [combination_count("QQP", 2), combination_count("QQP", 2),
               combination_count("WP", 1)]
    assert per_leg == [2, 2, 2]
    f = allup_formula(3, "3x4")
    assert allup_lines(f["sizes"], per_leg) == 20
    assert allup_lines(f["sizes"], per_leg) * 200 == 4000


def test_with_one_combination_a_leg_the_lines_are_the_formulas_own_number():
    for legs in (2, 3, 4, 5, 6):
        for f in allup_formulas(legs):
            assert allup_lines(f["sizes"], [1] * legs) == f["combinations"]


def test_a_leg_with_nothing_in_it_makes_no_lines():
    """Not one, and not the rest of the chain: an incomplete ticket costs
    nothing until it is finished, and quoting it as if the empty leg were a
    single selection would price a bet that cannot be struck."""
    assert allup_lines([2], [4, 0]) == 0


# ─── one ticket, two pools ────────────────────────────────────────────────────

def test_wp_and_qqp_cost_twice_because_they_are_two_pools():
    """HKJC sells them as one ticket and debits two. Statement ref 3597 is a
    QQP banker with two others at $10: two pairs, two pools, $40."""
    assert pools_per_ticket("WP") == pools_per_ticket("QQP") == 2
    assert pools_per_ticket("WIN") == pools_per_ticket("QIN") == 1
    assert combination_count("QQP", 2, has_banker=True) == 4
    assert combination_count("QQP", 2, has_banker=True) * 10 == 40

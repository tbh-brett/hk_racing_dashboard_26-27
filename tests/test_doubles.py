"""ingest/doubles — one bet spanning two consecutive races.

The test this module exists for is `test_a_double_is_ordered_and_must_not_be
_sorted`. Everywhere else in this package a two-horse combination is stored
smallest-first so that one bet cannot have two disagreeing rows; a double is
the one place where that would be a bug, because first-leg 3 with second-leg 7
is a different bet at a different price from 7 then 3.

The grid fixture is the real one from leg 1 of 2026-09-06 — race 1 had 14
runners and race 2 had 11, so rows 12 to 14 render empty.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import doubles
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db

CAPTURED = "2026-09-06T01:30:00"

GRID = [
    ["1st Leg"] + [str(i) for i in range(1, 15)],
    ["2nd Leg"],
    ["1", "147", "24", "54", "8.3", "47", "52", "25", "114", "463", "208",
     "47", "202", "88", "256"],
    ["2", "165", "27", "50", "7.5", "39", "35", "54", "125", "475", "246",
     "49", "248", "58", "316"],
    ["11", "999", "221", "299", "93", "202", "390", "301", "513", "999",
     "559", "148", "999", "253", "999"],
    # Race 2 has 11 runners; the table is still drawn 14 rows deep.
    ["12"] + [""] * 14,
    ["13"] + [""] * 14,
    ["14"] + [""] * 14,
]


def parse(grid=GRID, leg_no=1):
    return doubles.parse_grid(grid, date="2026-09-06", leg_no=leg_no,
                              captured_at=CAPTURED, venue="ST")


def test_a_leg_couples_two_consecutive_races():
    assert doubles.leg_races(1) == (1, 2)
    assert doubles.leg_races(9) == (9, 10)
    p = parse()
    assert (p["race_first"], p["race_second"]) == (1, 2)


def test_the_grid_is_read_by_label_not_by_position():
    """A scratching leaves a gap in the printed numbers. Reading columns
    positionally would shift every price from the gap onwards by one horse."""
    gapped = [r[:] for r in GRID]
    gapped[0] = ["1st Leg", "1", "2", "4", "5"]          # no 3
    gapped[2] = ["1", "147", "24", "54", "8.3"]
    p = parse(gapped)
    got = {(x["first"], x["second"]): x["odds"] for x in p["pairs"]}
    assert got[(4, 1)] == pytest.approx(54)
    assert (3, 1) not in got


def test_a_double_is_ordered_and_must_not_be_sorted(tmp_path):
    """The whole reason this is not stored in odds_pairs.

    3-then-7 and 7-then-3 are different bets at different prices. Sorting them
    the way a quinella pair is sorted would leave whichever was written last
    standing for both, and nothing would error.
    """
    conn = get_conn(tmp_path / "d.db")
    init_db(conn)
    upsert.upsert_odds_doubles(conn, [
        {"race_date": "2026-09-06", "leg_no": 1, "horse_first": 3,
         "horse_second": 7, "captured_at": CAPTURED, "odds": 10.0},
        {"race_date": "2026-09-06", "leg_no": 1, "horse_first": 7,
         "horse_second": 3, "captured_at": CAPTURED, "odds": 20.0},
    ])
    got = conn.execute(
        "SELECT horse_first, horse_second, odds FROM odds_doubles "
        "ORDER BY horse_first").fetchall()
    assert [(r["horse_first"], r["horse_second"], r["odds"]) for r in got] == [
        (3, 7, 10.0), (7, 3, 20.0)]
    conn.close()


def test_padding_rows_do_not_invent_runners():
    """Race 2 had 11 declared. Rows 12-14 are blank padding, and storing them
    would put prices on horses that were never in the race."""
    p = parse()
    seconds = {x["second"] for x in p["pairs"]}
    assert seconds == {1, 2, 11}
    assert not seconds & {12, 13, 14}


def test_the_display_cap_is_reported_rather_than_taken_as_a_price():
    """The page cannot print more than three digits, so anything at or past
    999 shows as 999. Inverted into a probability it is simply wrong."""
    p = parse()
    assert p["capped"] == 4
    assert any("display cap" in n for n in p["notes"])


def test_a_grid_that_is_not_there_raises():
    with pytest.raises(doubles.DoublesError, match="no doubles grid"):
        parse([])


def test_a_grid_of_the_wrong_shape_raises():
    """The quinella matrix is on a neighbouring page and has the same look.
    Reading one as the other would store race-pair prices as doubles."""
    with pytest.raises(doubles.DoublesError, match="1st Leg"):
        parse([["Quinella", "1", "2"], ["1", "", "5.5"]])


def test_a_grid_with_a_header_but_no_prices_raises():
    """An empty grid is not a leg with no market -- it is a page that did not
    finish rendering, and returning [] makes the two indistinguishable."""
    with pytest.raises(doubles.DoublesError, match="no priced combinations"):
        parse([["1st Leg", "1", "2"], ["2nd Leg"], ["1", "", ""]])


def test_a_capture_without_a_timestamp_is_refused():
    with pytest.raises(doubles.DoublesError, match="captured_at"):
        doubles.parse_grid(GRID, date="2026-09-06", leg_no=1, captured_at="")


def test_rows_carry_the_leg_and_the_write_is_idempotent(tmp_path):
    conn = get_conn(tmp_path / "d.db")
    init_db(conn)
    rows = doubles.double_rows(parse())
    assert all(r["leg_no"] == 1 for r in rows)
    assert upsert.upsert_odds_doubles(conn, rows) == len(rows)
    upsert.upsert_odds_doubles(conn, rows)
    assert conn.execute(
        "SELECT count(*) FROM odds_doubles").fetchone()[0] == len(rows)
    conn.close()


def test_the_double_is_shorter_than_the_product_of_the_win_odds():
    """A sanity check on the reading itself, not on HKJC.

    Two win bets pay the product of their prices after takeout twice; a double
    is one pool and one takeout, so it must come in UNDER that product. If this
    ever inverts, the grid is being read with its axes swapped.

    R1 #4 was 2.7 and R2 #1 was 3.9 at this capture -- product 10.53, double
    8.3.
    """
    p = parse()
    got = {(x["first"], x["second"]): x["odds"] for x in p["pairs"]}
    assert got[(4, 1)] == pytest.approx(8.3)
    assert got[(4, 1)] < 2.7 * 3.9

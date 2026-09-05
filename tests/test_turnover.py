"""ingest/turnover — how much money is in each pool.

Turnover is the denominator that turns a price into an amount. The tests that
matter here are the ones about ABSENCE: a pool HKJC does not operate has no
line on the page, and reading that as a failure would bury the notes that mean
something.

The fixture is the real block from race 3 of 2026-09-06 — six declared
starters, so no quinella place pool existed and KA YING RISING was 1.0, which
is why the win pool is ten times every other race on the card.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import turnover
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db

CAPTURED = "2026-09-06T01:15:00"

# Race 3, exactly as the page rendered it.
R3 = """Total Turnover
Win\t$ 4,288,122
Place\t$ 792,623
Quinella\t$ 324,933
Forecast\t$ 63,517
Tierce\t$ 94,809
Trio\t$ 43,801
Quartet and First 4\t$ 301,124
\t
Double\t$ 84,916
Treble\t$ 75,728
Double Trio\t$ 45,415
Race Total (Single Race Pools)\t$ 5,908,929
Race Total (All Pools)\t$ 6,114,988
Login
Total Amount:
$0
"""

# Race 1, which does operate a quinella place pool.
R1 = """Total Turnover
Win\t$ 1,481,817
Place\t$ 1,154,959
Quinella\t$ 1,470,068
Quinella Place\t$ 1,815,352
Forecast\t$ 157,181
Tierce\t$ 186,014
Trio\t$ 187,298
Quartet and First 4\t$ 301,781
Double\t$ 250,231
Double Trio\t$ 199,332
Race Total (Single Race Pools)\t$ 6,754,470
Race Total (All Pools)\t$ 7,204,033
Login
"""


def parse(text, race_no=3):
    return turnover.parse_turnover(text, date="2026-09-06", race_no=race_no,
                                   captured_at=CAPTURED, venue="ST")


def test_every_pool_on_the_page_is_read():
    p = parse(R3)
    assert p["pools"]["WIN"] == pytest.approx(4_288_122)
    assert p["pools"]["QIN"] == pytest.approx(324_933)
    assert p["pools"]["DTRIO"] == pytest.approx(45_415)


def test_quinella_place_is_not_filed_under_quinella():
    """'QUINELLA PLACE' starts with 'QUINELLA'. dividends.py has to scan its
    labels longest-first for exactly this reason; here the whole label is on
    the line, so the two must land in different pools."""
    p = parse(R1, race_no=1)
    assert p["pools"]["QIN"] == pytest.approx(1_470_068)
    assert p["pools"]["QPL"] == pytest.approx(1_815_352)


def test_a_pool_that_is_not_operated_is_absent_rather_than_zero():
    """Race 3 had six starters, so HKJC ran no quinella place pool at all.

    Zero would claim a pool existed and nobody bet into it, which is a
    different and false statement."""
    p = parse(R3)
    assert "QPL" not in p["pools"]
    assert "QPL" not in p["operated"]
    assert "QIN" in p["operated"]


def test_the_published_total_reconciles_with_the_pools():
    """The page's own subtotal, against the sum of what was recognised. If it
    stops matching, HKJC has added a pool this parser cannot see."""
    for text, no in ((R3, 3), (R1, 1)):
        assert parse(text, no)["notes"] == []


def test_a_pool_this_parser_cannot_name_is_reported():
    """Silence here is how the totals quietly stop adding up."""
    text = R3.replace("Trio\t$ 43,801",
                      "Trio\t$ 43,801\nSix Up Bonanza\t$ 12,345")
    p = parse(text)
    assert any("SIX UP BONANZA" in n for n in p["notes"])


def test_a_missing_pool_shows_up_as_a_total_that_does_not_add_up():
    text = R3.replace("Quinella\t$ 324,933\n", "")
    p = parse(text)
    assert any("sum to" in n and "missing" in n for n in p["notes"])


def test_a_block_that_is_not_there_raises():
    """A parser that cannot find the shape it was written for does not return
    an empty dict that reads like a quiet race."""
    with pytest.raises(turnover.TurnoverError, match="not found"):
        parse("Some other page entirely\nLogin\n")


def test_a_block_with_no_win_pool_raises():
    text = R3.replace("Win\t$ 4,288,122\n", "")
    with pytest.raises(turnover.TurnoverError, match="no WIN pool"):
        parse(text)


def test_a_capture_without_a_timestamp_is_refused():
    """Same rule as the price snapshots: the value is knowing WHEN it was true."""
    with pytest.raises(turnover.TurnoverError, match="captured_at"):
        turnover.parse_turnover(R3, date="2026-09-06", race_no=3, captured_at="")
    with pytest.raises(turnover.TurnoverError, match="unparseable"):
        turnover.parse_turnover(R3, date="2026-09-06", race_no=3,
                                captured_at="just after one")


def test_page_furniture_below_the_block_is_not_read_as_a_pool():
    """'Total Amount: $0' sits under the table. Read as a pool it becomes a
    turnover of nothing under whatever label preceded it."""
    p = parse(R3)
    assert all(v > 0 for v in p["pools"].values())
    assert len(p["pools"]) == 12


def test_rows_are_written_and_the_write_is_idempotent(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    rows = turnover.turnover_rows(parse(R3))
    assert upsert.upsert_pool_turnover(conn, rows) == len(rows)
    upsert.upsert_pool_turnover(conn, rows)
    assert conn.execute(
        "SELECT count(*) FROM odds_pool_turnover").fetchone()[0] == len(rows)
    conn.close()


def test_a_later_capture_is_a_new_row_not_a_correction(tmp_path):
    """Turnover only rises. Two captures are two facts about the same pool, and
    the series is the whole point -- one figure is a size, two are a rate."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    upsert.upsert_pool_turnover(conn, turnover.turnover_rows(parse(R3)))
    later = turnover.parse_turnover(
        R3.replace("Win\t$ 4,288,122", "Win\t$ 5,000,000"),
        date="2026-09-06", race_no=3, captured_at="2026-09-06T02:15:00")
    upsert.upsert_pool_turnover(conn, turnover.turnover_rows(later))
    got = conn.execute(
        "SELECT captured_at, turnover FROM odds_pool_turnover "
        "WHERE pool = 'WIN' ORDER BY captured_at").fetchall()
    assert [r["turnover"] for r in got] == [4_288_122, 5_000_000]
    conn.close()


def test_negative_turnover_is_refused(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    with pytest.raises(ValueError, match="negative"):
        upsert.upsert_pool_turnover(conn, [{
            "race_date": "2026-09-06", "race_no": 1, "pool": "WIN",
            "captured_at": CAPTURED, "turnover": -5}])
    conn.close()

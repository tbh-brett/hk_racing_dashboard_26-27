"""A split Quinella/Quinella-Place credit, and whether each half paid.

A "Quinella - Quinella Place" block on a statement is one credit across two
pools. The import halves the money and cannot say which pool came in, so `hit`
was left unknown on both halves -- and the ledger summary counted unknown as a
miss: 150 of this season's 553 statement bets that returned money read as
losers (17 Nov 2026). The dividends say what the statement cannot.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import import_statement
from hkrd.query import bets as bq
from hkrd.store import upsert
from hkrd.store.bets import resolve_split_hits
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-04-22"
SEP = "*" * 40


def _stmt(*blocks: str) -> str:
    head = ("Account Records - Download version for reference only\n"
            "From 22/04/2026 To 22/04/2026\n"
            "Betting Account No.: 40067995\n"
            "Balance: $636.40\n")
    body = f"\n{SEP}\n".join(b.strip("\n") for b in blocks)
    return f"{head}{SEP}\n{body}\n{SEP}\n- End -\n"


# A box of 3, 4 and 8 in race 1, one credit of $129.50 across both pools.
BOX = """\
2209
22/04/2026 18:40
Happy Valley
Wednesday
Quinella - Quinella Place
Race 1
3 CONCORDE STAR +
4 NEBRASKAN +
8 TEAM HAPPY

$10


$60.00
$129.50"""

# Banker 6 with 4, 7, 8 and 9 in race 2, one credit of $80.
BANKER = """\
2217
22/04/2026 19:05
Happy Valley
Wednesday
Quinella - Quinella Place
Race 2
6 DOUBLE BINGO
Banker with
4 TELECOM POWER +
7 PODIUM +
8 VERBIER +
9 DOUBLE SHOW

$10


$80.00
$80.00"""


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "s.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650} for n in (1, 2)])
    conn.close()
    return path


def _dividends(db, race_no: int, pool: str, *combinations: str) -> None:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_dividends(conn, [
            {"race_date": DATE, "race_no": race_no, "pool": pool,
             "combination": c, "dividend_per_10": 50.0} for c in combinations])
    conn.close()


def _import(db, tmp_path, *blocks: str) -> import_statement.StatementImportReport:
    src = tmp_path / "s.txt"
    src.write_text(_stmt(*blocks), encoding="utf-8")
    return import_statement.run(src, db=db)


def _hits(db) -> dict[str, int | None]:
    conn = get_conn(db)
    try:
        return {r["bet_type"]: r["hit"] for r in conn.execute(
            "SELECT bet_type, hit FROM bets")}
    finally:
        conn.close()


def test_without_dividends_both_halves_stay_unknown(db, tmp_path):
    """Not a miss: nothing yet says which pool the credit came from."""
    report = _import(db, tmp_path, BOX)
    assert _hits(db) == {"QIN": None, "QPL": None}
    assert report.split_settled == 0


def test_the_dividends_settle_each_half_on_its_own_pool(db, tmp_path):
    """3-4 ran first and second: both pools paid on it."""
    _dividends(db, 1, "QIN", "3,4")
    _dividends(db, 1, "QPL", "3,4", "3,9", "4,9")
    report = _import(db, tmp_path, BOX)
    assert _hits(db) == {"QIN": 1, "QPL": 1}
    assert report.split_settled == 2
    assert "Q+QP halves hit" in report.render()


def test_a_place_pair_without_the_quinella_is_one_hit_and_one_miss(db, tmp_path):
    """9 split the box's 3 and 4: the quinella missed and the place pair paid.
    The money is not re-split -- the apportionment is still the only figure."""
    _dividends(db, 1, "QIN", "3,9")
    _dividends(db, 1, "QPL", "3,9", "3,4", "4,9")
    _import(db, tmp_path, BOX)
    assert _hits(db) == {"QIN": 0, "QPL": 1}
    conn = get_conn(db)
    returned = [r[0] for r in conn.execute("SELECT returned FROM bets")]
    conn.close()
    assert sum(returned) == pytest.approx(129.50)


def test_a_banker_ticket_covers_only_pairs_with_its_banker(db, tmp_path):
    """4 and 7 are both on the ticket, but only as partners of 6."""
    _dividends(db, 2, "QIN", "4,7")
    _dividends(db, 2, "QPL", "4,7", "6,7", "4,6")
    _import(db, tmp_path, BANKER)
    assert _hits(db) == {"QIN_BANKER": 0, "QPL_BANKER": 1}


def test_a_combination_that_does_not_read_as_a_pair_settles_nothing(db, tmp_path):
    """A dead-heat or a format change must not turn a winner into a miss."""
    _dividends(db, 1, "QIN", "3,4>9")
    _dividends(db, 1, "QPL", "3,4", "3,9", "4,9")
    _import(db, tmp_path, BOX)
    assert _hits(db) == {"QIN": None, "QPL": 1}


def test_halves_written_before_the_dividends_settle_on_a_later_run(db, tmp_path):
    """Every unknown half is tried, so the ledger's earlier ones settle too."""
    _import(db, tmp_path, BOX)
    _dividends(db, 1, "QIN", "3,4")
    _dividends(db, 1, "QPL", "3,4", "3,9", "4,9")
    conn = get_conn(db)
    with transaction(conn):
        assert resolve_split_hits(conn) == 2
        assert resolve_split_hits(conn) == 0       # nothing left to settle
    conn.close()
    assert _hits(db) == {"QIN": 1, "QPL": 1}


def test_the_summary_strike_rate_leaves_unknown_out(db, tmp_path):
    """Two halves unknown and nothing else: no strike rate, not 0%."""
    _import(db, tmp_path, BOX)
    conn = get_conn(db)
    try:
        out = bq.summary(conn=conn)
    finally:
        conn.close()
    assert out["bets"] == 2 and out["hits"] == 0
    assert out["resolved"] == 0 and out["unresolved"] == 2
    assert out["strike_rate"] is None
    assert {r["bet_type"]: r["unresolved"] for r in out["by_type"]} == {
        "QIN": 1, "QPL": 1}

"""One book, two ledgers.

The blackbook is shared — a horse is followed for what it did, not for whose
money is on it. The bets are not, so "was this run backed" has a different
honest answer depending on which account is asking, and the difference between
those answers is a finding about each book's own discipline.

The invariant these tests protect: backed + missed equals runs since booking,
IN EVERY VIEW. A run Kelvin took and Brett did not is missed-for-Brett and
backed-overall — both true, answering different questions, and summing them
would break exactly that.
"""
from __future__ import annotations

import pytest

from hkrd.query import bets as bq
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATES = ("2026-05-01", "2026-05-08", "2026-05-15")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "bb.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650} for d in DATES])
        for i, d in enumerate(DATES):
            upsert.upsert_runners(conn, [
                {"race_date": d, "race_no": 1, "horse_no": 1,
                 "horse_name": "SHARED HORSE", "place": str(1 + i),
                 "win_odds": 6.0, "draw": 1}]
                + [{"race_date": d, "race_no": 1, "horse_no": n,
                    "horse_name": f"OTHER {n}", "place": str(n + 3),
                    "win_odds": 10.0, "draw": n} for n in range(2, 9)])
        conn.execute(
            "INSERT INTO blackbook (id, horse_name, added_date, status) "
            "VALUES ('bb_1', 'SHARED HORSE', '2026-04-01', 'active')")

        # Run 1 backed by Brett only. Run 2 by Kelvin only. Run 3 by neither.
        for bet_id, account, date in (("b1", "brett", DATES[0]),
                                      ("b2", "kelvin", DATES[1])):
            conn.execute(
                "INSERT INTO bets (bet_id, account, race_date, race_no, "
                "bet_type, stake, status, source) VALUES (?, ?, ?, 1, 'WIN', "
                "100, 'settled', 'manual')", (bet_id, account, date))
            conn.execute(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no,"
                " is_banker) VALUES (?, 1, 1, 0, 0)", (bet_id,))
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


def _bm(path, account=None):
    conn = get_conn(path)
    try:
        return bq.backed_and_missed(account=account, conn=conn)
    finally:
        conn.close()


def test_combined_counts_a_run_backed_by_either(db):
    """The book's own hit rate, independent of which wallet paid."""
    out = _bm(db)
    assert out["backed"]["runs"] == 2
    assert out["missed"]["runs"] == 1


def test_brett_counts_a_kelvin_only_run_as_missed(db):
    """The reading that measures Brett's own selection discipline."""
    out = _bm(db, "brett")
    assert out["backed"]["runs"] == 1
    assert out["missed"]["runs"] == 2


def test_kelvin_is_the_mirror(db):
    out = _bm(db, "kelvin")
    assert out["backed"]["runs"] == 1
    assert out["missed"]["runs"] == 2


@pytest.mark.parametrize("account", [None, "brett", "kelvin"])
def test_backed_plus_missed_equals_runs_in_every_view(db, account):
    """The invariant. If this breaks, the page is double-counting a run one
    book took and the other did not."""
    out = _bm(db, account)
    assert out["backed"]["runs"] + out["missed"]["runs"] == out["runs"] == 3


def test_the_split_counts_each_account_without_summing(db):
    """A run both books took is ONE run and appears in both counts."""
    out = _bm(db)
    assert out["by_account"] == {"brett": 1, "kelvin": 1}


def test_the_account_asked_about_travels_with_the_answer(db):
    assert _bm(db, "brett")["account"] == "brett"
    assert _bm(db)["account"] is None


def test_all_three_views_arrive_together(db):
    conn = get_conn(db)
    try:
        out = bq.backed_by_account(conn=conn)
    finally:
        conn.close()
    assert set(out) == {"combined", "brett", "kelvin"}
    assert out["combined"]["backed"]["runs"] == 2
    assert out["brett"]["backed"]["runs"] == 1

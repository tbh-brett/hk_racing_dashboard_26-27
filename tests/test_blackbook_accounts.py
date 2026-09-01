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


# ── the same comparison, one booking reason at a time ────────────────────────

def _tag(path, tag_id="bb_1", tag="trip"):
    conn = get_conn(path)
    try:
        with transaction(conn):
            conn.execute("INSERT OR REPLACE INTO blackbook_tags (id, tag) "
                         "VALUES (?, ?)", (tag_id, tag))
    finally:
        conn.close()


def _by_tag(path, account=None):
    conn = get_conn(path)
    try:
        return bq.backed_and_missed_by_tag(account=account, conn=conn)
    finally:
        conn.close()


def test_a_tag_reading_uses_the_same_arithmetic_as_the_whole_book(db):
    """Every tag goes through `backed_and_missed` itself.

    A per-tag figure computed its own way would disagree with the whole-book
    one and neither would be wrong enough for anyone to notice which.
    """
    _tag(db)
    conn = get_conn(db)
    try:
        direct = bq.backed_and_missed(tag="trip", conn=conn)
    finally:
        conn.close()
    got = _by_tag(db)["trip"]
    assert got["backed_roi"] == direct["backed"]["roi"]
    assert got["runs"] == direct["runs"]


def test_backed_plus_missed_holds_per_tag_too(db):
    """The invariant the whole page rests on, one reason at a time."""
    _tag(db)
    for account in (None, "brett", "kelvin"):
        d = _by_tag(db, account)["trip"]
        assert d["backed_runs"] + d["missed_runs"] == d["runs"], account


def test_a_tag_on_no_entries_is_absent_rather_than_zero(db):
    """A tag nobody has used is not a tag that failed."""
    assert "trip" not in _by_tag(db)


def test_the_tag_filter_actually_narrows(db):
    """A filter that silently matched everything would make every tag show the
    whole book's numbers, and the column would look plausible and mean
    nothing."""
    conn = get_conn(db)
    try:
        with transaction(conn):
            conn.execute(
                "INSERT OR REPLACE INTO blackbook (id, horse_name, added_date, "
                "status) VALUES ('bb_2', 'OTHER 2', '2026-04-01', 'active')")
            conn.execute("INSERT OR REPLACE INTO blackbook_tags (id, tag) "
                         "VALUES ('bb_1', 'trip')")
        whole = bq.backed_and_missed(conn=conn)["runs"]
        tagged = bq.backed_and_missed(tag="trip", conn=conn)["runs"]
    finally:
        conn.close()
    assert 0 < tagged < whole


# ── an account no page can show ──────────────────────────────────────────────

def test_a_bet_under_an_unknown_account_is_moved_not_left(db):
    """The legacy ledger filed 1,078 bets under "personal", which is neither
    of the two books the interface knows. Left there they are invisible: every
    account view asks for brett or kelvin, so the whole history reads as "no
    bets" and the Blackbook then calls every one of those runs a missed chance
    — a wrong answer that looks like a confident one.
    """
    from hkrd.store import bets as bets_store
    conn = get_conn(db)
    try:
        with transaction(conn):
            conn.execute("UPDATE bets SET account = 'personal'")
            moved = bets_store.normalise_accounts(conn)
        rows = dict(conn.execute(
            "SELECT account, count(*) FROM bets GROUP BY account").fetchall())
    finally:
        conn.close()
    assert moved == 2
    assert rows == {"brett": 2}


def test_normalising_twice_moves_nothing_the_second_time(db):
    """It runs on every bootstrap, and a bootstrap is meant to converge."""
    from hkrd.store import bets as bets_store
    conn = get_conn(db)
    try:
        with transaction(conn):
            conn.execute("UPDATE bets SET account = 'personal'")
            bets_store.normalise_accounts(conn)
        with transaction(conn):
            assert bets_store.normalise_accounts(conn) == 0
    finally:
        conn.close()


def test_a_known_account_is_never_reassigned(db):
    """Kelvin's bets must not be swept into Brett's book by a tidy-up."""
    from hkrd.store import bets as bets_store
    conn = get_conn(db)
    try:
        with transaction(conn):
            conn.execute("UPDATE bets SET account = 'kelvin' WHERE bet_id = 'b2'")
            bets_store.normalise_accounts(conn)
        rows = dict(conn.execute(
            "SELECT account, count(*) FROM bets GROUP BY account").fetchall())
    finally:
        conn.close()
    assert rows == {"brett": 1, "kelvin": 1}


def test_the_default_account_is_one_the_interface_knows():
    """The bug in one line: an import default that is not in ACCOUNTS files
    every bet where no page will look for it."""
    from hkrd.jobs import import_bets, import_statement
    from hkrd.query.prebet import ACCOUNTS
    known = {a["key"] for a in ACCOUNTS}
    assert import_bets.DEFAULT_ACCOUNT in known
    assert import_statement.DEFAULT_ACCOUNT in known

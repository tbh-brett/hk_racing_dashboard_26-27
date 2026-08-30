"""store/ — coercion and idempotent writes.

The formats exercised here were enumerated from all 21,423 legacy rows, so these
are the real shapes the scrapers produce, not invented ones.
"""
from __future__ import annotations

import pytest

from hkrd.store import coerce
from hkrd.store.connect import get_conn, init_db, transaction
from hkrd.store import upsert


@pytest.fixture()
def conn(tmp_path):
    c = get_conn(tmp_path / "t.db")
    init_db(c)
    yield c
    c.close()


# ── lbw: the parser that exists because pd.to_numeric drops 79.1% ─────────────

@pytest.mark.parametrize("token,expected", [
    ("3-1/4", 3.25), ("2-1/2", 2.5), ("1-3/4", 1.75),   # 13,126 legacy rows
    ("1/2", 0.5), ("3/4", 0.75), ("1/4", 0.25),         #    990 legacy rows
    ("NOSE", 0.05), ("SH", 0.10), ("HD", 0.20), ("N", 0.30),
    ("2", 2.0), ("0.5", 0.5), ("10", 10.0),
])
def test_parse_lbw_recovers_every_real_form(token, expected):
    assert coerce.parse_lbw(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["-", "---", "", None, "ML"])
def test_parse_lbw_returns_none_for_winners_and_unmeasured(token):
    assert coerce.parse_lbw(token) is None


def test_parse_lbw_beats_to_numeric_on_the_forms_that_matter():
    """The regression guard. pd.to_numeric turns every one of these into NaN.

    Measured across the full legacy table: to_numeric recovers 20.9% of values,
    this parser recovers 90.5% -- 14,894 margins that were being discarded.
    """
    pd = pytest.importorskip("pandas")
    fractions = ["3-1/4", "1/2", "2-3/4", "1-1/2", "HD", "N"]
    assert pd.to_numeric(pd.Series(fractions), errors="coerce").notna().sum() == 0
    assert all(coerce.parse_lbw(f) is not None for f in fractions)


def test_parse_lbw_raises_on_the_genuinely_unknown():
    with pytest.raises(coerce.CoerceError, match="unrecognised margin"):
        coerce.parse_lbw("banana")


# ── place: not an integer column in practice ─────────────────────────────────

def test_to_place_keeps_dead_heats_as_placings():
    """84 dead heats in the legacy data. They ARE placings; to_numeric drops them."""
    assert coerce.to_place("8 DH") == (8, "8 DH", True)
    assert coerce.to_place("3 DH") == (3, "3 DH", True)


@pytest.mark.parametrize("code", ["WV", "WV-A", "UR", "PU", "WX", "WXNR", "TNP", "DISQ"])
def test_to_place_preserves_why_a_horse_did_not_finish(code):
    place, raw, dh = coerce.to_place(code)
    assert place is None and raw == code and dh is False


def test_to_place_plain_integer():
    assert coerce.to_place("4") == (4, None, False)


# ── times, odds, dates ───────────────────────────────────────────────────────

@pytest.mark.parametrize("token,expected", [
    ("1:49.23", 109.23), ("1.11.38", 71.38), ("109.23", 109.23), ("55.33", 55.33),
])
def test_parse_finish_time_accepts_every_shape(token, expected):
    assert coerce.parse_finish_time(token) == pytest.approx(expected)


def test_to_odds_treats_the_dash_sentinel_as_no_price():
    assert coerce.to_odds("---") is None
    assert coerce.to_odds("3.7") == pytest.approx(3.7)


def test_to_odds_rejects_impossible_prices():
    with pytest.raises(coerce.CoerceError):
        coerce.to_odds("0")


@pytest.mark.parametrize("token", ["2026-07-15", "15/07/2026", "20260715"])
def test_to_date_normalises_to_one_format(token):
    assert coerce.to_date(token) == "2026-07-15"


def test_parse_section_times_ignores_hkjc_padding():
    assert coerce.parse_section_times("24.97; 22.82; 23.62; ; ") == (24.97, 22.82, 23.62)


def test_parse_running_positions_accepts_both_legacy_shapes():
    """The same fact was stored twice in two shapes; one silently died in May."""
    assert coerce.parse_running_positions("10 9 6 3 2") == (10, 9, 6, 3, 2)
    assert coerce.parse_running_positions("4; 4; 4; 1") == (4, 4, 4, 1)


# ── idempotency: the property every scrape depends on ────────────────────────

RACE = [{"race_date": "2026-07-15", "race_no": 3, "venue": "HV", "course": "C",
         "surface": "Turf", "going": "G", "distance": 1800, "race_class": "4"}]

RUNNERS = [
    {"race_date": "2026-07-15", "race_no": 3, "horse_no": 8,
     "horse_name": "FIREFOOT", "place": "2", "finish_time": "1:48.39",
     "lengths_behind": "3-1/4", "draw": 8, "win_odds": "7.5",
     "running_positions": "10 9 6 3 2"},
    {"race_date": "2026-07-15", "race_no": 3, "horse_no": 10,
     "horse_name": "KYRUS TREASURE", "place": "1", "finish_time": "1:48.00",
     "lengths_behind": "-", "draw": 6, "win_odds": "3.0",
     "running_positions": "1 1 1 1 1"},
]


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_upserting_the_same_batch_twice_changes_nothing(conn):
    """Re-running a scrape must converge, never duplicate."""
    with transaction(conn):
        upsert.upsert_races(conn, RACE)
        upsert.upsert_runners(conn, RUNNERS)
    first = (_count(conn, "races"), _count(conn, "runners"))

    with transaction(conn):
        upsert.upsert_races(conn, RACE)
        upsert.upsert_runners(conn, RUNNERS)
    assert (_count(conn, "races"), _count(conn, "runners")) == first == (1, 2)


def test_upsert_refreshes_values_rather_than_duplicating(conn):
    """A post-race scrape fills in place and time on rows the card created."""
    with transaction(conn):
        upsert.upsert_races(conn, RACE)
        upsert.upsert_runners(conn, [{**RUNNERS[0], "place": None, "finish_time": None}])
    assert conn.execute("SELECT place FROM runners").fetchone()[0] is None

    with transaction(conn):
        upsert.upsert_runners(conn, RUNNERS)
    row = conn.execute("SELECT place, finish_time FROM runners WHERE horse_no=8").fetchone()
    assert row["place"] == 2 and row["finish_time"] == pytest.approx(108.39)
    # Horse 8 was updated in place, not duplicated; horse 10 is genuinely new.
    assert _count(conn, "runners") == 2
    assert conn.execute(
        "SELECT count(*) FROM runners WHERE horse_no = 8").fetchone()[0] == 1


def test_values_are_coerced_on_the_way_in(conn):
    with transaction(conn):
        upsert.upsert_races(conn, RACE)
        upsert.upsert_runners(conn, RUNNERS)
    row = conn.execute("SELECT * FROM runners WHERE horse_no=8").fetchone()
    assert row["lengths_behind"] == pytest.approx(3.25)   # not NaN
    assert row["finish_time"] == pytest.approx(108.39)    # '1:48.39' -> seconds
    assert row["win_odds"] == pytest.approx(7.5)
    assert isinstance(row["place"], int)


def test_odds_snapshots_accumulate_and_are_never_replaced(conn):
    """Movement is the point. A second capture is a new row, not an overwrite."""
    with transaction(conn):
        upsert.upsert_races(conn, RACE)
        for ts, odds in (("2026-07-15T09:00:00", "6.7"), ("2026-07-15T16:20:00", "15.0")):
            upsert.upsert_odds_snapshots(conn, [{
                "race_date": "2026-07-15", "race_no": 3, "horse_no": 9,
                "captured_at": ts, "win_odds": odds, "place_odds": "2.1"}])
    rows = conn.execute(
        "SELECT win_odds FROM odds_snapshots ORDER BY captured_at").fetchall()
    assert [r["win_odds"] for r in rows] == [6.7, 15.0]


def test_wal_is_enabled(conn):
    """The scraper writes while the API reads, on race day."""
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_foreign_keys_are_enforced(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_rolls_back_on_failure(conn):
    with transaction(conn):
        upsert.upsert_races(conn, RACE)
    with pytest.raises(coerce.CoerceError):
        with transaction(conn):
            upsert.upsert_runners(conn, RUNNERS)
            upsert.upsert_runners(conn, [{**RUNNERS[0], "horse_no": 11,
                                          "lengths_behind": "banana"}])
    assert _count(conn, "runners") == 0

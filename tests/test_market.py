"""query/market — concentration, movement, coverage, and staleness.

The market's win odds rank horses better (AUC 0.785) than every model here, the
best of which reaches 0.727. So odds are an input and nothing in this module
tries to beat them; it reports what the market said and how much to trust the
timing of it.
"""
from __future__ import annotations

import pytest

from hkrd.query import market
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "mk.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-07-15", "race_no": 1, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650},
            {"race_date": "2026-07-15", "race_no": 2, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1200},
            # A meeting with results and no odds at all -- the real failure.
            {"race_date": "2026-05-06", "race_no": 1, "venue": "ST",
             "course": "A", "surface": "Turf", "going": "G", "distance": 1400},
        ])
        # Morning: spread money. Post time: concentrated on the top three.
        for ts, prices in (
            ("2026-07-15T06:00:00", [6.0, 7.0, 8.0, 9.0, 10.0, 12.0]),
            ("2026-07-15T12:30:00", [2.5, 3.5, 5.0, 14.0, 20.0, 30.0]),
        ):
            upsert.upsert_odds_snapshots(conn, [
                {"race_date": "2026-07-15", "race_no": 1, "horse_no": i + 1,
                 "captured_at": ts, "win_odds": p, "place_odds": p / 3}
                for i, p in enumerate(prices)])
    conn.close()
    return path


def test_concentration_uses_the_latest_capture(db):
    conn = get_conn(db)
    c = market.concentration("2026-07-15", 1, conn=conn)
    conn.close()
    assert c["captured_at"] == "2026-07-15T12:30:00"
    assert c["value"] > 0.6


def test_the_morning_price_reads_a_weaker_race_than_the_truth(db):
    """60% of races land in a different band read early, always downward. That
    causes under-covering of exactly the races a top-3 box performs best in."""
    conn = get_conn(db)
    early = market.concentration("2026-07-15", 1, at="2026-07-15T06:00:00", conn=conn)
    late = market.concentration("2026-07-15", 1, conn=conn)
    conn.close()
    assert early["value"] < late["value"]
    assert early["band"] != late["band"]


def test_a_stale_price_says_so_rather_than_looking_post_time(db):
    """Every surviving snapshot in the real archive is hours before racing.
    A figure computed from one must declare that, not present as post-time."""
    conn = get_conn(db)
    stale = market.concentration("2026-07-15", 1, at="2026-07-15T06:00:00", conn=conn)
    fresh = market.concentration("2026-07-15", 1, conn=conn)
    conn.close()
    assert stale["stale"] is True and "before racing" in stale["note"]
    assert fresh["stale"] is False and "note" not in fresh


def test_bands_are_ordered():
    assert market.band(0.75) == "strong"
    assert market.band(0.60) == "moderate"
    assert market.band(0.40) == "weak"
    assert market.band(None) is None


def test_too_few_priced_runners_returns_none_not_a_wrong_number(db):
    conn = get_conn(db)
    c = market.concentration("2026-07-15", 2, conn=conn)
    conn.close()
    assert c["value"] is None and "fewer than three" in c["note"]


def test_price_movement_reports_direction(db):
    conn = get_conn(db)
    moves = market.price_movement("2026-07-15", 1, conn=conn)
    conn.close()
    by_horse = {m["horse_no"]: m for m in moves}
    assert by_horse[1]["direction"] == "shortened"   # 6.0 -> 2.5
    assert by_horse[6]["direction"] == "drifted"     # 12.0 -> 30.0


def test_movement_needs_more_than_one_capture(db):
    conn = get_conn(db)
    assert market.price_movement("2026-07-15", 2, conn=conn) == []
    conn.close()


# ── coverage: the failure that actually cost the history ─────────────────────

def test_coverage_names_meetings_with_no_odds(db):
    """Snapshot rotation was blamed for the thin odds history, but no race ever
    reached the rotation threshold -- capture simply did not run. Eight real
    meetings have full results and no odds at all. A miss nobody sees is the
    failure mode, so coverage is reported."""
    conn = get_conn(db)
    cov = market.odds_coverage(conn=conn)
    conn.close()
    assert cov["meetings"] == 2                    # two distinct race dates
    assert cov["meetings_with_any_odds"] == 1
    assert "2026-05-06" in cov["missing"]


def test_coverage_distinguishes_partial_from_complete(db):
    """A meeting where only some races were captured is not covered."""
    conn = get_conn(db)
    cov = market.odds_coverage(conn=conn)
    conn.close()
    july = next(r for r in cov["detail"] if r["race_date"] == "2026-07-15")
    assert july["races"] == 2 and july["races_with_odds"] == 1
    assert july["complete"] is False


def test_snapshot_age_is_none_when_unparseable():
    assert market.snapshot_age_hours("2026-07-15", None) is None
    assert market.snapshot_age_hours("2026-07-15", "not a time") is None

"""What moved since the viewer last looked.

Brief 01 lists this among the four questions the page exists to answer twenty
minutes before a race, and notes the old dashboard did not show it at all. The
favourite changes between morning and post time in 44% of races, which is why
a swap names its race rather than being folded into a count.
"""
from __future__ import annotations

import pytest

from hkrd.query import market as mq
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-07-15"
EARLY = f"{DATE}T09:00:00"
LATE = f"{DATE}T16:20:00"


def _snap(conn, at, prices):
    for no, odds in prices.items():
        conn.execute(
            "INSERT OR REPLACE INTO odds_snapshots (race_date, race_no, "
            "horse_no, captured_at, win_odds) VALUES (?, 1, ?, ?, ?)",
            (DATE, no, at, odds))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "c.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        #        1 firms      2 drifts     3 flat    4 comes out
        _snap(conn, EARLY, {1: 6.7, 2: 3.7, 3: 10.0, 4: 12.0})
        _snap(conn, LATE, {1: 3.0, 2: 15.0, 3: 10.1})
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


def test_a_firming_and_a_drifting_runner_are_counted_apart(db):
    out = mq.changes_since(DATE, EARLY)
    assert out["firmers"] == 1
    assert out["drifts"] == 1


def test_a_price_that_barely_moved_is_neither(db):
    """2% is the same threshold price_movement uses to call a direction, so a
    runner cannot be drifting in the strip and flat in its own row."""
    out = mq.changes_since(DATE, EARLY)
    assert out["drifts"] + out["firmers"] == 2      # not 3


def test_the_favourite_swap_names_its_race(db):
    """44% of races. Folding it into a count would bury the headline."""
    out = mq.changes_since(DATE, EARLY)
    assert out["fav_swaps"] == [{"race_no": 1, "from": 2, "to": 1}]


def test_a_runner_priced_then_unpriced_is_a_scratching(db):
    out = mq.changes_since(DATE, EARLY)
    assert out["scratched"] == [{"race_no": 1, "horse_no": 4}]


def test_no_earlier_visit_invents_no_baseline(db):
    """A change count measured from an arbitrary moment looks informative and
    is not, so it says there is nothing to compare against."""
    out = mq.changes_since(DATE, None)
    assert out["observed"] is False
    assert out["drifts"] == 0 and out["note"]


def test_comparing_against_now_shows_nothing_moved(db):
    out = mq.changes_since(DATE, LATE)
    assert out["drifts"] == 0 and out["firmers"] == 0
    assert out["fav_swaps"] == []

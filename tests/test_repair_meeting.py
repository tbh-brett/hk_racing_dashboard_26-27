"""jobs/repair_meeting — undoing a meeting stored from the wrong page.

Asked for the results of a meeting that had not been run, HKJC served a page
rather than a 404, and the date we asked for was stamped onto whatever came
back. The scrape is fixed; this removes what it already wrote.
"""
from __future__ import annotations

import sqlite3

import pytest

from hkrd.jobs.repair_meeting import clear_results, repair
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

RACE = {"race_date": "2026-09-06", "race_no": 2, "venue": "ST", "course": "A",
        "surface": "Turf", "going": "G", "distance": 1000, "race_class": "4"}
KEEP = {"race_date": "2026-07-15", "race_no": 1, "venue": "HV", "course": "C",
        "surface": "Turf", "going": "G", "distance": 1650, "race_class": "5"}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [RACE, KEEP])
        upsert.upsert_runners(conn, [{
            "race_date": "2026-09-06", "race_no": 2, "horse_no": 1,
            "horse_name": "LIGHTNESS OF MUSIC", "draw": 2, "jockey": "Z Purton",
            "trainer": "C S Shum", "actual_weight": 135, "gear": "B",
            "place": "3", "finish_time": "0:57.20", "win_odds": 4.5}])
        upsert.upsert_runners(conn, [{
            "race_date": "2026-07-15", "race_no": 1, "horse_no": 1,
            "horse_name": "EMERGING STAR", "place": "10", "win_odds": 70.0}])
    conn.close()
    return path


def _row(path, date):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runners WHERE race_date = ?",
                       (date,)).fetchone()
    conn.close()
    return dict(row) if row else None


def test_clearing_results_keeps_the_declared_card(db):
    """The card is right and worth keeping; only the outcome has to go."""
    clear_results("2026-09-06", db=db)
    row = _row(db, "2026-09-06")
    assert row["horse_name"] == "LIGHTNESS OF MUSIC"
    assert row["draw"] == 2 and row["jockey"] == "Z Purton" and row["gear"] == "B"
    assert row["place"] is None
    assert row["finish_time"] is None
    assert row["win_odds"] is None


def test_clearing_results_leaves_every_other_meeting_alone(db):
    clear_results("2026-09-06", db=db)
    other = _row(db, "2026-07-15")
    assert other["place"] == 10 and other["win_odds"] == 70.0


def test_repair_removes_the_whole_meeting(db):
    report = repair("2026-09-06", db=db)
    assert _row(db, "2026-09-06") is None
    assert report.deleted["runners"] == 1 and report.deleted["races"] == 1


def test_repair_leaves_every_other_meeting_alone(db):
    repair("2026-09-06", db=db)
    assert _row(db, "2026-07-15")["horse_name"] == "EMERGING STAR"


def test_repairing_a_date_that_holds_nothing_says_so(db):
    report = repair("2030-01-01", db=db)
    assert not any(report.deleted.values())
    assert "nothing to remove" in report.render()

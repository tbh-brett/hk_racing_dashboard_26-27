"""Data that was scraped, stored, and never shown.

Three defects of one kind, found by putting the design's artboards beside the
built pages: the value was captured on every run since the first meeting, it
sat in the table, the query dropped it, and no test could see the difference
because every layer worked.

  · `trials.draw` / `trials.jockey` — the Trials page could not show a draw or
    a rider even though both were on every row it read.
  · `dividends` PLACE — what a $10 place ticket on a run actually paid. A
    placed run at 4.5 and a placed run at 60 are not the same result, and FIN
    alone hides that.

These tests assert the values REACH the caller, which is the step that was
missing. Whether they render is the conformance test's job.
"""
from __future__ import annotations

import pytest

from hkrd.query import race as race_q
from hkrd.query import trials as trials_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-06-03"


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "s.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1200,
             "race_class": "4"}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": n,
             "horse_name": f"HORSE {n}", "draw": n, "jockey": "Z PURTON",
             "trainer": "J SIZE", "place": str(n), "win_odds": 4.0 + n,
             "actual_weight": 126}
            for n in range(1, 7)])
        # Per $10, as HKJC publishes them. Runner 2 placed and 6 did not.
        upsert.upsert_dividends(conn, [
            {"race_date": DATE, "race_no": 1, "pool": "WIN",
             "combination": "1", "dividend_per_10": 51.0},
            {"race_date": DATE, "race_no": 1, "pool": "PLACE",
             "combination": "1", "dividend_per_10": 18.5},
            {"race_date": DATE, "race_no": 1, "pool": "PLACE",
             "combination": "2", "dividend_per_10": 24.0},
        ])
        conn.execute(
            "INSERT INTO trials (trial_date, trial_no, horse_name, place, "
            "finish_time, venue, surface, going, jockey, trainer, draw, gear, "
            "comment_text, section_times, running_positions) VALUES "
            "('2026-05-20', 1, 'HORSE 1', 1, 70.2, 'ST', 'Turf', 'GF', "
            "'Z PURTON', 'J SIZE', 7, 'B', 'held up, ran on', "
            "'24.1; 23.4; 22.7', '5 3 1')")
    yield conn
    conn.close()


def test_a_trial_carries_its_draw_and_its_rider(db) -> None:
    """Both were stored on every trial row and dropped by the query."""
    batch = trials_q.batch("2026-05-20", 1, conn=db)
    runner = batch["runners"][0]
    assert runner["draw"] == 7
    assert runner["jockey"] == "Z PURTON"
    assert runner["trainer"] == "J SIZE"


def test_a_trial_carries_the_going_it_was_run_on(db) -> None:
    """A trial time means nothing without the surface it was set on."""
    assert trials_q.batch("2026-05-20", 1, conn=db)["going"] == "GF"


def test_a_placed_run_carries_what_the_place_ticket_paid(db) -> None:
    runners = {r.horse_no: r for r in race_q.get_race(DATE, 1, conn=db).runners}
    assert runners[1].place_dividend == 18.5
    assert runners[2].place_dividend == 24.0


def test_an_unplaced_run_has_no_dividend_rather_than_a_zero(db) -> None:
    """A dividend of 0 would read as a ticket that paid nothing.

    There was no ticket. None and 0.0 are different facts and the page renders
    them differently — blank against `$0.0`.
    """
    runners = {r.horse_no: r for r in race_q.get_race(DATE, 1, conn=db).runners}
    assert runners[6].place_dividend is None


def test_the_win_pool_is_never_read_as_a_place_dividend(db) -> None:
    """Both pools carry combination '1'. Reading the wrong one would put a
    51.0 win dividend in a column labelled PLACE, and nothing would look
    wrong — the number is plausible."""
    runners = {r.horse_no: r for r in race_q.get_race(DATE, 1, conn=db).runners}
    assert runners[1].place_dividend == 18.5


def test_history_carries_the_dividend_too(db) -> None:
    """The Form Guide reads history, not the card, so the field has to travel
    on that path as well — the two used to be separate SELECTs."""
    form = race_q.get_horse_form("HORSE 1", limit=3, conn=db)
    assert form and form[0].place_dividend == 18.5

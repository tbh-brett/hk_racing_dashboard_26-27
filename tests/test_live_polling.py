"""The two endpoints a live page polls, and what makes polling affordable.

Odds are captured every minute through the last ten before a race. A page that
reads them once at load shows, twenty minutes later, the price from twenty
minutes ago — in exactly the window brief 01 says Race Day exists for. So both
pages poll, and these protect the three things that stop that costing anything:

  * a poll that finds nothing new never builds the answer. The card is 36 KB
    and ~220ms to assemble; the poll that discovers it has not changed answers
    from two indexed lookups and sends no body.
  * the interval comes from the SAME ladder the capture runs on, so the page
    cannot drift into asking hourly questions every thirty seconds, or minute
    questions once an hour.
  * a changed price changes the tag. An endpoint that answered 304 after new
    odds landed would freeze the page on a stale price with nothing to say so,
    which is worse than not polling at all.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from hkrd.api import live
from hkrd.api.app import app
from hkrd.query import market as market_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-06"
client = TestClient(app)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "live.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200,
             "race_class": "5", "off_time": "12:30"}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": i + 1,
             "horse_name": f"HORSE {i}", "draw": i + 1, "actual_weight": 120}
            for i in range(4)])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": i + 1,
             "captured_at": "2026-09-06T12:00:00", "win_odds": o,
             "place_odds": p}
            for i, (o, p) in enumerate([(2.6, 1.6), (8.5, 3.0),
                                        (9.5, 3.3), (25.0, 4.1)])])
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── the interval comes from the capture ladder ───────────────────────────────

def test_the_poll_interval_is_half_the_capture_interval():
    """One schedule, not two. A page sampling twice as often as the data is
    written bounds how stale the screen can be at half a capture cycle; a page
    on its own schedule drifts from the one the data actually arrives on."""
    off, on_day = "12:30", dt.datetime(2026, 9, 6, 12, 25)
    assert market_q.interval_for(
        market_q.minutes_to_off(DATE, off, on_day)) == 1
    assert market_q.poll_seconds(DATE, off, on_day) == 30

    day_before = dt.datetime(2026, 9, 5, 17, 0)
    assert market_q.interval_for(
        market_q.minutes_to_off(DATE, off, day_before)) == 60
    assert market_q.poll_seconds(DATE, off, day_before) == 1800


def test_the_interval_never_drops_below_the_floor():
    """However close the off, a page must not ask several times a second."""
    for minutes in (5, 0, -5, -25):
        at = dt.datetime(2026, 9, 6, 12, 30) - dt.timedelta(minutes=minutes)
        assert market_q.poll_seconds(DATE, "12:30", at) >= market_q.MIN_POLL_SECONDS


def test_a_card_with_no_off_time_polls_slowly_not_quickly():
    """Falling to the finest band on a missing column would put every page in
    the building on a fifteen-second loop for a card that is not running."""
    assert market_q.poll_seconds(DATE, None) == 1800


# ─── the cheap path ───────────────────────────────────────────────────────────

def test_an_unchanged_card_is_304_and_never_built(db, monkeypatch):
    first = client.get(f"/api/raceday/{DATE}/1")
    assert first.status_code == 200
    tag = first.headers["ETag"]
    assert first.headers[live.POLL_HEADER]

    from hkrd.query import raceday as raceday_q

    def explode(*a, **k):
        raise AssertionError("an unchanged poll must not assemble the card")

    monkeypatch.setattr(raceday_q, "build_card", explode)
    again = client.get(f"/api/raceday/{DATE}/1", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert not again.content                    # no body at all
    assert again.headers["ETag"] == tag
    assert again.headers[live.POLL_HEADER]      # still says when to come back


def test_the_blend_is_conditional_too(db):
    first = client.get(f"/api/model/blend/{DATE}/1")
    assert first.status_code == 200
    again = client.get(f"/api/model/blend/{DATE}/1",
                       headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def test_a_weight_change_is_not_answered_with_a_stale_304(db):
    """Moving the slider asks a different question of the same race. Answering
    "nothing changed" would show the old weight's numbers under the new
    label."""
    first = client.get(f"/api/model/blend/{DATE}/1?weight=0")
    other = client.get(f"/api/model/blend/{DATE}/1?weight=0.5",
                       headers={"If-None-Match": first.headers["ETag"]})
    assert other.status_code == 200
    assert other.json()["weight"] == pytest.approx(0.5)


# ─── the tag has to move when the price does ──────────────────────────────────

def test_a_new_capture_changes_the_tag(db):
    """An endpoint that answered 304 after new odds landed would freeze the
    page on a stale price with nothing on screen to say so."""
    first = client.get(f"/api/raceday/{DATE}/1")
    tag = first.headers["ETag"]

    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": 1,
             "captured_at": "2026-09-06T12:20:00", "win_odds": 2.1,
             "place_odds": 1.4}])
    conn.close()

    again = client.get(f"/api/raceday/{DATE}/1", headers={"If-None-Match": tag})
    assert again.status_code == 200
    assert again.headers["ETag"] != tag
    by_no = {r["horse_no"]: r for r in again.json()["runners"]}
    assert by_no[1]["win_odds"] == pytest.approx(2.1)


def test_the_payload_says_when_the_price_was_true(db):
    """Without it there is no way to tell a market that has not moved from a
    page that has stopped listening, and those need different reactions
    twenty minutes before the off."""
    body = client.get(f"/api/raceday/{DATE}/1").json()
    assert body["captured_at"] == "2026-09-06T12:00:00"
    assert body["poll_after"] >= market_q.MIN_POLL_SECONDS


def test_a_race_with_no_capture_still_answers(db):
    """A card with no market is not an error, and must not 500 a poll."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM odds_snapshots WHERE race_date = ?", (DATE,))
    conn.close()
    r = client.get(f"/api/raceday/{DATE}/1")
    assert r.status_code == 200
    assert r.json()["captured_at"] is None


# ─── the stamp itself ─────────────────────────────────────────────────────────

def test_the_stamp_moves_on_a_re_scrape_not_only_on_odds(db):
    """A scratching arrives with a card scrape. A tag watching only the odds
    would hold the page on a field that no longer exists."""
    conn = get_conn(db)
    before = market_q.stamp(DATE, 1, conn=conn)
    from hkrd.store import job_log
    with transaction(conn):
        job_log.record_source(conn, "scrape_meeting:card", ok=True,
                              detail="120 declared")
    after = market_q.stamp(DATE, 1, conn=conn)
    conn.close()
    assert after != before


def test_the_stamp_is_per_race(db):
    """R1 repricing must not repaint R7, which has not moved."""
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 2, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1000}])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 2, "horse_no": 1,
             "captured_at": "2026-09-06T11:00:00", "win_odds": 4.0}])
    one = market_q.stamp(DATE, 1, conn=conn)
    two = market_q.stamp(DATE, 2, conn=conn)
    conn.close()
    assert one != two

"""The doubles capture stops reading a leg once its grid is frozen and stored.

A double stops taking bets when its FIRST race goes off. On production's copy of
16 Nov 2026, 868,034 of the 2,174,538 stored doubles rows (40%) were captured
after that moment -- the same frozen grid read again, up to 67 times a leg --
and the table was 298 MB of a 624 MB database. On all 168 legs with a recorded
close, the first capture after the close already held the final grid. So a leg
is read once after its first race shuts, and never again.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.ingest import doubles as doubles_ingest
from hkrd.ingest import odds as odds_ingest
from hkrd.jobs import scrape_odds
from hkrd.query import pools
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-11-14"
VENUE = "ST"
OFFS = {1: "13:00", 2: "13:30", 3: "14:00", 4: "14:30"}


def _at(hhmm: str) -> str:
    return f"{DATE}T{hhmm}:00"


def _grid(leg: int, at: str) -> list[dict]:
    """A two-by-two grid for leg N, which couples race N with race N+1."""
    return [{"race_date": DATE, "leg_no": leg, "race_first": leg,
             "race_second": leg + 1, "horse_first": a, "horse_second": b,
             "captured_at": at, "odds": 20.0} for a in (1, 2) for b in (1, 2)]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "dbl.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": VENUE, "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200, "off_time": off}
            for n, off in OFFS.items()])
    conn.close()
    return path


def _seed(db, *, closes: dict[int, str], grids: dict[int, str]) -> None:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_market_close(conn, [
            {"race_date": DATE, "race_no": n, "closed_at": _at(t),
             "status": "STOP_SELL"} for n, t in closes.items()])
        for leg, t in grids.items():
            upsert.upsert_odds_doubles(conn, _grid(leg, _at(t)))
    conn.close()


def _frozen(db, live: set[int]) -> tuple[set[int], set[int]]:
    conn = get_conn(db)
    try:
        return pools.frozen_legs(conn, DATE, live)
    finally:
        conn.close()


def test_a_leg_is_read_once_after_its_first_race_shuts(db):
    """Leg 1 has its read after race 1 shut; leg 2's last read was before race
    2 shut, so it is owed one more; leg 3's first race is still betting."""
    _seed(db, closes={1: "13:01", 2: "13:31"},
          grids={1: "13:05", 2: "13:29", 3: "13:29"})
    frozen, known = _frozen(db, live={3, 4})
    assert known == {1, 2, 3}
    assert frozen == {1}


def test_a_first_race_gone_by_the_clock_is_frozen_as_it_stands(db):
    """No close was observed, so nothing more will come: what is stored is it."""
    _seed(db, closes={}, grids={1: "12:50", 2: "13:20", 3: "13:20"})
    frozen, _ = _frozen(db, live={3, 4})
    assert frozen == {1, 2}


@pytest.fixture()
def endpoint(db, monkeypatch):
    """No win/place reply, and a doubles reply covering legs 1 to 3 stamped
    with the tick's own time. Returns the doubles requests made, and a tick."""
    calls: list[str] = []
    stamp: dict[str, str] = {}

    def fetch_doubles(date, venue, legs=None, *, session=None, expect_id=None):
        calls.append(stamp["at"])
        return [row for leg in (1, 2, 3) for row in _grid(leg, stamp["at"])]

    monkeypatch.setattr(odds_ingest, "fetch_meeting", lambda *a, **k: [])
    monkeypatch.setattr(odds_ingest, "meeting_id", lambda *a, **k: "M")
    monkeypatch.setattr(doubles_ingest, "fetch_doubles", fetch_doubles)

    def tick(hhmm: str) -> scrape_odds.OddsRun:
        stamp["at"] = _at(hhmm)
        return scrape_odds.run(DATE, VENUE, db=str(db), turnover=False,
                               now=dt.datetime.fromisoformat(_at(hhmm)))

    return calls, tick


def _legs_at(db, hhmm: str) -> set[int]:
    conn = get_conn(db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT leg_no FROM odds_doubles WHERE captured_at = ?",
            (_at(hhmm),))}
    finally:
        conn.close()


def test_the_capture_keeps_open_legs_and_the_one_read_after_a_shut(db, endpoint):
    _, tick = endpoint
    _seed(db, closes={1: "13:01", 2: "13:31"},
          grids={1: "13:05", 2: "13:29", 3: "13:29"})

    report = tick("13:40")
    assert _legs_at(db, "13:40") == {2, 3}       # leg 1 is not written again
    assert report.legs == 2

    tick("13:45")
    assert _legs_at(db, "13:45") == {3}          # leg 2 has had its one read


def test_no_request_is_made_once_every_leg_is_settled(db, endpoint):
    """Race 4 still betting, but it starts no double: nothing left to ask."""
    calls, tick = endpoint
    _seed(db, closes={1: "13:01", 2: "13:31", 3: "14:01"},
          grids={1: "13:05", 2: "13:35", 3: "14:05"})

    report = tick("14:10")
    assert calls == []
    assert report.doubles == 0

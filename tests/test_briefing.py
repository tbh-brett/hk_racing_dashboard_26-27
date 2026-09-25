"""query/briefing — the Briefing's one answer per race, at each stage of the
days before a meeting.

Built on the Screen's own test card (`test_screen.db`): eight runners on
2026-09-27, one of them the race's only habitual leader, one with a good
trial, one in the blackbook. Tips and prices are added per test, the way
they arrive in life: the voices first, then the tote, then race day.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.jobs import import_tips
from hkrd.query import briefing
from hkrd.store import upsert
from hkrd.store.connect import transaction
from test_screen import NAMES, TODAY, db  # noqa: F401 — the fixture

TWO_DAYS_OUT = dt.datetime(2026, 9, 25, 12, 0)
NIGHT_BEFORE = dt.datetime(2026, 9, 26, 21, 0)
RACE_DAY = dt.datetime(2026, 9, 27, 11, 0)


def _path(conn) -> str:
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def _race(conn, now):
    return briefing.meeting(TODAY, now=now, conn=conn)


def _runner(race, name):
    return next(r for r in race["runners"] if r["horse_name"] == name)


def _tips(conn, source, tipster, picks, generated="2026-09-26T08:00:00Z"):
    import_tips.run({
        "payload_version": 1, "race_date": TODAY, "generated_at": generated,
        "extractor": "test", "sources": [source],
        "selections": [{"source": source, "tipster": tipster, "race_no": 1,
                        "horse_no": no, "pick_rank": rank}
                       for rank, no in enumerate(picks, start=1)]},
        db=_path(conn))


def _tote(conn, at):
    with transaction(conn):
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": TODAY, "race_no": 1, "horse_no": i + 1,
             "captured_at": at, "win_odds": 3.0 + 2 * i,
             "place_odds": 1.5 + i / 2} for i in range(len(NAMES))])


# ── two days out: the card and the Screen, nothing else ──────────────────────

def test_two_days_out_is_the_card_and_what_is_due(db):
    out = _race(db, TWO_DAYS_OUT)
    assert out["stage"] == "cold"
    clock = {c["key"]: c for c in out["clock"]}
    assert clock["factcheck"]["state"] == "due"
    assert clock["factcheck"]["due"] == "2026-09-25T20:00"
    assert clock["tote"]["due"] == "2026-09-26T12:00"
    assert clock["threads"]["state"] == "irregular"
    race = out["races"][0]
    assert race["market"] is None
    assert all(r["price"] is None and r["support"] is None
               for r in race["runners"])


def test_the_screen_s_reasons_are_there_before_anyone_speaks(db):
    race = _race(db, TWO_DAYS_OUT)["races"][0]
    keys = {x["key"] for x in race["reasons"]}
    assert "lone_leader" in keys
    assert all(x["kind"] == "computed" for x in race["reasons"])
    lone = next(x for x in race["reasons"] if x["key"] == "lone_leader")
    assert "LONE SPEED" in lone["text"] and "measured" in lone["text"]


def test_a_book_horse_is_a_reason_only_when_today_suits_it(db):
    race = _race(db, TWO_DAYS_OUT)["races"][0]
    booked = _runner(race, "BOOKED ONE")
    reason = [x for x in race["reasons"] if x["key"] == "book"]
    suits = (booked["screen"]["setup"] == "FAVOURABLE"
             or booked["blackbook"]["on_conditions"])
    assert bool(reason) == suits
    assert booked["blackbook"]["live"]           # shown on the runner either way


# ── the night before: the voices, and a thin tote ────────────────────────────

def test_the_voices_attach_to_the_horse_and_are_counted_as_sources(db):
    last = _race(db, TWO_DAYS_OUT)["races"][0]["runners"][-1]["horse_no"]
    _tips(db, "rtw_preview", "Paul Lally", [last, 1])
    _tips(db, "factcheck", "譚朗蔚", [last])
    out = _race(db, NIGHT_BEFORE)
    assert out["stage"] == "voices"
    race = out["races"][0]
    horse = next(r for r in race["runners"] if r["horse_no"] == last)
    assert horse["support"]["sources"] == 2
    assert {b["source"] for b in horse["support"]["backed_by"]} == \
        {"rtw_preview", "factcheck"}
    talked = [x for x in race["reasons"] if x["key"] == "talked_up"]
    assert talked and talked[0]["horse_no"] == last
    assert race["voices"]["sources"] == ["factcheck", "rtw_preview"]


def test_the_screen_s_first_choice_backed_by_nobody_is_said(db):
    first = _race(db, TWO_DAYS_OUT)["races"][0]["runners"][0]["horse_no"]
    others = [r["horse_no"] for r in
              _race(db, TWO_DAYS_OUT)["races"][0]["runners"][1:3]]
    _tips(db, "rtw_preview", "Paul Lally", others)
    _tips(db, "factcheck", "譚朗蔚", others[:1])
    race = _race(db, NIGHT_BEFORE)["races"][0]
    alone = [x for x in race["reasons"] if x["key"] == "screen_alone"]
    assert alone and alone[0]["horse_no"] == first


def test_a_price_before_race_day_is_shown_and_never_reasoned_from(db):
    """One bet prices a runner in the day-before pool (AGENTS.md)."""
    _tote(db, "2026-09-26T13:00:00")
    out = _race(db, NIGHT_BEFORE)
    assert out["stage"] == "priced"
    race = out["races"][0]
    assert all(r["price"]["tote"]["win"] for r in race["runners"])
    assert race["market"]["favourite"]["win"] == 3.0
    assert not [x for x in race["reasons"] if x["kind"] == "priced"]
    assert out["edges"] == []


# ── race day ─────────────────────────────────────────────────────────────────

def test_race_day_follows_the_market(db):
    _tote(db, "2026-09-27T10:30:00")
    out = _race(db, RACE_DAY)
    assert out["stage"] == "race_day"
    race = out["races"][0]
    assert race["minutes_to_off"] is None or isinstance(race["minutes_to_off"], int)
    ranks = sorted(r["market_rank"] for r in race["runners"])
    assert ranks == list(range(1, len(NAMES) + 1))


def test_the_order_is_by_how_much_there_is_to_read(db):
    out = _race(db, TWO_DAYS_OUT)
    assert out["order"] == [1]
    assert "not a chance" in out["order_rule"]


def test_the_route_404s_without_a_card(monkeypatch, db):
    from fastapi.testclient import TestClient

    from hkrd.api.app import app
    monkeypatch.setenv("HKRD_DB", _path(db))
    client = TestClient(app)
    assert client.get("/api/briefing/2026-01-01").status_code == 404
    body = client.get(f"/api/briefing/{TODAY}").json()
    assert body["races"][0]["runners"][0]["screen"]["tier"] == "SHORTLIST"


@pytest.mark.parametrize("key", [c.key for c in briefing.CLOCK])
def test_every_clock_source_says_when_it_usually_lands(key):
    due = next(c for c in briefing.CLOCK if c.key == key)
    assert due.usual and due.kind in ("said", "priced")


def test_the_route_reads_the_page_as_at_a_moment(monkeypatch, db):
    from fastapi.testclient import TestClient

    from hkrd.api.app import app
    monkeypatch.setenv("HKRD_DB", _path(db))
    client = TestClient(app)
    body = client.get(f"/api/briefing/{TODAY}?as_of=2026-09-25T12:00").json()
    assert body["as_of"] == "2026-09-25T12:00" and body["stage"] == "cold"
    assert client.get(f"/api/briefing/{TODAY}?as_of=tuesday").status_code == 422

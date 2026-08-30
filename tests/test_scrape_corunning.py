"""jobs/scrape_corunning — fetch, store, and read back through query/.

This is the path that replaces photo OCR for lane position: HKJC's own words
about where each horse travelled, landing on RunnerLine so the form guide and
lookup get them from the same object as everything else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import corunning
from hkrd.jobs import scrape_corunning
from hkrd.query import race as race_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
BODY = (FIXTURES / "corunning_4col.html").read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, status, text="", url=""):
        self.status_code, self.text, self.url = status, text, url


class _FakeSession:
    def __init__(self, races=2):
        self.races = races

    def get(self, url, params=None, timeout=None):
        no = int(params["raceno"])
        if no > self.races:
            return _FakeResponse(404, url=url)
        return _FakeResponse(200, BODY)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr("hkrd.ingest._client.MIN_INTERVAL", 0.0)


@pytest.fixture()
def db(tmp_path):
    """A meeting whose runners match the fixture's horse numbers."""
    path = tmp_path / "t.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": "2026-07-15", "race_no": n,
                                    "venue": "ST", "course": "A", "surface": "Turf",
                                    "going": "G", "distance": 1650, "race_class": "5"}
                                   for n in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-07-15", "race_no": n, "horse_no": h,
             "horse_name": name, "place": str(i + 1), "finish_time": 100.0 + i,
             "lengths_behind": "-" if i == 0 else f"{i}-1/4", "win_odds": "5.0"}
            for n in (1, 2)
            for i, (h, name) in enumerate([(3, "FASHION LEGEND"),
                                           (11, "TELECOM POWER"),
                                           (10, "LUCKY BLESSING")])
        ])
    conn.close()
    return path


def test_scrape_stores_comments_and_lane_tags(db):
    report = scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(2))
    assert report.races == 2
    assert report.comments == 6          # 3 runners x 2 races
    assert report.lane_tags > 0
    assert not report.errors


def test_lane_information_reaches_runnerline(db):
    """The whole point: form guide and lookup read lanes off the same object as
    the ET figure, with no second loader."""
    scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(1))
    conn = get_conn(db)
    race = race_q.get_race("2026-07-15", 1, conn=conn)
    conn.close()

    by_name = {r.horse_name: r for r in race.runners}
    assert "rail" in by_name["FASHION LEGEND"].lane_notes
    assert "three_wide" in by_name["TELECOM POWER"].lane_notes
    assert "one_off_rail" in by_name["LUCKY BLESSING"].lane_notes


def test_running_comment_is_kept_apart_from_the_incident_report(db):
    """Two accounts of one race. Corunning says where the horse went; the
    stewards say what went wrong. Both are shown, neither overwrites the other."""
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_comments(conn, [{
            "race_date": "2026-07-15", "race_no": 1, "horse_no": 3,
            "comment_text": "Steadied near the 1100 Metres.", "source": "incident"}])
    conn.close()

    scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(1))

    conn = get_conn(db)
    race = race_q.get_race("2026-07-15", 1, conn=conn)
    conn.close()
    horse = next(r for r in race.runners if r.horse_no == 3)
    assert "kicked clear" in horse.running_comment
    assert "Steadied" in horse.incident_comment


def test_lane_notes_do_not_pollute_the_trip_tag_list(db):
    """Lanes are namespaced so they share a table with trip tags without being
    mistaken for them -- where a horse ran is not a judgement about its trip."""
    scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(1))
    conn = get_conn(db)
    race = race_q.get_race("2026-07-15", 1, conn=conn)
    conn.close()
    for r in race.runners:
        assert not any(t.startswith("lane:") for t in r.tags)


def test_scrape_is_idempotent(db):
    first = scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(2))
    second = scrape_corunning.scrape("2026-07-15", db=db, session=_FakeSession(2))
    conn = get_conn(db)
    comments = conn.execute("SELECT count(*) FROM runner_comments").fetchone()[0]
    tags = conn.execute("SELECT count(*) FROM runner_tags").fetchone()[0]
    conn.close()
    assert first.comments == second.comments == comments
    assert tags == first.lane_tags


def test_a_failed_fetch_reports_rather_than_writing_nothing_quietly(db, monkeypatch):
    class Dead:
        def get(self, *a, **k):
            return _FakeResponse(500, url="x")
    report = scrape_corunning.scrape("2026-07-15", db=db, session=Dead())
    assert report.errors
    assert report.comments == 0

"""query/tips_record — how each source's picks ran — and jobs/record_screen.

The meeting here is made up and small enough to score by hand: two settled
races and one still to run, a withdrawn horse, a pick made after the off.
Every expected figure below is worked from the table in `db`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.jobs import record_screen
from hkrd.model import screen as model
from hkrd.query import tips_record
from hkrd.store import screen_picks, tips, upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-10-01"                        # after the Screen's fit
FETCHED = "2026-09-30T12:00:00Z"

#          race: [(horse, place, final win odds)]
RESULTS = {1: [(1, "1", 4.0), (2, "2", 2.0), (3, "3", 8.0), (4, "4", 10.0),
               (5, "5", 20.0), (6, "WV", None)],
           2: [(1, "3", 3.0), (2, "1", 6.0), (3, "2", 5.0), (4, "4", 12.0),
               (5, "5", 15.0)],
           3: [(1, None, None), (2, None, None)]}


def pick(source, race_no, horse_no, rank=None, published=None):
    return {"source": source, "tipster": source, "race_date": DATE,
            "race_no": race_no, "horse_no": horse_no, "pick_rank": rank,
            "published_at": published, "fetched_at": FETCHED}


def quote(race_no, horse_no, t):
    return {"race_date": DATE, "race_no": race_no, "horse_no": horse_no,
            "speaker": "A Trainer", "role": "trainer", "quote": "he's well",
            "source": "rtw_interview", "video_id": "vid", "t_start": t,
            "extracted_by": "rule:test", "url": "https://x", "fetched_at": FETCHED}


def screened(race_no, order):
    return [{"race_date": DATE, "race_no": race_no, "horse_no": no,
             "rank": i + 1, "win_pct": None, "place_pct": None, "tier": None,
             "version": model.VERSION, "fitted": model.FIT["fitted"],
             "recorded_at": FETCHED} for i, no in enumerate(order)]


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "record.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "ST", "distance": 1200,
             "off_time": off} for n, off in ((1, "13:00"), (2, "13:30"),
                                             (3, "14:00"))])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": n, "horse_no": h,
             "horse_name": f"HORSE {n}-{h}", "place": place, "win_odds": odds}
            for n, field in RESULTS.items() for h, place, odds in field])
        tips.upsert_selections(conn, [
            pick("racing_sports", 1, 1, 1), pick("racing_sports", 1, 2, 2),
            pick("racing_sports", 2, 3, 1), pick("racing_sports", 2, 2, 2),
            pick("racing_sports", 3, 1, 1),                # not run yet
            pick("threads", 1, 6),                         # withdrawn
            pick("threads", 2, 4, published="2026-10-01T14:00:00+08:00"),
            pick("factcheck", 1, 3), pick("factcheck", 1, 4),
            pick("factcheck", 2, 5)])
        tips.upsert_quotes(conn, [quote(1, 1, 10), quote(1, 1, 30),
                                  quote(2, 2, 50)])
        screen_picks.record(conn, screened(1, [2, 1, 3, 4, 5])
                            + screened(2, [2, 3, 1, 4, 5]))
    conn.close()
    return path


def lines(db: Path) -> dict[str, dict]:
    conn = get_conn(db)
    try:
        got = tips_record.record(since="2026-09-01", until="2026-10-31",
                                 conn=conn)
    finally:
        conn.close()
    assert (got["meetings"], got["races"]) == (1, 2)    # race 3 has no result
    return {x["key"]: x for x in got["lines"]}


def test_a_ranked_source_is_scored_on_its_first_pick(db):
    rs = lines(db)["racing_sports"]
    # Top picks R1 #1 (won at 4.0) and R2 #3 (second): $20 staked, $40 back.
    assert rs["top"] == {"n": 2, "won": 1, "placed": 2, "return_pct": 100.0,
                         # 1 win against 0.2439 + 0.2353 expected, de-vigged
                         "ae": 2.09,
                         # the favourites in those races: R1 #2 2nd, R2 #1 3rd
                         "fav_won": 0, "fav_placed": 2}
    assert rs["all"] == {"n": 4, "won": 2, "placed": 4}
    assert (rs["meetings"], rs["races"]) == (1, 2)


def test_several_unranked_picks_in_a_race_have_no_first(db):
    fc = lines(db)["factcheck"]
    # R1: two picks, neither ranked, so no top pick; R2: its only pick.
    assert fc["top"]["n"] == 1 and fc["top"]["won"] == 0
    assert fc["all"] == {"n": 3, "won": 0, "placed": 1}


def test_a_pick_after_the_off_and_a_withdrawn_horse_are_not_scored(db):
    hd = lines(db)["threads"]
    assert (hd["late"], hd["scratched"]) == (1, 1)
    assert hd["top"] is None and hd["all"] is None and hd["races"] == 0


def test_interviews_count_each_horse_once_and_have_no_top_pick(db):
    iv = lines(db)["rtw_interview"]
    assert iv["top"] is None
    assert iv["all"] == {"n": 2, "won": 2, "placed": 2}


def test_the_screen_is_read_from_what_was_recorded(db):
    sc = lines(db)["screen"]
    assert sc["top"]["n"] == 2 and sc["top"]["won"] == 1
    assert sc["all"] == {"n": 8, "won": 2, "placed": 6}


def test_the_favourite_is_the_benchmark(db):
    fav = lines(db)["favourite"]
    assert fav["top"]["n"] == 2 and fav["top"]["won"] == 0
    assert fav["top"]["fav_won"] is None
    # The market's four shortest in each race.
    assert fav["all"] == {"n": 8, "won": 2, "placed": 6}


def test_the_endpoint_answers_and_refuses_a_bad_date(db, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(db))
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    client = TestClient(app)
    got = client.get("/api/tips/record?since=2026-09-01&until=2026-10-31")
    assert got.status_code == 200, got.text
    assert {x["key"] for x in got.json()["lines"]} >= {"racing_sports",
                                                         "favourite"}
    assert client.get("/api/tips/record?since=last-week").status_code == 422


# ── jobs/record_screen ──────────────────────────────────────────────────────

def fake_meeting(place_pcts: dict[int, list[float]], fitted: str = "2026-01-01"):
    def meeting(date, *, conn=None):
        return {"race_date": date, "version": "gbm-test", "fit": {"fitted": fitted}, "races": [
            {"race_no": n, "run": n in (1, 2), "runners": [
                {"horse_no": i + 1, "rank": i + 1, "win_pct": p / 3,
                 "place_pct": p, "tier": "SHORTLIST" if i < 4 else "FIELD"}
                for i, p in enumerate(pcts)]}
            for n, pcts in place_pcts.items()]}
    return meeting


def test_a_meeting_inside_the_fit_is_not_recorded(db, monkeypatch):
    monkeypatch.setattr(record_screen.screen, "meeting",
                        fake_meeting({1: [60, 40]}, fitted=DATE))
    got = record_screen.record([DATE], db=db)
    assert got.recorded == {} and "inside the model's fit" in got.skipped[0]


def test_the_first_record_is_kept_and_only_run_races(db, monkeypatch):
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM screen_pick")
    conn.close()
    assert record_screen.pending(db=db) == []          # nothing the model scored
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO runner_gbm (race_date, race_no, horse_no, stage, p_win, "
                     "model_version, derive_version, inputs_key, scored_at) VALUES "
                     "(?, 1, 1, 'latest', 0.5, 'gbm-test', 'feat-1.0', 'k', ?)", (DATE, DATE))
    conn.close()
    assert record_screen.pending(db=db) == [DATE]
    monkeypatch.setattr(record_screen.screen, "meeting",
                        fake_meeting({1: [60, 40, 30, 20, 10], 2: [50, 45],
                                      3: [70, 30]}))
    assert record_screen.record([DATE], db=db).recorded == {DATE: 7}
    # A refit would give other numbers; the ones already kept stay.
    monkeypatch.setattr(record_screen.screen, "meeting",
                        fake_meeting({1: [10, 20, 30, 40, 50], 2: [1, 2]}))
    assert record_screen.record([DATE], db=db).recorded == {DATE: 0}
    conn = get_conn(db)
    try:
        rows = conn.execute("SELECT race_no, place_pct FROM screen_pick "
                            "WHERE horse_no = 1 ORDER BY race_no").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [(1, 60.0), (2, 50.0)]
    assert record_screen.pending(db=db) == []

"""The Trials page's DECLARED view: every declared runner's recent trials.

Asked for on 2026-10-08. Under test: the view is read as at the card (no trial
on or after the meeting); a trial is "since the last run" when it came after
the horse's last start, or the horse has never started; only the owner's note
counts as watched (the system's "System: ..." does not); a withdrawn horse is
not on the card; and the counts are what the page's strip prints.
"""
from __future__ import annotations

from hkrd.query import trial_card
from hkrd.store.connect import get_conn, init_db, transaction

CARD = "2026-10-11"


def _db(tmp_path):
    conn = get_conn(tmp_path / "hkrd.db")
    init_db(conn)
    return conn


def _race(conn, date, race_no, runners):
    conn.execute("INSERT INTO races (race_date, race_no, venue, distance, off_time, "
                 "race_class) VALUES (?, ?, 'ST', 1200, '13:00', '4')", (date, race_no))
    for no, name, place, code in runners:
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name, "
                     "place, place_code, win_odds, draw, jockey) "
                     "VALUES (?, ?, ?, ?, ?, ?, 8.0, ?, 'J Rider')",
                     (date, race_no, no, name, place, code, no))


def _trial(conn, date, no, name, place, comment, venue="ST"):
    conn.execute("INSERT INTO trials (trial_date, trial_no, horse_name, place, venue, "
                 "comment_text, finish_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (date, no, name, place, venue, comment, 58.0 + place))


def _setup(conn):
    with transaction(conn):
        _race(conn, "2026-09-13", 2, [(1, "RACED", 4, None), (2, "OTHER", 1, None)])
        _race(conn, CARD, 5, [(1, "RACED", None, None), (2, "DEBUTANT", None, None),
                              (3, "SCRATCHED", None, "WV")])
        # Before its last start, after it, and after the card (never read).
        _trial(conn, "2026-09-01", 1, "RACED", 2, "Jumped well; kept on.")
        _trial(conn, "2026-09-30", 2, "RACED", 1, "Led all the way to score.")
        _trial(conn, "2026-10-12", 3, "RACED", 1, "Drew away to score.")
        _trial(conn, "2026-09-30", 2, "DEBUTANT", 2, "Ran on when asked.")
        _trial(conn, "2026-10-02", 4, "DEBUTANT", 3, "Stayed on comfortably.")
        _trial(conn, "2026-10-02", 4, "SCRATCHED", 1, "Led all the way to score.")
        conn.execute("INSERT INTO trial_notes (horse_name, trial_date, trial_no, note, "
                     "written_at) VALUES ('RACED', '2026-09-30', 2, "
                     "'System: Won trial 2. Rated STANDOUT.', '2026-10-01T00:00:00+00:00')")
        conn.execute("INSERT INTO trial_notes (horse_name, trial_date, trial_no, note, "
                     "written_at) VALUES ('DEBUTANT', '2026-10-02', 4, "
                     "'asked, picked up well', '2026-10-03T00:00:00+00:00')")


def test_each_declared_runner_with_its_trials_read_as_at_the_card(tmp_path):
    conn = _db(tmp_path)
    _setup(conn)
    out = trial_card.meeting(CARD, conn=conn)
    (race,) = out["races"]
    names = [x["horse_name"] for x in race["runners"]]
    assert names == ["RACED", "DEBUTANT"]          # the scratched horse is not on it
    raced = race["runners"][0]
    assert [t["trial_date"] for t in raced["trials"]] == ["2026-09-30", "2026-09-01"]
    assert [t["since_last"] for t in raced["trials"]] == [True, False]
    assert raced["last_run"]["race_date"] == "2026-09-13"
    assert raced["last_run"]["days_ago"] == 28
    assert raced["since_last"] == 1


def test_a_horse_that_has_never_started_has_every_trial_since_its_last_run(tmp_path):
    conn = _db(tmp_path)
    _setup(conn)
    deb = trial_card.meeting(CARD, conn=conn)["races"][0]["runners"][1]
    assert deb["last_run"] is None
    assert all(t["since_last"] for t in deb["trials"])


def test_only_the_owners_note_counts_as_watched(tmp_path):
    conn = _db(tmp_path)
    _setup(conn)
    out = trial_card.meeting(CARD, conn=conn)
    raced, deb = out["races"][0]["runners"]
    assert raced["trials"][0]["owner_note"] is False      # the system's words
    assert deb["trials"][0]["owner_note"] is True
    assert out["counts"] == {"runners": 2, "trialled": 2, "trials": 3, "noted": 1,
                             "to_watch": 2, "standout": out["counts"]["standout"],
                             "debut": 1}


def test_no_card_is_an_empty_answer_not_an_error(tmp_path):
    conn = _db(tmp_path)
    assert trial_card.meeting(CARD, conn=conn) == {"race_date": CARD, "races": [],
                                                    "counts": None}


def test_the_route_answers_a_card_and_refuses_a_date_without_one(tmp_path, monkeypatch):
    db = tmp_path / "hkrd.db"
    monkeypatch.setenv("HKRD_DB", str(db))
    conn = get_conn(db)
    init_db(conn)
    _setup(conn)
    conn.close()
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    client = TestClient(app)
    ok = client.get(f"/api/trials/meeting/{CARD}")
    assert ok.status_code == 200 and ok.json()["counts"]["runners"] == 2
    missing = client.get("/api/trials/meeting/2026-10-14")
    assert missing.status_code == 404 and "no card" in missing.json()["detail"]

"""Race day: the card re-read for the races still to run, and a race that has
gone off left as the page showed it.

2026-10-04, both at once. A race counted as run only once its result was stored
at 19:00, so the 13:01 run rescored race 1, off at 12:30. And nothing re-read
the card between 13:00 and 19:00, so race 8 was shown for a rider who did not
ride it. A horse withdrawn on the card was parsed and never stored.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from hkrd.ingest import racecard
from hkrd.jobs import raceday_card, score_gbm, scrape_meeting
from hkrd.store import gbm as store
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction
from test_gbm import _scores, _scoring_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _shut(db: Path, date: str, race_no: int = 1) -> None:
    """What the odds capture writes the minute it sees the pool stop selling."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO market_close (race_date, race_no, closed_at, status) "
                     "VALUES (?, ?, ?, 'CLOSED')", (date, race_no, f"{date}T12:33:00"))
    conn.close()


def _page(db: Path, date: str, *, rider: tuple[int, str] | None = None,
          withdrawn: int | None = None) -> dict:
    """What HKJC's card answers for race 1: the stored field, with one rider
    changed and one horse marked withdrawn."""
    conn = get_conn(db)
    head = dict(conn.execute("SELECT race_date, race_no, venue, course, surface, distance "
                             "FROM races WHERE race_date = ? AND race_no = 1", (date,)).fetchone())
    rows = [dict(r) for r in conn.execute(
        "SELECT race_no, horse_no, horse_name, draw, jockey, trainer, actual_weight, "
        "declared_weight, rating FROM runners WHERE race_date = ? AND race_no = 1", (date,))]
    conn.close()
    for r in rows:
        r["scratched"] = r["horse_no"] == withdrawn
        if rider and r["horse_no"] == rider[0]:
            r["jockey"] = rider[1]
    return {"race": head, "runners": rows}


def test_a_race_whose_pool_has_shut_is_never_rescored(tmp_path) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    score_gbm.score(date, db)
    shown = _scores(db, date)
    _shut(db, date)
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE runners SET jockey = 'J LATE' WHERE race_date = ? AND horse_no = 3",
                     (date,))
    assert store.unrun_races(conn, date) == [] and store.unrun_dates(conn, today=date) == []
    conn.close()
    again = score_gbm.score(date, db)
    assert not again.scored and not again.errors
    pd.testing.assert_frame_equal(_scores(db, date), shown)


def test_race_day_rereads_the_card_and_scores_a_late_change(tmp_path, monkeypatch) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    score_gbm.score(date, db)
    first, first_read = _scores(db, date), _scores(db, date, "card")
    page = _page(db, date, rider=(3, "J LATE"), withdrawn=5)
    asked: list[int] = []

    def fetch(d, venue, no, *, session=None):
        asked.append(no)
        return page
    monkeypatch.setattr(racecard, "fetch_race", fetch)

    got = raceday_card.run(db, date=date)
    assert asked == [1] and not got.errors, got.render()
    assert any("#3" in c and "rider" in c and "J LATE" in c for c in got.changes), got.changes
    assert any("#5" in c and "withdrawn" in c for c in got.changes), got.changes
    latest = _scores(db, date)
    assert 5 not in set(latest["horse_no"]) and abs(latest["p_win"].sum() - 1) < 1e-9
    assert set(latest["inputs_key"]) != set(first["inputs_key"])
    pd.testing.assert_frame_equal(_scores(db, date, "card"), first_read)   # the first read stays

    again = raceday_card.run(db, date=date)                 # the same page: nothing new
    assert again.changes == [] and any("unchanged" in s for s in again.scored)

    _shut(db, date)                                          # off: never asked about again
    asked.clear()
    assert raceday_card.run(db, date=date).races == [] and asked == []


def test_a_quiet_day_asks_hkjc_nothing(tmp_path, monkeypatch, capsys) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    monkeypatch.setattr(racecard, "fetch_race", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("fetched on a day with nothing to run")))
    assert raceday_card.main(["--db", str(db), "--date", "2026-03-02"]) == 0
    assert "no race still to run" in capsys.readouterr().out


def test_the_card_records_a_withdrawn_runner_as_wx(tmp_path) -> None:
    html = (FIXTURES / "racecard.html").read_text(encoding="utf-8")
    runners = racecard.parse_racecard(html, 4)
    db = tmp_path / "c.db"
    conn = get_conn(db)
    init_db(conn)
    conn.close()
    scrape_meeting.store_card(db, {"races": [{
        "race": {"race_date": "2026-10-11", "race_no": 4, "venue": "ST"}, "runners": runners}]})
    conn = get_conn(db)
    got = dict(conn.execute("SELECT horse_no, place_code FROM runners").fetchall())
    conn.close()
    assert got[9] == "WX" and all(v is None for k, v in got.items() if k != 9)


def test_a_card_withdrawal_gives_way_to_the_result(tmp_path) -> None:
    """The result's own code replaces WX, and a placing clears it: a card that
    marked the wrong horse cannot outlive the result."""
    conn = get_conn(tmp_path / "u.db")
    init_db(conn)
    key = {"race_date": "2026-10-11", "race_no": 1}
    with transaction(conn):
        upsert.upsert_races(conn, [{**key, "venue": "ST"}])
        upsert.upsert_runners(conn, [{**key, "horse_no": n, "horse_name": f"H{n}", "place": "WX"}
                                     for n in (1, 2)])
        upsert.upsert_runners(conn, [{**key, "horse_no": 1, "horse_name": "H1", "place": "4"},
                                     {**key, "horse_no": 2, "horse_name": "H2", "place": "WV"}])
    got = {n: (c, p) for n, c, p in conn.execute(
        "SELECT horse_no, place_code, place FROM runners ORDER BY horse_no")}
    conn.close()
    assert got == {1: (None, 4), 2: ("WV", None)}

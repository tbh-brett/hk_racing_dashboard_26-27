"""A finished race, assembled once — the Results page's query layer.

Everything the page shows is computed somewhere else and joined here. These
tests hold the two claims the page makes that are not records, and the one
distinction it must never blur: a race that has not been run is not a race in
which nobody finished.
"""
from __future__ import annotations

import pytest

from hkrd.query import results as rq
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """One meeting: race 1 run, race 2 declared but not yet run."""
    path = tmp_path / "r.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-05-01", "race_no": n, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650,
             "race_class": "4"} for n in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-05-01", "race_no": 1, "horse_no": i,
             "horse_name": f"HORSE {i}", "place": str(9 - i), "draw": i,
             "win_odds": 4.0 if i == 8 else 10.0, "finish_time": 100.0 + i,
             "jockey": "Z PURTON", "trainer": "J SIZE"}
            for i in range(1, 9)])
        # Declared, not run: runner rows with no finishing position.
        upsert.upsert_runners(conn, [
            {"race_date": "2026-05-01", "race_no": 2, "horse_no": i,
             "horse_name": f"LATER {i}", "draw": i, "win_odds": 10.0}
            for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO dividends (race_date, race_no, pool, combination, "
            "dividend_per_10) VALUES ('2026-05-01', 1, ?, ?, ?)",
            [("QIN", "7,8", 145.0), ("WIN", "8", 41.0), ("PLACE", "8", 15.5)])
        conn.executemany(
            "INSERT INTO runner_comments (race_date, race_no, horse_no, "
            "comment_text, source) VALUES ('2026-05-01', 1, ?, ?, 'incident')",
            [(3, "Raced wide throughout."), (5, "")])
    conn.close()
    return path


def test_a_race_not_yet_run_is_not_a_race_nobody_finished(db):
    """The page must not draw one as the other."""
    conn = get_conn(db)
    run = rq.race_result("2026-05-01", 1, conn=conn)
    pending = rq.race_result("2026-05-01", 2, conn=conn)
    conn.close()
    assert run["run"] is True
    assert pending["run"] is False
    # Nothing invented for the race that has not happened.
    assert pending["dividends"] == [] and pending["stewards"] == []
    assert pending["money"] is None and pending["quality"] is None
    assert pending["winning_time"] is None
    # The runners are still there — it is declared, not empty.
    assert len(pending["race"]["runners"]) == 8


def test_the_meeting_says_how_many_have_been_run(db):
    conn = get_conn(db)
    m = rq.meeting_results("2026-05-01", conn=conn)
    conn.close()
    assert m["run"] == 1 and m["total"] == 2
    assert [r["run"] for r in m["races"]] == [True, False]


def test_dividends_come_back_in_the_order_the_card_pays_them(db):
    """WIN before PLACE before the exotics is how a result is read.
    Alphabetical would put PLACE before WIN and QIN before both."""
    conn = get_conn(db)
    rows = rq.dividends("2026-05-01", 1, conn=conn)
    conn.close()
    assert [r["pool"] for r in rows] == ["WIN", "PLACE", "QIN"]


def test_dividends_stay_per_ten_dollars_as_hkjc_publishes_them(db):
    """The number on the page should be the number on the ticket."""
    conn = get_conn(db)
    rows = rq.dividends("2026-05-01", 1, conn=conn)
    conn.close()
    assert next(r for r in rows if r["pool"] == "WIN")["dividend_per_10"] == 41.0


def test_an_empty_stewards_note_is_not_an_entry(db):
    """A blank row would render as a runner the stewards had nothing to say
    about, which is the opposite of what a blank means."""
    conn = get_conn(db)
    rows = rq.stewards("2026-05-01", 1, conn=conn)
    conn.close()
    assert [r["horse_no"] for r in rows] == [3]
    assert rows[0]["horse_name"] == "HORSE 3"


def _bet(conn, bet_id, race_no, *horses, stake=100.0, returned=0.0,
         bet_type="QIN", legs=None):
    conn.execute(
        "INSERT INTO bets (bet_id, race_date, race_no, bet_type, stake, "
        "returned, pnl, status, hit, source) VALUES (?,'2026-05-01',?,?,?,?,?,"
        "'settled',?, 'legacy_log')",
        (bet_id, None if legs else race_no, bet_type, stake, returned,
         returned - stake, 1 if returned > 0 else 0))
    for i, (rn, hn) in enumerate(legs or [(race_no, h) for h in horses], start=1):
        conn.execute(
            "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
            "is_banker) VALUES (?,?,?,?,0)", (bet_id, rn, hn, i if legs else 0))


def test_an_all_up_through_this_race_is_shown_but_not_counted_in_its_pl(db):
    """Its stake rides on other races too, so summing it into a race P/L would
    say money was won or lost here that was not."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "single", 1, 7, 8, stake=100.0, returned=250.0)
        _bet(conn, "chain", 1, stake=400.0, returned=0.0, bet_type="ALLUP_QQP",
             legs=[(1, 8), (2, 3)])
    m = rq.race_money("2026-05-01", 1, conn=conn)
    conn.close()
    assert m["bets"] == 2 and m["spanning"] == 1
    # Only the single-race ticket's money.
    assert m["staked"] == 100.0 and m["returned"] == 250.0
    assert m["pnl"] == 150.0
    chain = next(t for t in m["tickets"] if t["bet_id"] == "chain")
    assert chain["spans_races"] is True


def test_a_ticket_carries_the_horses_it_backed_and_how_they_finished(db):
    """Re-joining for the names in the page would put the same join in two
    places."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "q", 1, 7, 8)
    m = rq.race_money("2026-05-01", 1, conn=conn)
    conn.close()
    picks = {s["horse_name"]: s["place"] for s in m["tickets"][0]["selections"]}
    assert picks == {"HORSE 7": 2, "HORSE 8": 1}


def test_a_booked_horse_that_ran_unbacked_is_found_by_a_join(db):
    """Design brief 06: "the user shouldn't have to remember to record an
    absence." """
    conn = get_conn(db)
    with transaction(conn):
        for i, name in ((8, "HORSE 8"), (6, "HORSE 6")):
            conn.execute(
                "INSERT INTO blackbook (id, horse_name, added_date, status) "
                "VALUES (?, ?, '2026-04-01', 'active')", (f"bb_{i}", name))
        _bet(conn, "onlyone", 1, 8)
    booked = rq._booked_that_ran(conn, "2026-05-01", 1)
    conn.close()
    by_name = {b["horse_name"]: b for b in booked}
    assert bool(by_name["HORSE 8"]["backed"]) is True
    assert bool(by_name["HORSE 6"]["backed"]) is False


def test_a_horse_booked_after_the_race_is_not_credited_to_it(db):
    """Otherwise the book takes credit for form it was written from."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute(
            "INSERT INTO blackbook (id, horse_name, added_date, status) "
            "VALUES ('late', 'HORSE 8', '2026-06-01', 'active')")
    booked = rq._booked_that_ran(conn, "2026-05-01", 1)
    conn.close()
    assert booked == []


def test_the_quality_verdict_says_it_is_provisional(db):
    """Design brief 04: "Whether the first five go on to win is what settles
    this — those runs have not happened yet. The verdict above is provisional
    and will be revised, not backfilled silently." """
    conn = get_conn(db)
    r = rq.race_result("2026-05-01", 1, conn=conn)
    conn.close()
    assert r["quality"]["provisional"] is True
    assert "provisional" in r["quality"]["note"]


def test_a_race_that_does_not_exist_raises_rather_than_rendering_blank(db):
    """`get_race` returns an empty RaceLine rather than raising, so R11 of a
    nine-race meeting came back 200 with every field null. Silent success and
    silent failure must never look the same."""
    conn = get_conn(db)
    with pytest.raises(LookupError, match="no race 11"):
        rq.race_result("2026-05-01", 11, conn=conn)
    conn.close()


def test_a_meeting_that_does_not_exist_comes_back_empty_not_invented(db):
    conn = get_conn(db)
    m = rq.meeting_results("1999-01-01", conn=conn)
    conn.close()
    assert m["races"] == [] and m["total"] == 0

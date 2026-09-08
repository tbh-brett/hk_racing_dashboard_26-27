"""ingest/doubles — the pool that spans two races.

Two tests here guard mistakes that would have produced no error at all:

`test_the_combination_separator_is_a_slash` — a double is `"02/04"` where a
quinella is `"02,04"`. `odds._combination` splits on a comma, so reusing it
returns an empty list for every node and the capture reports a quiet pool.

`test_a_double_is_ordered_and_must_not_be_sorted` — everywhere else in this
package a two-horse combination is stored smallest-first so one bet cannot have
two disagreeing rows. Here that would collapse two different bets at two
different prices into whichever was written last.

The fixture is the reply shape read off the live endpoint for 2026-09-09 HV.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import doubles
from hkrd.ingest.odds import OddsError
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db

DATE = "2026-09-09"
MID = "MTG_20260909_0001"
CAP = "2026-09-08T11:00:00"


def _pool(leg_no, races, nodes, pid=None):
    return {"id": pid or f"{MID}DBL{leg_no}", "oddsType": "DBL",
            "status": "START_SELL", "sellStatus": "START_SELL",
            "leg": {"number": leg_no, "races": races},
            "oddsNodes": [{"combString": c, "oddsValue": v} for c, v in nodes]}


def parse(pools):
    return doubles.double_rows({"pmPools": pools}, date=DATE, venue="HV",
                               expect_id=MID, captured_at=CAP)


def test_the_combination_separator_is_a_slash():
    """`"DBL" === o && (v = e.combString.split("/"))` in HKJC's own bundle.

    A comma split returns nothing for every node, and a capture that stores
    nothing looks exactly like a pool with no money in it.
    """
    rows = parse([_pool(1, [1, 2], [("02/04", "8.3"), ("11/07", "93")])])
    got = {(r["horse_first"], r["horse_second"]): r["odds"] for r in rows}
    assert got == {(2, 4): 8.3, (11, 7): 93.0}


def test_a_comma_separated_node_is_not_read_as_a_double():
    """A quinella node arriving under a DBL pool means the shapes have been
    crossed, and pairing 02 with 04 out of it would be a fabricated price."""
    with pytest.raises(OddsError, match="separator"):
        parse([_pool(1, [1, 2], [("02,04", "8.3"), ("03,05", "12")])])


def test_the_leg_names_its_races_rather_than_deriving_them():
    """N and N+1 holds on every card seen, but HKJC states it, and a card that
    loses a race is exactly where a derived +1 files a leg against the wrong
    pair."""
    rows = parse([_pool(4, [4, 6], [("01/02", "20")])])
    assert (rows[0]["race_first"], rows[0]["race_second"]) == (4, 6)
    assert rows[0]["leg_no"] == 4


def test_a_leg_that_does_not_couple_two_races_raises():
    with pytest.raises(OddsError, match="two races"):
        parse([_pool(1, [1], [("01/02", "20")])])


def test_a_pool_from_another_meeting_is_refused():
    """HKJC answers a date it has no meeting for with whatever meeting is
    current. Storing that files one card's prices under another's numbers."""
    with pytest.raises(OddsError, match="does not belong"):
        parse([_pool(1, [1, 2], [("01/02", "20")], pid="MTG_20260912_0001DBL1")])


def test_an_unopened_pool_is_an_empty_capture_not_a_failure():
    """Every DBL pool present with no oddsNodes is what a declared card looks
    like before it opens — observed live on 2026-09-09 HV."""
    assert parse([_pool(n, [n, n + 1], []) for n in range(1, 8)]) == []


def test_a_double_is_ordered_and_must_not_be_sorted(tmp_path):
    conn = get_conn(tmp_path / "d.db")
    init_db(conn)
    upsert.upsert_odds_doubles(conn, [
        {"race_date": DATE, "leg_no": 1, "race_first": 1, "race_second": 2,
         "horse_first": 3, "horse_second": 7, "captured_at": CAP, "odds": 10.0},
        {"race_date": DATE, "leg_no": 1, "race_first": 1, "race_second": 2,
         "horse_first": 7, "horse_second": 3, "captured_at": CAP, "odds": 20.0},
    ])
    got = conn.execute(
        "SELECT horse_first, horse_second, odds FROM odds_doubles "
        "ORDER BY horse_first").fetchall()
    assert [(r["horse_first"], r["horse_second"], r["odds"]) for r in got] == [
        (3, 7, 10.0), (7, 3, 20.0)]
    conn.close()


def test_the_write_is_idempotent(tmp_path):
    conn = get_conn(tmp_path / "d.db")
    init_db(conn)
    rows = parse([_pool(1, [1, 2], [("02/04", "8.3"), ("03/05", "12")])])
    assert upsert.upsert_odds_doubles(conn, rows) == 2
    upsert.upsert_odds_doubles(conn, rows)
    assert conn.execute("SELECT count(*) FROM odds_doubles").fetchone()[0] == 2
    conn.close()


def test_the_separator_is_confirmed_against_a_selling_pool():
    """The one claim on this path that could only be read off the site's own
    bundle, now read off a live reply.

    `"DBL" === o && (v = e.combString.split("/"))` was the whole evidence when
    `ingest/doubles` was written, because no double pool was selling. On
    2026-09-08 the 2026-09-09 Happy Valley leg 1 pool was START_SELL with 120
    nodes, and every combString was of the form `01/01` — 936 rows across all
    seven legs parsed, none dropped.

    Kept as a fixture-shaped assertion rather than a live call: a test that
    reaches HKJC fails when the network does, and the fact it is pinning is
    about the FORMAT, which does not change with the meeting.
    """
    from hkrd.ingest import doubles as dbl
    assert dbl.COMB_SEPARATOR == "/"
    assert dbl._combination("01/01") == [1, 1]
    assert dbl._combination("12/07") == [12, 7]
    # A comma is what a quinella uses, and splitting a double on one yields
    # nothing at all rather than erroring — which is why this is pinned.
    assert dbl._combination("02,04") == []


# ─── after the first leg has run ──────────────────────────────────────────────

def test_the_winners_row_is_a_settled_book_on_the_second_race(tmp_path):
    """Betting on a double shuts when its FIRST race goes off, so from that
    moment the grid is frozen — and once the first race is decided, only the
    winner's row can still pay.

    That row is a complete book on the second race, and the first leg divides
    out EXACTLY: the price of (winner, X) is the two legs multiplied, so across
    X the first-leg probability is a constant and normalising removes it. No
    estimate of the first leg is needed and none is made, which is the
    difference between this and the pre-race reading.
    """
    from hkrd.query import pools
    from hkrd.store import upsert
    from hkrd.store.connect import get_conn, init_db, transaction

    conn = get_conn(tmp_path / "d.db")
    init_db(conn)
    at = "2026-09-09T13:00:00"
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-09-09", "race_no": n, "venue": "HV",
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200}
            for n in (1, 2)])
        # Race 1 is decided: horse 3 won, 1 was second, 2 was third.
        upsert.upsert_runners(conn, [
            {"race_date": "2026-09-09", "race_no": 1, "horse_no": n,
             "horse_name": "FIRST {}".format(n), "place": str(place)}
            for n, place in ((3, 1), (1, 2), (2, 3))])
        # The frozen grid. The winner's row prices race 2 at 4 / 8 / 8, which
        # is 50% / 25% / 25% once normalised. The losing rows are scaled by a
        # different first leg and must not touch the answer.
        upsert.upsert_odds_doubles(conn, [
            {"race_date": "2026-09-09", "leg_no": 1, "race_first": 1,
             "race_second": 2, "horse_first": f, "horse_second": s,
             "captured_at": at, "odds": o}
            for f, s, o in ((3, 1, 4.0), (3, 2, 8.0), (3, 3, 8.0),
                            (1, 1, 40.0), (1, 2, 80.0), (1, 3, 80.0))])
        # Race 2's own market has moved on since: it now likes horse 2.
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": "2026-09-09", "race_no": 2, "horse_no": n,
             "captured_at": "2026-09-09T13:25:00", "win_odds": o,
             "place_odds": None}
            for n, o in ((1, 4.0), (2, 2.0), (3, 8.0))])
    try:
        got = pools.doubles_after_leg("2026-09-09", 1, conn=conn)
        assert got["settled"] is True and got["winners"] == [3]
        assert got["combinations"] == 3, "only the winner's row is still live"
        rows = {r["horse_no"]: r for r in got["runners"]}
        assert rows[1]["implied_pct"] == pytest.approx(50.0, abs=0.1)
        assert rows[2]["implied_pct"] == pytest.approx(25.0, abs=0.1)
        # And the disagreement with the live market is the point of it. Race
        # 2's own prices de-vig to 28.6 / 57.1 / 14.3, so the frozen double
        # rated horse 1 twenty-one points higher than the market does now, and
        # horse 2 thirty-two points lower.
        assert rows[2]["win_pct"] == pytest.approx(57.1, abs=0.1)
        assert rows[2]["gap_points"] == pytest.approx(-32.1, abs=0.2)
        assert rows[1]["win_pct"] == pytest.approx(28.6, abs=0.1)
        assert rows[1]["gap_points"] == pytest.approx(21.4, abs=0.2)
    finally:
        conn.close()


def test_a_losing_first_leg_cannot_touch_the_answer(tmp_path):
    """Its row is priced by a different first leg, so including it would mix
    two scales and the normalisation would launder the mistake into something
    plausible."""
    from hkrd.query import pools
    from hkrd.store import upsert
    from hkrd.store.connect import get_conn, init_db, transaction

    conn = get_conn(tmp_path / "d3.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-09-09", "race_no": n, "venue": "HV"}
            for n in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-09-09", "race_no": 1, "horse_no": n,
             "horse_name": "FIRST {}".format(n),
             "place": str(1 if n == 1 else n)} for n in (1, 2)])
        upsert.upsert_odds_doubles(conn, [
            {"race_date": "2026-09-09", "leg_no": 1, "race_first": 1,
             "race_second": 2, "horse_first": f, "horse_second": s,
             "captured_at": "2026-09-09T13:00:00", "odds": o}
            # The winner's row is even money across the two; the loser's row
            # is wildly lopsided and must be ignored entirely.
            for f, s, o in ((1, 1, 6.0), (1, 2, 6.0),
                            (2, 1, 2.0), (2, 2, 200.0))])
    try:
        got = pools.doubles_after_leg("2026-09-09", 1, conn=conn)
        assert got["combinations"] == 2
        assert [r["implied_pct"] for r in got["runners"]] == [50.0, 50.0]
    finally:
        conn.close()


def test_before_the_first_leg_runs_there_is_nothing_frozen_to_read(tmp_path):
    """The whole grid is still live, and the reading that applies is the one
    that weights over the first-leg field."""
    from hkrd.query import pools
    from hkrd.store import upsert
    from hkrd.store.connect import get_conn, init_db, transaction

    conn = get_conn(tmp_path / "d2.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-09-09", "race_no": n, "venue": "HV"}
            for n in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-09-09", "race_no": 1, "horse_no": n,
             "horse_name": "FIRST {}".format(n)} for n in (1, 2)])
        upsert.upsert_odds_doubles(conn, [
            {"race_date": "2026-09-09", "leg_no": 1, "race_first": 1,
             "race_second": 2, "horse_first": 1, "horse_second": 1,
             "captured_at": "2026-09-09T12:00:00", "odds": 10.0}])
    try:
        got = pools.doubles_after_leg("2026-09-09", 1, conn=conn)
        assert got["settled"] is False and got["runners"] == []
        assert "has not been decided" in got["note"]
    finally:
        conn.close()

"""query/money and pools.doubles_conditional — the scale behind the prices.

Odds say how the money is divided. These say how much of it there is, which is
the difference between "drifted 20%" and "$210,000 left this horse".
"""
from __future__ import annotations

import pytest

from hkrd.query import money, pools
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-09"
CAP = "2026-09-09T17:00:00"
LATER = "2026-09-09T18:00:00"


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "m.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "HV", "course": "A",
             "surface": "Turf", "going": "GOOD", "distance": 1200}
            for n in (1, 2)])
        # Race 1: four runners at 2.0, 4.0, 8.0, 8.0.
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": n,
             "captured_at": CAP, "win_odds": o, "place_odds": None}
            for n, o in ((1, 2.0), (2, 4.0), (3, 8.0), (4, 8.0))])
        # Race 2: two runners, both 4.0 — an even split in its own win market.
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 2, "horse_no": n,
             "captured_at": CAP, "win_odds": 4.0, "place_odds": None}
            for n in (5, 6)])
    yield conn
    conn.close()


def _turnover(conn, rows):
    with transaction(conn):
        upsert.upsert_pool_turnover(conn, rows)


def test_dollars_are_the_pool_times_the_devigged_share(db):
    """In a tote this is arithmetic, not an assumption: the dividend is
    pool x (1 - takeout) / stake, so the de-vigged share IS the money share."""
    _turnover(db, [{"race_date": DATE, "race_no": 2, "pool": "WIN",
                    "captured_at": CAP, "turnover": 1_000_000}])
    mf = money.money_flow(DATE, 2, conn=db)
    assert mf["win_pool"] == 1_000_000
    assert [r["dollars"] for r in mf["runners"]] == [500_000, 500_000]


def test_without_a_denominator_the_shares_still_show(db):
    """A missing pool costs the dollars, never the shares — and it says which
    of the two happened."""
    mf = money.money_flow(DATE, 1, conn=db)
    assert mf["win_pool"] is None
    assert all(r["dollars"] is None for r in mf["runners"])
    assert all(r["share_pct"] > 0 for r in mf["runners"])
    assert "dollars cannot be" in mf["note"]


def test_growth_is_measured_from_the_first_capture_that_held_a_number(db):
    """A card scraped the night before records nulls for hours. Measuring from
    the first ROW rather than the first amount would report the whole pool as
    having arrived the instant it opened."""
    _turnover(db, [
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": "2026-09-08T22:00:00", "turnover": None},
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": CAP, "turnover": 1_000_000},
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": LATER, "turnover": 1_500_000}])
    t = money.pool_turnover(DATE, 1, conn=db)
    assert t["pools"]["WIN"]["turnover"] == 1_500_000
    assert t["pools"]["WIN"]["since_first"] == 500_000
    assert t["pools"]["WIN"]["growth_pct"] == 50.0


def test_a_pool_with_no_money_yet_is_reported_as_not_open(db):
    _turnover(db, [
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": CAP, "turnover": 100.0},
        {"race_date": DATE, "race_no": 1, "pool": "QPL",
         "captured_at": CAP, "turnover": None}])
    t = money.pool_turnover(DATE, 1, conn=db)
    assert t["open"] == ["WIN"]
    assert t["pools"]["QPL"]["open"] is False
    assert t["pools"]["QPL"]["turnover"] is None


def test_a_card_captured_before_it_opened_is_not_observed(db):
    """Rows for every pool and money in none of them is a real state, and it is
    not the same answer as "nothing was captured"."""
    _turnover(db, [{"race_date": DATE, "race_no": 1, "pool": "WIN",
                    "captured_at": CAP, "turnover": None}])
    assert money.meeting_money(DATE, conn=db)["observed"] is False
    _turnover(db, [{"race_date": DATE, "race_no": 1, "pool": "WIN",
                    "captured_at": LATER, "turnover": 50.0}])
    assert money.meeting_money(DATE, conn=db)["observed"] is True


def test_the_meeting_total_is_not_counted_as_a_race(db):
    _turnover(db, [
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": CAP, "turnover": 100.0},
        {"race_date": DATE, "race_no": 0, "pool": money.TOTAL_POOL,
         "captured_at": CAP, "turnover": 900.0}])
    mm = money.meeting_money(DATE, conn=db)
    assert [r["race_no"] for r in mm["races"]] == [1]
    assert mm["meeting_total"] == 900.0


# ── doubles ───────────────────────────────────────────────────────────────────

def _doubles(conn):
    with transaction(conn):
        upsert.upsert_odds_doubles(conn, [
            {"race_date": DATE, "leg_no": 1, "race_first": 1, "race_second": 2,
             "horse_first": f, "horse_second": s, "captured_at": CAP, "odds": o}
            # First leg: 1 is 2.0 and 2 is 4.0. Both doubles onto second-leg 5
            # imply 5.0 on it; both onto 6 imply 10.0.
            for f, s, o in ((1, 5, 10.0), (2, 5, 20.0),
                            (1, 6, 20.0), (2, 6, 40.0))])


def test_the_doubles_pool_prices_the_second_leg(db):
    """A double is the two legs multiplied, so dividing out the first leg's own
    price leaves what the pool offers on the second-leg runner."""
    _doubles(db)
    dc = pools.doubles_conditional(DATE, 1, conn=db)
    assert (dc["race_first"], dc["race_second"]) == (1, 2)
    implied = {r["horse_no"]: r["implied_odds"] for r in dc["runners"]}
    assert implied == {5: pytest.approx(5.0), 6: pytest.approx(10.0)}


def test_the_comparable_figure_is_the_share_not_the_price(db):
    """A double carries one takeout; two win bets carry two. So the raw implied
    price sits above the second leg's own win price for every runner, and
    comparing them directly reports an overlay on the whole field. The
    normalised share is the like-for-like quantity.

    Race 2's own market has both runners at 4.0 — an even split. The doubles
    say 66.7 / 33.3, which is a different SHAPE, and that is the signal.
    """
    _doubles(db)
    dc = pools.doubles_conditional(DATE, 1, conn=db)
    assert sum(r["implied_pct"] for r in dc["runners"]) == pytest.approx(100.0, abs=0.2)
    assert [r["implied_pct"] for r in dc["runners"]] == [
        pytest.approx(66.7, abs=0.2), pytest.approx(33.3, abs=0.2)]


def test_a_leg_with_nothing_captured_says_so(db):
    dc = pools.doubles_conditional(DATE, 4, conn=db)
    assert dc["runners"] == [] and "no doubles captured" in dc["note"]

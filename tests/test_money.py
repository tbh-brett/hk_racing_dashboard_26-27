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


# ─── the distribution across every pool ───────────────────────────────────────
#
# The win pool is the one the card is sorted by and it is not the biggest.
# Measured on 2026-09-09 HV race 1, captured the day before racing: quinella
# place $251,403, quinella $202,395, win $174,539, place $145,593. A win-only
# reading of "where the money is" misses more than half of it, and the half it
# misses is the half a QQP ticket is struck into.

def test_the_money_share_normalises_to_one_not_to_three(db):
    """The same division answers two questions and only the target differs.

    In every one of these pools the dividend is `pool x (1 - takeout) / stake`,
    so stake is proportional to the reciprocal of the price — including in the
    place pool, which is split into three equal parts one per placed horse, so
    the identity survives. Normalised to ONE that is a share of the money;
    normalised to three it is a place probability, which is a different
    quantity. A share of a pool that sums to 300% is not a share of anything.
    """
    with transaction(db):
        upsert.upsert_odds_snapshots(db, [
            {"race_date": DATE, "race_no": 2, "horse_no": n,
             "captured_at": CAP, "win_odds": 4.0, "place_odds": p}
            for n, p in ((5, 2.0), (6, 2.0))])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 2, "pool": "PLA",
             "captured_at": CAP, "turnover": 300_000}])
    got = money.runner_money(DATE, 2, conn=db)
    shares = [r["pools"]["PLA"]["share_pct"] for r in got["runners"]]
    assert sum(shares) == pytest.approx(100.0, abs=0.2)
    assert [r["pools"]["PLA"]["dollars"] for r in got["runners"]] == [150_000,
                                                                     150_000]


def test_win_money_and_place_money_are_separate_markets(db):
    """A horse can hold a tenth of one and a twentieth of the other, and the
    gap is a fact about how the crowd expects it to run."""
    with transaction(db):
        upsert.upsert_odds_snapshots(db, [
            # Same win price, different place price: the place pool disagrees.
            {"race_date": DATE, "race_no": 2, "horse_no": 5,
             "captured_at": CAP, "win_odds": 4.0, "place_odds": 1.5},
            {"race_date": DATE, "race_no": 2, "horse_no": 6,
             "captured_at": CAP, "win_odds": 4.0, "place_odds": 3.0}])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 2, "pool": "WIN",
             "captured_at": CAP, "turnover": 100_000},
            {"race_date": DATE, "race_no": 2, "pool": "PLA",
             "captured_at": CAP, "turnover": 100_000}])
    rows = {r["horse_no"]: r for r in money.runner_money(DATE, 2, conn=db)["runners"]}
    assert rows[5]["pools"]["WIN"]["share_pct"] == rows[6]["pools"]["WIN"]["share_pct"]
    assert rows[5]["pools"]["PLA"]["share_pct"] > rows[6]["pools"]["PLA"]["share_pct"]


def test_qqp_money_is_the_two_pair_pools_added(db):
    """QQP is a TICKET, not a pool — one selection struck into the quinella and
    the quinella place — so the money it competes with is the two together."""
    with transaction(db):
        upsert.upsert_odds_pairs(db, [
            {"race_date": DATE, "race_no": 1, "pool": pool, "horse_a": a,
             "horse_b": b, "captured_at": CAP, "odds": o}
            for pool, a, b, o in (("QIN", 1, 2, 4.0), ("QIN", 1, 3, 4.0),
                                  ("QPL", 1, 2, 2.0), ("QPL", 1, 3, 6.0))])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": "QIN",
             "captured_at": CAP, "turnover": 200_000},
            {"race_date": DATE, "race_no": 1, "pool": "QPL",
             "captured_at": CAP, "turnover": 400_000}])
    got = money.pair_money(DATE, 1, conn=db)
    by_pair = {tuple(p["horse_nos"]): p for p in got["pairs"]}
    # QIN splits evenly; QPL is 3:1 towards (1,2).
    assert by_pair[(1, 2)]["pools"]["QIN"]["dollars"] == 100_000
    assert by_pair[(1, 2)]["pools"]["QPL"]["dollars"] == 300_000
    assert by_pair[(1, 2)]["combined"] == 400_000
    assert by_pair[(1, 3)]["combined"] == 200_000
    # And the ranking is by the combined figure, which is the one that matters.
    assert [tuple(p["horse_nos"]) for p in got["pairs"]] == [(1, 2), (1, 3)]


def test_a_pair_priced_in_only_one_pool_has_no_combined_figure(db):
    """Half a QQP's money shown beside complete figures would rank the pair
    last for having been measured differently, not for being unbacked."""
    with transaction(db):
        upsert.upsert_odds_pairs(db, [
            {"race_date": DATE, "race_no": 1, "pool": "QIN", "horse_a": 1,
             "horse_b": 2, "captured_at": CAP, "odds": 4.0},
            {"race_date": DATE, "race_no": 1, "pool": "QIN", "horse_a": 1,
             "horse_b": 3, "captured_at": CAP, "odds": 4.0},
            {"race_date": DATE, "race_no": 1, "pool": "QPL", "horse_a": 1,
             "horse_b": 2, "captured_at": CAP, "odds": 2.0},
            {"race_date": DATE, "race_no": 1, "pool": "QPL", "horse_a": 1,
             "horse_b": 4, "captured_at": CAP, "odds": 2.0}])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": "QIN",
             "captured_at": CAP, "turnover": 200_000},
            {"race_date": DATE, "race_no": 1, "pool": "QPL",
             "captured_at": CAP, "turnover": 400_000}])
    by_pair = {tuple(p["horse_nos"]): p
               for p in money.pair_money(DATE, 1, conn=db)["pairs"]}
    assert by_pair[(1, 2)]["combined"] is not None
    assert by_pair[(1, 3)]["combined"] is None      # QIN only
    assert by_pair[(1, 4)]["combined"] is None      # QPL only


def test_a_merged_pool_is_not_counted_twice(db):
    """HKJC merges Quartet into First 4 and reports the same money under both
    ids. Measured on 2026-09-09 HV race 1, FF and QTT both read $28,006, so a
    sum over every pool overstates the race by exactly that."""
    _turnover(db, [
        {"race_date": DATE, "race_no": 1, "pool": "WIN",
         "captured_at": CAP, "turnover": 100_000},
        {"race_date": DATE, "race_no": 1, "pool": "FF",
         "captured_at": CAP, "turnover": 28_006, "merged_into": "0000155402"},
        {"race_date": DATE, "race_no": 1, "pool": "QTT",
         "captured_at": CAP, "turnover": 28_006, "merged_into": "0000155402"},
    ])
    got = money.pool_turnover(DATE, 1, conn=db)
    assert got["pools"]["FF"]["counted_elsewhere"] is False
    assert got["pools"]["QTT"]["counted_elsewhere"] is True
    # Both figures are still shown — each is correct about its own pool.
    assert got["pools"]["QTT"]["turnover"] == 28_006
    assert got["race_total"] == 128_006, "the naive sum would be 156,012"

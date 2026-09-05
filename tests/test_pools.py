"""query/pools — the quinella, doubles and turnover tables, finally read.

`odds_pairs` was written by every scrape from the day the scraper landed and
selected by nothing: the pre-bet panel derived its quinella pairs from win odds
through Harville while the market's own pair prices sat in the table beside it.
These tests exist so that stays fixed.
"""
from __future__ import annotations

import pytest

from hkrd.query import pools
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-06"
CAP = "2026-09-06T11:00:00"
LATER = "2026-09-06T12:00:00"


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "p.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "GOOD", "distance": 1200}
            for n in (1, 2)])
        # Six priced runners in race 1 -- the minimum the divergence will read.
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": n, "captured_at": CAP,
             "win_odds": o, "place_odds": None}
            for n, o in ((1, 2.0), (2, 4.0), (3, 8.0), (4, 8.0), (5, 16.0),
                         (6, 16.0))])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 2, "horse_no": n, "captured_at": CAP,
             "win_odds": o, "place_odds": None}
            for n, o in ((5, 4.0), (6, 4.0))])
    yield conn
    conn.close()


def _all_pairs(odds_for):
    return [{"race_date": DATE, "race_no": 1, "pool": "QIN", "horse_a": a,
             "horse_b": b, "captured_at": CAP, "odds": odds_for(a, b)}
            for a in range(1, 7) for b in range(a + 1, 7)]


def test_the_market_pair_prices_are_read_at_all(db):
    """The regression this file exists for: odds_pairs was write-only."""
    with transaction(db):
        upsert.upsert_odds_pairs(db, _all_pairs(lambda a, b: 10.0 * (a + b)))
    got = pools.market_pairs(DATE, 1, conn=db, top=3)
    assert got["combinations"] == 15
    assert got["pairs"][0]["horse_nos"] == [1, 2]        # shortest price
    assert sum(p["prob"] for p in got["pairs"]) > 0


def test_market_pairs_says_so_when_nothing_was_captured(db):
    got = pools.market_pairs(DATE, 1, conn=db)
    assert got["pairs"] == [] and "no QIN prices" in got["note"]


def test_a_horse_the_pair_pool_will_not_combine_is_flagged_cold(db):
    """Money on it to win, none on it to be in the first two.

    Horse 2 is priced 4.0 in the win pool but every pair containing it is sold
    at four times its fair price, so the pool is carrying far less of it than
    the win market implies.
    """
    def odds(a, b):
        base = 10.0 * (a + b)
        return base * 4 if 2 in (a, b) else base

    with transaction(db):
        upsert.upsert_odds_pairs(db, _all_pairs(odds))
    d = pools.pair_divergence(DATE, 1, conn=db)
    assert d["complete"] is True
    assert 2 in d["cold"]
    two = next(r for r in d["runners"] if r["horse_no"] == 2)
    assert two["ratio"] < pools.COLD_PAIR_RATIO
    # And the runners come back coldest first, so the flag is the top of the list.
    assert d["runners"][0]["horse_no"] == 2


def test_a_pool_that_agrees_with_the_win_market_flags_nothing(db):
    """The ratio is built so that a pair market matching Harville sits at 1.0.
    If this drifts, every cold flag downstream is measuring the wrong baseline."""
    from hkrd.derive.probability import pair_probability
    hv = pair_probability([2.0, 4.0, 8.0, 8.0, 16.0, 16.0])
    nos = [1, 2, 3, 4, 5, 6]
    idx = {n: i for i, n in enumerate(nos)}
    with transaction(db):
        upsert.upsert_odds_pairs(db, _all_pairs(
            lambda a, b: 1.0 / hv[(idx[a], idx[b])]))
    d = pools.pair_divergence(DATE, 1, conn=db)
    assert d["cold"] == []
    assert all(0.98 <= r["ratio"] <= 1.02 for r in d["runners"])


def test_a_partial_matrix_is_reported_rather_than_averaged_over(db):
    """A ratio measured over half the combinations is not the same figure."""
    with transaction(db):
        upsert.upsert_odds_pairs(db, _all_pairs(lambda a, b: 10.0 * (a + b))[:8])
    d = pools.pair_divergence(DATE, 1, conn=db)
    assert d["complete"] is False
    assert "partial matrix" in d["note"]


def test_a_field_too_small_to_read_says_so(db):
    d = pools.pair_divergence(DATE, 2, conn=db)
    assert d["runners"] == [] and "fewer than" in d["note"]


def test_turnover_growth_is_measured_from_the_first_capture(db):
    with transaction(db):
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": "WIN",
             "captured_at": CAP, "turnover": 1_000_000},
            {"race_date": DATE, "race_no": 1, "pool": "WIN",
             "captured_at": LATER, "turnover": 1_500_000}])
    t = pools.pool_turnover(DATE, 1, conn=db)
    assert t["pools"]["WIN"]["turnover"] == 1_500_000
    assert t["pools"]["WIN"]["since_first"] == 500_000
    assert t["pools"]["WIN"]["growth_pct"] == 50.0


def test_a_pool_that_is_not_operated_is_absent_from_operated(db):
    with transaction(db):
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": p,
             "captured_at": CAP, "turnover": v}
            for p, v in (("WIN", 100.0), ("QIN", 50.0),
                         ("RACE_TOTAL_ALL", 150.0))])
    t = pools.pool_turnover(DATE, 1, conn=db)
    assert t["operated"] == ["QIN", "WIN"]      # the total is not a pool


def test_dollars_are_the_pool_times_the_devigged_share(db):
    """In a tote this is arithmetic, not an assumption: the dividend is
    pool x (1 - takeout) / stake, so the de-vigged share IS the money share."""
    with transaction(db):
        upsert.upsert_odds_snapshots(db, [
            {"race_date": DATE, "race_no": 2, "horse_no": n,
             "captured_at": LATER, "win_odds": 2.0, "place_odds": None}
            for n in (5, 6)])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 2, "pool": "WIN",
             "captured_at": LATER, "turnover": 1_000_000}])
    mf = pools.money_flow(DATE, 2, conn=db)
    assert mf["win_pool"] == 1_000_000
    assert [r["dollars"] for r in mf["runners"]] == [500_000, 500_000]


def test_without_turnover_the_shares_still_show_and_the_dollars_do_not(db):
    """A missing denominator costs the dollars, never the shares -- and it says
    which of the two happened."""
    mf = pools.money_flow(DATE, 1, conn=db)
    assert mf["win_pool"] is None
    assert all(r["dollars"] is None for r in mf["runners"])
    assert all(r["share_pct"] > 0 for r in mf["runners"])
    assert "dollars cannot be" in mf["note"]


def test_the_doubles_pool_prices_the_second_leg(db):
    """A double is the two legs multiplied, so dividing out the first leg's own
    price leaves what the pool is offering on the second-leg runner."""
    with transaction(db):
        upsert.upsert_odds_doubles(db, [
            {"race_date": DATE, "leg_no": 1, "horse_first": f,
             "horse_second": s, "captured_at": CAP, "odds": o}
            # first-leg 1 is 2.0 and 2 is 4.0; both doubles onto second-leg 5
            # imply a price of 5.0 on it.
            for f, s, o in ((1, 5, 10.0), (2, 5, 20.0),
                            (1, 6, 20.0), (2, 6, 40.0))])
    dc = pools.doubles_conditional(DATE, 1, conn=db)
    assert (dc["race_first"], dc["race_second"]) == (1, 2)
    implied = {r["horse_no"]: r["implied_odds"] for r in dc["runners"]}
    assert implied[5] == pytest.approx(5.0)
    assert implied[6] == pytest.approx(10.0)


def test_combinations_at_the_display_cap_are_excluded_not_inverted(db):
    """999 means 'at least 999'. Treated as a price it claims a probability
    for a combination that is worth several thousand."""
    with transaction(db):
        upsert.upsert_odds_doubles(db, [
            {"race_date": DATE, "leg_no": 1, "horse_first": 1,
             "horse_second": 5, "captured_at": CAP, "odds": 10.0},
            {"race_date": DATE, "leg_no": 1, "horse_first": 2,
             "horse_second": 5, "captured_at": CAP,
             "odds": pools.DOUBLES_DISPLAY_CAP}])
    dc = pools.doubles_conditional(DATE, 1, conn=db)
    assert "display cap" in dc["note"]
    # Only the uncapped combination fed the estimate: 10.0 / 2.0.
    assert dc["runners"][0]["implied_odds"] == pytest.approx(5.0)


def test_pool_changes_distinguishes_quiet_from_uncaptured(db):
    """An empty list of divergences and a meeting nothing was captured for
    are different answers, and only one of them is interesting."""
    assert pools.pool_changes(DATE, conn=db)["observed"] is False
    with transaction(db):
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": "WIN",
             "captured_at": CAP, "turnover": 100.0}])
    changed = pools.pool_changes(DATE, conn=db)
    assert changed["observed"] is True
    assert changed["races"][0]["win_pool"] == 100.0


EARLY = "2026-09-06T01:00:00"        # ~12h before racing


def test_an_immature_pair_pool_flags_nothing(db):
    """Found by running it on a live card at eleven hours out, where it called
    6 of 14 runners cold.

    The pair pool fills much later than the win pool. Early on it holds a
    fraction of the money, its marginals swing on single bets, and the ratio is
    wide for the whole field — so the flag fires on half of them and means
    nothing. The 0.85 boundary was measured on captures taken AT racing.
    """
    def odds(a, b):
        base = 10.0 * (a + b)
        return base * 4 if 2 in (a, b) else base

    with transaction(db):
        upsert.upsert_odds_snapshots(db, [
            {"race_date": DATE, "race_no": 1, "horse_no": n,
             "captured_at": EARLY, "win_odds": o, "place_odds": None}
            for n, o in ((1, 2.0), (2, 4.0), (3, 8.0), (4, 8.0), (5, 16.0),
                         (6, 16.0))])
        upsert.upsert_odds_pairs(db, [
            {**r, "captured_at": EARLY} for r in _all_pairs(odds)])

    d = pools.pair_divergence(DATE, 1, conn=db)
    assert d["early"] is True
    assert d["cold"] == []
    # The ratios stay on the record. Nothing is hidden -- what is withheld is
    # the assertion that a number this early means what it means at the off.
    assert len(d["runners"]) == 6
    assert any(r["ratio"] < pools.COLD_PAIR_RATIO for r in d["runners"])
    assert "before racing" in d["note"]


def test_the_meeting_view_does_not_flag_what_the_guard_suppressed(db):
    """pool_changes read the per-runner boolean rather than the guarded list,
    and reported cold runners on a pool it had just called too early."""
    with transaction(db):
        upsert.upsert_odds_snapshots(db, [
            {"race_date": DATE, "race_no": 1, "horse_no": n,
             "captured_at": EARLY, "win_odds": o, "place_odds": None}
            for n, o in ((1, 2.0), (2, 4.0), (3, 8.0), (4, 8.0), (5, 16.0),
                         (6, 16.0))])
        upsert.upsert_odds_pairs(db, [
            {**r, "captured_at": EARLY} for r in _all_pairs(
                lambda a, b: 10.0 * (a + b) * (4 if 2 in (a, b) else 1))])
        upsert.upsert_pool_turnover(db, [
            {"race_date": DATE, "race_no": 1, "pool": "WIN",
             "captured_at": EARLY, "turnover": 100.0}])
    pc = pools.pool_changes(DATE, conn=db)
    assert pc["too_early"] is True
    assert pc["races_with_cold"] == 0


def test_the_comparable_figure_is_the_share_not_the_price(db):
    """A double is one takeout; two win bets are two.

    So `double / first-leg price` comes in short of the second leg's own win
    price for every runner, and comparing those two numbers directly would
    report an overlay on the whole field. The normalised share is the
    like-for-like quantity, and it sums to 100.
    """
    with transaction(db):
        upsert.upsert_odds_doubles(db, [
            {"race_date": DATE, "leg_no": 1, "horse_first": f,
             "horse_second": s, "captured_at": CAP, "odds": o}
            for f, s, o in ((1, 5, 10.0), (2, 5, 20.0),
                            (1, 6, 20.0), (2, 6, 40.0))])
    dc = pools.doubles_conditional(DATE, 1, conn=db)
    assert sum(r["implied_pct"] for r in dc["runners"]) == pytest.approx(100.0, abs=0.2)
    # Race 2's own win market has both runners at 4.0, an even split. The
    # doubles say 5.0 and 10.0 -- a different SHAPE, which is the signal --
    # while both raw prices sit above the win price purely from the takeout.
    assert [r["implied_odds"] for r in dc["runners"]] == [5.0, 10.0]
    assert [r["implied_pct"] for r in dc["runners"]] == [pytest.approx(66.7, abs=0.2),
                                                         pytest.approx(33.3, abs=0.2)]


def test_a_meeting_with_pairs_but_no_turnover_is_still_observed(db):
    """Every meeting scraped before turnover capture existed is in this state.

    Keyed on the win pool alone, `observed` came back False on a full quinella
    archive, and the Race Day strip returns early on it — so seven races of
    real cold flags went unrendered because a denominator was missing.
    """
    def odds(a, b):
        base = 10.0 * (a + b)
        return base * 4 if 2 in (a, b) else base

    with transaction(db):
        upsert.upsert_odds_pairs(db, _all_pairs(odds))
    pc = pools.pool_changes(DATE, conn=db)
    assert pc["races_with_cold"] == 1
    assert pc["observed"] is True
    assert pc["races"][0]["win_pool"] is None

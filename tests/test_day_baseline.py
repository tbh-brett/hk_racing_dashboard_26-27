"""Every change on a race card is measured from midnight on the race day.

THE FAULT THIS FIXES, from the live card of 2026-09-09 Happy Valley race 3.
HKJC opens a pool around midday the day BEFORE racing. For the first hours it
is nearly empty, and an almost-empty tote pool quotes numbers that are real,
finite, plausible and meaningless — one $10 bet in a pool holding a couple of
hundred dollars prices a runner at 2.2. MACANESE MASTER was 2.2 at 12:01 on the
8th, 8.8 at midnight, and 10.0 by the afternoon of the 9th.

Measured from the first capture ever taken, the card said **+354%**. Measured
from midnight it says **+13.6%**, which is what actually happened to it on race
day. Across that race the old baseline was not merely inflated — it pointed the
WRONG WAY on five of the twelve runners:

    8 MR GOOD VIBES     +700%  ->   -7.7%
   10 WINNING DIAMOND   +718%  ->  -10.0%
    5 CONRAD THE GREAT   -7.1% ->  +13.0%
    9 THOUSAND CUPS      -7.8% ->  +15.3%

The 999.0 guard cannot catch this. 999.0 is HKJC saying "no price"; 2.2 is HKJC
saying "one person has bet". Both are useless as a baseline and only one of them
is detectable by its value, which is why the fix is a CLOCK and not a threshold.

Nothing is deleted. The day-before rows stay and are still the opening price of
the pool. They are simply not the baseline for "how has this moved today".
"""
from __future__ import annotations

import pytest

from hkrd.query import market, money, movement
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-09"
EVE = "2026-09-08"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """The real shape of 2026-09-09 HV race 3, reduced to three runners.

    Horse 4 is MACANESE MASTER's actual series. Horse 8 is the case where the
    two baselines disagree about the DIRECTION. Horse 1 never traded before
    midnight at all, which is what every meeting looks like now that the
    capture no longer runs the day before.
    """
    path = tmp_path / "baseline.db"
    conn = get_conn(path)
    init_db(conn)
    rows: list[dict] = []

    def cap(at, prices, pool=None):
        for horse_no, win in prices.items():
            rows.append({"race_date": DATE, "race_no": 3, "horse_no": horse_no,
                         "captured_at": at, "win_odds": win,
                         "place_odds": None if win is None else round(win / 3, 1)})

    # The day before: an open pool with almost nothing in it.
    cap(f"{EVE}T12:01:00", {4: 999.0, 8: 999.0})     # HKJC's "no price"
    cap(f"{EVE}T13:01:00", {4: 2.2, 8: 3.0})         # one bet each — real, useless
    cap(f"{EVE}T15:21:00", {4: 9.3, 8: 20.0})
    # Race day.
    cap(f"{DATE}T00:00:00", {4: 8.8, 8: 26.0, 1: 5.0})
    cap(f"{DATE}T06:00:00", {4: 9.0, 8: 25.0, 1: 4.8})
    cap(f"{DATE}T13:00:00", {4: 10.0, 8: 24.0, 1: 4.4})

    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 3, "venue": "HV", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200,
             "off_time": "20:05"}])
        upsert.upsert_odds_snapshots(conn, rows)
        upsert.upsert_pool_turnover(conn, [
            {"race_date": DATE, "race_no": 3, "pool": "WIN",
             "captured_at": f"{EVE}T13:01:00", "turnover": 4_000},
            {"race_date": DATE, "race_no": 3, "pool": "WIN",
             "captured_at": f"{DATE}T00:00:00", "turnover": 100_000},
            {"race_date": DATE, "race_no": 3, "pool": "WIN",
             "captured_at": f"{DATE}T13:00:00", "turnover": 300_000}])
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── the baseline itself ──────────────────────────────────────────────────────

def test_the_baseline_is_midnight_on_the_race_day(db):
    conn = get_conn(db)
    try:
        assert market.day_start(DATE) == f"{DATE}T00:00:00"
        assert market.opening_capture(conn, DATE, 3) == f"{DATE}T00:00:00"
    finally:
        conn.close()


def test_a_meeting_captured_only_the_day_before_still_has_one(db):
    """Most of what the legacy import rescued is like this. The earliest real
    price is the only baseline there is, and it is still an honest one."""
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 9, "horse_no": 1,
             "captured_at": f"{EVE}T18:00:00", "win_odds": 4.0,
             "place_odds": 1.8}])
    try:
        assert market.opening_capture(conn, DATE, 9) == f"{EVE}T18:00:00"
        # And a race with nothing priced at all has none, rather than a
        # timestamp pointing at a price that never existed.
        assert market.opening_capture(conn, DATE, 11) is None
    finally:
        conn.close()


# ─── what it changes on the card ──────────────────────────────────────────────

def test_the_move_is_the_race_days_move(db):
    """MACANESE MASTER's real numbers: +354% from the first capture ever,
    +13.6% from midnight."""
    by_no = {m["horse_no"]: m for m in movement.split_move(DATE, 3)}
    assert by_no[4]["early"] == 8.8
    assert by_no[4]["late"] == 10.0
    assert by_no[4]["change_pct"] == pytest.approx(13.6, abs=0.1)


def test_the_old_baseline_pointed_the_wrong_way_on_a_third_of_the_field(db):
    """Horse 8 went 3.0 -> 24.0 from the first capture, which is a drift of
    700%, and 26.0 -> 24.0 across race day, which is a runner being BACKED.
    They are not the same fact and one of them is not about this race."""
    by_no = {m["horse_no"]: m for m in movement.split_move(DATE, 3)}
    assert by_no[8]["early"] == 26.0
    assert by_no[8]["change_pct"] == pytest.approx(-7.7, abs=0.1)
    assert by_no[8]["direction"] == "shortened"


def test_price_movement_agrees_with_the_card(db):
    """Two functions, one baseline. A page showing a runner firming while
    another shows it drifting is the failure this shares a definition to
    avoid."""
    conn = get_conn(db)
    try:
        a = {m["horse_no"]: m for m in movement.price_movement(DATE, 3, conn=conn)}
        b = {m["horse_no"]: m for m in movement.split_move(DATE, 3, conn=conn)}
    finally:
        conn.close()
    for horse_no in a:
        assert a[horse_no]["early"] == b[horse_no]["early"]
        assert a[horse_no]["early_at"] == b[horse_no]["early_at"]
        assert a[horse_no]["direction"] == b[horse_no]["direction"]


def test_the_day_before_rows_are_still_there(db):
    """Nothing deletes from `odds_snapshots`. They are the opening price of the
    pool and remain readable; they are just not the baseline."""
    conn = get_conn(db)
    try:
        n = conn.execute(
            "SELECT count(*) FROM odds_snapshots "
            "WHERE race_date = ? AND captured_at < ?",
            (DATE, market.day_start(DATE))).fetchone()[0]
    finally:
        conn.close()
    assert n == 6


# ─── and on the money ─────────────────────────────────────────────────────────

def test_money_arrived_counts_the_race_days_money(db):
    """The pool held $4,000 the evening before and $100,000 at midnight. The
    question is how much has come in TODAY, so the answer is $200,000 — not
    $296,000, which is mostly the market opening at all."""
    got = money.money_arrived(DATE, 3, conn=None)
    assert got["observed"] is True
    assert got["pool_then"] == 100_000
    assert got["arrived"] == 200_000
    assert got["opened"] == f"{DATE}T00:00:00"


def test_pool_growth_is_measured_from_midnight_too(db):
    got = money.pool_turnover(DATE, 3)
    assert got["pools"]["WIN"]["since_first"] == 200_000
    assert got["pools"]["WIN"]["growth_pct"] == pytest.approx(200.0, abs=0.1)

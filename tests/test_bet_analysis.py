"""What the ledger says about how the betting is going.

The design brief prints one rule across the whole analysis section:

    "EVERY FIGURE CARRIES n AND AN INTERVAL · A 12-BET SLICE IS NOT A FINDING"

These tests hold that rule to it. The point is not that the arithmetic is
right — it is that a slice with six bets in it cannot be read as a result, and
that the module says so rather than leaving the page to notice.
"""
from __future__ import annotations

import pytest

from hkrd.query import bet_analysis as ba
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """Four meetings, two races each, eight runners a race. Horse 1 is always
    the favourite at 2.0 and always wins; horse 8 is always 40.0 and never
    places."""
    path = tmp_path / "a.db"
    conn = get_conn(path)
    init_db(conn)
    dates = ("2026-05-01", "2026-05-08", "2026-05-15", "2026-05-22")
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": r, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650}
            for d in dates for r in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": d, "race_no": r, "horse_no": h,
             "horse_name": f"H{h}", "place": str(h), "draw": h,
             "win_odds": {1: 2.0, 2: 4.0, 3: 6.0}.get(h, 40.0)}
            for d in dates for r in (1, 2) for h in range(1, 9)])
    conn.close()
    return path


def _bet(conn, bet_id, date, race_no, *horses, stake=100.0, returned=0.0,
         bet_type="QIN", account="personal", source="legacy_log",
         placed_at=None, hit=None):
    conn.execute(
        "INSERT INTO bets (bet_id, race_date, race_no, bet_type, stake, "
        "returned, pnl, status, hit, account, source, placed_at) "
        "VALUES (?,?,?,?,?,?,?,'settled',?,?,?,?)",
        (bet_id, date, race_no, bet_type, stake, returned, returned - stake,
         hit if hit is not None else (1 if returned > 0 else 0),
         account, source, placed_at or f"{date}T12:00:00"))
    for h in horses:
        conn.execute(
            "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
            "is_banker) VALUES (?,?,?,0,0)", (bet_id, race_no, h))


# ── the interval ─────────────────────────────────────────────────────────────

def test_one_bet_gets_no_interval_rather_than_a_zero_width_one():
    assert ba.roi_interval([(100.0, 250.0)]) is None
    assert ba.roi_interval([]) is None


def test_the_interval_is_the_same_figure_twice():
    """A number quoted off the page has to still be that number tomorrow."""
    pairs = [(100.0, 0.0)] * 20 + [(100.0, 900.0)] * 3
    assert ba.roi_interval(pairs) == ba.roi_interval(pairs)


def test_a_long_shot_ledger_gets_an_asymmetric_interval():
    """A quinella paying 40-1 makes the per-bet return heavy-tailed. A normal
    interval would centre on the point estimate; the bootstrap does not."""
    pairs = [(100.0, 0.0)] * 40 + [(100.0, 4000.0)] * 2
    roi = (sum(p[1] for p in pairs) - 4200) / 4200
    lo, hi = ba.roi_interval(pairs)
    assert lo < roi < hi
    assert (hi - roi) > (roi - lo)          # the upside tail is the longer one


def test_a_slice_that_never_won_has_an_interval_pinned_at_total_loss():
    stats = ba.slice_stats([{"stake": 100.0, "returned": 0.0, "hit": 0}] * 8)
    assert stats["roi"] == -1.0
    assert stats["roi_ci"] == [-1.0, -1.0]
    assert stats["clears_zero"] is True     # losing everything IS a finding


# ── the rule the section is built on ─────────────────────────────────────────

def test_a_twelve_bet_slice_is_marked_thin(db):
    conn = get_conn(db)
    with transaction(conn):
        for i in range(12):
            _bet(conn, f"b{i}", "2026-05-01", 1, 1, 2, returned=250.0)
    stats = ba.by_type(conn=conn)[0]
    conn.close()
    assert stats["bets"] == 12
    assert stats["thin"] is True
    assert ba.THIN_BETS == 30


def test_no_slice_comes_back_without_n_and_an_interval(db):
    """The whole section is unreadable if one figure is a bare number."""
    conn = get_conn(db)
    with transaction(conn):
        for i in range(40):
            _bet(conn, f"b{i}", "2026-05-01", 1 + i % 2, 1, 2,
                 returned=250.0 if i % 4 == 0 else 0.0)
    a = ba.analysis(conn=conn)
    conn.close()

    slices = ([a["overall"]] + a["by_type"]
              + [a["all_up"]["all_up"], a["all_up"]["straight"]]
              + a["concentration"]["bands"]
              + [a["favourite"]["included"], a["favourite"]["excluded"]])
    for s in slices:
        assert "bets" in s and "thin" in s, s["label"]
        assert "roi_ci" in s, s["label"]
        if s["bets"] >= 2 and s["staked"]:
            assert s["roi_ci"] is not None, s["label"]


def test_a_profitable_slice_whose_interval_straddles_zero_does_not_clear_it(db):
    """One 40-1 winner in thirty bets shows +33% and means nothing."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "win", "2026-05-01", 1, 1, returned=4000.0)
        for i in range(29):
            _bet(conn, f"l{i}", "2026-05-01", 1, 8)
    stats = ba.slice_stats([dict(r) for r in conn.execute(
        "SELECT stake, returned, hit FROM bets")])
    conn.close()
    assert stats["roi"] > 0
    assert stats["clears_zero"] is False


# ── the slices ───────────────────────────────────────────────────────────────

def test_the_curve_carries_forward_by_meeting_not_by_bet(db):
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "a", "2026-05-01", 1, 1, stake=100.0, returned=300.0)
        _bet(conn, "b", "2026-05-01", 2, 1, stake=100.0, returned=0.0)
        _bet(conn, "c", "2026-05-08", 1, 1, stake=100.0, returned=0.0)
    c = ba.cumulative_pnl(conn=conn)
    conn.close()
    assert [s["race_date"] for s in c["series"]] == ["2026-05-01", "2026-05-08"]
    assert [s["cumulative"] for s in c["series"]] == [100.0, 0.0]
    assert c["peak"] == 100.0 and c["final"] == 0.0
    assert c["series"][0]["bets"] == 2       # a day, not a bet


def test_clv_is_measured_per_selection_because_a_ticket_has_no_one_price(db):
    """A quinella backs three horses at three prices. There is no single price
    the ticket was struck at."""
    conn = get_conn(db)
    with transaction(conn):
        for h, odds in ((1, 3.0), (2, 6.0), (3, 5.0)):
            conn.execute(
                "INSERT INTO odds_snapshots (race_date, race_no, horse_no, "
                "captured_at, win_odds) VALUES ('2026-05-01',1,?, "
                "'2026-05-01T11:00:00', ?)", (h, odds))
        _bet(conn, "q", "2026-05-01", 1, 1, 2, 3)
    v = ba.clv(conn=conn)
    conn.close()
    assert v["selections"] == 3              # three, not one
    # taken 3.0/6.0/5.0 against closes of 2.0/4.0/6.0
    assert v["average"] == pytest.approx((0.5 + 0.5 - 1 / 6) / 3, abs=1e-4)
    assert v["beat_close"] == 2


def test_a_bet_logged_after_the_meeting_is_not_priced(db):
    """426 of the 1,078 real bets carry a `placed_at` from days or weeks after
    the race — that is when the row was written. Pricing off it would compare
    a wager to a market that had already settled."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute(
            "INSERT INTO odds_snapshots (race_date, race_no, horse_no, "
            "captured_at, win_odds) VALUES ('2026-05-01',1,1,"
            "'2026-05-01T11:00:00',3.0)")
        _bet(conn, "late", "2026-05-01", 1, 1, placed_at="2026-05-20T02:00:00")
    v = ba.clv(conn=conn)
    conn.close()
    assert v["selections"] == 0
    assert v["of_selections"] == 1           # counted, not hidden


def test_the_all_up_comparison_is_restricted_to_the_days_it_was_struck(db):
    """Comparing all-ups against every straight bet in the ledger compares two
    different sets of days as well as two different bet shapes."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "chain", "2026-05-01", None, bet_type="ALLUP_QQP",
             stake=200.0, returned=0.0)
        _bet(conn, "same", "2026-05-01", 1, 1, stake=100.0, returned=300.0)
        _bet(conn, "other", "2026-05-22", 1, 1, stake=100.0, returned=900.0)
    a = ba.allup_vs_straight(conn=conn)
    conn.close()
    assert a["meetings"] == 1
    assert a["all_up"]["bets"] == 1
    assert a["straight"]["bets"] == 1        # not 2 — the other day is excluded
    assert a["straight"]["returned"] == 300.0


def test_an_all_up_is_not_assigned_a_concentration_band_it_does_not_have(db):
    """It spans races, so it has no single market."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "chain", "2026-05-01", None, bet_type="ALLUP_QQP", stake=200.0)
        _bet(conn, "single", "2026-05-01", 1, 1, stake=100.0)
    c = ba.by_concentration(conn=conn)
    conn.close()
    assert c["spanning_races"]["bets"] == 1
    assert sum(b["bets"] for b in c["bands"]) == 1


def test_a_ticket_counts_as_including_the_favourite_if_any_leg_did(db):
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "with", "2026-05-01", 1, 1, 8)     # horse 1 is the 2.0 fav
        _bet(conn, "without", "2026-05-01", 1, 5, 8)
    f = ba.favourite_split(conn=conn)
    conn.close()
    assert f["included"]["bets"] == 1 and f["excluded"]["bets"] == 1
    assert f["included"]["share"] == 0.5


# ── reconciliation ───────────────────────────────────────────────────────────

def test_a_reference_the_log_quotes_is_not_a_statement_that_was_read(db):
    """Reporting those as reconciled said all 1,078 bets were checked when two
    statements covering two of thirty meetings had been read."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "quoted", "2026-05-01", 1, 1)
        conn.execute("UPDATE bets SET bookie_ref = '2209' WHERE bet_id='quoted'")
        _bet(conn, "read", "2026-05-01", 2, 1, stake=60.0, returned=129.5)
        conn.execute("UPDATE bets SET bookie_ref = '2210' WHERE bet_id='read'")
        conn.execute(
            "INSERT INTO bet_statement_rows (bet_id, bookie_ref, source_file, "
            "stake, returned, imported_at) VALUES ('read','2210','apr.txt',"
            "60.0,129.5,'2026-08-01T00:00:00')")
    r = ba.reconciliation(conn=conn)
    conn.close()
    assert r["total"] == 2
    assert r["confirmed"] == 1
    assert r["quoted_not_read"] == 1
    assert r["disagrees"] == []


def test_a_block_the_ledger_and_the_statement_disagree_on_is_reported(db):
    """The case the section exists for. The legacy settler had refs 2217 and
    2218 at $0 returned while its own notes quoted credits of $80 and $40."""
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "half_a", "2026-05-01", 1, 1, stake=40.0, returned=0.0)
        _bet(conn, "half_b", "2026-05-01", 1, 1, stake=40.0, returned=0.0,
             bet_type="QPL")
        for bid in ("half_a", "half_b"):
            conn.execute("UPDATE bets SET bookie_ref='2217' WHERE bet_id=?", (bid,))
            conn.execute(
                "INSERT INTO bet_statement_rows (bet_id, bookie_ref, "
                "source_file, stake, returned, imported_at) VALUES "
                "(?,'2217','apr.txt',80.0,80.0,'2026-08-01T00:00:00')", (bid,))
    r = ba.reconciliation(conn=conn)
    conn.close()
    assert len(r["disagrees"]) == 1          # one BLOCK, not two rows
    d = r["disagrees"][0]
    assert d["bets"] == 2
    assert d["ledger"] == [80.0, 0.0] and d["statement"] == [80.0, 80.0]

"""Bet entry: what a ticket costs, what it is warned about, and what it writes.

Two things are pinned hard here because the design briefs give exact numbers for
them and a silent drift would be invisible on screen:

  * the combination table from design brief 07 §3.3, and
  * that a guardrail NEVER blocks a bet — it records an override and the bet is
    written either way.

The second is the one worth stating twice. A blocked bet leaves no trace, and
"reviewing which flags were overridden and how those bets performed is a genuine
analysis, and it's only possible if the override is logged rather than the bet
blocked."
"""
from __future__ import annotations

import pytest

from hkrd.jobs import place_bet
from hkrd.query import prebet
from hkrd.store import bets as bet_store
from hkrd.store.connect import get_conn, init_db, transaction
from hkrd.store import upsert

DATE = "2026-07-15"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """One 12-runner card with a priced latest snapshot, as the design uses."""
    path = tmp_path / "entry.db"
    conn = get_conn(path)
    init_db(conn)
    # The design's own card: KYRUS TREASURE 3.0 in, NOBLE PURSUIT out at 15.
    odds = [3.0, 4.9, 6.1, 7.5, 12.0, 15.0, 16.0, 18.0, 26.0, 32.0, 33.0, 51.0]
    names = ["KYRUS TREASURE", "CASA ROCHESTER", "OCEAN IMPACT", "FIREFOOT",
             "ROMANTIC LAOS", "NOBLE PURSUIT", "HARMONY GALAXY",
             "STORM RUNNER", "NORTHERN BEAST", "VOLCANIC SPARK", "SERANGOON",
             "FAST SPEED"]
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 3, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1800,
             "race_class": "4"}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 3, "horse_no": i, "horse_name": n,
             "draw": i, "win_odds": o, "jockey": "J MOREIRA"}
            for i, (n, o) in enumerate(zip(names, odds), start=1)])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 3, "horse_no": i,
             "captured_at": f"{DATE}T07:30:00+00:00", "win_odds": o,
             "place_odds": round(1 + o / 4, 1)}
            for i, o in enumerate(odds, start=1)])
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── combinations ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("picks,expected", [(4, 6), (5, 10), (6, 15)])
def test_pair_pool_combinations_match_the_brief(picks, expected):
    """Design brief 07 §3.3: no banker, n picks, C(n,2)."""
    assert prebet.combination_count("QIN", picks) == expected


@pytest.mark.parametrize("legs,expected", [(4, 4), (5, 5)])
def test_banker_combinations_match_the_brief(legs, expected):
    """A banker appears in every line, so it multiplies rather than combines."""
    assert prebet.combination_count("QIN", legs, has_banker=True) == expected


def test_a_pair_pool_needs_two_selections():
    assert prebet.combination_count("QPL", 1) == 0


def test_win_is_one_line_per_selection():
    assert prebet.combination_count("WIN", 3) == 3


def test_all_up_formulas_are_generated_not_memorised():
    """Design brief 07 §4's table: 5 races gives 5x1, 4x5, 3x10, 2x10."""
    got = [(f["legs"], f["combinations"]) for f in prebet.formulas(5)]
    assert got == [(5, 1), (4, 5), (3, 10), (2, 10)]


def test_a_single_race_cannot_form_a_chain():
    assert prebet.formulas(1) == []


# ─── the place transform ──────────────────────────────────────────────────────

def test_place_percentage_is_harville_not_the_three_times_rule(db):
    """The rule of thumb overstates a short banker, and the gap is reported.

    This is the 34-point finding made visible rather than silently corrected:
    the design shows both numbers side by side so the rule the user would
    otherwise reach for can be seen failing.
    """
    card = prebet.entry_card(DATE, 3)
    fav = next(r for r in card["runners"] if r["horse_no"] == 1)
    assert fav["place_pct"] is not None
    assert fav["linear_pct"] > fav["place_pct"]
    assert fav["gap_points"] == pytest.approx(
        fav["linear_pct"] - fav["place_pct"], abs=0.11)


def test_place_odds_are_scraped_never_derived(db):
    """No runner's place odd is a function of its win odd."""
    card = prebet.entry_card(DATE, 3)
    ratios = {round(r["win_odds"] / r["place_odds"], 2)
              for r in card["runners"] if r["place_odds"]}
    assert len(ratios) > 1, "a single ratio would mean place was computed"


# ─── guardrails warn, they never block ────────────────────────────────────────

def test_ceiling_flag_fires_without_making_the_ticket_unplaceable(db):
    conn = get_conn(db)
    bet_store.set_setting(conn, "raceday_ceiling", 100.0)
    conn.close()
    t = prebet.evaluate(DATE, bet_type="QIN", race_no=3, selections=[1, 2, 3],
                        unit_stake=200.0, account="brett")
    assert any(f["flag"] == "raceday_ceiling" for f in t["flags"])
    assert t["placeable"] is True


def test_excluding_the_favourite_is_flagged(db):
    t = prebet.evaluate(DATE, bet_type="QIN", race_no=3, selections=[5, 6, 7],
                        unit_stake=10.0, account="brett")
    assert any(f["flag"] == "favourite_excluded" for f in t["flags"])
    assert t["placeable"] is True


def test_a_non_favourite_banker_in_qin_is_flagged_toward_qpl(db):
    t = prebet.evaluate(DATE, bet_type="QIN", race_no=3, selections=[1, 2],
                        banker=6, unit_stake=10.0, account="brett")
    assert any(f["flag"] == "non_fav_banker_qin" for f in t["flags"])


def test_an_incoherent_ticket_says_why_rather_than_throwing(db):
    t = prebet.evaluate(DATE, bet_type="QIN", race_no=3, selections=[1],
                        unit_stake=10.0, account="brett")
    assert t["placeable"] is False
    assert t["reason"]


def test_the_banker_is_never_also_a_leg(db):
    """Counting the anchor twice is how a combination count silently doubles."""
    t = prebet.evaluate(DATE, bet_type="QIN", race_no=3, selections=[1, 2, 3],
                        banker=1, unit_stake=10.0, account="brett")
    assert t["selections"] == [2, 3]
    assert t["combinations"] == 2


# ─── writing ──────────────────────────────────────────────────────────────────

def test_placing_a_flagged_bet_writes_it_and_records_the_override(db):
    conn = get_conn(db)
    bet_store.set_setting(conn, "raceday_ceiling", 50.0)
    conn.close()

    out = place_bet.place(DATE, bet_type="QIN", account="brett", race_no=3,
                          selections=[1, 2, 3], unit_stake=100.0,
                          acknowledged=["raceday_ceiling"], db=str(db))

    assert out["stake"] == 300.0
    assert out["overrides_logged"] == ["raceday_ceiling"]

    conn = get_conn(db)
    try:
        assert conn.execute("SELECT count(*) FROM bets").fetchone()[0] == 1
        assert bet_store.overrides_for(conn, [out["bet_id"]])[out["bet_id"]]
        assert conn.execute(
            "SELECT count(*) FROM bet_selections WHERE bet_id = ?",
            (out["bet_id"],)).fetchone()[0] == 3
    finally:
        conn.close()


def test_an_unacknowledged_flag_still_writes_the_bet(db):
    """The bet is never blocked. It is reported back as un-acknowledged."""
    conn = get_conn(db)
    bet_store.set_setting(conn, "raceday_ceiling", 50.0)
    conn.close()
    out = place_bet.place(DATE, bet_type="QIN", account="brett", race_no=3,
                          selections=[1, 2], unit_stake=100.0, db=str(db))
    assert "raceday_ceiling" in out["flags_unacknowledged"]
    assert out["overrides_logged"] == []
    assert out["bet_id"]


def test_a_banker_is_stored_as_the_banker(db):
    out = place_bet.place(DATE, bet_type="QPL", account="kelvin", race_no=3,
                          selections=[2, 3], banker=1, unit_stake=10.0,
                          db=str(db))
    conn = get_conn(db)
    try:
        rows = conn.execute(
            "SELECT horse_no, is_banker FROM bet_selections WHERE bet_id = ? "
            "ORDER BY horse_no", (out["bet_id"],)).fetchall()
    finally:
        conn.close()
    assert [(r["horse_no"], r["is_banker"]) for r in rows] == [(1, 1), (2, 0), (3, 0)]


def test_an_unknown_account_is_refused(db):
    with pytest.raises(ValueError, match="unknown account"):
        place_bet.place(DATE, bet_type="WIN", account="client", race_no=3,
                        selections=[1], unit_stake=10.0, db=str(db))


def test_an_all_up_spans_races_and_stores_its_formula(db, tmp_path):
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 4, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 4, "horse_no": i,
             "horse_name": f"OTHER {i}", "draw": i, "win_odds": 5.0}
            for i in range(1, 9)])
    conn.close()

    out = place_bet.place(
        DATE, bet_type="ALLUP", account="brett", unit_stake=10.0,
        legs=[{"race_no": 3, "bet_type": "WIN", "selections": [1]},
              {"race_no": 4, "bet_type": "WIN", "selections": [2]}],
        legs_required=2, db=str(db))

    assert out["bet_type"] == "ALLUP_2x1"
    assert out["combinations"] == 1
    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT race_no FROM bets WHERE bet_id = ?",
            (out["bet_id"],)).fetchone()["race_no"] is None
        legs = conn.execute(
            "SELECT DISTINCT leg_no FROM bet_selections WHERE bet_id = ? "
            "ORDER BY leg_no", (out["bet_id"],)).fetchall()
    finally:
        conn.close()
    assert [r["leg_no"] for r in legs] == [1, 2]


def test_raceday_total_counts_what_is_already_staked(db):
    place_bet.place(DATE, bet_type="WIN", account="brett", race_no=3,
                    selections=[1], unit_stake=80.0, db=str(db))
    day = prebet.raceday_total(DATE, account="brett")
    assert day["staked"] == 80.0
    assert day["remaining"] == day["ceiling"] - 80.0


def test_accounts_are_brett_and_kelvin_only():
    """Design brief 07 §3.1 removed Client entirely."""
    assert [a["key"] for a in prebet.ACCOUNTS] == ["brett", "kelvin"]

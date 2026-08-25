"""The bets ledger, and the backed-versus-missed join it exists for.

Design brief 06 Part 1: "A blackbook horse ran and was not backed. This must be
recorded, and it is the single most important feature on the page... The system
knows when a booked horse is declared, so it can detect a non-bet automatically
— the user shouldn't have to remember to record an absence."

So the tests here are mostly about the JOIN being right. A missed run is a run
with no matching selection; nothing is logged, and nothing can be forgotten.
"""
from __future__ import annotations

import json

import pytest

from hkrd.jobs import import_bets
from hkrd.query import bets as bq
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """One horse booked, four runs since, two of them backed."""
    path = tmp_path / "b.db"
    conn = get_conn(path)
    init_db(conn)
    dates = ("2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01")
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650} for d in dates])
        for date, place in zip(dates, (3, 1, 5, 1, 7)):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": 1,
                 "horse_name": "FAST ONE", "place": str(place), "win_odds": 5.0,
                 "draw": 1}]
                + [{"race_date": date, "race_no": 1, "horse_no": i,
                    "horse_name": f"FILLER {i}", "place": str(i + 1),
                    "win_odds": 10.0, "draw": i} for i in range(2, 8)])
        conn.execute(
            "INSERT INTO blackbook (id, horse_name, added_date, status) "
            "VALUES ('bb_1', 'FAST ONE', '2026-04-15', 'active')")
    conn.close()
    return path


def _log(tmp_path, rows):
    p = tmp_path / "bets.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def _bet(**kw):
    base = {"bet_id": "b1", "meeting_date": "20260501", "venue": "HV",
            "race_number": 1, "bet_type": "QIN", "selections": [1, 2],
            "banker": None, "stake_hkd": 100.0, "return_hkd": 0.0,
            "pnl_hkd": -100.0, "status": "settled", "hit": False}
    base.update(kw)
    return base


# ── import ───────────────────────────────────────────────────────────────────

def test_selections_are_normalised_so_they_can_be_joined(tmp_path, db):
    """A JSON list of horse numbers cannot be joined to a blackbook entry, and
    that join is the entire reason for importing this."""
    src = _log(tmp_path, [_bet(selections=[1, 2, 5])])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    rows = [tuple(r) for r in conn.execute(
        "SELECT race_no, horse_no FROM bet_selections ORDER BY horse_no")]
    conn.close()
    assert rows == [(1, 1), (1, 2), (1, 5)]


def test_the_compact_legacy_date_is_coerced(tmp_path, db):
    src = _log(tmp_path, [_bet(meeting_date="20260501")])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    assert conn.execute("SELECT race_date FROM bets").fetchone()[0] == "2026-05-01"
    conn.close()


def test_an_all_up_leg_lands_in_its_own_race(tmp_path, db):
    """`legs` carries two meanings under one name. An ALLUP's legs are dicts,
    each a RACE; putting their horses in the bet's own race number would file
    every leg under the first one."""
    src = _log(tmp_path, [_bet(
        bet_type="ALLUP_WP", selections=[], race_number=1,
        legs=[{"race_number": 3, "banker": None, "selections": [4]},
              {"race_number": 6, "banker": 2, "selections": [2, 9]}])])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    rows = [tuple(r) for r in conn.execute(
        "SELECT leg_no, race_no, horse_no, is_banker FROM bet_selections "
        "ORDER BY leg_no, horse_no")]
    race_no = conn.execute("SELECT race_no FROM bets").fetchone()[0]
    conn.close()
    assert rows == [(1, 3, 4, 0), (2, 6, 2, 1), (2, 6, 9, 0)]
    assert race_no is None       # an all-up spans races and has none of its own


def test_a_quartet_multi_banker_keeps_its_single_race(tmp_path, db):
    """QTT_MB's `legs` are POSITION groups inside one race, not races. Treating
    them like all-up legs would scatter one ticket across four races."""
    src = _log(tmp_path, [_bet(
        bet_type="QTT_MB", race_number=4, selections=[1, 6, 9],
        legs=[[1, 6], [1, 6, 9], [1, 9], [6, 9]])])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    races = {r[0] for r in conn.execute("SELECT race_no FROM bet_selections")}
    bet_race = conn.execute("SELECT race_no FROM bets").fetchone()[0]
    conn.close()
    assert races == {4} and bet_race == 4


def test_a_banker_outside_the_selection_list_is_still_a_selection(tmp_path, db):
    src = _log(tmp_path, [_bet(selections=[2, 5], banker=1)])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT horse_no, is_banker FROM bet_selections")}
    conn.close()
    assert rows == {1: 1, 2: 0, 5: 0}


def test_import_is_idempotent(tmp_path, db):
    src = _log(tmp_path, [_bet(), _bet(bet_id="b2", bet_type="QPL")])
    first = import_bets.run(src, db=db)
    second = import_bets.run(src, db=db)
    assert (first.bets, first.selections) == (second.bets, second.selections) == (2, 4)


# ── backed vs missed ─────────────────────────────────────────────────────────

@pytest.fixture()
def booked(tmp_path, db):
    """FAST ONE backed on 2026-05-01 (won) and 2026-06-01 (lost); its
    2026-07-01 win and 2026-08-01 run were not backed."""
    src = _log(tmp_path, [
        _bet(bet_id="b1", meeting_date="20260501", selections=[1, 2],
             return_hkd=250.0, pnl_hkd=150.0, hit=True),
        _bet(bet_id="b2", meeting_date="20260601", selections=[1, 3]),
    ])
    import_bets.run(src, db=db)
    return db


def test_a_run_with_no_matching_bet_is_a_miss_without_anyone_logging_it(booked):
    conn = get_conn(booked)
    out = bq.backed_and_missed(conn=conn)
    conn.close()
    # Four runs after 2026-04-15; two carry a bet, two do not.
    assert out["runs"] == 4
    assert out["backed"]["runs"] == 2
    assert out["missed"]["runs"] == 2


def test_the_run_before_the_booking_is_not_in_the_comparison(booked):
    """2026-04-01 is before the entry was added. Counting it would let a bet
    placed before the thesis existed argue for the thesis."""
    conn = get_conn(booked)
    out = bq.backed_and_missed(conn=conn)
    conn.close()
    assert out["runs"] == 4          # not 5


def test_both_sides_are_priced_at_the_same_notional_stake(booked):
    """The real ledger is quinellas and multi-leg tickets. Comparing those
    against a notional win bet would measure the bet type, not the selection."""
    conn = get_conn(booked)
    out = bq.backed_and_missed(conn=conn)
    conn.close()
    assert out["backed"]["notional"] and out["missed"]["notional"]
    assert out["notional_stake"] == bq.NOTIONAL_STAKE
    # Both sides: 2 runs at 5.0, one winner each → same notional return.
    assert out["backed"]["staked"] == out["missed"]["staked"]


def test_the_strike_rate_travels_with_the_price_that_implied_it(booked):
    """A strike rate alone reads as skill. Beside the implied rate it reads as
    what it is — over the real book both sides land within a point of theirs."""
    conn = get_conn(booked)
    out = bq.backed_and_missed(conn=conn)
    conn.close()
    for side in ("backed", "missed"):
        d = out[side]
        assert d["median_odds"] == 5.0
        assert d["implied_rate"] == 0.2
        assert d["vs_implied"] == pytest.approx(d["strike_rate"] - 0.2, abs=1e-9)


def test_what_was_actually_staked_is_reported_separately(booked):
    """The notional comparison answers "was the selection any good"; the actual
    ledger answers "what did this cost". They are different questions."""
    conn = get_conn(booked)
    out = bq.backed_and_missed(conn=conn)
    conn.close()
    assert out["actual"]["bets"] == 2
    assert out["actual"]["staked"] == 200.0
    assert out["actual"]["returned"] == 250.0
    assert out["actual"]["roi"] == pytest.approx(0.25)


def test_one_entry_can_be_asked_about_on_its_own(booked):
    conn = get_conn(booked)
    whole = bq.backed_and_missed(conn=conn)
    one = bq.backed_and_missed(entry_id="bb_1", conn=conn)
    missing = bq.backed_and_missed(entry_id="bb_nope", conn=conn)
    conn.close()
    assert one["runs"] == whole["runs"]      # only one entry in this book
    assert missing["runs"] == 0


# ── ledger ───────────────────────────────────────────────────────────────────

def test_the_ledger_names_the_horses_a_bet_backed(booked):
    conn = get_conn(booked)
    rows = bq.ledger(conn=conn)
    conn.close()
    names = {s["horse_name"] for b in rows for s in b["selections"]}
    assert "FAST ONE" in names


def test_a_horse_lookup_matches_on_the_runner_row_not_a_typed_name(booked):
    """The slip carries a number, not a name. Matching through the runner row
    is what makes a bet attributable to a horse at all."""
    conn = get_conn(booked)
    rows = bq.bets_for_horse("fast one", conn=conn)
    conn.close()
    assert len(rows) == 2
    assert {r["bet_type"] for r in rows} == {"QIN"}


def test_an_all_up_still_shows_up_in_the_race_it_passes_through(tmp_path, db):
    """An all-up has no race number of its own, so a bet touching race 6 would
    vanish from race 6 without the join through its legs."""
    src = _log(tmp_path, [_bet(
        bet_id="au", bet_type="ALLUP_WP", selections=[], meeting_date="20260501",
        legs=[{"race_number": 1, "banker": None, "selections": [1]}])])
    import_bets.run(src, db=db)
    conn = get_conn(db)
    rows = bq.bets_for_race("2026-05-01", 1, conn=conn)
    conn.close()
    assert [r["bet_id"] for r in rows] == ["au"]


def test_the_summary_splits_by_bet_type(booked):
    conn = get_conn(booked)
    s = bq.summary(conn=conn)
    conn.close()
    assert s["bets"] == 2 and s["staked"] == 200.0
    assert s["roi"] == pytest.approx(0.25)
    assert [t["bet_type"] for t in s["by_type"]] == ["QIN"]

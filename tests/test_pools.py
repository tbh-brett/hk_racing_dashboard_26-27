"""The pools price the questions the model used to estimate.

Until the capture moved off Playwright this project had win odds and nothing
else, so a place probability and a pair probability were both inferred from
them by Harville-Henery. One GraphQL call now brings back WIN, PLA, QIN and QPL
together, and three of those answer directly.

What is pinned here is the ORDER OF AUTHORITY and that it is always visible:
the pool where one was captured, the model where one was not, and every figure
saying which of the two it was. A number sourced two ways and labelled once is
how two surfaces end up disagreeing with nothing to tell them apart.

The model is not deleted and is not hidden — it is the benchmark. It is the
only reading on the model that does not have to wait for a result.
"""
from __future__ import annotations

import pytest

from hkrd.derive.probability import (
    devig_to, market_pair_probability, market_place_probability, pair_hits,
)
from hkrd.query import pools
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-07-15"
WIN = [3.0, 4.9, 6.1, 7.5, 12.0, 15.0, 16.0, 18.0, 26.0, 32.0]
# Deliberately NOT a fixed multiple of the win price: the two are separate
# pools with separate money in them, and the whole reason place is captured
# rather than derived is that no single ratio reproduces it.
PLACE = [1.4, 1.9, 2.3, 2.6, 3.4, 4.6, 3.9, 5.2, 6.8, 8.1]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A ten-runner race with a win, place and quinella-place capture."""
    path = tmp_path / "pools.db"
    conn = get_conn(path)
    init_db(conn)
    at = f"{DATE}T12:40:00"
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650},
            {"race_date": DATE, "race_no": 2, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": r, "horse_no": i,
             "captured_at": at, "win_odds": w, "place_odds": p}
            for r in (1, 2)
            for i, (w, p) in enumerate(zip(WIN, PLACE), start=1)])
        # Only race 1 has a pair pool captured. Race 2 is the fallback case —
        # a field too small for HKJC to run one, or a capture that has not
        # reached the pair ladder yet.
        upsert.upsert_odds_pairs(conn, [
            {"race_date": DATE, "race_no": 1, "pool": "QPL", "horse_a": a,
             "horse_b": b, "captured_at": at, "odds": round(2.0 + a + b, 1)}
            for a in range(1, 11) for b in range(a + 1, 11)])
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── the transforms ───────────────────────────────────────────────────────────

def test_a_pool_is_normalised_to_how_many_of_its_outcomes_come_true():
    """One horse wins; three place; three PAIRS are among the first three.

    Getting the target wrong scales every probability by a constant, which is
    invisible in a ranking and wrong everywhere a figure is read as a percent.
    """
    assert pair_hits(3) == 3 and pair_hits(2) == 1
    assert sum(devig_to([2.0, 4.0, 8.0], 1.0)) == pytest.approx(1.0)
    assert sum(devig_to([2.0, 4.0, 8.0], 3.0)) == pytest.approx(3.0)


def test_a_price_the_pool_did_not_quote_is_absent_not_zero():
    """A scratched runner has no place price. Zero is a claim about it that
    the pool never made, and it would drag the normalisation with it."""
    got = market_place_probability([1.5, 2.0, None, 6.0], places=3)
    assert got[2] != got[2]                       # NaN
    assert all(v == v for v in [got[0], got[1], got[3]])

    pairs = market_pair_probability({(1, 2): 4.0, (1, 3): None}, hits=3)
    assert list(pairs) == [(1, 2)]


def test_a_probability_never_normalises_past_certainty():
    """Proportional de-vigging assumes the takeout is spread evenly, and in a
    place pool it is not — the short prices carry less of it. Clipped, because
    a place probability of 112% is not readable as a percentage."""
    assert market_place_probability([1.05, 30.0, 40.0, 50.0],
                                    places=3).max() <= 1.0


# ─── which answer wins ────────────────────────────────────────────────────────

def test_the_place_pool_answers_and_the_model_comes_back_beside_it(db):
    got = pools.place_probabilities(DATE, 1)
    assert got["places"] == 3 and got["from_pool"] == len(WIN)
    for r in got["runners"]:
        assert r["place_source"] == "place pool"
        assert r["place_pct"] == r["market_pct"]
        assert r["model_pct"] is not None
        assert r["gap_points"] == pytest.approx(
            round(r["market_pct"] - r["model_pct"], 1), abs=0.11)


def test_two_horses_at_one_win_price_can_have_different_place_chances(db):
    """The reason the pool is worth reading at all.

    Harville works from the win odds, so two runners quoted the same to win are
    the same to place by construction. The place pool disagrees, because it has
    its own money in it — which is a fact about the race the model cannot see.
    """
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": 6,
             "captured_at": f"{DATE}T12:40:00", "win_odds": 15.0,
             "place_odds": 4.6},
            {"race_date": DATE, "race_no": 1, "horse_no": 7,
             "captured_at": f"{DATE}T12:40:00", "win_odds": 15.0,
             "place_odds": 3.9}])
    conn.close()

    rows = {r["horse_no"]: r for r in pools.place_probabilities(DATE, 1)["runners"]}
    assert rows[6]["win_odds"] == rows[7]["win_odds"] == 15.0
    assert rows[6]["model_pct"] == rows[7]["model_pct"]
    assert rows[6]["place_pct"] < rows[7]["place_pct"]


def test_the_pairs_are_ranked_in_the_pool_that_pays_them(db):
    ranked = pools.ranked_pairs(DATE, 1, pool="QPL")
    assert [r["pool"] for r in ranked] == ["QPL"] * len(ranked)
    assert [r["prob"] for r in ranked] == sorted(
        (r["prob"] for r in ranked), reverse=True)
    # The pool's own price comes back beside the probability it was derived
    # from, so the ranking is checkable against the thing it was read off.
    assert all(r["odds"] for r in ranked)


def test_no_pair_pool_falls_back_to_the_model_and_says_so(db):
    """Race 2 has win and place captured and no pair pool at all — a field of
    six has no quinella place pool to capture, and that is a fact about the
    race rather than a failed capture."""
    got = pools.pair_probabilities(DATE, 2, pool="QPL")
    assert got["source"] == "model"
    assert "Harville" in got["note"]
    assert got["pairs"]
    assert all(r["pool"] == "model" for r in pools.ranked_pairs(DATE, 2))


def test_the_quinella_and_the_quinella_place_are_different_questions(db):
    """QPL is "both in the first three"; QIN is "these two, first and second".

    A pair that is short in one and long in the other is not a disagreement,
    and ranking a quinella-place ticket on a quinella number recommends a
    different set of pairs.
    """
    with pytest.raises(ValueError):
        pools.pair_probabilities(DATE, 1, pool="TRIO")
    qpl = pools.pair_probabilities(DATE, 1, pool="QPL")
    qin = pools.pair_probabilities(DATE, 1, pool="QIN")
    assert qpl["source"] == "QPL" and qin["source"] == "model"
    assert sum(qpl["pairs"].values()) > sum(qin["pairs"].values())


def test_a_race_with_no_price_at_all_says_so_rather_than_returning_a_number(db):
    got = pools.place_probabilities(DATE, 9)
    assert got["runners"] == [] and got["priced_pool"] is False
    assert "fewer than two priced runners" in got["note"]
    assert pools.ranked_pairs(DATE, 9) == []

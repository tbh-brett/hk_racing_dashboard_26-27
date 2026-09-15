"""Is the model calibrated, and is there anything to bet on.

Two questions, and they are not the same one. A model can be profitable and
badly calibrated, or perfectly calibrated and unprofitable; reporting only the
second is how a filter that got lucky over forty races becomes a rule.

The tests that matter here are the ones that stop a false positive: the split
is by date and not by row, a bin below the minimum is marked, and a weight at
which the model IS the market returns no value bets rather than the ROI of
picking at random.
"""
from __future__ import annotations

import pytest

from hkrd.model import backtest as bt
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """Twelve meetings, one race each, eight runners. Horse 1 is the 2.0
    favourite and wins two thirds of the time — a market that is roughly
    right, which is what the archive's is."""
    path = tmp_path / "b.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        for d in range(12):
            date = f"2026-0{1 + d // 4}-{(d % 4) * 7 + 1:02d}"
            upsert.upsert_races(conn, [
                {"race_date": date, "race_no": 1, "venue": "ST", "course": "A",
                 "surface": "Turf", "going": "G", "distance": 1200}])
            winner = 1 if d % 3 else 5
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": h,
                 "horse_name": f"H{h}", "draw": h,
                 "place": "1" if h == winner else str(h + 1),
                 "win_odds": 2.0 if h == 1 else 10.0}
                for h in range(1, 9)])
            conn.executemany(
                "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, "
                "sarr_rank, derive_version) VALUES (?, 1, ?, ?, ?, 't')",
                [(date, h, float(h), h) for h in range(1, 9)])
    conn.close()
    return path


def test_a_race_missing_odds_or_a_winner_is_left_out(db):
    """The two requirements that are still requirements. A race missing one
    runner's odds cannot be de-vigged -- the overround IS the gap between the
    book and 100%, so a book missing a runner has a gap that is partly the
    missing runner -- and one with no winner cannot be evaluated."""
    conn = get_conn(db)
    before = len(bt.races_for_backtest(conn=conn))
    with transaction(conn):
        conn.execute("UPDATE runners SET win_odds = NULL "
                     "WHERE race_date = '2026-01-01' AND horse_no = 4")
        conn.execute("UPDATE runners SET place = '2' "
                     "WHERE race_date = '2026-01-15' AND place = '1'")
    after = bt.races_for_backtest(conn=conn)
    conn.close()
    assert len(after) == before - 2
    dates = {r["race_date"] for r in after}
    assert {"2026-01-01", "2026-01-15"}.isdisjoint(dates)


def test_a_race_with_an_unrated_runner_is_measured_not_dropped(db):
    """The selection this replaces. "Every runner rated" means "no debutant
    declared", which is a property of the CARD and not of the model -- 65.2% of
    the archive fails it, so the table asking whether the model beats the price
    was answering on the third of races that happen to carry no newcomer."""
    conn = get_conn(db)
    before = len(bt.races_for_backtest(conn=conn))
    with transaction(conn):
        conn.execute("DELETE FROM runner_sarr WHERE race_date = '2026-01-08' "
                     "AND horse_no = 2")
    after = bt.races_for_backtest(conn=conn)
    conn.close()
    assert len(after) == before
    kept = next(r for r in after if r["race_date"] == "2026-01-08")
    # And it says what it is measuring on, rather than leaving the reader to
    # assume a full field.
    assert kept["rated"] == kept["runners"] - 1


def test_a_race_with_one_rated_runner_is_left_out(db):
    """A softmax over one runner is 1.0 whatever its score, which is not an
    opinion. `fit_blend` draws the floor in the same place."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_sarr WHERE race_date = '2026-01-08' "
                     "AND horse_no > 1")
    after = bt.races_for_backtest(conn=conn)
    conn.close()
    assert "2026-01-08" not in {r["race_date"] for r in after}


def test_an_unrated_runner_is_held_at_its_market_price_at_every_weight(db):
    """`w*m + (1-w)*m` is `m`. The honest reading of "unrated": not a horse
    with no chance, which is what dropping the race said, and not the field
    average, which is a number nobody measured."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_sarr WHERE race_date = '2026-01-08' "
                     "AND horse_no = 2")
    race = next(r for r in bt.races_for_backtest(conn=conn)
                if r["race_date"] == "2026-01-08")
    conn.close()
    field = race["field"]
    market = bt.blend_m.market_probability([f["win_odds"] for f in field])
    i = next(n for n, f in enumerate(field) if f["sarr"] is None)
    for weight in (0.0, 0.25, 1.0):
        p = bt._probabilities(field, weight)
        assert p[i] == pytest.approx(market[i], abs=1e-9)
        assert p.sum() == pytest.approx(1.0, abs=1e-9)


def test_the_split_is_by_date_never_by_row(db):
    """Two runners in the same race are not independent observations: if one
    wins the others did not. Splitting between them leaks the answer across
    the boundary."""
    conn = get_conn(db)
    w = bt.walk_forward(conn=conn)
    races = bt.races_for_backtest(conn=conn)
    conn.close()
    train = {r["race_date"] for r in races if r["race_date"] < w["split_date"]}
    test = {r["race_date"] for r in races if r["race_date"] >= w["split_date"]}
    assert train and test
    assert not (train & test)          # no meeting appears on both sides
    assert w["train_races"] + w["test_races"] == w["races"]


def test_at_the_fitted_weight_there_are_no_value_bets_by_construction(db):
    """The fitted weight on the fundamental stream is zero, so the blended
    probability IS the de-vigged market. Without a floor on the comparison,
    the two are the same number reached by two code paths, differ in their
    last bits, and half the field reads as value — which returned 882 "value
    bets" on the real archive at a weight where there are none, and with them
    the ROI of picking at random."""
    conn = get_conn(db)
    races = bt.races_for_backtest(conn=conn)
    conn.close()
    v = bt.value_bets(races, weight=0.0)
    assert v["bets"] == 0
    assert v["roi"] is None


def test_a_thin_calibration_bin_is_marked_rather_than_dropped(db):
    """Removing it hides how much of the curve is unsupported."""
    conn = get_conn(db)
    races = bt.races_for_backtest(conn=conn)
    conn.close()
    c = bt.calibration(races)
    assert c["bins"]
    assert all("thin" in b and "ci" in b for b in c["bins"])
    assert c["min_bin"] == bt.MIN_BIN
    # The flag is the count, not a judgement: 96 runners over 12 races, and
    # the seven non-favourites in each share one price, so one bin carries 84
    # of them and the favourite's bin carries 12.
    for b in c["bins"]:
        assert b["thin"] is (b["runners"] < bt.MIN_BIN)
    assert any(b["thin"] for b in c["bins"])


def test_a_bin_whose_prediction_falls_outside_its_interval_is_flagged(db):
    """That is a miscalibration, not a near miss."""
    conn = get_conn(db)
    races = bt.races_for_backtest(conn=conn)
    conn.close()
    c = bt.calibration(races)
    for b in c["bins"]:
        if b["ci"]:
            inside = b["ci"][0] <= b["predicted"] <= b["ci"][1]
            assert b["off"] is not inside


def test_the_market_is_returned_as_the_thing_to_beat(db):
    """A model's calibration alone says nothing about whether it is worth
    having. The comparison is the price."""
    conn = get_conn(db)
    w = bt.walk_forward(conn=conn, weight=1.0)
    conn.close()
    assert w["market_calibration"]["weight"] == 0.0
    assert w["calibration"]["weight"] == 1.0


def test_an_empty_archive_reports_unusable_rather_than_zero(db, tmp_path):
    """A backtest over nothing is not a backtest that found nothing."""
    empty = tmp_path / "empty.db"
    conn = get_conn(empty)
    init_db(conn)
    conn.close()
    conn = get_conn(empty)
    w = bt.walk_forward(conn=conn)
    conn.close()
    assert w["usable"] is False and w["races"] == 0


def test_value_bets_compare_against_the_de_vigged_price_not_the_raw_one(db):
    """Comparing against raw 1/odds makes every runner look overpriced by the
    overround, and calls the whole field a value bet."""
    conn = get_conn(db)
    races = bt.races_for_backtest(conn=conn)
    conn.close()
    # Weight 1.0 puts everything on the fundamental stream, which is the most
    # disagreement available — and it still cannot pick the whole field.
    v = bt.value_bets(races, weight=1.0)
    runners = sum(len(r["field"]) for r in races)
    assert 0 < v["bets"] < runners


# ── one definition of the fundamental stream ─────────────────────────────────

def test_the_backtest_builds_the_same_stream_the_page_and_the_fit_do(db):
    """Three callers wanted "the softmax, scaled to the market's own share of
    the runners it rated", and the one that worked it out differently was this
    module -- which is why it dropped the races instead."""
    import inspect
    from hkrd.jobs import fit_blend
    from hkrd.query import model as model_q
    for module in (bt, fit_blend, model_q):
        assert "fundamental_for_race" in inspect.getsource(module), \
            module.__name__


def test_a_fully_rated_field_returns_exactly_what_it_returned_before(db):
    """The published figures were fitted on fully rated fields. If the widening
    had moved those races too, every constant in the repo would be wrong rather
    than merely measured on a narrower population."""
    conn = get_conn(db)
    race = next(r for r in bt.races_for_backtest(conn=conn))
    conn.close()
    field = race["field"]
    assert all(f["sarr"] is not None for f in field)
    market = bt.blend_m.market_probability([f["win_odds"] for f in field])
    plain = bt.blend_m.fundamental_probability([f["sarr"] for f in field])
    shared = bt.blend_m.fundamental_for_race([f["sarr"] for f in field], market)
    for a, b in zip(plain, shared):
        assert a == pytest.approx(b, abs=1e-12)


def test_the_payload_says_which_population_it_measured(db):
    """Never a bare number. A table whose selection changed has to carry the
    selection, or it cannot be compared with the version a reader remembers."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_sarr WHERE race_date = '2026-02-22' "
                     "AND horse_no = 3")
    out = bt.walk_forward(conn=conn)
    conn.close()
    cov = out["coverage"]
    assert cov["races"] == out["test_races"]
    assert cov["runners_rated"] <= cov["runners"]
    assert cov["races_with_an_unrated_runner"] >= 0


def test_the_published_block_is_labelled_with_the_selection_that_made_it():
    """`MEASURED` was produced under the old rule and cannot be recomputed
    without the real archive. Left unlabelled beside the live figures it reads
    as a disagreement rather than as a different population."""
    assert "population" in bt.MEASURED
    assert "pre-2026-09-15" in bt.MEASURED["population"]

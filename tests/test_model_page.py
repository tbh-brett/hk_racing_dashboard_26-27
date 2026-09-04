"""SARR components, the blend, and what the Model Analysis page is allowed to claim.

Design brief 05 §5 asks the page to show WHY each model ranked each horse where
it did, and to double as documentation of the model's own limitations. Both are
testable: the components must sum to the score they sit beside, and the blend's
published calibration must be the one the fitting job actually produces.
"""
from __future__ import annotations

import numpy as np
import pytest

from hkrd.jobs import rebuild_sarr
from hkrd.model import blend, sarr
from hkrd.query import model
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def profile():
    return {"fmrp": 1.5, "lsa": -0.4, "esz": 0.2, "traj": 0.8, "avg_ssi": -0.1,
            "style": "Closer", "rating": 60.0, "place_rate": 0.25,
            "place_rate_band": None, "n_runs": 6}


# ── the model ────────────────────────────────────────────────────────────────

def test_the_components_sum_to_the_score(profile):
    """One function produces both, so the columns on screen cannot fail to add
    up to the number beside them."""
    parts = sarr.contributions(profile, 1650, "HV", 55.0)
    assert sum(parts.values()) == pytest.approx(
        sarr.score(profile, 1650, "HV", 55.0), abs=1e-12)


def test_every_declared_component_is_produced(profile):
    """COMPONENTS is what the page renders columns from. A term added to the
    score and forgotten here would be silently dropped from the breakdown."""
    parts = sarr.contributions(profile, 1650, "HV", 55.0)
    assert set(parts) == set(sarr.COMPONENTS) == set(sarr.COMPONENT_WEIGHTS)


def test_a_missing_rating_degrades_to_zero_rather_than_poisoning_the_score(profile):
    """rating is 100% null from July 2026. A NaN-propagating version returns no
    score at all for any recent meeting."""
    profile["rating"] = float("nan")
    parts = sarr.contributions(profile, 1650, "HV", float("nan"))
    assert parts["rating"] == 0.0
    assert not np.isnan(sum(parts.values()))


# ── the blend ────────────────────────────────────────────────────────────────

def test_both_streams_are_probabilities():
    fund = blend.fundamental_probability([0.4, -0.1, 0.25, 0.0])
    mkt = blend.market_probability([3.0, 5.0, 8.0, 21.0])
    assert fund.sum() == pytest.approx(1.0)
    assert mkt.sum() == pytest.approx(1.0)


def test_a_lower_sarr_gets_a_higher_probability():
    """SARR is lower-is-better. Getting the sign wrong here would invert every
    row of the page without any other symptom."""
    p = blend.fundamental_probability([-0.5, 0.0, 0.5])
    assert p[0] > p[1] > p[2]


def test_the_fundamental_stream_ignores_the_level_of_the_scores():
    """Centred before exponentiating, so rebuilding the ET reference — which
    shifts every SARR by a constant — does not move the page's probabilities."""
    a = blend.fundamental_probability([0.1, -0.2, 0.3])
    b = blend.fundamental_probability([10.1, 9.8, 10.3])
    assert a == pytest.approx(b)


def test_the_default_blend_weight_is_the_market_alone():
    """Not a placeholder: 0.00 is the fitted value, and the page says so."""
    fund = blend.fundamental_probability([0.4, -0.1, 0.25])
    mkt = blend.market_probability([3.0, 5.0, 8.0])
    assert blend.blend(fund, mkt) == pytest.approx(mkt)
    assert blend.DEFAULT_BLEND_WEIGHT == 0.0


def test_a_missing_stream_does_not_void_the_race():
    """FUSE returned None for a whole race when any stream held a NaN — one
    missing odds field killed it. That is a fault, not a design."""
    mkt = blend.market_probability([3.0, 5.0, 8.0])
    assert blend.blend(np.array([]), mkt) == pytest.approx(mkt)


def test_the_published_calibration_keys_survive_json():
    """JSON turns 1.0 into "1.0"; a reader looking up "1" finds nothing, and
    the page rendered `undefined` until these were strings."""
    keys = blend.CALIBRATION["log_loss_by_weight"]
    assert all(isinstance(k, str) and len(k) == 4 for k in keys)
    assert f"{blend.DEFAULT_BLEND_WEIGHT:.2f}" in keys


# ── the query layer, end to end ──────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """Enough history for SARR to score: 8 horses over 12 meetings."""
    import datetime as dt

    path = tmp_path / "m.db"
    conn = get_conn(path)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2026, 1, 4)
    for d in range(12):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1650, "race_class": "4"})
        for h in range(8):
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(((h + d) % 8) + 1),
                "finish_time": 100.0 + h * 0.2 + (d % 3) * 0.1,
                "lengths_behind": "-" if h == 0 else f"{h}-1/4",
                "draw": h + 1, "actual_weight": 120 + h, "rating": 60 - h,
                "win_odds": str(3.0 + h * 2), "running_positions": "1 2 3 4",
                "section_times": "24.9; 22.8; 23.6; 24.1",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    rebuild_sarr.rebuild(path, min_prior=2)
    return path


def test_the_rebuild_stores_a_component_row_for_every_term(db):
    conn = get_conn(db)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM runner_sarr), "
        "(SELECT count(*) FROM runner_sarr_component)").fetchone()
    conn.close()
    assert counts[0] > 0
    assert counts[1] == counts[0] * len(sarr.COMPONENTS)


def test_stored_components_sum_to_the_stored_score(db):
    """The invariant that makes the page trustworthy, asserted over real rows."""
    conn = get_conn(db)
    mismatched = conn.execute("""
        SELECT count(*) FROM (
          SELECT s.sarr, sum(c.contribution) total
          FROM runner_sarr s
          JOIN runner_sarr_component c USING (race_date, race_no, horse_no)
          GROUP BY s.race_date, s.race_no, s.horse_no
          HAVING abs(s.sarr - total) > 1e-9)""").fetchone()[0]
    conn.close()
    assert mismatched == 0


def test_the_breakdown_reports_whether_its_own_rows_add_up(db):
    conn = get_conn(db)
    out = model.sarr_breakdown("2026-03-22", 1, conn=conn)
    conn.close()
    assert out["runners"]
    assert all(r["components_sum_to_score"] for r in out["runners"])
    assert all(set(r["components"]) == set(sarr.COMPONENTS) for r in out["runners"])


def test_influence_separates_the_fitted_weight_from_the_realised_one(db):
    """`draw` carries the widest multiplier (1.5) and nothing like the widest
    realised influence, because its score is a small centred number rather than
    a raw deviation. A page showing only the coefficient would put it at the top
    of the table.

    This test used to assert the opposite -- that draw moved scores by exactly
    zero -- and it was right to: the term was wired end to end and fed no value.
    That is now fixed, and the assertion is inverted rather than deleted so the
    two readings of "how big is this term" stay pinned apart."""
    conn = get_conn(db)
    rows = {c["component"]: c for c in model.sarr_influence(conn=conn)}
    conn.close()
    assert rows["draw"]["weight_share"] == 1.0        # widest coefficient
    assert rows["draw"]["influence_share"] < 1.0      # not the widest influence
    assert rows["fmrp"]["influence_share"] > rows["traj"]["influence_share"]


def test_unscored_runners_are_named_not_silently_dropped(db):
    """A field of 8 showing 6 rows with no explanation is how the old page let
    a silent skip look like a short field."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_sarr_component WHERE horse_no = 1")
        conn.execute("DELETE FROM runner_sarr WHERE horse_no = 1")
    out = model.sarr_breakdown("2026-03-22", 1, conn=conn)
    conn.close()
    assert out["unscored"] == ["HORSE 0"]
    assert all(r["horse_no"] != 1 for r in out["runners"])


def test_the_blend_breakdown_shows_the_overround_rather_than_hiding_it(db):
    conn = get_conn(db)
    out = model.blend_breakdown("2026-03-22", 1, conn=conn)
    conn.close()
    raw = sum(r["market_raw"] for r in out["runners"])
    devig = sum(r["market_devig"] for r in out["runners"])
    assert raw == pytest.approx(100 + out["overround"], abs=0.5)
    assert devig == pytest.approx(100, abs=0.5)


def test_at_the_fitted_weight_the_blend_is_the_market(db):
    conn = get_conn(db)
    out = model.blend_breakdown("2026-03-22", 1, conn=conn)
    conn.close()
    assert out["weight"] == 0.0
    assert all(r["blended"] == r["market_devig"] for r in out["runners"])


def test_a_weight_the_reader_picks_is_honoured_and_clamped(db):
    conn = get_conn(db)
    half = model.blend_breakdown("2026-03-22", 1, weight=0.5, conn=conn)
    over = model.blend_breakdown("2026-03-22", 1, weight=7.0, conn=conn)
    conn.close()
    assert half["weight"] == 0.5
    assert any(r["blended"] != r["market_devig"] for r in half["runners"])
    assert over["weight"] == 1.0


def test_a_partly_priced_field_blanks_the_stream_rather_than_mixing_scales(db):
    """A de-vig over some of the prices is normalised against a denominator
    missing terms, so it would not mean what the column header says."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE runners SET win_odds = NULL "
                     "WHERE race_date = '2026-03-22' AND horse_no = 3")
    out = model.blend_breakdown("2026-03-22", 1, conn=conn)
    conn.close()
    assert out["missing"]["unpriced"] == 1
    assert out["overround"] is None
    assert all(r["market_devig"] is None for r in out["runners"])

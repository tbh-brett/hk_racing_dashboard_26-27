"""model/evaluate and model/power — can this archive tell two models apart?

The question these pin is not "is the model good" but "would we know". Four
candidate changes were each measured once, each came back "not significant",
and that was read as four negative results when it was one fact about the size
of the test.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.model import evaluate, power
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _seed(db, meetings: int = 30, runners: int = 8):
    conn = get_conn(db)
    init_db(conn)
    races, runs = [], []
    start = dt.date(2025, 1, 5)
    for d in range(meetings):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1800, "race_class": "4"})
        for h in range(runners):
            runs.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(((h + d) % runners) + 1),
                "finish_time": 108.0 + ((h + d) % runners) * 0.3,
                "lengths_behind": "-" if (h + d) % runners == 0 else "1-1/4",
                "draw": h + 1, "rating": 60 + h, "win_odds": "5.0",
                "section_times": "13.0; 24.0; 23.5; 23.8; 24.2",
                "running_positions": f"{h+1} {h+1} {h+1} {h+1}"})
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runs)
    conn.close()


# ── the anti-drift guarantee ─────────────────────────────────────────────────

def test_the_evaluator_reproduces_what_the_job_writes(tmp_path):
    """Both arms must come from ONE scoring loop. A harness with its own copy
    reports the difference between two loops as a difference between two
    models, and nothing in the output would show it."""
    from hkrd.jobs import rebuild_sarr
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    written = {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT race_date, race_no, horse_no, sarr FROM runner_sarr")}
    scored = evaluate.score(conn)
    conn.close()
    assert len(scored) == len(written)
    for r in scored.itertuples():
        assert r.sarr == pytest.approx(
            written[(r.race_date, r.race_no, r.horse_no)], abs=1e-12)


def test_an_unchanged_variant_reports_inert_not_insignificant(tmp_path):
    """A variant that never fired is a wiring mistake, and must not hide behind
    a plausible-looking p-value."""
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_conn(db)
    base = evaluate.score(conn)
    same = evaluate.score(conn, adjust=lambda profile, rec, prior: profile)
    conn.close()
    c = evaluate.compare(base, same)
    assert c.inert and c.delta == 0.0
    assert "INERT" in c.render()


def test_a_variant_that_moves_scores_is_not_inert(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_conn(db)
    base = evaluate.score(conn)
    worse = evaluate.score(conn, adjust=lambda p, rec, prior: {
        **p, "fmrp": -p["fmrp"] if p["fmrp"] is not None else p["fmrp"]})
    conn.close()
    c = evaluate.compare(base, worse)
    assert not c.inert
    assert c.n_races > 0


def test_a_race_is_one_observation_not_eleven(tmp_path):
    """The unit is the race. Counting runners multiplies n by about eleven and
    shrinks every interval by the square root of that."""
    db = tmp_path / "t.db"
    _seed(db, meetings=12)
    conn = get_conn(db)
    scored = evaluate.score(conn)
    conn.close()
    rho = evaluate.per_race_rho(scored)
    assert len(rho) == scored.groupby(["race_date", "race_no"]).ngroups
    assert len(rho) < len(scored)


def test_a_field_too_short_to_rank_is_dropped(tmp_path):
    """Under four runners a rank correlation is one of three values and would
    dominate the variance of everything else."""
    import pandas as pd
    tiny = pd.DataFrame({"race_date": ["2025-01-05"] * 3, "race_no": [1] * 3,
                         "horse_no": [1, 2, 3], "sarr": [-0.1, 0.0, 0.1],
                         "place": [1, 2, 3]})
    assert evaluate.per_race_rho(tiny).empty


# ── the power arithmetic ─────────────────────────────────────────────────────

def test_a_smaller_archive_sees_less():
    a = power.detectable_effect(300, 0.035)
    b = power.detectable_effect(3000, 0.035)
    assert a > b
    # four times the races halves the smallest visible effect
    assert power.detectable_effect(1200, 0.035) == pytest.approx(a / 2, rel=1e-9)


def test_races_needed_inverts_detectable_effect():
    n = power.races_needed(0.002, 0.035)
    assert power.detectable_effect(n, 0.035) == pytest.approx(0.002, rel=2e-3)


def test_the_measured_case_is_reported_as_unresolvable():
    """306 held-out races and a paired sd of 0.035: the smallest visible effect
    is +0.0056, and every change measured so far was under +0.003."""
    p = power.paired_power([0.0024] * 0 + list(
        __import__("numpy").random.default_rng(0).normal(0.0024, 0.035, 306)))
    assert p.detectable > 0.003
    assert p.needed > 1000

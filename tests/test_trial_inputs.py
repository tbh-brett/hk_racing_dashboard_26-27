"""What the model reads from barrier trials (derive/trial_inputs, recipe gbm-1.1).

The same question as every input test: can anything from the race day or after
reach a race's row? Here that is a trial run on or after the race, a trial
before the previous start, and a trial older than the window. Then the switch
from a model that read no trials to one that does: the old one must go on
scoring until the new one passes the gate.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from hkrd.derive import features as F
from hkrd.derive import trial_inputs as ti
from hkrd.store.connect import get_conn, init_db, transaction

COLS = ["trial_date", "trial_no", "horse_name", "venue", "course", "distance", "finish_time",
        "running_positions", "comment_text"]


def _trial(date, no, horse, *, course="SHA TIN TURF", venue="ST", dist=1000, time=58.0,
           pos="3 2 1", comment="Ran on well.") -> dict:
    return dict(zip(COLS, [date, no, horse, venue, course, dist, time, pos, comment]))


# ── HKJC's comment ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("comment, effort, finish", [
    # CAVAMAZING, 19 Sep 2026: asked, and found something
    ("Settled behind runners; made progress when hard ridden.", 2.0, 1.0),
    ("Under a hold in the Straight; not tested.", 0.0, np.nan),
    ("Ridden conservatively; not asked; stayed on comfortably.", 0.0, 2.0),
    # urged to LEAD is the start, not the finish; the ask is in the straight
    ("Urged to lead along the rail; limited response when asked in the straight.", 1.0, 0.0),
    ("Led all the way to score under a hold.", 0.0, np.nan),
    # denies the whip without saying it was held: no read, not "hard ridden"
    ("Not hard ridden; kept on evenly.", np.nan, 1.0),
    ("Unimpressive.", np.nan, np.nan),
    ("Drew away to score; weakened late.", np.nan, 1.0),       # both said: fair
    (None, np.nan, np.nan),
])
def test_the_comment_is_read_for_the_ask_and_the_answer(comment, effort, finish):
    assert ti.effort(comment) == effort or (np.isnan(effort) and np.isnan(ti.effort(comment)))
    got = ti.finish_words(comment)
    assert got == finish or (np.isnan(finish) and np.isnan(got))


# ── one trial run ─────────────────────────────────────────────────────────────

def test_each_venues_batch_one_is_its_own_batch_and_conghua_is_read_off_the_course():
    rows = [_trial("2026-08-25", 1, h, pos=p, time=t) for h, p, t in
            (("A", "1 1 1", 57.0), ("B", "3 3 2", 57.5), ("C", "2 2 3", 58.5))]
    # the legacy import stored Conghua as HV; the course says Conghua
    rows += [_trial("2026-08-25", 1, h, course="CONGHUA TURF", venue="HV", pos=p, time=t)
             for h, p, t in (("D", "2 2 1", 59.0), ("E", "1 1 2", 59.4))]
    r = ti.reads(pd.DataFrame(rows)).set_index("horse_name")
    assert r.loc["A", "tr_fin"] == 0 and r.loc["C", "tr_fin"] == 1.0         # three-horse batch
    assert r.loc["D", "tr_fin"] == 0 and r.loc["E", "tr_fin"] == 1.0         # two-horse batch
    assert r.loc["D", "tr_behind"] == 0 and np.isclose(r.loc["E", "tr_behind"], 0.4)
    assert r.loc["B", "tr_late_gain"] == 1 and r.loc["C", "tr_late_gain"] == -1
    assert r.loc["D", "tr_at_ch"] == 1 and r.loc["D", "tr_at_hv"] == 0
    assert r.loc["A", "tr_at_ch"] == 0 and r.loc["A", "tr_at_hv"] == 0


def test_time_is_read_against_that_mornings_runners_over_the_same_course_and_trip():
    rows = [_trial("2026-08-25", 1, "A", time=57.0), _trial("2026-08-25", 2, "B", time=59.0),
            _trial("2026-08-25", 3, "C", time=58.0),
            _trial("2026-08-25", 4, "D", dist=1200, time=70.0),       # another trip
            _trial("2026-08-26", 1, "E", time=50.0)]                  # another morning
    r = ti.reads(pd.DataFrame(rows)).set_index("horse_name")
    assert r.loc["A", "tr_vs_morning"] == -1.0 and r.loc["B", "tr_vs_morning"] == 1.0
    assert r.loc["D", "tr_vs_morning"] == 0 and r.loc["E", "tr_vs_morning"] == 0


def test_a_time_that_is_not_a_time_is_missing():
    r = ti.reads(pd.DataFrame([_trial("2026-08-25", 1, "A", time=0.0),
                               _trial("2026-08-25", 1, "B", time=58.0)]))
    assert r["tr_behind"].isna().tolist() == [True, False]


# ── which trial a run reads ───────────────────────────────────────────────────

def _attach(trials: list[dict], race: str, prev: str | None) -> pd.Series:
    got = ti.attach(pd.Series(["H"]), pd.Series([pd.Timestamp(race)]),
                    pd.Series([pd.Timestamp(prev) if prev else pd.NaT]),
                    ti.reads(pd.DataFrame(trials)))
    return got.iloc[0]


def test_the_latest_trial_since_the_last_start_is_read_and_the_others_counted():
    trials = [_trial("2026-09-05", 1, "H", comment="Under a hold."),
              _trial("2026-09-20", 1, "H", comment="Hard ridden; ran on well.")]
    got = _attach(trials, "2026-10-01", "2026-09-01")
    assert got["tr_days"] == 11 and got["tr_n"] == 2
    assert got["tr_effort"] == 2 and got["tr_finish"] == 2


def test_a_trial_before_the_last_start_says_nothing_new():
    got = _attach([_trial("2026-08-25", 1, "H")], "2026-10-01", "2026-09-01")
    assert got.isna().all()


def test_never_a_trial_on_the_race_day_or_after():
    trials = [_trial("2026-10-01", 1, "H"), _trial("2026-10-02", 1, "H")]
    assert _attach(trials, "2026-10-01", None).isna().all()


def test_a_debut_reads_its_trials_within_the_window_only():
    trials = [_trial("2026-05-01", 1, "H"), _trial("2026-09-01", 1, "H")]
    got = _attach(trials, "2026-10-01", None)
    assert got["tr_days"] == 30 and got["tr_n"] == 1                      # May is 153 days back
    assert _attach([_trial("2026-05-01", 1, "H")], "2026-10-01", None).isna().all()


def test_a_horse_with_no_trials_and_a_horse_with_one_side_by_side():
    got = ti.attach(pd.Series(["H", "NONE"]),
                    pd.Series([pd.Timestamp("2026-10-01")] * 2),
                    pd.Series([pd.NaT, pd.NaT]),
                    ti.reads(pd.DataFrame([_trial("2026-09-20", 1, "H")])))
    assert got.loc[0, "tr_days"] == 11 and got.loc[1].isna().all()


# ── in the build ──────────────────────────────────────────────────────────────

def _runs() -> pd.DataFrame:
    """Two meetings, one race each, the same two horses."""
    rows = []
    for date, odds in (("2026-09-10", (2.0, 4.0)), ("2026-10-01", (3.0, 2.5))):
        for i, h in enumerate(("H", "G")):
            rows.append({"race_date": date, "race_no": 1, "horse_no": i + 1, "horse_name": h,
                         "place": i + 1, "place_code": None, "finish_time": 70.0 + i,
                         "lengths_behind": float(i), "draw": i + 1, "jockey": "J",
                         "trainer": "T", "actual_weight": 120, "declared_weight": 1100,
                         "rating": 60, "win_odds": odds[i], "running_positions": "1 1 1",
                         "venue": "ST", "surface": "Turf", "going": "G", "distance": 1200,
                         "race_class": "4", "early_dev": 0.0, "late_dev": 0.0,
                         "n_trouble": np.nan, "n_wide": np.nan, "n_vet": np.nan,
                         "eased": np.nan, "weakened": np.nan, "keen": np.nan})
    return pd.DataFrame(rows)


def test_the_build_must_be_told_the_trials():
    with pytest.raises(TypeError):
        F.build(_runs())                                       # type: ignore[call-arg]


def test_a_trial_reaches_the_next_race_only_and_moves_none_of_the_lab_inputs():
    none = pd.DataFrame(columns=COLS)
    trials = pd.DataFrame([_trial("2026-09-20", 1, "H"), _trial("2026-10-01", 1, "G"),
                           _trial("2026-10-05", 1, "H")])
    a, b = F.build(_runs(), trials=none), F.build(_runs(), trials=trials)
    lab = [*F.LAB_FEATURES, *F.CATEGORICAL]
    pd.testing.assert_frame_equal(a[lab], b[lab])
    row = b.set_index(["race_date", "horse_name"])
    assert row.loc[("2026-10-01", "H"), "tr_days"] == 11
    assert row.loc[("2026-09-10", "H"), ti.INPUTS].isna().all()        # nothing before it
    assert row.loc[("2026-10-01", "G"), ti.INPUTS].isna().all()        # race-day trial
    assert set(ti.INPUTS) <= set(b.columns)


# ── the fit needs the archive ─────────────────────────────────────────────────

def _days(conn, season: int, n: int) -> None:
    start = pd.Timestamp(f"{season}-09-01")
    rows = [(str((start + pd.Timedelta(days=d)).date()), 1, f"H{d}", "ST", "SHA TIN TURF")
            for d in range(n)]
    conn.executemany("INSERT INTO trials (trial_date, trial_no, horse_name, venue, course) "
                     "VALUES (?, ?, ?, ?, ?)", rows)


def test_the_fit_refuses_an_archive_that_was_never_backfilled(tmp_path):
    from hkrd.jobs import fit_gbm
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    with transaction(conn):
        for season in range(2019, 2026):
            _days(conn, season, 150 if season >= 2025 else 0)
    msg = fit_gbm.short_of_trials(conn, 2026)
    assert "2019-20 0 days" in msg and "2024-25 0 days" in msg and "2025-26" not in msg
    assert "--backfill 2019-05-01" in msg
    with transaction(conn):
        for season in range(2019, 2025):
            _days(conn, season, 150)
    assert fit_gbm.short_of_trials(conn, 2026) == ""
    conn.close()


# ── from a model that read no trials to one that does ─────────────────────────

def _scoring_db(tmp_path, *, old_model: bool):
    """test_gbm's synthetic archive and card, with the live model fitted on the
    lab's inputs only (as gbm-1.0 was) or on today's."""
    from test_gbm import _scoring_db as base
    db, date = base(tmp_path / "s.db")
    if old_model:
        from hkrd.model import gbm
        from hkrd.store import gbm as store
        conn = get_conn(db)
        frame = F.build(store.load_runs(conn), trials=store.load_trials(conn))
        hist = frame[~frame["is_card"]].reset_index(drop=True)
        lab = [*F.LAB_FEATURES, *F.CATEGORICAL]
        # fitted on the lab's columns only, the way the live gbm-1.0 booster was
        saved = gbm.INPUTS
        try:
            gbm.INPUTS = lab
            old = gbm.fit(hist, rounds=20)
        finally:
            gbm.INPUTS = saved
        with transaction(conn):
            conn.execute("UPDATE gbm_models SET model_text = ?, features_version = 'feat-1.0', "
                         "record_json = record_json || ' '", (old.model_to_string(),))
        conn.close()
    return db, date


def test_a_model_fitted_before_trials_goes_on_scoring_on_its_own_inputs(tmp_path):
    from hkrd.jobs import score_gbm
    db, date = _scoring_db(tmp_path, old_model=True)
    got = score_gbm.score(date, db)
    assert got.scored and not got.errors
    conn = get_conn(db)
    contrib = [json.loads(r[0]) for r in conn.execute(
        "SELECT contrib_json FROM runner_gbm WHERE race_date = ? AND stage = 'latest'", (date,))]
    conn.close()
    assert contrib and all("trials" not in c and "form" in c for c in contrib)


def test_a_trial_stored_after_the_card_was_scored_scores_it_again(tmp_path):
    from hkrd.jobs import score_gbm
    db, date = _scoring_db(tmp_path, old_model=False)
    assert score_gbm.score(date, db).scored
    assert score_gbm.score(date, db).unchanged == [date]
    conn = get_conn(db)
    horse = conn.execute("SELECT horse_name FROM runners WHERE race_date = ? AND race_no = 1 "
                         "AND horse_no = 1", (date,)).fetchone()[0]
    day_before = str((pd.Timestamp(date) - pd.Timedelta(days=1)).date())
    with transaction(conn):
        conn.execute("INSERT INTO trials (trial_date, trial_no, horse_name, venue, course, "
                     "distance, finish_time, running_positions, comment_text) VALUES "
                     "(?, 1, ?, 'ST', 'SHA TIN TURF', 1000, 57.5, '2 1 1', 'Ran on well.')",
                     (day_before, horse))
    conn.close()
    again = score_gbm.score(date, db)
    assert again.scored and not again.errors

"""What the fundamental model cannot see (model/gbm_unseen): the rules, and the
record the fit keeps of what each has been worth against the model.

The Briefing names these beside the model's number -- a trial since the last
run, a new stable -- with the figure the walk-forward record gives. The rules
here are the ones both the page and the record read, so the page never shows a
fact the record did not measure the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hkrd.derive import features as F
from hkrd.jobs import fit_gbm
from hkrd.model import gbm, gbm_record
from hkrd.model.gbm_unseen import UNSEEN, facts, mark, new_stable, trial_since

RACE = "2026-10-01"


def _t(date: str, band: str = "STANDOUT") -> dict:
    return {"trial_date": date, "band": band}


def test_a_trial_counts_only_after_the_last_run_and_within_sixty_days() -> None:
    assert trial_since(RACE, "2026-09-10", [_t("2026-09-20")])["trial_date"] == "2026-09-20"
    # before the last run: the run since has already said what it had to say
    assert trial_since(RACE, "2026-09-10", [_t("2026-09-05")]) is None
    # first-up, but the trial is older than the window
    assert trial_since(RACE, None, [_t("2026-07-20")]) is None
    assert trial_since(RACE, None, [_t("2026-08-20")]) is not None
    # never the race day itself, never after it
    assert trial_since(RACE, None, [_t(RACE), _t("2026-10-02")]) is None


def test_the_newest_qualifying_trial_is_the_one_read() -> None:
    got = trial_since(RACE, None, [_t("2026-09-25", "NEGATIVE"), _t("2026-09-10")])
    assert got["band"] == "NEGATIVE"


def test_a_new_stable_is_a_different_name_not_a_different_spelling() -> None:
    assert new_stable("C H Yip", "P F Yiu")
    assert not new_stable("C H  YIP", "c h yip")
    assert not new_stable("C H Yip", None) and not new_stable(None, "C H Yip")


def test_facts_come_in_the_pages_order_and_untested_trials_say_nothing() -> None:
    got = facts(RACE, "2026-09-01", "NEW", "OLD", [_t("2026-09-20", "POSITIVE")])
    assert [k for k, _ in got] == ["trial_positive", "new_stable"]
    assert facts(RACE, "2026-09-01", "T", "T", [_t("2026-09-20", "UNTESTED")]) == []
    assert list(UNSEEN) == ["trial_standout", "trial_positive", "trial_negative", "new_stable"]


def test_the_fit_marks_every_start_against_that_horses_last_one() -> None:
    starts = pd.DataFrame({
        "race_date": ["2026-09-06", "2026-09-20", "2026-10-01", "2026-09-20"],
        "race_no": [1, 2, 3, 4], "horse_no": [5, 5, 5, 1],
        "horse_name": ["A", "A", "A", "B"], "trainer": ["T1", "T1", "T2", "T9"]})
    trials = {"A": [_t("2026-09-25"), _t("2026-09-01", "NEGATIVE")],
              "B": [_t("2026-09-15", "POSITIVE")]}
    m = mark(starts, trials).set_index(["race_date", "race_no"])
    assert m.loc[("2026-10-01", 3), "trial_standout"] and m.loc[("2026-10-01", 3), "new_stable"]
    # its first start: first-up, a NEGATIVE trial within the window, no stable to move from
    assert m.loc[("2026-09-06", 1), "trial_negative"]
    assert not m.loc[("2026-09-06", 1), "new_stable"]
    assert not m.loc[("2026-09-20", 2)].drop("horse_no").any()
    assert m.loc[("2026-09-20", 4), "trial_positive"]


def _preds(seed: int = 3) -> pd.DataFrame:
    """A walk-forward's output: chances, the price, the flag facts and the marks."""
    rng = np.random.default_rng(seed)
    races, field = 420, 10          # 1 Sep 2024 to late Oct 2025: two seasons
    n = races * field
    rid = np.repeat(np.arange(races), field)
    date = np.repeat(pd.date_range("2024-09-01", periods=races, freq="D").strftime("%Y-%m-%d"), field)
    p = pd.Series(rng.dirichlet(np.ones(field), races).ravel())
    q = p * np.exp(rng.normal(0, 0.3, n))
    q = q / q.groupby(rid).transform("sum")          # the model: the price, give or take
    won = np.zeros(n)
    for r in range(races):
        won[r * field + rng.choice(field, p=p[r * field:(r + 1) * field] / p[r * field:(r + 1) * field].sum())] = 1
    df = pd.DataFrame({
        "race_id": [f"{d}_{r}" for d, r in zip(date, rid)], "race_date": date,
        "horse_no": np.tile(np.arange(1, field + 1), races),
        "season": np.asarray(F.season_of(pd.to_datetime(pd.Series(date)))),
        "y": won, "won": won, "p_mkt": p, "p_model": q, "rounds": 10,
        "vs": "ST_Turf", "dist": 1200.0, "draw": np.tile(np.arange(1, field + 1), races).astype(float),
        "prep_run": 3, "l1_place": 4, "l1_vs": "ST_Turf", "l2_vs": "ST_Turf", "l3_vs": "ST_Turf",
        "hab_early": 0.5, "n_leaders": 2, "n_prior": 6})
    for k in UNSEEN:
        df[k] = rng.random(n) < 0.05
    df.loc[df["season"] < 2025, ["trial_standout", "trial_positive", "trial_negative"]] = False
    return df


def test_the_record_says_what_each_fact_was_worth_against_the_model_and_the_price() -> None:
    wf = gbm_record.record(_preds())
    rows = {r["fact"]: r for r in wf["unseen"]}
    assert list(rows) == list(UNSEEN)
    for k, r in rows.items():
        assert r["label"] == UNSEEN[k]["label"] and r["runs"] > 0
        assert set(r["model"]) >= {"ae", "ae_lo", "ae_hi"} and "ae" in r["price"]
    # a trial row rests only on the seasons that have trials; a stable row on all
    assert rows["trial_standout"]["seasons"] == ["2025-26"]
    assert rows["new_stable"]["seasons"] == ["2024-25", "2025-26"]


def test_a_record_without_the_table_is_never_carried() -> None:
    recipe = gbm.recipe(355)
    seasons = [2021, 2022, 2023, 2024, 2025]
    wf = {"test_seasons": ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]}
    prior = {"params": recipe, "features_version": F.DERIVE_VERSION, "record": {"walk_forward": wf}}
    assert not fit_gbm._carries(prior, recipe, seasons)
    wf["unseen"] = []
    assert fit_gbm._carries(prior, recipe, seasons)
    assert not fit_gbm._carries(prior, recipe, seasons + [2026])
    assert not fit_gbm._carries(None, recipe, seasons)

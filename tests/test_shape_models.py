"""The shape models and the simulator's tables (racesim-SPEC §6, §7, §11.2, §12).

Small and always run: the least squares with race-clustered errors against a computation by hand,
the AUC by ranks against a count of pairs, and the ex-ante rules of gbm-SPEC §9 -- a run appended
after race X leaves race X's features byte-identical, and a rider's same-day rides never move his
forwardness.

Against the lab (§11.2), needing the 23 Oct production pull (`hkrd-fly.db`) and the lab folder
(`claude/tools/`), both kept off the server: HKRD_LAB_PARITY=1. The trip table is the lab's own
(its first parser, which its tables and models were fitted on), built by its trip2d.py. On it the
port reproduces sim_tables_2026-10-25.json section by section, Part A3's coefficients and intervals,
and the 25 Oct card's shape predictions to 1e-6 (3 minutes of LightGBM).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hkrd.model import shape, sim_tables

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "shape"
LAB = ROOT / "claude" / "tools"
PULL = ROOT / "hkrd-fly.db"


# ── least squares, clustered by race ─────────────────────────────────────────────────────────
def test_the_clustered_interval_is_statsmodels_formula() -> None:
    rng = np.random.default_rng(3)
    n = 300
    df = pd.DataFrame({"race_id": np.repeat([f"r{i:03d}" for i in range(60)], 5), "x": rng.normal(size=n),
                       "c": rng.choice(["a", "b", "c"], size=n)})
    df["y"] = 1.5 * df.x + (df.c == "b") * 0.7 + rng.normal(size=n) + np.repeat(rng.normal(size=60), 5)
    params, ci, scale, rows = sim_tables._ols(df, "y", [("num", "x"), ("cat", "c", None)], cluster="race_id")
    X = np.column_stack([np.ones(n), df.x, df.c == "b", df.c == "c"]).astype(float)
    beta = np.linalg.solve(X.T @ X, X.T @ df.y.values)
    u = df.y.values - X @ beta
    meat = sum(np.outer(X[g].T @ u[g], X[g].T @ u[g]) for g in [df.race_id.values == r for r in df.race_id.unique()])
    bread = np.linalg.inv(X.T @ X)
    cov = bread @ meat @ bread * (60 / 59) * ((n - 1) / (n - 4))
    se = np.sqrt(np.diag(cov))
    assert params["x"] == pytest.approx(beta[1], abs=1e-10)
    assert params["C(c)[T.b]"] == pytest.approx(beta[2], abs=1e-10)
    assert ci["x"][1] - params["x"] == pytest.approx(1.959963984540054 * se[1], abs=1e-10)
    assert rows == n and scale == pytest.approx(float(u @ u) / (n - 4))


def test_the_auc_by_ranks_is_the_share_of_pairs_ordered_right() -> None:
    rng = np.random.default_rng(5)
    y = rng.integers(0, 2, 200)
    p = np.round(rng.random(200) + 0.3 * y, 2)                     # rounded: ties, counted as half
    pairs = [(a > b) + 0.5 * (a == b) for a in p[y == 1] for b in p[y == 0]]
    assert shape._auc(y, p) == pytest.approx(np.mean(pairs), abs=1e-12)


def test_the_trip_groups_split_sha_tins_one_turn_trips_from_the_two_turn() -> None:
    assert sim_tables.cost_group("ST", "Turf", 1600) == sim_tables.cost_group("ST", "Turf", 1800) == "ST 1600-1800"
    assert sim_tables.cost_group("ST", "Turf", 2000) == "ST 2000+"
    assert sim_tables.cost_group("ST", "AWT", 1650) == "ST AWT 1650+"
    assert sim_tables.cost_group("HV", "Turf", 2200) == "HV 1800+"


# ── ex ante: previous meetings only ───────────────────────────────────────────────────────────
def _frame(n_meetings: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    for m in range(n_meetings):
        day = pd.Timestamp("2026-09-07") + pd.Timedelta(days=7 * m)
        for race in (1, 2):
            for h in range(8):
                rows.append(dict(race_date=day.strftime("%Y-%m-%d"), race_no=race, horse_no=h + 1,
                                 horse_name=f"H{(h + race * 3 + m) % 12}", jockey=f"J{h % 5}", draw=h + 1, field=8,
                                 early_frac=h / 7 if rng.random() > 0.1 else np.nan, mid_frac=rng.random(),
                                 wide3_pre=int(rng.random() < 0.2), nocover_pre=int(rng.random() < 0.05),
                                 rail_pre=int(rng.random() < 0.2), stated=1, lane_max_pre=float(rng.integers(1, 5)),
                                 lane_settle=float(rng.integers(1, 5)), slow_bad=int(rng.random() < 0.1),
                                 gear="B1" if rng.random() < 0.1 else "B", course="A", vs="ST_Turf",
                                 distance=1200, dist=1200.0, race_class="4", going="G", is_card=0))
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d.race_date)
    d["race_id"] = d.race_date + "_" + d.race_no.astype(str).str.zfill(2)
    return d


def test_a_run_appended_after_a_race_leaves_its_features_byte_identical() -> None:
    d = _frame()
    early = d[d.race_date < "2026-09-28"]
    a = shape.features(early)
    b = shape.features(d)
    b = b[b.race_date < "2026-09-28"].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[shape.RUNNER].reset_index(drop=True), b[shape.RUNNER], check_exact=True)


def test_a_riders_same_day_rides_never_move_his_forwardness() -> None:
    d = _frame()
    last = d.race_date.max()
    a = shape.features(d)
    moved = d.copy()
    moved.loc[(moved.race_date == last) & (moved.race_no == 1), "early_frac"] = 0.0
    b = shape.features(moved)
    key = ["race_id", "horse_no"]
    fa = a[a.race_date == last].set_index(key).rider_fwd
    fb = b[b.race_date == last].set_index(key).rider_fwd
    pd.testing.assert_series_equal(fa, fb)


# ── §11.2: against the lab, on its own inputs ─────────────────────────────────────────────────
lab_parity = pytest.mark.skipif(not (os.environ.get("HKRD_LAB_PARITY") and PULL.is_file() and LAB.is_dir()),
                                reason="needs HKRD_LAB_PARITY=1, the 23 Oct pull (hkrd-fly.db) and claude/tools/")


@pytest.fixture(scope="module")
def lab_trip() -> pd.DataFrame:
    os.environ["HKRD_DB"] = str(PULL)
    sys.path.insert(0, str(LAB / "race-shape-2d"))
    try:
        import trip2d                                          # the lab's, for its inputs only
        return trip2d.build(str(PULL))
    finally:
        sys.path.remove(str(LAB / "race-shape-2d"))


def _std_cum(db: Path):
    from hkrd.jobs.fit_sim_tables import _std_cum
    from hkrd.store.connect import get_conn
    conn = get_conn(db)
    try:
        return _std_cum(conn)
    finally:
        conn.close()


@lab_parity
def test_the_tables_are_the_labs_on_its_inputs(lab_trip: pd.DataFrame) -> None:
    with gzip.open(FIX / "races_2026-10-14.json.gz", "rt", encoding="utf-8") as f:
        races = json.load(f)["races"]
    contacts = [dict(distance=r["distance"], venue=r["venue"], surface=r["surface"], runners=len(r["runners"]),
                     events=r["events"]) for r in races]
    got = json.loads(json.dumps(sim_tables.fit(lab_trip, "2026-10-25", std_cum=_std_cum(PULL),
                                               contact_races=contacts, last_season=2025)))
    with gzip.open(FIX / "sim_tables_2026-10-25.json.gz", "rt", encoding="utf-8") as f:
        ref = json.load(f)
    for key in ref:
        assert got[key] == ref[key], key


@lab_parity
def test_part_a3_is_the_studys_on_its_inputs(lab_trip: pd.DataFrame) -> None:
    got = sim_tables.trip_regressions(lab_trip, last_season=2025)
    ref = json.loads((LAB / "race-shape-2d" / "out" / "partA3.json").read_text())
    ref.update(json.loads((LAB / "race-shape-2d" / "out" / "partA3_turns.json").read_text()))
    assert {g: got[g] for g in ref} == ref


@lab_parity
@pytest.mark.skipif(not os.environ.get("HKRD_SLOW"), reason="3 minutes of LightGBM: HKRD_SLOW=1 as well")
def test_the_shape_models_are_the_labs_on_its_inputs(lab_trip: pd.DataFrame) -> None:
    import sqlite3
    c = sqlite3.connect(f"file:{PULL}?mode=ro", uri=True)
    card = pd.read_sql("""SELECT u.race_date, u.race_no, u.horse_no, u.horse_name, u.draw, u.jockey, u.trainer,
                                 u.gear, u.actual_weight, u.rating, a.venue, a.course, a.surface, a.going, a.distance,
                                 a.race_class, a.race_name, a.off_time
                            FROM runners u JOIN races a USING (race_date, race_no) WHERE u.race_date = '2026-10-25'""", c)
    c.close()
    t = lab_trip.copy()
    t["is_card"] = 0
    ex = shape.features(pd.concat([t, shape.card_rows(card)], ignore_index=True, sort=False))
    hist, C = ex[ex.is_card == 0], ex[ex.is_card == 1]
    boosters = {k: shape.fit_final(hist, k, shape.fit_rounds(hist, k, season=2026), train_to=2026) for k in shape.KINDS}
    P = shape.predict(boosters, C)
    lab = pd.read_csv(LAB / "race-shape-2d" / "out" / "card_2026-10-25_lanes.csv")
    m = P.merge(lab, on=["race_no", "horse_no"], suffixes=("", "_lab"))
    assert len(m) == 142
    for k in ("pred_early", "p_lead", "exp_lane", "p_nocover"):
        assert (m[k] - m[f"{k}_lab"]).abs().max() < 1e-6, k

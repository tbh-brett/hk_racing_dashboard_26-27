"""The fundamental model (gbm-SPEC §9, §14.2).

Step 1 is the inputs, and every test here asks the same question from a
different side: can anything that happened at or after a race reach that
race's row? A later run, the same day's earlier races, the card's own price or
result, or a whole meeting cut away from the database.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hkrd.derive import features as F
from hkrd.store import asof
from hkrd.store.connect import get_conn, init_db, transaction
from hkrd.store.gbm import load_runs

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = Path(os.environ.get("HKRD_TEST_DB", ROOT / "hkrd.db"))
INPUTS = F.FEATURES + F.CATEGORICAL

HORSES = [f"HORSE {i:02d}" for i in range(24)]
JOCKEYS = ["J ONE", "J TWO", "J THREE", "J FOUR", "J FIVE"]
TRAINERS = ["T ONE", "T TWO", "T THREE"]


def _race(rng, date: str, race_no: int, horses: list[str], *, venue="ST",
          surface="Turf", distance=1200, going="G", race_class="4") -> list[dict]:
    """One run race: a random finish, times and margins that follow it."""
    order = rng.permutation(len(horses))
    rows = []
    for i, h in enumerate(horses):
        place = int(order[i]) + 1
        rows.append({
            "race_date": date, "race_no": race_no, "horse_no": i + 1, "horse_name": h,
            "place": place, "place_code": None,
            "finish_time": 70.0 + 0.2 * place + rng.normal(0, 0.05),
            "lengths_behind": 0.0 if place == 1 else 0.75 * (place - 1),
            "draw": i + 1, "jockey": JOCKEYS[(i + race_no) % len(JOCKEYS)],
            "trainer": TRAINERS[i % len(TRAINERS)], "actual_weight": 120 + i,
            "declared_weight": 1100 + 10 * i, "rating": 60 - i,
            "win_odds": float(rng.choice([2.5, 4.0, 6.0, 10.0, 10.0, 20.0, 50.0])),
            "running_positions": " ".join(str(rng.integers(1, len(horses) + 1)) for _ in range(3)),
            "venue": venue, "surface": surface, "going": going,
            "distance": distance, "race_class": race_class,
            "early_dev": rng.normal(), "late_dev": rng.normal(),
            **({"n_trouble": float(rng.integers(0, 2)), "n_wide": 0.0, "n_vet": 0.0,
                "eased": 0.0, "weakened": 1.0, "keen": 0.0} if i % 3 else
               dict.fromkeys(["n_trouble", "n_wide", "n_vet", "eased", "weakened", "keen"],
                             np.nan)),
        })
    return rows


def _archive(seed: int = 7, meetings: int = 14) -> pd.DataFrame:
    """Two races a meeting, a week apart, from a pool of 24 horses: enough for
    every horse to have lags, a preparation break and a rider record."""
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(meetings):
        # a 70-day gap before meeting 8 starts new preparations
        date = (pd.Timestamp("2025-09-07") + pd.Timedelta(days=7 * m + (70 if m >= 8 else 0))
                ).strftime("%Y-%m-%d")
        pool = list(rng.permutation(HORSES))
        rows += _race(rng, date, 1, pool[:10], distance=1200 if m % 2 else None)
        rows += _race(rng, date, 2, pool[10:20], venue="HV", distance=1650)
    return pd.DataFrame(rows)


def _rows(df: pd.DataFrame, date: str, race_no: int | None = None) -> pd.DataFrame:
    sel = df["race_date"] == date
    if race_no is not None:
        sel &= df["race_no"] == race_no
    return df.loc[sel, ["horse_name", *INPUTS, *F.FACTS]].reset_index(drop=True)


# ─── the definition ──────────────────────────────────────────────────────────

def test_the_groups_cover_every_input_once() -> None:
    # 83 numbers and two categories: the "85 of 85 inputs" of the replay audit
    assert len(INPUTS) == 85 and len(set(INPUTS)) == 85
    grouped = [f for fs in F.GROUPS.values() for f in fs]
    assert sorted(grouped) == sorted(INPUTS), "every input in exactly one group"
    assert list(F.GROUPS) == ["form", "market history", "rider", "stable", "draw", "weight",
                              "rating & class", "campaign", "trip & track",
                              "pace & sectionals", "trouble & vet", "race"]
    assert set(F.TIME) <= set(F.FEATURES)


def test_the_price_is_never_an_input() -> None:
    # the market's PAST opinion is allowed; today's closing price is only the benchmark
    assert "p_mkt" not in INPUTS and "win_odds" not in INPUTS
    assert {"l1_logp_run", "ew_logp_run"} <= set(F.FEATURES)


# ─── nothing from later ──────────────────────────────────────────────────────

def test_a_later_run_changes_nothing_before_it() -> None:
    arc = _archive()
    last = arc["race_date"].max()
    before = F.build(arc[arc["race_date"] < last])
    after = F.build(arc)
    earlier = after[after["race_date"] < last].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, earlier, check_exact=True)


def test_a_later_result_rewritten_changes_nothing_before_it() -> None:
    arc = _archive()
    last = arc["race_date"].max()
    edited = arc.copy()
    later = edited["race_date"] == last
    edited.loc[later, "place"] = edited.loc[later, "place"].to_numpy()[::-1]
    edited.loc[later, "win_odds"] = 99.0
    a, b = F.build(arc), F.build(edited)
    keep = a["race_date"] < last
    pd.testing.assert_frame_equal(a[keep], b[keep], check_exact=True)


def test_the_same_days_earlier_race_does_not_move_a_later_one() -> None:
    """A rider's win in race 1 must not move his figures in race 2 that day."""
    arc = _archive()
    date = sorted(arc["race_date"].unique())[9]
    r1 = (arc["race_date"] == date) & (arc["race_no"] == 1)
    flipped = arc.copy()
    flipped.loc[r1, "place"] = flipped.loc[r1, "place"].to_numpy()[::-1]
    flipped.loc[r1, "win_odds"] = flipped.loc[r1, "win_odds"].to_numpy()[::-1]
    a, b = _rows(F.build(arc), date, 2), _rows(F.build(flipped), date, 2)
    riders = set(arc.loc[r1, "jockey"]) & set(arc.loc[(arc["race_date"] == date)
                                                      & (arc["race_no"] == 2), "jockey"])
    assert riders, "the fixture must share a rider across the day's two races"
    pd.testing.assert_frame_equal(a, b, check_exact=True)


# ─── the card ────────────────────────────────────────────────────────────────

def _with_card(arc: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    date = "2026-03-01"     # after the archive's last meeting
    assert date > arc["race_date"].max()
    rng = np.random.default_rng(3)
    card = pd.DataFrame(_race(rng, date, 1, list(HORSES[:12])))
    return pd.concat([arc, card], ignore_index=True), date


def test_a_cards_price_and_result_never_reach_it() -> None:
    """Whatever the table holds for a race not yet run, the inputs are those of
    a card with every result column blank."""
    arc, date = _with_card(_archive())
    blank = arc.copy()
    on = blank["race_date"] == date
    for c in ("place", "finish_time", "lengths_behind", "win_odds", "running_positions",
              "early_dev", "late_dev"):
        blank.loc[on, c] = np.nan
    a = F.build(arc, card_dates=(date,))
    b = F.build(blank, card_dates=(date,))
    pd.testing.assert_frame_equal(_rows(a, date), _rows(b, date), check_exact=True)
    card = a[a["race_date"] == date]
    assert card["is_card"].all()
    assert card["p_mkt"].isna().all() and card["won"].isna().all() and (card["y"] == 0).all()


def test_a_meeting_scored_as_a_card_has_the_inputs_it_has_once_run() -> None:
    """§14.2 without a database: nothing a run produces is read for that run."""
    arc = _archive()
    last = arc["race_date"].max()
    as_card = _rows(F.build(arc, card_dates=(last,)), last)
    as_run = _rows(F.build(arc), last)
    pd.testing.assert_frame_equal(as_card, as_run, check_exact=True, check_dtype=False)


def test_a_cards_apprentice_is_read_as_the_results_name_him() -> None:
    """The card: "J TWO (-7)" at 127 lb. The result: "J TWO" carrying 120. The
    model learned the second, so the first must read as it (1 Oct 2026, H Y Yuen)."""
    arc, date = _with_card(_archive())
    plain = arc.copy()
    claimed = arc.copy()
    on = (claimed["race_date"] == date) & (claimed["horse_no"] <= 4)
    claimed.loc[on, "jockey"] = claimed.loc[on, "jockey"] + " (-7)"
    claimed.loc[on, "actual_weight"] = claimed.loc[on, "actual_weight"] + 7
    a = _rows(F.build(plain, card_dates=(date,)), date)
    b = _rows(F.build(claimed, card_dates=(date,)), date)
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_a_scratched_runner_is_not_in_the_field() -> None:
    arc, date = _with_card(_archive())
    out = arc["race_date"].eq(date) & arc["horse_no"].eq(3)
    arc.loc[out, "place_code"] = "WV"
    card = F.build(arc, card_dates=(date,))
    card = card[card["race_date"] == date]
    assert 3 not in set(card["horse_no"])
    assert (card["field"] == 11).all()


def test_a_card_without_body_weight_carries_the_last_runs() -> None:
    arc, date = _with_card(_archive())
    arc.loc[arc["race_date"] == date, "declared_weight"] = np.nan
    out = F.build(arc, card_dates=(date,))
    hist = out[~out["is_card"]].groupby("horse_name").tail(1).set_index("horse_name")
    card = out[out["race_date"] == date].set_index("horse_name")
    raced = card[card["n_prior"] > 0]
    assert len(raced) > 0
    assert np.array_equal(raced["declared_weight"].to_numpy(dtype=float),
                          hist.loc[raced.index, "declared_weight"].to_numpy(dtype=float))
    assert (raced["bw_change"] == 0).all()


# ─── the store ───────────────────────────────────────────────────────────────

def test_load_reads_key_order_and_leaves_untagged_runs_unknown(tmp_path) -> None:
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    with transaction(conn):
        conn.execute("INSERT INTO races (race_date, race_no, venue, surface, distance) "
                     "VALUES ('2026-01-04', 1, 'ST', 'Turf', 1200)")
        for no in (3, 1, 2):
            conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name, place, "
                         "win_odds) VALUES ('2026-01-04', 1, ?, ?, ?, 5.0)", (no, f"H{no}", no))
        conn.execute("INSERT INTO runner_tags (race_date, race_no, horse_no, tag) "
                     "VALUES ('2026-01-04', 1, 2, 'bumped')")
    runs = load_runs(conn)
    conn.close()
    assert list(runs["horse_no"]) == [1, 2, 3]
    # rating is NULL on every row here, as in any chunk before 2024: still a number
    assert runs["rating"].dtype == float and runs["race_no"].dtype.kind == "i"
    assert runs.loc[1, "n_trouble"] == 1 and runs.loc[1, "keen"] == 0
    assert runs.loc[[0, 2], "n_trouble"].isna().all()


# ─── a real meeting, cut from the database (gbm-SPEC §14.2) ──────────────────

AS_OF = "2026-09-06"


def _has_meeting(db: Path, date: str) -> bool:
    if not db.is_file():
        return False
    conn = get_conn(db)
    try:
        return conn.execute("SELECT count(*) FROM races WHERE race_date = ?",
                            (date,)).fetchone()[0] > 0
    finally:
        conn.close()


@pytest.mark.skipif(not _has_meeting(REAL_DB, AS_OF),
                    reason=f"needs a database holding {AS_OF} (HKRD_TEST_DB)")
def test_a_meeting_cut_from_the_database_has_the_same_inputs(tmp_path) -> None:
    """The world as it stood that morning -- every table cut at the day, the
    day put back as a card -- gives the same inputs as the full archive."""
    cut = tmp_path / "asof.db"
    report = asof.cut_copy(REAL_DB, cut, AS_OF)
    assert report["result_cells_on_card"] == 0 and report["runners_after"] == 0
    conn = get_conn(cut)
    card = F.build(load_runs(conn), card_dates=(AS_OF,))
    conn.close()
    conn = get_conn(REAL_DB)
    full = F.build(load_runs(conn))
    conn.close()
    a, b = _rows(card, AS_OF), _rows(full, AS_OF)
    assert len(a) == report["card_runners"] > 0
    pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=False)


# ─── step 2: the model ───────────────────────────────────────────────────────

from hkrd.jobs import fit_gbm  # noqa: E402
from hkrd.model import gbm, gbm_record  # noqa: E402
from hkrd.store import gbm as store  # noqa: E402


def _frame(races: int = 480, field: int = 10, seed: int = 11, signal: float = 1.2,
           start: str = "2020-09-06") -> pd.DataFrame:
    """A model-ready frame: every input present, the winner drawn from a
    softmax on the draw (inside is better), everything else noise."""
    rng = np.random.default_rng(seed)
    n = races * field
    f = pd.DataFrame(rng.normal(size=(n, len(F.FEATURES))), columns=F.FEATURES)
    f["draw"] = np.tile(np.arange(1, field + 1), races).astype(float)
    f["vs"] = rng.choice(["ST_Turf", "HV_Turf", "ST_AWT"], n)
    f["going_g"] = "G"
    days = np.repeat(np.arange(races) // 8 * 4, field)
    f["race_date"] = (pd.Timestamp(start) + pd.to_timedelta(days, "D")).strftime("%Y-%m-%d")
    f["race_no"] = np.repeat(np.arange(races) % 8 + 1, field)
    f["horse_no"] = np.tile(np.arange(1, field + 1), races)
    f["race_id"] = f["race_date"] + "_" + f["race_no"].astype(str)
    f["season"] = F.season_of(pd.to_datetime(f["race_date"]))
    races_ = gbm.Races(f["race_id"].to_numpy())
    p = races_.softmax(-signal * f["draw"].to_numpy() / field)
    winner = np.array([s + rng.choice(c, p=p[s:s + c]) for s, c in zip(races_.starts, races_.counts)])
    f["won"] = 0.0
    f.loc[winner, "won"] = 1.0
    f["y"], f["p_mkt"] = f["won"], p
    return f.sort_values(["race_id"], kind="stable").reset_index(drop=True)


def test_the_race_softmax_gradient_is_the_derivative_of_the_race_loss() -> None:
    races = gbm.Races(np.array(["a"] * 4 + ["b"] * 3))
    y = np.array([0, 1, 0, 0, 0.5, 0.5, 0])          # a dead heat splits the win
    s = np.random.default_rng(0).normal(size=7)
    grad, hess = gbm._objective(races, y)(s, None)
    loss = lambda v: races.nll(races.softmax(v), y).sum()  # noqa: E731
    h = 1e-6
    fd = np.array([(loss(s + h * e) - loss(s - h * e)) / (2 * h) for e in np.eye(7)])
    assert np.allclose(grad, fd, atol=1e-6)
    assert (hess > 0).all()


def test_every_races_chances_add_to_one_and_places_to_the_places_paid() -> None:
    f = _frame(races=320)
    b = gbm.fit(f, rounds=30)
    p = gbm.predict(b, f)
    total = pd.Series(p).groupby(f["race_id"]).sum()
    assert np.allclose(total, 1.0)
    pl = gbm.place_chances(p, f["race_id"].to_numpy())
    assert np.allclose(pd.Series(pl).groupby(f["race_id"]).sum(), 3.0)
    assert (pl >= p - 1e-12).all() and (pl <= 1).all()


def test_a_model_reloaded_from_its_text_scores_the_same() -> None:
    f = _frame(races=320)
    b = gbm.fit(f, rounds=30)
    card = f.iloc[:40].copy()
    card["vs"] = "HV_Turf"                            # one category seen: the codes must hold
    again = gbm.from_text(b.model_to_string())
    assert np.array_equal(gbm.predict(b, card), gbm.predict(again, card))
    g = gbm.contributions(again, card)
    assert list(g.columns) == list(F.GROUPS)
    assert np.allclose(g.groupby(card["race_id"].to_numpy()).mean(), 0.0)


def test_a_worse_model_is_not_promoted_and_a_same_one_is() -> None:
    """gbm-SPEC §9: fewer trees than the live recipe is a worse model."""
    f = _frame(races=1600, signal=2.5)
    dates = sorted(f["race_date"].unique())
    live = {"version": "live", "features_version": F.DERIVE_VERSION, "rounds": 150,
            "params": gbm.recipe(150)}
    none = store.shown(None, [])
    worse = fit_gbm._gate(f, dates, 1, live, none)
    assert not worse["promote"] and "worse" in worse["reason"]
    same = fit_gbm._gate(f, dates, 150, live, none)
    assert same["promote"] and same["same_recipe"]
    assert fit_gbm._gate(f, dates, 150, None, none)["promote"]


def test_once_the_page_has_shown_the_window_that_is_the_benchmark() -> None:
    f = _frame(races=1600, signal=2.5)
    dates = sorted(f["race_date"].unique())
    live = {"version": "live", "features_version": F.DERIVE_VERSION, "rounds": 150,
            "params": gbm.recipe(150)}
    window = f[f["race_date"].isin(dates[-8:])]
    flat = window[["race_date", "race_no", "horse_no"]].assign(p_win=0.1)   # a page that knew nothing
    v = fit_gbm._gate(f, dates, 150, live, flat)
    assert v["benchmark"] == "what the page showed" and v["promote"] and v["diff"] < 0
    holed = fit_gbm._gate(f, dates, 150, live, flat.iloc[1:])             # one starter missing
    assert holed["benchmark"].startswith("the live recipe")


def test_calibration_fails_only_a_model_that_is_plainly_off() -> None:
    rng = np.random.default_rng(5)
    p = rng.uniform(0.01, 0.34, 20_000)
    honest = (rng.uniform(size=p.size) < p).astype(float)
    assert gbm_record.calibration_check(p, honest) == []
    doubled = (rng.uniform(size=p.size) < np.clip(2 * p, 0, 1)).astype(float)
    assert gbm_record.calibration_check(p, doubled)


def test_the_flags_follow_their_rules() -> None:
    f = pd.DataFrame({
        "prep_run": [2, 2, 1, 1, 3], "l1_place": [7, 6, np.nan, 4, 1],
        "n_prior": [5, 5, 0, 9, 9], "vs": ["ST_Turf", "HV_Turf", "ST_AWT", "ST_Turf", "ST_Turf"],
        "l1_vs": ["ST_AWT", "ST_Turf", None, "ST_AWT", "ST_Turf"],
        "l2_vs": ["ST_AWT", "ST_Turf", None, "ST_Turf", "ST_Turf"],
        "l3_vs": ["HV_Turf", "ST_AWT", None, "ST_Turf", "ST_Turf"],
        "hab_early": [0.1, 0.5, np.nan, 0.1, 0.2], "n_leaders": [1, 1, 1, 2, 0]})
    fl = gbm_record.flags(f)
    assert fl["second_up_bad"].tolist() == [True, False, False, False, False]
    assert fl["awt_form"].tolist() == [True, False, False, False, False]
    assert fl["lone_leader"].tolist() == [True, False, False, False, False]
    assert fl["first_up"].tolist() == [False, False, False, True, False]
    assert fl["no_hk"].tolist() == [False, False, True, False, False]


def test_one_model_is_live_and_a_superseded_fit_gives_up_its_text(tmp_path) -> None:
    conn = get_conn(tmp_path / "m.db")
    init_db(conn)

    def fit(v: str, at: str) -> dict:
        return {"version": v, "kind": "fundamental", "trained_through": "2026-09-27",
                "features_version": F.DERIVE_VERSION, "params": gbm.recipe(10), "rounds": 10,
                "model_text": f"model {v}", "record": {"gate": {}}, "created_at": at}
    with transaction(conn):
        store.save_model(conn, fit("a", "2026-09-28T00:00:00"))
        store.promote(conn, "a", at="2026-09-28T00:00:01")
        store.save_model(conn, fit("b", "2026-09-29T00:00:00"))     # held back
        store.save_model(conn, fit("c", "2026-09-30T00:00:00"))
        store.save_model(conn, fit("c", "2026-09-30T00:00:00"))     # a re-run: one row
        store.promote(conn, "c", at="2026-09-30T00:00:01")
    assert store.live_model(conn)["version"] == "c"
    assert conn.execute("SELECT count(*) FROM gbm_models").fetchone()[0] == 3
    assert store.model_row(conn, "a")["model_text"] == "model a"     # was live: kept
    assert store.model_row(conn, "b")["model_text"] == ""            # never live: released
    assert store.model_row(conn, "a")["promoted_at"] is not None
    conn.close()


# ─── step 3: scoring a card ──────────────────────────────────────────────────

from hkrd.jobs import score_gbm  # noqa: E402

_RACE_COLS = ["race_date", "race_no", "venue", "surface", "distance", "race_class", "going"]
_RUNNER_COLS = ["race_date", "race_no", "horse_no", "horse_name", "place", "place_code",
                "finish_time", "lengths_behind", "draw", "jockey", "trainer", "actual_weight",
                "declared_weight", "rating", "win_odds", "running_positions"]


def _scoring_db(path: Path) -> tuple[Path, str]:
    """The synthetic archive in a real database, a card after it with no
    results, no body weights and no going, and a live model fitted on it."""
    arc, date = _with_card(_archive())
    on = arc["race_date"] == date
    arc.loc[on, ["place", "finish_time", "lengths_behind", "win_odds", "running_positions",
                 "declared_weight"]] = None
    arc.loc[on, "going"] = None
    arc.loc[on & (arc["horse_no"] == 2), "jockey"] = "J TWO (-7)"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        races = arc[_RACE_COLS].drop_duplicates(["race_date", "race_no"])
        conn.executemany(f"INSERT INTO races ({', '.join(_RACE_COLS)}) VALUES "
                         f"({', '.join('?' * len(_RACE_COLS))})",
                         races.astype(object).where(races.notna(), None).values.tolist())
        run = arc[_RUNNER_COLS].astype(object)
        conn.executemany(f"INSERT INTO runners ({', '.join(_RUNNER_COLS)}) VALUES "
                         f"({', '.join('?' * len(_RUNNER_COLS))})",
                         run.where(run.notna(), None).values.tolist())
    frame = F.build(load_runs(conn))
    hist = frame[~frame["is_card"]].reset_index(drop=True)
    booster = gbm.fit(hist, rounds=20)
    with transaction(conn):
        store.save_model(conn, {"version": "test+1", "kind": gbm.KIND,
                                "trained_through": hist["race_date"].max(),
                                "features_version": F.DERIVE_VERSION, "params": gbm.recipe(20),
                                "rounds": 20, "model_text": booster.model_to_string(),
                                "record": {}, "created_at": "2026-02-20T00:00:00"})
        store.promote(conn, "test+1", at="2026-02-20T00:00:01")
    conn.close()
    return path, date


def _scores(db: Path, date: str, stage: str = "latest") -> pd.DataFrame:
    conn = get_conn(db)
    try:
        return pd.read_sql("SELECT * FROM runner_gbm WHERE race_date = ? AND stage = ? "
                           "ORDER BY race_no, horse_no", conn, params=(date, stage))
    finally:
        conn.close()


def test_a_card_is_scored_once_and_again_only_when_it_changes(tmp_path) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    first = score_gbm.score(date, db)
    assert first.scored and not first.errors
    latest, card = _scores(db, date), _scores(db, date, "card")
    assert len(latest) == len(card) == 12
    assert np.isclose(latest["p_win"].sum(), 1.0)
    assert (latest["p_place"] >= latest["p_win"]).all()
    facts = [__import__("json").loads(f) for f in latest["facts_json"]]
    assert {f["going"] for f in facts} == {"assumed"}
    assert {f["body_weight"] for f in facts} <= {"last run", "median"}
    again = score_gbm.score(date, db)
    assert again.unchanged == [date] and not again.scored
    assert _scores(db, date)["scored_at"].tolist() == latest["scored_at"].tolist()


def test_a_scratching_rescores_the_race_and_the_first_read_stays(tmp_path) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    score_gbm.score(date, db)
    card_before = _scores(db, date, "card")
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE runners SET place_code = 'WV' WHERE race_date = ? AND horse_no = 5",
                     (date,))
    conn.close()
    assert score_gbm.score(date, db).scored
    latest = _scores(db, date)
    assert 5 not in set(latest["horse_no"]) and np.isclose(latest["p_win"].sum(), 1.0)
    pd.testing.assert_frame_equal(_scores(db, date, "card"), card_before)


def test_no_live_model_is_a_note_not_an_error(tmp_path) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE gbm_models SET promoted = 0")
    conn.close()
    got = score_gbm.score(date, db)
    assert not got.errors and got.notes and not got.scored


def test_the_web_process_never_loads_lightgbm() -> None:
    """gbm-SPEC §6: pages read runner_gbm. The Card button runs scrape_meeting
    and project_card inside a request, so neither may pull the model in."""
    import subprocess
    import sys
    code = ("import sys, hkrd.api.app, hkrd.jobs.scrape_meeting, hkrd.jobs.project_card; "
            "print('lightgbm' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, check=True)
    assert out.stdout.strip() == "False", out.stderr


# ─── step 4: what the pages read ─────────────────────────────────────────────

from hkrd.query import gbm as gbm_q  # noqa: E402


def test_a_scratching_after_the_score_is_renormalised_on_read(tmp_path) -> None:
    db, date = _scoring_db(tmp_path / "s.db")
    score_gbm.score(date, db)
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE runners SET place_code = 'WV' WHERE race_date = ? AND horse_no = 5",
                     (date,))
    got = gbm_q.scores(date, conn=conn)
    conn.close()
    assert (1, 5) not in got and len(got) == 11
    assert abs(sum(x["model_pct"] for x in got.values()) - 100) < 0.2
    assert abs(sum(x["place_pct"] for x in got.values()) - 300) < 0.5
    assert all(len(x["flags"]) <= gbm_q.MAX_FLAGS for x in got.values())
    assert all("body weight" in " ".join(x["assumed"]) for x in got.values())


def test_the_routes_answer_from_tables_or_say_what_is_missing(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from hkrd.api.app import app
    db, date = _scoring_db(tmp_path / "s.db")
    score_gbm.score(date, db)
    monkeypatch.setenv("HKRD_DB", str(db))
    client = TestClient(app)
    body = client.get(f"/api/model/gbm/{date}/1").json()
    assert len(body["runners"]) == 12 and body["going_assumed"]
    assert all(r["market_pct"] is None and r["gap"] is None for r in body["runners"])
    assert client.get(f"/api/model/gbm/{date}/9").status_code == 404
    rec = client.get("/api/model/gbm/record").json()
    assert rec["live"]["version"] == "test+1" and len(rec["groups"]) == 12
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE gbm_models SET promoted = 0")
    conn.close()
    assert client.get("/api/model/gbm/record").status_code == 404

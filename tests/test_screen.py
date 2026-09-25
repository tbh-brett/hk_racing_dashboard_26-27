"""The Screen — who is a chance, before the price.

model/screen (features, scoring), model/screen_fit (the fit),
query/screen_inputs (what it reads, as at the race), query/screen (the page's
payload) and the route.
"""
from __future__ import annotations

import warnings
from itertools import permutations

import numpy as np
import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from hkrd.derive.probability import _position_probabilities, place_from_win
from hkrd.model import screen as model
from hkrd.model.screen_fit import choice_sets, fit, win_probabilities
from hkrd.query import screen as screen_q
from hkrd.query.screen_inputs import gather
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

TODAY = "2026-09-27"
JUNE, JULY = "2026-06-20", "2026-07-01"
LONG_AGO = "2025-03-01"


# ── the place arithmetic ───────────────────────────────────────────────────

@pytest.mark.parametrize("n", [5, 8, 12, 14])
@pytest.mark.parametrize("places", [2, 3])
def test_the_array_form_is_the_same_harville_henery_as_the_walk(n, places) -> None:
    rng = np.random.default_rng(n * 10 + places)
    p = rng.dirichlet(np.ones(n) * 0.7)
    assert np.allclose(place_from_win(p, places=places),
                       _position_probabilities(p, 0.81, places), atol=1e-12)


def test_place_chances_add_up_to_the_places_paid() -> None:
    p = np.array([0.4, 0.2, 0.15, 0.1, 0.08, 0.04, 0.03])
    assert place_from_win(p, places=3).sum() == pytest.approx(3.0)
    assert place_from_win(p[:4] / p[:4].sum(), places=3).sum() == pytest.approx(3.0)
    assert place_from_win(p[:3] / p[:3].sum(), places=3).tolist() == [1, 1, 1]


# ── the campaign and the pieces ────────────────────────────────────────────

def test_stage_counts_runs_since_the_last_spell() -> None:
    assert model.stage(TODAY, []) == 0                               # debut
    assert model.stage(TODAY, ["2026-06-01"]) == 1                   # 118 days
    assert model.stage(TODAY, ["2026-09-13", "2026-06-01"]) == 2
    assert model.stage(TODAY, ["2026-09-20", "2026-09-06", "2026-08-20"]) == 4
    # A 59-day gap is still the same campaign; sixty starts a new one.
    assert model.stage("2026-03-01", ["2026-01-01"]) == 2
    assert model.stage("2026-03-02", ["2026-01-01"]) == 1


def test_a_jockey_on_few_rides_is_shrunk_toward_the_year() -> None:
    assert model.jockey_rate(3, 10, 0.08) == pytest.approx((3 + 4) / 60)
    assert model.jockey_rate(150, 700, 0.08) == pytest.approx(154 / 750)


def test_the_race_call_says_trouble_in_its_own_words() -> None:
    assert model.call_flags("Blocked near 200M, finished off well.")["blocked"]
    assert model.call_flags("Raced 3 wide without cover.")["wide"]
    assert model.call_flags(None) == {"blocked": False, "wide": False}


def test_a_group_race_is_above_class_one() -> None:
    assert model.class_number("Group 1") == 0.0
    assert model.class_number("1") == 1.0
    assert model.class_number("Griffin Race") == 6.0
    assert model.class_number("International") is None


# ── one runner ─────────────────────────────────────────────────────────────

def _race(**over):
    return {"race_date": TODAY, "sarr_mean": 0.0, "sarr_sd": 1.0, "n_leaders": 2,
            "j_base": 0.08, **over}


def _runner(**over):
    prev = {"race_date": "2026-09-13", "race_no": 3, "horse_no": 5, "place": 2,
            "field_size": 12, "draw": 6, "jockey": "A", "trainer": "T1",
            "rating": 60, "race_class": "4", "venue": "ST", "distance": 1200,
            "tags": set(), "running_comment": "Raced handy.", "incident_comment": None,
            "lengths_behind": 0.5}
    r = {"horse_no": 1, "horse_name": "X", "draw": 6, "jockey": "A", "trainer": "T1",
         "rating": 60, "race_class": "4", "venue": "ST", "sarr": 0.0, "style": None,
         "j_wins": 10, "j_rides": 100, "prior_dates": ["2026-09-13"], "prev": prev,
         "trials": [], "place": None, "win_odds": None}
    r.update(over)
    return r


def test_nothing_about_the_result_or_the_price_moves_a_feature() -> None:
    blind = model.features(_runner(), _race())
    told = model.features(_runner(place=1, win_odds=1.8, lengths_behind=0.0), _race())
    assert blind == told


def test_every_factor_is_scored_and_every_feature_is_a_factor() -> None:
    values, _ = model.features(_runner(), _race())
    assert set(values) == set(model.BY_KEY)


def test_beaten_last_start_with_an_excuse() -> None:
    prev = {**_runner()["prev"], "place": 9, "tags": {"held_up"}}
    v, why = model.features(_runner(prev=prev), _race())
    assert v["prev_beaten"] == 1 and v["prev_excuse_beaten"] == 1
    assert "held up" in why["prev_excuse_beaten"]
    # Beaten with nothing to blame is beaten, and no more.
    prev["tags"] = set()
    v, _ = model.features(_runner(prev=prev), _race())
    assert v["prev_beaten"] == 1 and v["prev_excuse_beaten"] == 0


def test_second_up_is_only_marked_down_after_a_bad_first_up() -> None:
    dates = ["2026-09-13", "2026-06-01"]
    prev = {**_runner()["prev"], "place": 8}
    v, _ = model.features(_runner(prior_dates=dates, prev=prev), _race())
    assert v["second_up_bad"] == 1
    prev["place"] = 3
    v, _ = model.features(_runner(prior_dates=dates, prev=prev), _race())
    assert v["second_up_bad"] == 0


def test_a_leader_is_worth_less_the_more_of_them_there_are() -> None:
    lone, _ = model.features(_runner(style="Leader"), _race(n_leaders=1))
    crowd, _ = model.features(_runner(style="Leader"), _race(n_leaders=4))
    assert lone["leader_alone"] == 1 and crowd["leader_crowd"] == 1
    assert (model.BY_KEY["leader_alone"].weight
            > model.BY_KEY["leader_crowd"].weight)


def test_only_a_trial_since_the_last_run_counts() -> None:
    before = {"trial_date": "2026-09-01", "trial_no": 1, "place": 1,
              "field_size": 10, "band": "STANDOUT"}
    after = {**before, "trial_date": "2026-09-20"}
    v, _ = model.features(_runner(trials=[before]), _race())
    assert v["trial_good"] == 0
    v, why = model.features(_runner(trials=[after]), _race())
    assert v["trial_good"] == 1 and "STANDOUT" in why["trial_good"]


def test_the_changes_since_last_start() -> None:
    v, _ = model.features(_runner(draw=1, rating=64, race_class="3",
                                  trainer="T2", venue="HV"), _race())
    for key in ("draw_in", "rating_up", "class_rise", "trainer_change", "venue_change"):
        assert v[key] == 1, key
    v, _ = model.features(_runner(draw=11, rating=56), _race())
    assert v["draw_out"] == 1 and v["rating_down"] == 1


def test_the_gate_is_read_against_the_last_three_starts_not_just_the_last() -> None:
    """Gates 12, 14, 3 and today 4: the last start says nothing moved, the
    campaign says the draw has been against it. `draw_in` cannot see it."""
    wide = [{"draw": 3}, {"draw": 14}, {"draw": 12}]
    prev = {**_runner()["prev"], "draw": 3}
    v, why = model.features(_runner(draw=4, prev=prev, history=wide), _race())
    assert v["draw_in_3"] == 1 and v["draw_in"] == 0
    assert why["draw_in_3"] == "gates 12, 14, 3, today 4"


def test_the_two_draw_readings_never_fire_together() -> None:
    """Where the last start already says it, the three-run reading stays
    quiet: one fact is never split across two columns for the fit."""
    v, _ = model.features(
        _runner(draw=1, history=[{"draw": 12}, {"draw": 11}, {"draw": 13}]), _race())
    assert v["draw_in"] == 1 and v["draw_in_3"] == 0


def test_a_horse_with_too_little_history_gets_no_three_run_reading() -> None:
    v, _ = model.features(_runner(draw=4, history=[{"draw": 14}, {"draw": 12}]), _race())
    assert v["draw_in_3"] == 0 and v["draw_out_3"] == 0


def test_the_tags_that_did_not_survive_are_not_scored() -> None:
    """`eased`, `weakened` and `bumped` all looked real against the Screen's
    own probability and none survived a walk-forward fit. They stay unread,
    and this fails if one is quietly wired back in without a refit."""
    prev = {**_runner()["prev"], "tags": {"eased", "weakened", "bumped"}}
    v, _ = model.features(_runner(prev=prev), _race())
    clean, _ = model.features(_runner(), _race())
    assert v == clean


# ── one race ───────────────────────────────────────────────────────────────

def test_a_race_s_chances_are_a_distribution() -> None:
    runners = [_runner(horse_no=i, sarr=float(i), style="Leader" if i == 3 else None)
               for i in range(1, 9)]
    out = model.score_race({"race_date": TODAY, "j_base": 0.08}, runners)
    assert sum(x["win"] for x in out) == pytest.approx(1.0)
    assert sum(x["place"] for x in out) == pytest.approx(3.0)
    # SARR: lower is better.
    assert out[0]["win"] > out[-1]["win"]


# ── the fit ────────────────────────────────────────────────────────────────

def test_the_risk_sets_read_the_first_three_home() -> None:
    S = choice_sets(np.array([0, 0, 0, 0]), np.array([2.0, 1.0, np.nan, 3.0]))
    # winner chosen from 4, second from 3, third from 2
    assert S.lens.tolist() == [4, 3, 2]
    assert S.rows[S.chosen == 1].tolist() == [1, 0, 3]


def test_the_fit_finds_a_factor_that_really_helps() -> None:
    rng = np.random.default_rng(7)
    races, n = 400, 10
    x = rng.integers(0, 2, size=races * n).astype(float)
    ids = np.repeat(np.arange(races), n)
    places = np.empty(races * n)
    for r in range(races):
        s = 0.7 * x[r * n:(r + 1) * n] + rng.gumbel(size=n)
        places[r * n:(r + 1) * n] = np.argsort(np.argsort(-s)) + 1
    beta, se = fit(x[:, None], ids, places, l2=0.0)
    assert abs(beta[0] - 0.7) < 3 * se[0]
    p = win_probabilities(x[:, None], ids, beta)
    assert np.allclose(np.bincount(ids, weights=p), 1.0)


# ── what it reads, as at the race ──────────────────────────────────────────

NAMES = ["LONE SPEED", "HELD UP HORSE", "TRIAL STAR", "CLOSE LOSER",
         "CLOSE WINNER", "BOOKED ONE", "BACK FROM INJURY", "PLAIN SAILING"]


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "s.db")
    init_db(conn)
    races = [{"race_date": d, "race_no": 1, "venue": "ST", "course": "A",
              "surface": "Turf", "going": "G", "distance": 1200, "race_class": "4"}
             for d in (LONG_AGO, JUNE, JULY, TODAY)]
    runners = []
    # Everyone but the returning horse ran in June and in July.
    for d, order in ((JUNE, [4, 3, 2, 1, 5, 6, 8]), (JULY, [1, 2, 3, 5, 4, 6, 8])):
        for i, pos in enumerate(order):
            name = NAMES[i if i < 6 else 7]
            runners.append({
                "race_date": d, "race_no": 1, "horse_no": i + 1, "horse_name": name,
                "place": str(pos), "lengths_behind": "-" if pos == 1 else str(0.5 * pos),
                "draw": 10 if name == "CLOSE LOSER" else i + 1, "jockey": f"J{i}",
                "trainer": "T", "actual_weight": 120 + (9 if name == "CLOSE LOSER" else 0),
                "running_positions": f"{1 if name == 'LONE SPEED' else 5} 5 5",
                "finish_time": 70 + pos * 0.1})
    runners.append({"race_date": LONG_AGO, "race_no": 1, "horse_no": 1,
                    "horse_name": "BACK FROM INJURY", "place": "1", "draw": 1,
                    "jockey": "J9", "trainer": "T", "finish_time": 70.0})
    for i, name in enumerate(NAMES):
        runners.append({"race_date": TODAY, "race_no": 1, "horse_no": i + 1,
                        "horse_name": name, "draw": 1 if name == "CLOSE LOSER" else i + 2,
                        "jockey": f"J{i}", "trainer": "T", "actual_weight": 125})
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
        upsert.upsert_comments(conn, [{
            "race_date": JULY, "race_no": 1, "horse_no": 2, "source": "incident",
            "comment_text": "Held up for clear running near the 200 Metres."}])
        conn.execute("INSERT INTO runner_tags (race_date, race_no, horse_no, tag, "
                     "confidence) VALUES (?, 1, 2, 'held_up', 0.9)", (JULY,))
        for d in (JUNE, JULY):
            for i in range(7):
                style = "Leader" if i == 0 else "Midfield"
                conn.execute(
                    "INSERT INTO runner_pace (race_date, race_no, horse_no, pace_style, "
                    "derive_version) VALUES (?, 1, ?, ?, 'test')", (d, i + 1, style))
        for i in range(8):
            conn.execute(
                "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, sarr_rank, "
                "n_prior, derive_version) VALUES (?, 1, ?, ?, ?, 2, 'test')",
                (TODAY, i + 1, float(i), i + 1))
        upsert.upsert_trials(conn, [{
            "trial_date": "2026-09-10", "trial_no": 1, "horse_name": "TRIAL STAR",
            "place": "1", "finish_time": "0:59.50", "venue": "ST",
            "comment_text": "Drew away to score."}])
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status, "
                     "reasoning, confidence) VALUES ('bb_1', 'BOOKED ONE', '2026-07-02', "
                     "'active', 'wants a sit', 'high')")
        conn.execute("INSERT INTO run_notes (horse_name, race_date, race_no, note, "
                     "written_at) VALUES ('PLAIN SAILING', ?, 1, 'needed it', ?)",
                     (JULY, JULY))
    yield conn
    conn.close()


def _one(races, name):
    return next(r for r in races[0]["runners"] if r["horse_name"] == name)


def test_the_inputs_are_read_as_at_the_race(db) -> None:
    races = gather(TODAY, conn=db)
    assert len(races) == 1 and races[0]["race"]["field_size"] == 8
    held = _one(races, "HELD UP HORSE")
    assert held["prev"]["race_date"] == JULY
    assert "held_up" in held["prev"]["tags"]
    assert held["prev"]["incident_comment"].startswith("Held up")
    assert held["prior_dates"] == [JULY, JUNE]
    assert _one(races, "LONE SPEED")["style"] == "Leader"
    assert _one(races, "TRIAL STAR")["trials"][0]["band"] in ("STANDOUT", "POSITIVE")


def test_a_horse_back_from_a_long_break_is_first_up_not_a_debutant(db) -> None:
    back = _one(gather(TODAY, conn=db), "BACK FROM INJURY")
    assert back["prior_dates"] == [LONG_AGO]
    assert model.stage(TODAY, back["prior_dates"]) == 1


def test_the_meeting_shortlists_four_by_chance_to_place(db) -> None:
    out = screen_q.meeting(TODAY, conn=db)
    runners = out["races"][0]["runners"]
    places = [r["place_pct"] for r in runners]
    assert places == sorted(places, reverse=True)
    assert [r["tier"] for r in runners[:4]] == ["SHORTLIST"] * 4
    assert all(r["tier"] in ("CASE", "FIELD") for r in runners[4:])
    lone = next(r for r in runners if r["horse_name"] == "LONE SPEED")
    assert any(f["key"] == "leader_alone" for f in lone["for"])
    assert out["races"][0]["pace"]["leaders"][0]["horse_name"] == "LONE SPEED"


def test_a_close_defeat_is_named_with_the_draw_and_never_the_weight(db) -> None:
    out = screen_q.meeting(TODAY, conn=db)
    loser = next(r for r in out["races"][0]["runners"] if r["horse_name"] == "CLOSE LOSER")
    note = next(n for n in loser["reversals"] if n["vs_name"] == "CLOSE WINNER")
    assert note["margin"] <= screen_q.REVERSAL_MARGIN
    assert any("draw" in m for m in note["moved"])
    text = " ".join([note["note"], *note["moved"]]).lower()
    assert "lb" not in text and "weight" not in text


def test_the_book_and_the_notes_travel_with_the_horse(db) -> None:
    out = screen_q.meeting(TODAY, conn=db)
    runners = {r["horse_name"]: r for r in out["races"][0]["runners"]}
    book = runners["BOOKED ONE"]["blackbook"]
    assert book["live"] and book["reasoning"] == "wants a sit"
    assert runners["BOOKED ONE"]["setup"] in ("FAVOURABLE", "NEUTRAL", "AGAINST")
    assert runners["PLAIN SAILING"]["notes"][0]["note"] == "needed it"
    assert runners["PLAIN SAILING"]["blackbook"] is None


def test_the_route_404s_without_a_card(tmp_path, monkeypatch, db) -> None:
    from fastapi.testclient import TestClient

    from hkrd.api.app import app
    path = db.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setenv("HKRD_DB", path)
    client = TestClient(app)
    assert client.get("/api/screen/2026-01-01").status_code == 404
    body = client.get(f"/api/screen/{TODAY}").json()
    assert body["races"][0]["race_no"] == 1
    assert {f["key"] for f in body["factors"]} == {
        f.key for f in model.FACTORS if f.shown}

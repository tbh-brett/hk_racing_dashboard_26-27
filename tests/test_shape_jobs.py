"""The simulation's jobs and their rules (racesim-SPEC §5, §9.3, §12).

jobs/sim_card with node stubbed out: the fingerprint ignores a v2 move under 0.01 and catches a
scratching or a new rider; `--next` never touches a race that has gone off, and a simulation that
finishes after the off is not kept; a throttled tick does nothing and says so; tables a simulation
names are never rewritten. query/shape_record: the record row is the last one before the off.
jobs/import_sim_record: running it twice changes nothing. The card assembly (query/shape_sim)
round-trips the lab's 25 Oct card.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from hkrd.jobs import import_sim_record, sim_card
from hkrd.model import sim_tables
from hkrd.query import shape_record, shape_sim
from hkrd.store import gbm as gbm_store
from hkrd.store import job_log, upsert
from hkrd.store import shape as shape_store
from hkrd.store import sim as store
from hkrd.store.connect import get_conn, init_db, transaction

FIX = Path(__file__).parent / "fixtures" / "shape"
ROOT = Path(__file__).resolve().parent.parent
DATE = "2026-10-25"
SETTINGS = sim_card.SETTINGS


def _card() -> dict:
    with gzip.open(FIX / "card_inputs_2026-10-25.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def _seed(db: Path, races: list[dict]) -> None:
    """A stored card exactly as the lab's card_inputs file describes it."""
    conn = get_conn(db)
    init_db(conn)
    now = "2026-10-22T04:20:13+00:00"
    with transaction(conn):
        for r in races:
            upsert.upsert_races(conn, [dict(race_date=DATE, race_no=r["no"], venue=r["venue"], course=r["course"],
                                            surface=r["surface"], going=r["going"], distance=r["distance"],
                                            race_class=r["cls"], race_name=r["name"], off_time=r["off"])])
            upsert.upsert_runners(conn, [dict(race_date=DATE, race_no=r["no"], horse_no=h["no"], horse_name=h["name"],
                                              draw=h["draw"], jockey=h["jockey"], trainer=h["trainer"],
                                              actual_weight=h["wt"], rating=h["rating"], gear=h["gear"])
                                         for h in r["runners"]])
            gbm_store.write_scores(conn, [dict(race_date=DATE, race_no=r["no"], horse_no=h["no"], p_win=h["p_win"],
                                               p_place=h["p_place"], contrib={}, facts={}, model_version="gbm-test",
                                               derive_version="t", inputs_key="k", scored_at=now)
                                          for h in r["runners"]])
            if r["par"]:
                conn.execute("INSERT OR REPLACE INTO standard_times (venue, surface, distance, class_key, standard_time, "
                             "sections, fetched_at) VALUES (?,?,?,?,?,?,?)",
                             (r["venue"], r["surface"], r["distance"], r["cls"], sum(r["par"]),
                              ";".join(str(x) for x in r["par"]), now))
            conn.executemany("INSERT INTO runner_projection (race_date, race_no, horse_no, style, settle_band, "
                             "field_size, derive_version) VALUES (?,?,?,?,?,?,?)",
                             [(DATE, r["no"], h["no"], h["prod_style"], h["prod_band"], len(r["runners"]), "t")
                              for h in r["runners"]])
        store.save_tables(conn, as_of=DATE, version=sim_tables.VERSION, trained_through="2026-10-21",
                          tables={"stub": True}, created_at=now)
    shape_store.write_shape_scores(conn, DATE, [r["no"] for r in races], [
        dict(race_date=DATE, race_no=r["no"], horse_no=h["no"], pred_early=h["pred_early"], p_lead=h["p_lead"],
             exp_lane=h["exp_lane"], p_nocover=h["p_nocover"], slow_rate=h["slow_rate"], n_prior=h["n_prior"],
             model_version="shape-test", inputs_key="k", scored_at=now) for r in races for h in r["runners"]])
    conn.close()


def _fake_result(race: dict) -> str:
    n = len(race["runners"])
    summary = [dict(no=h["no"], v2_win=h["p_win"], v2_place=h["p_place"], win=h["p_win"], top3=0.3, lead=1 / n,
                    front=0.3, mid=0.4, back=0.3, first_call_pos=5.0, nocover=0.1, wide3=0.2, held=0.05,
                    trip_cost=0.0, win_if_led=0.2, led_n=500, first_call_dist=[1 / n] * n) for h in race["runners"]]
    engine = [dict(no=h["no"], held=0.1, nocover_most=0.1, ground_lengths=1.0, run_share=0.2, round_share=0.1,
                   through_share=0.1, win=h["p_win"], top3=0.3) for h in race["runners"]]
    return json.dumps(dict(id=race["id"], anchor=dict(a=[0.0] * n, max_err=0.0002, passes=40, sims=10000),
                           summary=dict(sims=20000, runners=summary, pace={"Neutral": 1.0}, settle_sd=0.2, templates=10),
                           engine=engine, coupling={}, settle_sd=0.2, sigma=4.0, templates=["x"],
                           coupled_check=dict(runs=300, mean_win_gap_to_v2=0.01, mc_sd_mean=0.012)))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    path = tmp_path / "t.db"
    _seed(path, [r for r in _card()["races"] if r["no"] in (1, 2)])
    calls: list[int] = []

    def fake(doc: dict) -> tuple[str, float]:
        calls.append(doc["race_no"])
        return _fake_result(next(r for r in doc["card"]["races"] if r["no"] == doc["race_no"])), 1.0
    monkeypatch.setattr(sim_card, "run_node", fake)
    monkeypatch.setattr(sim_card, "CPU_STAT", tmp_path / "no-cpu.stat")
    return SimpleNamespace(db=path, calls=calls)


# ── the card assembly (§11.2) ─────────────────────────────────────────────────────────────────
def test_the_card_assembly_round_trips_the_labs_card(tmp_path: Path) -> None:
    lab = _card()
    _seed(tmp_path / "c.db", lab["races"])
    conn = get_conn(tmp_path / "c.db")
    got = shape_sim.race_inputs(shape_sim.card_inputs(conn, DATE))
    conn.close()
    assert got["races"] == lab["races"]
    assert (got["meta"]["races"], got["meta"]["runners"]) == (11, 142)


# ── the fingerprint (§5.2) ────────────────────────────────────────────────────────────────────
def _fp(race: dict) -> str:
    return shape_sim.fingerprint(race, versions=dict(engine="e", tables=[DATE, "t"], v2="v", shape="s"),
                                 settings=SETTINGS)


def test_the_fingerprint_ignores_a_v2_move_under_a_point_and_catches_a_change() -> None:
    race = _card()["races"][6]
    base = _fp(race)
    nudged = copy.deepcopy(race)
    nudged["runners"][0]["p_win"] = round(race["runners"][0]["p_win"] + 0.0004, 5)
    assert _fp(nudged) == base
    moved = copy.deepcopy(race)
    moved["runners"][0]["p_win"] = race["runners"][0]["p_win"] + 0.02
    assert _fp(moved) != base
    scratched = copy.deepcopy(race)
    scratched["runners"].pop()
    assert _fp(scratched) != base
    rider = copy.deepcopy(race)
    rider["runners"][1]["jockey"] = "Somebody Else"
    assert _fp(rider) != base


# ── which race, and when not (§5.3) ──────────────────────────────────────────────────────────
def test_next_simulates_one_race_a_tick_earliest_first_then_nothing(env: SimpleNamespace) -> None:
    out = sim_card.next_tick(env.db, today=DATE)
    assert "simulated 2026-10-25 R1" in out.did and len(out.checks) == 1
    out = sim_card.next_tick(env.db, today=DATE)
    assert "R2" in out.did
    out = sim_card.next_tick(env.db, today=DATE)
    assert out.did.startswith("nothing to simulate")
    assert env.calls == [1, 2]                                       


def test_a_race_that_has_gone_off_is_never_simulated(env: SimpleNamespace) -> None:
    conn = get_conn(env.db)
    with transaction(conn):
        upsert.upsert_market_close(conn, [dict(race_date=DATE, race_no=1, closed_at="2026-10-25T04:40:00+00:00")])
    conn.close()
    out = sim_card.next_tick(env.db, today=DATE)
    assert "R2" in out.did and env.calls == [2]                      


def test_a_simulation_that_finishes_after_the_off_is_not_kept(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(doc: dict) -> tuple[str, float]:
        conn = get_conn(env.db)
        with transaction(conn):
            upsert.upsert_market_close(conn, [dict(race_date=DATE, race_no=doc["race_no"], closed_at="x")])
        conn.close()
        return _fake_result(next(r for r in doc["card"]["races"] if r["no"] == doc["race_no"])), 1.0
    monkeypatch.setattr(sim_card, "run_node", slow)
    out = sim_card.next_tick(env.db, today=DATE)
    assert "went off while it was simulated" in out.did
    conn = get_conn(env.db)
    assert store.latest_sim(conn, DATE, 1) is None
    conn.close()


def test_a_throttled_tick_does_nothing_and_says_so(env: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stat = tmp_path / "cpu.stat"
    monkeypatch.setattr(sim_card, "CPU_STAT", stat)
    stat.write_text("usage_usec 1\nnr_periods 9\nnr_throttled 3\nthrottled_usec 7\n")
    first = sim_card.next_tick(env.db, today=DATE)
    conn = get_conn(env.db)                    # the last tick, fifteen minutes ago, as job_runs keeps it
    with transaction(conn):
        job_log.record(conn, "sim_card", started_at="2026-10-23T06:52:00+00:00", ok=True, detail=first.throttle)
    conn.close()
    stat.write_text("usage_usec 1\nnr_periods 12\nnr_throttled 5\nthrottled_usec 9\n")
    # as cron runs it: inside its own job_runs row, which is open while the tick reads the last one
    with job_log.running("sim_card", env.db) as o:
        out = sim_card.next_tick(env.db, today=DATE)
        o["ok"], o["detail"] = True, out.throttle
    assert "throttled since" in out.did and "nr_throttled=5 (was 3" in out.throttle
    assert env.calls == [1]                                          


def test_tables_a_simulation_names_are_never_rewritten(env: SimpleNamespace) -> None:
    sim_card.next_tick(env.db, today=DATE)
    conn = get_conn(env.db)
    with transaction(conn):
        wrote = store.save_tables(conn, as_of=DATE, version=sim_tables.VERSION, trained_through="x",
                                  tables={"changed": True}, created_at="now")
    assert wrote is False and store.tables_row(conn, DATE, sim_tables.VERSION)["tables"] == {"stub": True}
    conn.close()


# ── the record (§9.3) ────────────────────────────────────────────────────────────────────────
def test_the_record_is_the_last_simulation_before_the_off(env: SimpleNamespace) -> None:
    conn = get_conn(env.db)
    for at, p in (("2026-10-24T01:00:00+00:00", 0.9), ("2026-10-25T04:41:00+00:00", 0.1)):
        race = next(r for r in _card()["races"] if r["no"] == 1)
        res = json.loads(_fake_result(race))
        for s in res["summary"]["runners"]:
            s["lead"] = p if s["no"] == 1 else (1 - p) / (len(race["runners"]) - 1)
        rows = sim_card._runner_rows(res)
        with transaction(conn):
            store.write_sim(conn, dict(race_date=DATE, race_no=1, fingerprint=at, simulated_at=at, engine_version="e",
                                       tables_as_of=DATE, tables_version="t", v2_version="v", shape_version="s",
                                       settings_json=SETTINGS, cpu_seconds=1.0),
                            dict(card=dict(races=[race])), json.dumps(res), rows)
    with transaction(conn):
        upsert.upsert_market_close(conn, [dict(race_date=DATE, race_no=1, closed_at="2026-10-25T04:40:00+00:00")])
        upsert.upsert_runners(conn, [dict(race_date=DATE, race_no=1, horse_no=h["no"], horse_name=h["name"],
                                          place=str(i + 1), win_odds=5.0, running_positions=f"{i + 1} {i + 1} {i + 1}")
                                     for i, h in enumerate(race["runners"])])
    got = shape_record.record(conn=conn)
    conn.close()
    # horse 1 led; the record row gave it 90% to lead, the row after the off 10%
    rows = got["meetings"][0]
    assert rows["races"] == 1 and rows["p_lead"]["calibration"][-1]["led"] == 1
    assert rows["p_lead"]["log_loss"] == pytest.approx(-math.log(0.9), abs=1e-3)


def test_importing_the_19_oct_record_twice_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    path = ROOT / "ops" / "records" / "sim_2026-10-19.json.gz"
    assert import_sim_record.run(path, db).endswith("11 races written, 0 already held")
    assert import_sim_record.run(path, db).endswith("0 races written, 11 already held")
    conn = get_conn(db)
    n = conn.execute("SELECT count(*) FROM runner_sim WHERE race_date = '2026-10-19'").fetchone()[0]
    conn.close()
    assert n == 145

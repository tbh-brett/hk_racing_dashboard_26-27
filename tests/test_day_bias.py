"""A meeting's own bias (model/day_bias, store/day, jobs/save_day_lanes, jobs/fetch_run_races,
query/shape_day, /api/shape/day), and the finishing spread by trip (model/sim_tables).

The logit recovers a bias it was given; the persistence test finds a day effect that is there and
none where there is none -- the record's verdict on the archive (614 meetings, none) is only worth
something if the test can see one. Brett's lane reads round-trip and clear. The mid-meeting fetch
asks HKJC only about races that have gone off, and stores nothing whose runners this meeting never
declared. A card's AWT races are read apart from its turf ones, in their own trip's lengths.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hkrd.jobs import fetch_run_races, save_day_lanes
from hkrd.model import day_bias, sim_tables
from hkrd.query import shape_day
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _synthetic(meetings: int, races: int, day_sd: float, base: float = 0.0, seed: int = 1) -> pd.DataFrame:
    """Races won under a known bias: each meeting's front-runner effect is base + N(0, day_sd)."""
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(meetings):
        b = base + rng.normal(0, day_sd)
        for r in range(1, races + 1):
            n = 12
            p = rng.dirichlet(np.ones(n) * 2)
            front, inside, rail = rng.random(n), rng.random(n), (rng.random(n) < 0.3).astype(float)
            w = p * np.exp(b * front)
            winner = rng.choice(n, p=w / w.sum())
            for i in range(n):
                rows.append(dict(race_date=f"2025-{m // 28 + 1:02d}-{m % 28 + 1:02d}", race_no=r,
                                 race_id=f"{m}_{r:02d}", p_mkt=p[i], won=float(i == winner), front=front[i],
                                 inside=inside[i], rail=rail[i]))
    return pd.DataFrame(rows)


def test_the_logit_recovers_the_bias_it_was_given() -> None:
    d = _synthetic(200, 10, day_sd=0.0, base=1.2)
    b, se = day_bias.fit(d)
    assert b[0] == pytest.approx(1.2, abs=3 * se[0])
    assert abs(b[1]) < 3 * se[1] and abs(b[2]) < 3 * se[2]


def test_persistence_finds_a_day_effect_that_is_there_and_none_that_is_not() -> None:
    there = day_bias.persistence(_synthetic(150, 10, day_sd=1.5, seed=2))
    assert there["front"]["z"] > 3 and there["front"]["day_sd"] > 0.4
    none = day_bias.persistence(_synthetic(150, 10, day_sd=0.0, seed=3))
    assert abs(none["front"]["z"]) < 3 and none["front"]["day_sd"] < there["front"]["day_sd"] / 2


def test_a_coefficient_turns_into_lengths_on_the_simulators_noise() -> None:
    assert day_bias.logit_to_lengths(1.0, 3.8) == pytest.approx(2.964)


# ── the day itself ─────────────────────────────────────────────────────────────────────────────
DATE = "2026-10-25"


def _meeting(db: Path, *, run: int, closed: int, awt: tuple[int, ...] = ()) -> None:
    """A four-race card: `run` races with a result, `closed` more off with none stored; the races
    in `awt` on the all-weather."""
    conn = get_conn(db)
    init_db(conn)
    with transaction(conn):
        for no in range(1, 5):
            upsert.upsert_races(conn, [dict(race_date=DATE, race_no=no, venue="ST", surface="AWT" if no in awt else "Turf",
                                            distance=1200,
                                            race_class="4", course="A", off_time=f"1{no}:00")])
            result = no <= run
            upsert.upsert_runners(conn, [dict(race_date=DATE, race_no=no, horse_no=h, horse_name=f"R{no} HORSE {h}",
                                              draw=h, win_odds=float(2 + h), place=str(h) if result else None,
                                              running_positions=(f"{h} {h} {h}" if result else None))
                                         for h in range(1, 7)])
            if no <= run + closed:
                upsert.upsert_market_close(conn, [dict(race_date=DATE, race_no=no, closed_at=f"2026-10-25T0{no}:00:00+00:00")])
    conn.close()


def test_lane_reads_round_trip_a_second_click_takes_one_back_and_clear_removes_a_race(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    _meeting(db, run=2, closed=0)
    save_day_lanes.save(DATE, 1, 1, "rail", db=db)
    save_day_lanes.save(DATE, 1, 2, "outside", db=db)
    save_day_lanes.save(DATE, 2, 1, "middle", db=db)
    conn = get_conn(db)
    got = shape_day.day(DATE, conn=conn)
    assert got["races"][0]["placed"][0]["lane"] == "rail"
    assert got["surfaces"][0]["tally"]["placed_lanes"] == {"rail": 1, "middle": 1, "outside": 1}
    conn.close()
    save_day_lanes.save(DATE, 1, 2, None, db=db)
    assert save_day_lanes.clear(DATE, 1, db=db)["cleared"] == 1
    conn = get_conn(db)
    got = shape_day.day(DATE, conn=conn)
    assert [h["lane"] for h in got["races"][0]["placed"]] == [None, None, None]
    assert got["races"][1]["placed"][0]["lane"] == "middle"
    conn.close()
    with pytest.raises(ValueError):
        save_day_lanes.save(DATE, 1, 1, "wide", db=db)


def test_the_days_read_needs_two_finished_races_and_says_where_each_race_stands(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    _meeting(db, run=1, closed=1)
    conn = get_conn(db)
    got = shape_day.day(DATE, conn=conn)
    assert [r["status"] for r in got["races"]] == ["result", "off", "to run", "to run"]
    turf = got["surfaces"][0]
    assert turf["read"]["note"].startswith("one finished race")
    assert turf["tally"]["races"] == 1 and turf["tally"]["winner_front_third"] == 1      # gate 1 led and won
    conn.close()
    _meeting(db, run=3, closed=0)
    conn = get_conn(db)
    read = shape_day.day(DATE, conn=conn)["surfaces"][0]["read"]
    assert read["races"] == 3 and read["front"]["lengths"] > 0 and "rail" not in read
    assert read["sigma"] == shape_day.SIGMA_LENGTHS                   # no tables for the card: the pooled spread
    conn.close()


def test_a_cards_awt_races_are_read_apart_from_its_turf_ones(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    _meeting(db, run=4, closed=0, awt=(3, 4))
    conn = get_conn(db)
    got = shape_day.day(DATE, conn=conn)
    by = {s["surface"]: s for s in got["surfaces"]}
    assert set(by) == {"Turf", "AWT"} and [r["surface"] for r in got["races"]] == ["Turf", "Turf", "AWT", "AWT"]
    assert by["Turf"]["tally"]["races"] == 2 and by["AWT"]["tally"]["races"] == 2
    assert by["Turf"]["read"]["races"] == 2 and by["AWT"]["read"]["races"] == 2
    conn.close()


def test_the_finishing_spread_is_each_trips_own_where_it_has_enough_runs() -> None:
    rng = np.random.default_rng(4)
    rows = []
    for venue, surface, dist, sd, races in (("ST", "AWT", 1650, 5.5, 120), ("ST", "Turf", 1200, 3.5, 120),
                                            ("HV", "Turf", 1650, 4.0, 20)):
        for r in range(races):
            for h in range(12):
                rows.append(dict(venue=venue, surface=surface, distance=dist, logp=np.log(1 / 12), field=12,
                                 cls="4", lens_vs_med=rng.normal(0, sd)))
    got = sim_tables.group_noise(pd.DataFrame(rows))
    assert got["ST AWT 1650+"]["sd_lengths"] == pytest.approx(5.5, abs=0.2)
    assert got["ST 1200"]["sd_lengths"] == pytest.approx(3.5, abs=0.2)
    assert "HV 1650" not in got                                        # 240 runs: too few for a spread of its own
    T = dict(noise=dict(sd_lengths=4.0, by_group=got), costs={})
    assert sim_tables.for_race(T, "ST AWT 1650+")["noise"]["sd_lengths"] == got["ST AWT 1650+"]["sd_lengths"]
    assert sim_tables.for_race(T, "HV 1650")["noise"]["sd_lengths"] == 4.0
    assert T["noise"]["sd_lengths"] == 4.0                             # the stored tables are not changed


def test_a_trips_facts_say_how_its_winners_raced_against_the_price() -> None:
    rows = []
    for r in range(40):
        for h in range(1, 13):
            led = h == 1                                               # gate 1 leads and, at a fair price, wins
            rows.append(dict(venue="ST", surface="AWT", distance=1650, race_id=f"r{r}", field=12, draw=h,
                             early_frac=(h - 1) / 11, early_rank=h / 12, won=float(led), p_mkt=1 / 12,
                             place=h, lengths_behind=None if led else 2.5 * (h - 1)))
    got = day_bias.trip_facts(pd.DataFrame(rows))["ST_AWT_1650"]
    assert got["races"] == 40 and got["led_ae"] > 1 and got["closer_ae"] == 0
    assert got["won_by_2"] == 1.0 and got["margin_median"] == 2.5 and got["inside_win"] > got["wide_win"]


def test_the_fetch_asks_only_about_races_gone_off_and_refuses_another_meetings_page(tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "d.db"
    _meeting(db, run=1, closed=2)                    # R1 has a result, R2 and R3 are off without one, R4 to run
    asked = []

    def fake(date, venue, race_no, session=None):
        asked.append(race_no)
        names = [f"R{race_no} HORSE {h}" for h in range(1, 7)] if race_no == 2 else [f"OTHER {h}" for h in range(1, 7)]
        return dict(race_no=race_no, venue="ST", distance=1200, runners=[
            dict(horse_no=h, horse_name=n, place=str(h), win_odds=3.0, draw=h, running_position=f"{h} {h} {h}")
            for h, n in enumerate(names, start=1)], incidents=[])
    monkeypatch.setattr(fetch_run_races.results_ingest, "fetch_race", fake)
    out = fetch_run_races.fetch(DATE, db)
    assert asked == [2, 3] and out.stored == [2] and out.waiting == ["R3: result not up yet"]
    conn = get_conn(db)
    assert [r["status"] for r in shape_day.day(DATE, conn=conn)["races"]] == ["result", "result", "off", "to run"]
    conn.close()


def test_the_day_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    db = tmp_path / "d.db"
    _meeting(db, run=2, closed=0)
    monkeypatch.setenv("HKRD_DB", str(db))
    monkeypatch.setenv("HKRD_ALLOW_NO_AUTH", "1")
    from hkrd.api.app import app
    c = TestClient(app)
    assert c.post("/api/shape/day/lane", json=dict(race_date=DATE, race_no=1, horse_no=1, lane="rail")).json()["lane"] == "rail"
    assert c.post("/api/shape/day/lane", json=dict(race_date=DATE, race_no=1, horse_no=1, lane="wide")).status_code == 422
    day = c.get(f"/api/shape/day/{DATE}").json()
    assert day["races"][0]["placed"][0]["lane"] == "rail"
    assert c.post("/api/shape/day/clear", json=dict(race_date=DATE, race_no=1)).json()["cleared"] == 1
    assert c.get("/api/shape/day/2026-01-01").status_code == 404

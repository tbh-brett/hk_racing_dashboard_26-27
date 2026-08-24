"""api/ — the JSON surface the web layer consumes."""
from __future__ import annotations

import datetime as dt
import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from hkrd.jobs import rebuild_et
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    monkeypatch.setenv("HKRD_DB", str(db))

    conn = get_conn(db)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2025, 1, 4)
    for d in range(90):
        date = (start + dt.timedelta(days=d * 4)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST", "course": "A",
                      "surface": "Turf", "going": "G", "distance": 1800,
                      "race_class": "4"})
        for h in range(8):
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(h + 1),
                "finish_time": 108.0 + h * 0.25 + (d % 5) * 0.1,
                "lengths_behind": "-" if h == 0 else f"{h}-1/4",
                "draw": h + 1, "actual_weight": 120 + h, "win_odds": "5.0",
                "running_positions": "1 1 1 1 1",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    rebuild_et.rebuild(db, window_months=0)

    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_race_returns_runner_lines(client):
    body = client.get("/api/race/2025-01-04/1").json()
    assert body["field_size"] == 8
    assert len(body["runners"]) == 8


def test_missing_race_is_404_not_an_empty_list(client):
    """Silent success and silent failure must never look the same."""
    assert client.get("/api/race/1999-01-01/1").status_code == 404


def test_runner_lines_carry_precomputed_displays(client):
    """Formatting rules live in query/, so the browser cannot reimplement them
    differently on each page."""
    runner = client.get("/api/race/2025-01-04/1").json()["runners"][0]
    assert runner["finish_time_display"].startswith("1:")   # m:ss.xx, not 108.0
    assert "vs par" in runner["figure_display"]             # never a bare number
    assert "n=" in runner["figure_display"]                 # always a sample size


def test_horse_form_keys_on_name(client):
    body = client.get("/api/horse/HORSE 3?limit=5").json()
    assert body["horse_name"] == "HORSE 3"
    assert 0 < len(body["runs"]) <= 5


def test_et_breakdown_exposes_the_par_invariant(client):
    body = client.get("/api/model/et/2025-01-04/1").json()
    assert body["distinct_pars"] == 1
    assert body["par_time"] is not None


def test_et_summary_reports_coverage_and_confidence(client):
    body = client.get("/api/model/et/summary").json()
    assert body["rows"] > 0
    assert 0 < body["coverage"] <= 1.0
    assert set(body["confidence"]) <= {"low", "medium", "high"}


def test_status_reports_staleness_per_derived_table(client):
    body = client.get("/api/status").json()
    assert body["latest_meeting"]
    assert body["tables"]["runner_et"]["rows"] > 0
    # Nothing has derived pace yet, so it must report as not current.
    assert body["tables"]["runner_pace"]["current"] is False


def test_rebuild_button_endpoint_reports_counts(client):
    """A job that reports nothing looks the same whether it worked or did not."""
    body = client.post("/api/jobs/rebuild-et?window_months=0").json()
    assert body["rows_written"] > 0 and body["runs_loaded"] > 0
    assert body["window"] and body["sec_per_length"] > 0
    assert body["errors"] == []


def test_rebuild_is_idempotent_through_the_api(client):
    first = client.post("/api/jobs/rebuild-et?window_months=0").json()["rows_written"]
    second = client.post("/api/jobs/rebuild-et?window_months=0").json()["rows_written"]
    assert first == second
    assert client.get("/api/model/et/summary").json()["rows"] == first

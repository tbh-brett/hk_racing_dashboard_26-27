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


# ── blackbook ────────────────────────────────────────────────────────────────

@pytest.fixture()
def booked(client, tmp_path):
    """HORSE 0 booked partway through the fixture's 90 meetings."""
    import json

    from hkrd.jobs import import_blackbook
    src = tmp_path / "bb.json"
    src.write_text(json.dumps({"entries": [
        {"id": "bb_1", "horse_name": "HORSE 0", "added_date": "2025-06-01",
         "status": "active", "confidence": "high", "tags": ["traffic"],
         "reasoning": "blocked at the 300", "source_race": "2025-05-28 R1"}],
        "tag_definitions": {"traffic": "blocked run last start"}}), encoding="utf-8")
    import_blackbook.run(src, db=tmp_path / "api.db")
    return client


def test_blackbook_list_carries_the_derived_record(booked):
    body = booked.get("/api/blackbook").json()
    assert body["count"] == 1
    entry = body["entries"][0]
    # The fixture runs HORSE 0 every four days for 90 meetings, so a booking in
    # June has a substantial record behind it -- none of it hand-logged.
    assert entry["runs_since"] > 0
    assert entry["wins_since"] == entry["runs_since"]   # HORSE 0 always wins
    assert entry["tags"] == ["traffic"]


def test_blackbook_filters_reach_the_query(booked):
    assert booked.get("/api/blackbook?tag=traffic").json()["count"] == 1
    assert booked.get("/api/blackbook?tag=nope").json()["count"] == 0
    assert booked.get("/api/blackbook?status=retired").json()["count"] == 0


def test_blackbook_tags_route_is_not_shadowed_by_the_entry_route(booked):
    """/api/blackbook/tags must not be read as an entry id of "tags"."""
    body = booked.get("/api/blackbook/tags").json()
    assert [t["tag"] for t in body["tags"]] == ["traffic"]


def test_a_missing_blackbook_entry_is_404(booked):
    assert booked.get("/api/blackbook/bb_nope").status_code == 404


def test_the_race_card_flags_its_booked_runners(booked):
    card = booked.get("/api/raceday/2025-06-13/1").json()
    booked_rows = [r for r in card["runners"] if r["blackbook"]]
    assert [r["horse_name"] for r in booked_rows] == ["HORSE 0"]
    assert booked_rows[0]["blackbook"]["live_at_race"] is True
    # The band and the rows are built from one query, so they cannot disagree.
    assert ([b["horse_name"] for b in card["blackbook"]]
            == [r["horse_name"] for r in booked_rows])


def test_the_meeting_band_reports_no_movement_rather_than_zero(booked):
    """The fixture has no odds snapshots. A 0% would read as a steady market."""
    body = booked.get("/api/raceday/2025-06-13/blackbook").json()
    assert body["count"] == 1
    assert body["entries"][0]["change_pct"] is None
    assert body["entries"][0]["observed"] is False


def test_the_band_marks_a_booking_made_after_an_archived_meeting(booked):
    """HORSE 0 runs at every meeting, including ones months before it was
    booked. The band still lists it — it IS in the book — but must not imply
    the thesis was live that day."""
    before = booked.get("/api/raceday/2025-01-04/blackbook").json()
    after = booked.get("/api/raceday/2025-06-13/blackbook").json()
    assert before["entries"][0]["booked_before_race"] is False
    assert after["entries"][0]["booked_before_race"] is True


def test_a_meeting_with_nothing_booked_is_empty_not_an_error(booked, tmp_path):
    from hkrd.store.connect import get_conn, transaction
    conn = get_conn(tmp_path / "api.db")
    with transaction(conn):
        conn.execute("DELETE FROM blackbook_tags")
        conn.execute("DELETE FROM blackbook")
    conn.close()
    body = booked.get("/api/raceday/2025-01-04/blackbook").json()
    assert body == {"race_date": "2025-01-04", "entries": [], "count": 0}

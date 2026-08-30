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


# ── model analysis ───────────────────────────────────────────────────────────

def test_sarr_route_is_404_when_the_race_does_not_exist(client):
    assert client.get("/api/model/sarr/1999-01-01/1").status_code == 404


def test_blend_route_accepts_a_weight(client):
    """The page lets the reader move the weight, which is what makes the
    fitted value checkable rather than asserted."""
    a = client.get("/api/model/blend/2025-06-13/1").json()
    b = client.get("/api/model/blend/2025-06-13/1?weight=0.5").json()
    assert a["weight"] == 0.0
    assert b["weight"] == 0.5
    assert a["calibration"]["fitted_weight"] == 0.0


def test_blend_route_publishes_the_calibration_it_used(client):
    """The page prints these figures verbatim; they must come from the model,
    not be retyped into the front end."""
    cal = client.get("/api/model/blend/2025-06-13/1").json()["calibration"]
    assert cal["log_loss"]["market"] < cal["log_loss"]["fundamental"]
    assert cal["log_loss_by_weight"]["0.00"] <= min(
        cal["log_loss_by_weight"].values())


# ── form guide writes ────────────────────────────────────────────────────────

def test_a_note_is_saved_and_read_back_for_the_card(client):
    body = client.post("/api/notes", json={
        "horse_name": "HORSE 0", "race_date": "2025-06-13", "race_no": 1,
        "note": "held up, no room from the 400"}).json()
    assert body["note"] == "held up, no room from the 400"

    read = client.get("/api/notes?horses=HORSE 0,HORSE 1").json()["notes"]
    assert list(read) == ["HORSE 0"]
    assert read["HORSE 0"][0]["race_no"] == 1


def test_saving_a_note_does_not_create_a_blackbook_entry(client):
    """Design brief 06 Part 0. Promotion is a separate, deliberate call."""
    before = client.get("/api/blackbook").json()["count"]
    client.post("/api/notes", json={
        "horse_name": "HORSE 2", "race_date": "2025-06-13", "race_no": 1,
        "note": "ordinary run"})
    assert client.get("/api/blackbook").json()["count"] == before


def test_an_empty_note_is_a_422_not_a_blank_row(client):
    r = client.post("/api/notes", json={
        "horse_name": "HORSE 0", "race_date": "2025-06-13", "race_no": 1,
        "note": ""})
    assert r.status_code == 422


def test_promotion_creates_an_entry_the_list_then_shows(client):
    entry = client.post("/api/blackbook", json={
        "horse_name": "HORSE 4", "reasoning": "blocked at the 300",
        "source_date": "2025-06-13", "source_race_no": 1,
        "tags": ["traffic"]}).json()
    assert entry["source_race"] == "2025-06-13 R1"

    listed = client.get("/api/blackbook?tag=traffic").json()
    assert entry["id"] in [e["id"] for e in listed["entries"]]


def test_the_pace_route_reports_its_own_coverage(client):
    """The fixture records no running styles, so there is no read — and the
    route has to say so rather than return a band."""
    body = client.get("/api/pace/2025-06-13/1").json()
    assert body["band"] is None
    assert body["unknown"] == body["field_size"] == 8
    assert body["confident"] is False


def test_the_bets_analysis_route_carries_n_and_an_interval_on_every_slice(client):
    """The rule the design prints across the whole section: a 12-bet slice is
    not a finding. A route that returned a bare ROI would leave the page to
    invent the caveat."""
    body = client.get("/api/bets/analysis").json()
    assert body["thin_bets"] == 30
    for key in ("bets", "roi_ci", "thin", "clears_zero"):
        assert key in body["overall"], key
    assert "series" in body["cumulative"]
    assert "selections" in body["clv"]      # n, even when it is zero
    assert isinstance(body["by_type"], list)


def test_an_empty_ledger_reports_zero_rather_than_failing(client):
    """No bets have been imported into this fixture. The page still has to
    render, and a null ROI is not the same as a break-even one."""
    body = client.get("/api/bets/analysis").json()
    assert body["overall"]["bets"] == 0
    assert body["overall"]["roi"] is None
    assert body["overall"]["roi_ci"] is None
    assert body["clv"]["selections"] == 0


def test_the_reconciliation_route_separates_read_from_quoted(client):
    body = client.get("/api/bets/reconciliation").json()
    assert body["confirmed"] == 0
    assert body["quoted_not_read"] == 0
    assert body["disagrees"] == []


def test_the_lookup_filter_vocabulary_is_served_from_one_definition(client):
    """The page renders its panel from this, so a filter the query layer does
    not accept cannot appear on screen."""
    body = client.get("/api/lookup/filters").json()
    assert "race context" in body["groups"]
    assert "draw" in body["dimensions"] and "strike_rate" in body["metrics"]
    assert body["min_sample"] == 30 and body["outlier_delta"] == 6


def test_a_breakdown_route_carries_the_expected_by_chance_count(client):
    body = client.get("/api/lookup/breakdown?dimension=draw").json()
    assert body["dimension"] == "draw"
    assert "expected_by_chance" in body and "cleared" in body
    for row in body["rows"]:
        assert len(row["win_ci"]) == 2 and "thin" in row


def test_an_unknown_dimension_is_a_422_not_a_500(client):
    assert client.get("/api/lookup/breakdown?dimension=vibes").status_code == 422
    assert client.get("/api/lookup/pivot?metric=vibes").status_code == 422


def test_a_pivot_route_returns_every_cell_with_its_n(client):
    body = client.get("/api/lookup/pivot?rows=venue&cols=draw").json()
    assert body["cells"] >= 1
    for row in body["grid"].values():
        for cell in row.values():
            assert cell["runs"] >= 1 and "thin" in cell


def test_a_filter_reaches_every_lookup_route_not_just_the_grid(client):
    """"every panel and tab is computed on all matching runs" — the artboard's
    own line. A filter the panels ignore makes that false."""
    whole = client.get("/api/lookup/breakdown?dimension=draw").json()
    one = client.get("/api/lookup/breakdown?dimension=draw&draw_max=2").json()
    assert one["baseline"]["runs"] < whole["baseline"]["runs"]
    assert {r["value"] for r in one["rows"]} <= {1, 2}

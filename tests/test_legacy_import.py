"""jobs/import_legacy_reports and jobs/rebuild_tags.

662 JSON files were read from disk at 98 sites in the old dashboard. Getting
them into tables is what turns those 98 read sites into query calls.
"""
from __future__ import annotations

import json

import pytest

from hkrd.jobs import import_legacy_reports as importer, rebuild_tags
from hkrd.store.connect import get_conn, init_db


INCIDENTS = {
    "date": "2026-07-15", "venue": "HV", "n_races": 1,
    "races": [{
        "race_number": 1,
        "incident_report": [
            {"place": "1", "horse_no": "3", "horse_name": "FASHION LEGEND",
             "horse_code": "J080",
             "incident": "Was steadied near the 1100 Metres and raced wide."},
            {"place": "2", "horse_no": "11", "horse_name": "TELECOM POWER",
             "horse_code": "J332",
             "incident": "A veterinary inspection immediately following the race "
                         "did not show any significant findings."},
        ],
        # Present in every real file, and every record in it is broken.
        "comments_on_running": [
            {"horse_no": "1", "horse_name": "3", "comment": "FASHION LEGEND (J080)"},
            {"horse_no": "2", "horse_name": "11", "comment": "TELECOM POWER (J332)"},
        ],
    }],
}

DIVIDENDS = {
    "date": "2026-07-15", "venue": "HV", "n_races": 1,
    "races": [{"race_number": 1, "dividends": [
        {"pool": "WIN", "combination": "3", "dividend_per_10": 111.0},
        {"pool": "QIN", "combination": "3,11", "dividend_per_10": 325.0},
    ]}],
}

TRIALS = {
    "date": "2026-08-21", "n_batches": 1,
    "batches": [{
        "batch_number": 1, "course": "SHA TIN ALL WEATHER TRACK",
        "distance_m": 1200, "going": "WET", "overall_time": "1.11.38",
        "sectional_times": ["24.5", "22.9", "23.9"], "n_horses": 1,
        "horses": [{"horse_name": "CALA DEI MORI", "horse_code": "L428",
                    "draw": 3, "gear": "", "lbw": "-",
                    "running_positions": [1, 1, 1], "time": "1.11.38",
                    "result": "",
                    "comment": "Urged to lead along the rail; won narrowly."}],
    }],
}


@pytest.fixture()
def reports(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "incidents_20260715.json").write_text(json.dumps(INCIDENTS))
    (d / "dividends_20260715.json").write_text(json.dumps(DIVIDENDS))
    (d / "trials_20260821.json").write_text(json.dumps(TRIALS))
    # Deliberately present and deliberately not imported.
    (d / "race_day_report_20260715.json").write_text(json.dumps({"anything": 1}))
    return d


def test_imports_the_stable_families(reports, tmp_path):
    db = tmp_path / "t.db"
    report = importer.run(reports, db=db)
    assert report.comments == 2
    assert report.dividends == 2
    assert report.trials == 1
    assert not report.errors


def test_race_day_report_is_never_imported(reports, tmp_path):
    """It is the one family written by generated code, and it drifted to three
    schemas with 17 inconsistent keys."""
    db = tmp_path / "t.db"
    report = importer.run(reports, db=db)
    assert report.files_read == 3   # the four files on disk, minus race_day_report


def test_the_broken_corunning_block_is_skipped_and_counted(reports, tmp_path):
    """Those records carry a horse number where the name belongs and no comment
    text at all. Importing them would store 10,690 rows of nothing."""
    db = tmp_path / "t.db"
    report = importer.run(reports, db=db)
    assert report.skipped_corunning == 2
    conn = get_conn(db)
    sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM runner_comments")}
    conn.close()
    assert sources == {"incident"}


def test_trial_place_comes_from_the_last_running_position(reports, tmp_path):
    """The scrape's own `result` field is empty on every row -- which settles
    open question C2: RESULT was never populated at source."""
    db = tmp_path / "t.db"
    importer.run(reports, db=db)
    conn = get_conn(db)
    row = conn.execute("SELECT place, venue, surface, finish_time FROM trials").fetchone()
    conn.close()
    assert row["place"] == 1
    assert row["venue"] == "ST" and row["surface"] == "AWT"
    assert row["finish_time"] == pytest.approx(71.38)   # '1.11.38' parsed


def test_import_is_idempotent(reports, tmp_path):
    db = tmp_path / "t.db"
    first = importer.run(reports, db=db)
    importer.run(reports, db=db)
    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM runner_comments").fetchone()[0] == first.comments
    assert conn.execute("SELECT count(*) FROM dividends").fetchone()[0] == first.dividends
    conn.close()


# ── tagging ──────────────────────────────────────────────────────────────────

def test_rebuild_tags_derives_from_stored_comments(reports, tmp_path):
    db = tmp_path / "t.db"
    importer.run(reports, db=db)
    report = rebuild_tags.rebuild(db)
    assert report.comments_read == 2
    assert report.tags_written > 0
    assert report.by_kind.get("trouble", 0) > 0
    assert report.by_kind.get("routine", 0) > 0


def test_tags_are_droppable_and_rebuildable(reports, tmp_path):
    """Every derived table must survive DROP and rebuild identically."""
    db = tmp_path / "t.db"
    importer.run(reports, db=db)
    first = rebuild_tags.rebuild(db).tags_written
    second = rebuild_tags.rebuild(db).tags_written
    conn = get_conn(db)
    total = conn.execute("SELECT count(*) FROM runner_tags").fetchone()[0]
    conn.close()
    assert first == second == total


def test_rebuild_does_not_delete_lane_tags_it_did_not_create(reports, tmp_path):
    """Lane tags come from the corunning scrape. A job must not remove work
    another job produced."""
    db = tmp_path / "t.db"
    importer.run(reports, db=db)
    conn = get_conn(db)
    conn.execute("INSERT INTO runner_tags VALUES ('2026-07-15', 1, 3, 'lane:rail', 1.0)")
    conn.close()

    rebuild_tags.rebuild(db)

    conn = get_conn(db)
    kept = conn.execute(
        "SELECT count(*) FROM runner_tags WHERE tag LIKE 'lane:%'").fetchone()[0]
    conn.close()
    assert kept == 1


def test_routine_and_trouble_are_counted_separately(reports, tmp_path):
    db = tmp_path / "t.db"
    importer.run(reports, db=db)
    report = rebuild_tags.rebuild(db)
    conn = get_conn(db)
    tags = {r[0] for r in conn.execute("SELECT tag FROM runner_tags")}
    conn.close()
    assert "vet_routine" in tags       # the passed examination
    assert "steadied" in tags          # real trouble
    assert "vet_finding" not in tags   # nothing here was a real finding


def test_missing_directory_is_reported(tmp_path):
    assert importer.main(["--reports", str(tmp_path / "nope")]) == 1

"""jobs/backfill_draw — recovering a gate from an archive, and refusing to guess.

The 15 July 2026 card carries results for all 107 runners and no gates: the
results scrape succeeded and the racecard scrape did not, and HKJC has since
taken that season's racecards down. The legacy `cache/form_guide_*.json` has
them, because it was written while the card was still up.

The job that reads it is narrow on purpose. Saddlecloth numbers are reused when
a horse is scratched and a reserve takes its place, and the cache predates those
late changes -- so the tests that matter here are the ones about what it
REFUSES to write.
"""
from __future__ import annotations

import json

import pytest

from hkrd.jobs import backfill_draw
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _cache(tmp_path, horses, *, date="2026-07-15", name=None):
    path = tmp_path / (name or f"form_guide_{date}.json")
    path.write_text(json.dumps({
        "date": date,
        "races": [{"race_number": 1, "race_name": "T", "race_class": "4",
                   "distance": 1650, "race_course": "C", "horses": horses}],
    }), encoding="utf-8")
    return path


def _db(tmp_path, runners, *, date="2026-07-15"):
    path = tmp_path / "b.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{
            "race_date": date, "race_no": 1, "venue": "ST", "course": "C",
            "surface": "Turf", "going": "G", "distance": 1650, "race_class": "4"}])
        upsert.upsert_runners(conn, [
            {"race_date": date, "race_no": 1, **r} for r in runners])
    conn.close()
    return path


def _draws(path, date="2026-07-15"):
    conn = get_conn(path)
    rows = {r["horse_no"]: r["draw"] for r in conn.execute(
        "SELECT horse_no, draw FROM runners WHERE race_date = ?", (date,))}
    conn.close()
    return rows


# ── the recovery ─────────────────────────────────────────────────────────────

def test_it_fills_a_gate_the_racecard_scrape_never_delivered(tmp_path):
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"},
                        {"horse_no": 2, "horse_name": "SILVER GRECIAN"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10},
                              {"horse_no": 2, "horse_name": "SILVER GRECIAN", "draw": 3}])
    report = backfill_draw.backfill(cache, db=db)
    assert report.filled == 2
    assert _draws(db) == {1: 10, 2: 3}


def test_a_dry_run_reports_the_same_count_and_writes_nothing(tmp_path):
    """The step you read before the one that changes production."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10}])
    dry = backfill_draw.backfill(cache, db=db, dry_run=True)
    assert dry.filled == 1
    assert _draws(db) == {1: None}
    assert "DRY RUN" in dry.render()
    assert backfill_draw.backfill(cache, db=db).filled == dry.filled


def test_it_is_idempotent(tmp_path):
    """Every write in this package is. Running it twice must not be a decision."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10}])
    backfill_draw.backfill(cache, db=db)
    second = backfill_draw.backfill(cache, db=db)
    assert second.filled == 0
    assert second.already_had_a_draw == 1
    assert _draws(db) == {1: 10}


# ── what it refuses, which is the point of it ────────────────────────────────

def test_it_refuses_when_the_cache_names_a_different_horse(tmp_path):
    """A saddlecloth is reused when a horse is scratched and a reserve takes its
    place. The cache was written before that happened, so the number matches and
    the horse does not. Writing the scratched horse's gate onto the reserve
    would be worse than the missing value: a missing value is visible."""
    db = _db(tmp_path, [{"horse_no": 4, "horse_name": "LATE RESERVE"}])
    cache = _cache(tmp_path, [{"horse_no": 4, "horse_name": "CONSPIRATOR (SCRATCHED)",
                               "draw": 7}])
    report = backfill_draw.backfill(cache, db=db)
    assert report.filled == 0
    assert report.refused_name_mismatch == 1
    assert _draws(db) == {4: None}
    assert "CONSPIRATOR (SCRATCHED)" in report.render()


def test_a_bracketed_suffix_is_not_normalised_away(tmp_path):
    """`CONSPIRATOR (SCRATCHED)` must not match `CONSPIRATOR`. A normaliser
    tolerant enough to call those the same horse defeats the guard entirely."""
    assert (backfill_draw.normalise_name("CONSPIRATOR (SCRATCHED)")
            != backfill_draw.normalise_name("CONSPIRATOR"))
    # ... while genuine formatting differences still match
    assert (backfill_draw.normalise_name("  emerging   star ")
            == backfill_draw.normalise_name("EMERGING STAR"))


def test_it_never_overwrites_a_draw_that_is_already_stored(tmp_path):
    """The database's value came from the scrape; the cache's is an archive.
    Where they disagree the job is not the thing that should decide."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 2}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 11}])
    report = backfill_draw.backfill(cache, db=db)
    assert report.filled == 0
    assert report.already_had_a_draw == 1
    assert _draws(db) == {1: 2}


def test_it_writes_only_the_draw_column(tmp_path):
    """A general-purpose importer is not what this is. The cache also carries a
    rating, a jockey and a trainer, and none of them are its business."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR",
                         "rating": 40, "jockey": "R Kingscote"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR",
                               "draw": 10, "rating": 99, "jockey": "SOMEONE ELSE"}])
    backfill_draw.backfill(cache, db=db)
    conn = get_conn(db)
    row = conn.execute("SELECT draw, rating, jockey FROM runners "
                       "WHERE race_date='2026-07-15' AND horse_no=1").fetchone()
    conn.close()
    assert row["draw"] == 10
    assert row["rating"] == 40
    assert row["jockey"] == "R Kingscote"


def test_a_runner_the_database_never_stored_is_counted_not_inserted(tmp_path):
    """The cache is a repair for rows that exist, not a source of new ones."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10},
                              {"horse_no": 9, "horse_name": "NEVER STORED", "draw": 4}])
    report = backfill_draw.backfill(cache, db=db)
    assert report.filled == 1
    assert report.no_such_runner == 1
    assert set(_draws(db)) == {1}


def test_a_cache_row_with_no_draw_is_counted_not_written_as_zero(tmp_path):
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": None}])
    report = backfill_draw.backfill(cache, db=db)
    assert report.filled == 0
    assert report.cache_row_had_no_draw == 1
    assert _draws(db) == {1: None}


# ── selecting the files ──────────────────────────────────────────────────────

def test_a_directory_of_cache_files_can_be_narrowed_to_one_meeting(tmp_path):
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10}])
    _cache(tmp_path, [{"horse_no": 1, "horse_name": "OTHER MEETING", "draw": 5}],
           date="2026-07-12")
    only = backfill_draw.backfill(tmp_path, db=db, date="2026-07-15")
    assert only.files_read == 1
    assert only.filled == 1


def test_a_missing_cache_is_an_error_not_a_quiet_zero(tmp_path):
    """A backfill that reports "0 filled" because it found no files looks
    exactly like one that found nothing to do."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    report = backfill_draw.backfill(tmp_path / "nowhere", db=db)
    assert report.errors
    assert report.filled == 0


def test_the_date_comes_from_the_file_contents_when_the_name_disagrees(tmp_path):
    """A file can be renamed; its contents cannot be renamed by accident."""
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    cache = _cache(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR", "draw": 10}],
                   date="2026-07-15", name="form_guide_2020-01-01.json")
    assert backfill_draw.backfill(cache, db=db).filled == 1
    assert _draws(db) == {1: 10}


def test_a_malformed_file_is_named_rather_than_skipped_silently(tmp_path):
    db = _db(tmp_path, [{"horse_no": 1, "horse_name": "EMERGING STAR"}])
    bad = tmp_path / "form_guide_2026-07-15.json"
    bad.write_text("{not json", encoding="utf-8")
    report = backfill_draw.backfill(bad, db=db)
    assert report.errors
    assert "form_guide_2026-07-15.json" in report.errors[0]

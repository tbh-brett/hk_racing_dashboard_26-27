"""model/sarr and jobs/rebuild_sarr — field-relative sectional strength.

SARR ranks properly but carries no edge in the place it could live: it agrees
with the market favourite in about a third of races, and in exactly those
disagreement races it returns what its price says it should. It is a
descriptive read, not a selection rule, and these tests pin the properties that
make it trustworthy as one.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.derive import pace
from hkrd.jobs import derive_all, rebuild_sarr
from hkrd.model import sarr
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


# ── the duplication that was collapsed ───────────────────────────────────────

def test_sarr_uses_the_canonical_style_classifier():
    """SARR was built and backtested against the field-size-scaled classifier,
    which decision A2 then made canonical. It must be literally that function,
    not a copy that can drift away from it."""
    assert sarr.classify_style is pace.classify_style


def test_sarr_uses_the_canonical_section_table():
    assert sarr.SECTION_LENGTHS is pace.SECTION_LENGTHS


def test_sarr_uses_the_shared_section_parser():
    assert sarr.parse_sections("24.97; 22.82; ; ") == [24.97, 22.82]


def test_weights_match_the_documented_model():
    """Design brief 05 §5 publishes these on the Model Analysis page, so a
    silent change here would make the page lie about the model.

    Tolerance is 3 decimal places because that is the precision the brief
    publishes: the model holds 0.2901 and the page shows 0.290. Asserting exact
    equality would be testing the rounding, not the agreement.
    """
    assert sarr.WEIGHTS["f_fmrp"] == pytest.approx(0.290, abs=5e-4)
    assert sarr.WEIGHTS["f_lsa"] == pytest.approx(0.091, abs=5e-4)
    assert sarr.WEIGHTS["f_traj"] == pytest.approx(-0.071, abs=5e-4)
    # fmrp carries roughly 3x the next-largest term; the page shows that visually.
    others = [abs(v) for k, v in sarr.WEIGHTS.items() if k != "f_fmrp"]
    assert abs(sarr.WEIGHTS["f_fmrp"]) > 3 * max(others) * 0.9


# ── the rebuild job ──────────────────────────────────────────────────────────

def _seed(db, meetings: int = 40, runners: int = 8):
    conn = get_conn(db)
    init_db(conn)
    races, runs = [], []
    start = dt.date(2025, 1, 4)
    for d in range(meetings):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST", "course": "A",
                      "surface": "Turf", "going": "G", "distance": 1800,
                      "race_class": "4"})
        for h in range(runners):
            runs.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(((h + d) % runners) + 1),
                "finish_time": 108.0 + ((h + d) % runners) * 0.3,
                "lengths_behind": "-" if (h + d) % runners == 0 else "1-1/4",
                "draw": h + 1, "rating": 60 + h, "win_odds": "5.0",
                "section_times": "13.0; 24.0; 23.5; 23.8; 24.2",
                "running_positions": f"{h+1} {h+1} {h+1} {h+1}",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runs)
    conn.close()


def test_rebuild_writes_ranked_rows(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    report = rebuild_sarr.rebuild(db)
    assert report.rows_written > 0 and not report.errors
    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM runner_sarr").fetchone()[0] == report.rows_written
    conn.close()


def test_exactly_one_rank_one_per_race(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    dupes = conn.execute(
        "SELECT count(*) FROM (SELECT race_date, race_no, count(*) n FROM runner_sarr "
        "WHERE sarr_rank = 1 GROUP BY 1, 2 HAVING n > 1)").fetchone()[0]
    conn.close()
    assert dupes == 0


def test_ranks_are_dense_and_start_at_one(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    for row in conn.execute("SELECT race_date, race_no FROM runner_sarr "
                            "GROUP BY 1, 2 LIMIT 5"):
        ranks = [r[0] for r in conn.execute(
            "SELECT sarr_rank FROM runner_sarr WHERE race_date=? AND race_no=? "
            "ORDER BY sarr_rank", (row["race_date"], row["race_no"]))]
        assert ranks == list(range(1, len(ranks) + 1))
    conn.close()


def test_lower_score_ranks_better(tmp_path):
    """SARR is a composite where lower is better; a rank that inverts that is
    the whole model reading backwards."""
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    rows = conn.execute(
        "SELECT sarr, sarr_rank FROM runner_sarr WHERE race_date = "
        "(SELECT max(race_date) FROM runner_sarr) ORDER BY sarr_rank").fetchall()
    conn.close()
    scores = [r["sarr"] for r in rows]
    assert scores == sorted(scores)


def test_a_horse_with_no_history_is_skipped_and_counted(tmp_path):
    """The first meeting has no prior runs for anyone, so nothing there can be
    rated. That must be reported, not silently produce zeros."""
    db = tmp_path / "t.db"
    _seed(db, meetings=3)
    report = rebuild_sarr.rebuild(db, min_prior=2)
    assert report.skipped_no_history > 0
    conn = get_conn(db)
    first = conn.execute("SELECT min(race_date) FROM races").fetchone()[0]
    rated = conn.execute(
        "SELECT count(*) FROM runner_sarr WHERE race_date = ?", (first,)).fetchone()[0]
    conn.close()
    assert rated == 0


def test_date_scoped_rebuild_scores_a_declared_card_from_prior_runs(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, meetings=3)
    date = "2026-09-06"
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [{
            "race_date": date, "race_no": 1, "venue": "ST", "course": "A",
            "surface": "Turf", "going": "G", "distance": 1800,
            "race_class": "4",
        }])
        upsert.upsert_runners(conn, [{
            "race_date": date, "race_no": 1, "horse_no": horse_no + 1,
            "horse_name": f"HORSE {horse_no}", "draw": horse_no + 1,
            "rating": 60 + horse_no,
        } for horse_no in range(8)])
    conn.close()

    report = derive_all.run(db, date=date, only=("sarr",))

    conn = get_conn(db)
    rows = conn.execute(
        "SELECT s.horse_no, s.sarr_rank, r.finish_time "
        "FROM runner_sarr s JOIN runners r USING (race_date, race_no, horse_no) "
        "WHERE s.race_date = ? ORDER BY s.sarr_rank", (date,)).fetchall()
    conn.close()
    assert report.written["runner_sarr"] == 8
    assert [row["sarr_rank"] for row in rows] == list(range(1, 9))
    assert all(row["finish_time"] is None for row in rows)


def test_a_race_without_a_distance_is_skipped_not_crashed(tmp_path):
    """Five legacy races carry no distance -- their venue column holds a course
    code, so the source rows are malformed. SARR's distance term cannot be
    computed, so those runners are skipped and counted rather than guessed."""
    db = tmp_path / "t.db"
    _seed(db, meetings=10)
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE races SET distance = NULL WHERE race_date = "
                     "(SELECT max(race_date) FROM races)")
    conn.close()
    report = rebuild_sarr.rebuild(db)
    assert report.skipped_no_distance > 0
    assert not report.errors


def test_rebuild_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    first = rebuild_sarr.rebuild(db).rows_written
    second = rebuild_sarr.rebuild(db).rows_written
    conn = get_conn(db)
    total = conn.execute("SELECT count(*) FROM runner_sarr").fetchone()[0]
    conn.close()
    assert first == second == total


def test_empty_database_reports_rather_than_crashing(tmp_path):
    db = tmp_path / "empty.db"
    conn = get_conn(db)
    init_db(conn)
    conn.close()
    report = rebuild_sarr.rebuild(db)
    assert report.rows_written == 0 and report.errors

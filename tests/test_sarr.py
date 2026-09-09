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


# ── a run a vet found something on is weaker evidence ────────────────────────

def test_a_clean_run_keeps_full_trust():
    """The default has to be 1.0 or every profile in the archive quietly
    shrinks. `None` is the only value that means "nothing was found"."""
    assert sarr.vet_trust(None) == 1.0
    assert sarr.vet_trust("") == 1.0


def test_an_unknown_category_is_treated_as_a_finding_not_as_clean():
    """HKJC's vocabulary grows without asking us. A word we have not seen
    before describes an injury we have not seen before, and must not restore
    full trust to the run it appears on."""
    assert sarr.vet_trust("SOMETHING NEW") == sarr.VET_TRUST_POOLED
    assert sarr.vet_trust("cardiac") == sarr.vet_trust("CARDIAC")


def test_every_category_is_discounted():
    assert all(0.0 < v < 1.0 for v in sarr.VET_TRUST.values())


def test_a_flagged_run_moves_the_profile_less_than_a_clean_one():
    """The whole mechanism in one assertion: same catastrophic run, once as
    evidence and once as a finding, and the finding must weigh less."""
    good = [{"fmrp": -1.0, "late_dev": 0.0, "early_dev": 0.0, "ssi": 0.0,
             "style": "On-Pace", "rating": 60, "place": 1, "distance": 1200,
             "venue": "HV", "surface": "Turf", "going": "G"} for _ in range(3)]
    bad = dict(good[0], fmrp=3.4, place=12)
    clean = sarr.build_profile([bad, *good], 1200, "HV", "Turf", "G")
    flagged = sarr.build_profile([dict(bad, vet_category="CARDIAC"), *good],
                                 1200, "HV", "Turf", "G")
    assert flagged["fmrp"] < clean["fmrp"]
    # ... and still worse than if the run had never happened, because the
    # weight is a discount and not a delete.
    assert flagged["fmrp"] > sarr.build_profile(good, 1200, "HV", "Turf", "G")["fmrp"]


def test_a_flagged_run_is_kept_out_of_the_trajectory_line():
    """A weighted mean dilutes a bad point; a regression lets it lever the
    answer. TYCOON RESOURCES into 2026-09-09 was three wins and a cardiac
    finding, and the slope through all four called it the most steeply
    deteriorating horse in the race."""
    runs = [{"fmrp": v, "late_dev": 0.0, "early_dev": 0.0, "ssi": 0.0,
             "style": "On-Pace", "rating": 60, "place": 1, "distance": 1200,
             "venue": "HV", "surface": "Turf", "going": "G"}
            for v in (3.4, -0.65, -0.80, -1.17)]
    through_all = sarr.build_profile(runs, 1200, "HV", "Turf", "G")["traj"]
    runs[0]["vet_category"] = "CARDIAC"
    flagged = sarr.build_profile(runs, 1200, "HV", "Turf", "G")["traj"]
    without = sarr.build_profile(runs[1:], 1200, "HV", "Turf", "G")["traj"]

    # The flagged point is not on the line at all -- exactly as if the run had
    # not happened. That is stricter than "less steep" and is the property the
    # term needs: a slope has no way to half-count a point.
    assert flagged == pytest.approx(without)
    # And it was doing most of the work before: the three clean runs do decline
    # mildly, and the model still says so rather than calling the horse sound.
    assert through_all < flagged < 0
    assert abs(through_all) > 4 * abs(flagged)


def test_vet_flags_are_none_not_nan_for_a_clean_run(tmp_path):
    """NaN is truthy. Left in place it reaches `vet_trust` as the string "NAN",
    misses the table and takes the unrecognised-category branch, discounting
    every clean run in the archive to 0.21 while every counter and log line
    still reads normally. This is the assertion that catches that."""
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_conn(db)
    from hkrd.jobs.rebuild_sarr import RUNS_SQL, _vet_flags
    import pandas as pd
    runs = sarr.annotate_runs(pd.read_sql(RUNS_SQL, conn))
    flags = _vet_flags(conn, runs)
    conn.close()
    assert flags.isna().all()
    assert all(f is None for f in flags)
    assert all(sarr.vet_trust(f) == 1.0 for f in flags)


def test_a_vet_record_discounts_the_run_it_was_made_on(tmp_path):
    """End to end through the job: the finding lives on a different HKJC page
    from the run, joined on the date it was made."""
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_conn(db)
    target = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
    flagged = conn.execute(
        "SELECT race_date FROM races WHERE race_date < ? "
        "ORDER BY race_date DESC LIMIT 1", (target,)).fetchone()[0]
    conn.close()
    before = rebuild_sarr.rebuild(db)
    assert before.vet_flagged_runs == 0 and before.profiles_with_vet_run == 0

    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_vet_records(conn, [{
            "race_date": target, "race_no": 1, "horse_no": 1,
            "horse_name": "HORSE 0", "record_date": flagged,
            "detail": "Heart irregularity noted after racing.",
            "passed_date": None, "category": "CARDIAC"}])
    conn.close()
    after = rebuild_sarr.rebuild(db)
    assert after.vet_flagged_runs == 1
    assert after.profiles_with_vet_run > 0
    assert after.rows_written == before.rows_written  # a discount, not a drop


def test_the_model_stamps_its_own_version(tmp_path):
    """The job reads `sarr.DERIVE_VERSION` and falls back to a literal when the
    module has none. With no constant here the fallback wrote "sarr-1.0"
    whatever the model was doing, so rebuilding part of the archive under a
    changed model produced rows indistinguishable from the rest."""
    assert getattr(sarr, "DERIVE_VERSION", None), "the model must name its version"
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    stamped = {r[0] for r in conn.execute(
        "SELECT DISTINCT derive_version FROM runner_sarr")}
    conn.close()
    assert stamped == {sarr.DERIVE_VERSION}


def test_a_scratched_runner_does_not_keep_its_rank(tmp_path):
    """The write is an upsert, so a runner that used to score and no longer
    does keeps its row -- and its RANK. That happens every meeting: the card is
    scored the day before, a horse is scratched, and the results write leaves
    it with no finish time, so it drops out of the source query and is never
    rescored. 2026-09-06 race 7 carried ten rows for nine runners with a
    non-runner third, pushing every real runner below it one place down."""
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_sarr.rebuild(db)
    conn = get_conn(db)
    date, race_no = conn.execute(
        "SELECT race_date, race_no FROM runner_sarr ORDER BY race_date DESC "
        "LIMIT 1").fetchone()
    horse_no = conn.execute(
        "SELECT horse_no FROM runner_sarr WHERE race_date = ? AND race_no = ? "
        "ORDER BY sarr_rank LIMIT 1", (date, race_no)).fetchone()[0]
    # scratched: declared, then never ran
    with transaction(conn):
        conn.execute("UPDATE runners SET finish_time = NULL, place = NULL "
                     "WHERE race_date = ? AND race_no = ? AND horse_no = ?",
                     (date, race_no, horse_no))
    before = conn.execute("SELECT count(*) FROM runner_sarr WHERE race_date = ? "
                          "AND race_no = ?", (date, race_no)).fetchone()[0]
    conn.close()

    report = rebuild_sarr.rebuild(db)
    assert report.stale_rows_cleared >= 1
    conn = get_conn(db)
    rows = conn.execute("SELECT horse_no, sarr_rank FROM runner_sarr "
                        "WHERE race_date = ? AND race_no = ? ORDER BY sarr_rank",
                        (date, race_no)).fetchall()
    orphan = conn.execute(
        "SELECT count(*) FROM runner_sarr_component WHERE race_date = ? "
        "AND race_no = ? AND horse_no = ?", (date, race_no, horse_no)).fetchone()[0]
    conn.close()
    assert horse_no not in {r[0] for r in rows}
    assert orphan == 0
    assert len(rows) == before - 1
    # and the ranks close up rather than leaving a hole where it stood
    assert [r[1] for r in rows] == list(range(1, len(rows) + 1))


def test_a_full_rebuild_leaves_an_unrun_card_alone(tmp_path):
    """The clear-out is scoped to races the pass actually scored. A card scored
    the day before racing has no finish times at all, so an un-dated rebuild
    must not treat every one of its runners as stale."""
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_conn(db)
    last = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
    card = (dt.date.fromisoformat(last) + dt.timedelta(days=7)).isoformat()
    with transaction(conn):
        upsert.upsert_races(conn, [{
            "race_date": card, "race_no": 1, "venue": "ST", "course": "A",
            "surface": "Turf", "going": "G", "distance": 1800, "race_class": "4"}])
        upsert.upsert_runners(conn, [{
            "race_date": card, "race_no": 1, "horse_no": h + 1,
            "horse_name": f"HORSE {h}", "draw": h + 1, "rating": 60 + h}
            for h in range(8)])
    conn.close()

    rebuild_sarr.rebuild(db, date=card)
    conn = get_conn(db)
    scored = conn.execute("SELECT count(*) FROM runner_sarr WHERE race_date = ?",
                          (card,)).fetchone()[0]
    conn.close()
    assert scored > 0

    rebuild_sarr.rebuild(db)          # the full pass must not touch the card
    conn = get_conn(db)
    still = conn.execute("SELECT count(*) FROM runner_sarr WHERE race_date = ?",
                         (card,)).fetchone()[0]
    conn.close()
    assert still == scored

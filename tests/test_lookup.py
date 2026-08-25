"""query/lookup — filtered exploration, and the honesty the page requires.

PROMPTS.md Phase 3: "Every filter in search_runs is a WHERE clause. Nothing
filtered in pandas." Design brief 07 §8: "This page's genuine risk is
manufacturing false signals through repeated slicing", so n and the
expected-by-chance count are part of every answer, not decoration on top.
"""
from __future__ import annotations

import time

import pytest

from hkrd.query import lookup
from hkrd.query.types import RunnerLine
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """Two meetings at two venues, plus a trial."""
    path = tmp_path / "l.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-05-01", "race_no": 1, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650,
             "race_class": "4"},
            {"race_date": "2026-06-01", "race_no": 1, "venue": "ST",
             "course": "A", "surface": "Turf", "going": "GF", "distance": 1200,
             "race_class": "3"},
        ])
        for date, odds in (("2026-05-01", 4.0), ("2026-06-01", 9.0)):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": i,
                 "horse_name": f"HORSE {i}", "place": str(i),
                 "draw": i, "actual_weight": 118 + i,
                 "win_odds": odds if i == 1 else 10.0,
                 "jockey": "Z PURTON" if i == 1 else "J MOREIRA",
                 "trainer": "J SIZE"}
                for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, pace_style, "
            "derive_version) VALUES (?, 1, ?, ?, 't')",
            [("2026-05-01", i, "Leader" if i <= 2 else "Closer") for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_et (race_date, race_no, horse_no, figure, "
            "derive_version) VALUES (?, 1, ?, ?, 't')",
            [("2026-05-01", i, 110.0 - i) for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_tags (race_date, race_no, horse_no, tag) "
            "VALUES ('2026-05-01', 1, ?, ?)", [(1, "traffic"), (2, "checked")])
        conn.executemany(
            "INSERT INTO trials (trial_date, trial_no, horse_name, place, "
            "venue, surface, comment_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("2026-05-20", 1, "HORSE 1", 1, "HV", "AWT", "Ran on well"),
             ("2026-05-20", 1, "HORSE 3", 2, "HV", "AWT", "Never nearer")])
    conn.close()
    return path


# ── one type, everywhere ─────────────────────────────────────────────────────

def test_a_run_here_is_the_same_object_as_a_run_anywhere_else(db):
    """"A horse's past run in the form guide and the same run in race lookup
    must be literally the same object" — PROMPTS.md Phase 3."""
    conn = get_conn(db)
    runs = lookup.search_runs(conn=conn)
    conn.close()
    assert runs and all(isinstance(r, RunnerLine) for r in runs)
    assert runs[0].figure_display is not None or runs[0].et_figure is None


# ── filters are WHERE clauses ────────────────────────────────────────────────

@pytest.mark.parametrize("filters,expected", [
    ({"venue": "HV"}, 8),
    ({"venue": "ST"}, 8),
    ({"distance_min": 1400}, 8),                 # only the 1650m meeting
    ({"race_class": "3"}, 8),
    ({"jockey": "Z PURTON"}, 2),
    ({"draw_max": 2}, 4),
    ({"pace_style": "Leader"}, 2),
    ({"et_min": 108}, 2),
    ({"tag": "traffic"}, 1),
    ({"won": True}, 2),
    ({"place": 3}, 2),
    ({"odds_max": 5.0}, 1),
    ({"venue": "HV", "pace_style": "Closer", "won": True}, 0),
])
def test_each_filter_narrows_the_set(db, filters, expected):
    conn = get_conn(db)
    runs = lookup.search_runs(conn=conn, **filters)
    conn.close()
    assert len(runs) == expected


def test_placed_uses_the_hong_kong_rule_not_a_flat_top_three(db):
    """Three places in fields of seven or more, two below — a flat top-3 would
    credit a place that never paid."""
    conn = get_conn(db)
    eight = lookup.search_runs(placed=True, conn=conn)
    with transaction(conn):
        conn.execute("DELETE FROM runners WHERE horse_no > 6")
    six = lookup.search_runs(placed=True, conn=conn)
    conn.close()
    assert len(eight) == 6          # top 3 of two eight-runner fields
    assert len(six) == 4            # top 2 of two six-runner fields


def test_field_size_filters_on_the_field_not_the_runner(db):
    conn = get_conn(db)
    assert len(lookup.search_runs(field_size_min=8, conn=conn)) == 16
    assert len(lookup.search_runs(field_size_min=9, conn=conn)) == 0
    conn.close()


def test_an_unknown_filter_is_ignored_rather_than_silently_matching_nothing(db):
    """A typo in a query string must not look like a slice with no runs."""
    conn = get_conn(db)
    runs = lookup.search_runs(conn=conn, nonsense="x")
    conn.close()
    assert len(runs) == 16


def test_the_limit_is_honoured(db):
    conn = get_conn(db)
    assert len(lookup.search_runs(limit=5, conn=conn)) == 5
    conn.close()


def test_search_stays_under_the_half_second_budget(db):
    """"No function may exceed 500ms on current data." Small fixture, so this
    catches a structural regression — an N+1 or a dropped index — rather than
    measuring the machine."""
    conn = get_conn(db)
    lookup.search_runs(conn=conn)                     # warm the page cache
    started = time.perf_counter()
    lookup.search_runs(venue="HV", pace_style="Leader", conn=conn)
    elapsed = time.perf_counter() - started
    conn.close()
    assert elapsed < 0.5


# ── the source dimension ─────────────────────────────────────────────────────

def test_trials_come_back_through_the_same_search(db):
    """Design note 11 §2: Trials had its own search over half the data. One
    interface, filtered to a source, replaces it."""
    conn = get_conn(db)
    trials = lookup.search_runs(source="trial", conn=conn)
    conn.close()
    assert len(trials) == 2
    assert all(isinstance(t, RunnerLine) for t in trials)
    assert trials[0].tags == ("trial",)


def test_a_filter_trials_cannot_honour_excludes_them_rather_than_ignoring_it(db):
    """Returning trials that quietly ignore a distance filter would be worse
    than returning none — the user would read them as matching."""
    conn = get_conn(db)
    assert lookup.search_runs(source="trial", distance_min=1200, conn=conn) == []
    assert lookup.search_runs(source="trial", jockey="Z PURTON", conn=conn) == []
    assert len(lookup.search_runs(source="trial", venue="HV", conn=conn)) == 2
    conn.close()


def test_both_sources_merge_into_one_ordered_set(db):
    conn = get_conn(db)
    both = lookup.search_runs(source="both", horse="HORSE 1", conn=conn)
    conn.close()
    # Two race runs and one trial, newest first.
    assert len(both) == 3
    assert [r.race_date for r in both] == sorted(
        [r.race_date for r in both], reverse=True)


def test_an_unknown_source_is_refused(db):
    conn = get_conn(db)
    with pytest.raises(ValueError, match="source"):
        lookup.search_runs(source="nonsense", conn=conn)
    conn.close()


# ── the insight panel ────────────────────────────────────────────────────────

def test_every_figure_carries_its_sample(db):
    conn = get_conn(db)
    out = lookup.insight(venue="HV", conn=conn)
    conn.close()
    assert out["runs"] == 8 and out["races"] == 1
    assert out["strike_rate"] == pytest.approx(1 / 8)
    assert out["figures"] == 8          # how many runs actually had a figure
    assert out["by_style"][0]["runs"]   # and per style too


def test_a_thin_slice_says_it_is_thin(db):
    """"A 12-run slice must not visually resemble a 400-run one." The judgement
    is made here so every surface agrees on it."""
    conn = get_conn(db)
    assert lookup.insight(venue="HV", conn=conn)["thin"] is True
    conn.close()


def test_the_expected_by_chance_count_is_always_present(db):
    """Brief 07 §8 makes this non-negotiable: 8 cells cleared significance
    where 7.0 were expected at random, and any one of them looked convincing
    alone."""
    conn = get_conn(db)
    out = lookup.insight(conn=conn)
    conn.close()
    assert out["expected_by_chance"] == 0.05
    assert "clears" in out


def test_ae_is_computed_against_the_devigged_price(db):
    """One definition of A/E, shared with the Blackbook — not a second one."""
    conn = get_conn(db)
    out = lookup.insight(venue="HV", conn=conn)
    conn.close()
    assert out["ae"] is not None
    assert out["ae_lo"] <= out["ae"] <= out["ae_hi"]


def test_insight_and_search_agree_on_what_the_slice_is(db):
    """If they disagree the panel describes a different set from the grid."""
    conn = get_conn(db)
    filters = {"venue": "HV", "pace_style": "Closer"}
    runs = lookup.search_runs(conn=conn, **filters)
    out = lookup.insight(conn=conn, **filters)
    conn.close()
    assert out["runs"] == len([r for r in runs if r.place is not None])

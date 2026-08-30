"""query/lookup — filtered exploration, and the honesty the page requires.

PROMPTS.md Phase 3: "Every filter in search_runs is a WHERE clause. Nothing
filtered in pandas." Design brief 07 §8: "This page's genuine risk is
manufacturing false signals through repeated slicing", so n and the
expected-by-chance count are part of every answer, not decoration on top.
"""
from __future__ import annotations

import time

import pytest

from hkrd.query import lookup, slices
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


# ── slicing, and the risk it carries ─────────────────────────────────────────
#
# Brief 07 §8: "This page's genuine risk is manufacturing false signals through
# repeated slicing. A pivot is the easiest way in this whole tool to manufacture
# a false finding." These tests hold the query layer to saying so.

def test_a_breakdown_compares_against_the_filtered_slice_not_the_database(db):
    """Sliced to Happy Valley, the question is which draws beat Happy Valley.
    Comparing them to every race ever run folds the venue into every row."""
    conn = get_conn(db)
    whole = slices.breakdown("draw", conn=conn)["baseline"]
    hv = slices.breakdown("draw", venue="HV", conn=conn)["baseline"]
    conn.close()
    assert whole["runs"] == 16 and hv["runs"] == 8


def test_every_breakdown_row_carries_n_and_an_interval(db):
    conn = get_conn(db)
    b = slices.breakdown("venue", conn=conn)
    conn.close()
    assert b["rows"]
    for row in b["rows"]:
        assert row["runs"] >= 1
        assert len(row["win_ci"]) == 2
        assert row["win_delta"] is not None
        assert "thin" in row and "clears" in row


def test_a_zero_for_eight_cell_does_not_claim_a_certainty(db):
    """The normal approximation gives [0, 0] on 0 of 8 — a claim eight runs
    cannot support. Wilson gives an upper bound instead."""
    lo, hi = slices._rate_interval(0, 8)
    assert lo == 0.0
    assert 0.2 < hi < 0.5


def test_the_expected_by_chance_count_is_returned_beside_the_cleared_one(db):
    """Eight cells clearing p<.05 out of 153 is not eight findings when 7.0
    were expected at random — brief 07 §8's own example."""
    conn = get_conn(db)
    b = slices.breakdown("draw", conn=conn, min_sample=1)
    conn.close()
    assert b["cells"] == len([r for r in b["rows"] if not r["thin"]])
    assert b["expected_by_chance"] == round(b["cells"] * 0.05, 1)


def test_a_thin_row_is_marked_rather_than_dropped(db):
    """Removing it would hide how much of the breakdown is noise."""
    conn = get_conn(db)
    b = slices.breakdown("draw", conn=conn)
    conn.close()
    assert all(r["thin"] for r in b["rows"])      # 2 runs per draw
    assert b["thin_hidden"] == len(b["rows"])
    assert b["cells"] == 0 and b["cleared"] == 0


def test_every_pivot_cell_carries_its_own_n(db):
    conn = get_conn(db)
    p = slices.pivot("venue", "draw", conn=conn)
    conn.close()
    assert p["cells"] == 16
    for rv in p["row_values"]:
        for cv, cell in p["grid"][str(rv)].items():
            assert cell["runs"] >= 1 and "thin" in cell


def test_a_pivot_says_how_many_cells_will_look_notable_by_luck(db):
    conn = get_conn(db)
    p = slices.pivot("venue", "draw", conn=conn, min_sample=1)
    conn.close()
    assert p["expected_notable"] == round(p["cells"] * 0.05, 1)


def test_the_ae_metric_is_the_only_one_that_pays_for_the_market_join(db):
    """Asking for the field's implied book on every cell regardless took a
    style-by-venue pivot from 40ms to 460ms."""
    conn = get_conn(db)
    fast = slices.pivot("venue", "draw", metric="strike_rate", conn=conn)
    ae = slices.pivot("venue", "draw", metric="ae", conn=conn)
    conn.close()
    cell = fast["grid"]["HV"]["1"]
    assert "expected_wins" not in cell
    assert "expected_wins" in ae["grid"]["HV"]["1"]


def test_an_unknown_dimension_raises_rather_than_returning_nothing(db):
    conn = get_conn(db)
    with pytest.raises(ValueError, match="dimension must be one of"):
        slices.breakdown("colour of silks", conn=conn)
    with pytest.raises(ValueError, match="metric must be one of"):
        slices.pivot("venue", "draw", metric="vibes", conn=conn)
    conn.close()


# ── outliers ─────────────────────────────────────────────────────────────────

def test_an_outlier_is_measured_against_the_market_rank_not_the_odds(db):
    """Horse 1 is 4.0 in a field where everything else is 10.0, so it is the
    favourite; finishing first is no outlier. In the second race it is 9.0
    against 10.0s — still favourite — and finishing first is still none."""
    conn = get_conn(db)
    o = slices.outliers(delta=6, conn=conn)
    conn.close()
    flagged = {(r["race_date"], r["horse_no"]) for r in o["runs"]}
    assert ("2026-05-01", 1) not in flagged
    # Horse 8 finished 8th at 10.0, joint-second favourite on price. Its market
    # rank is 2, so it ran 6 places worse than the market ranked it.
    assert ("2026-05-01", 8) in flagged


def test_ties_share_a_market_rank(db):
    """Seven horses at 10.0 are all joint-second favourite, not second through
    eighth. Ranking them 2..8 would invent an ordering the market never made
    and flag six runners as outliers for finishing where they were priced."""
    conn = get_conn(db)
    o = slices.outliers(delta=1, conn=conn)
    conn.close()
    ranks = {r["market_rank"] for r in o["runs"] if r["race_date"] == "2026-05-01"}
    assert ranks <= {1, 2}


def test_the_match_count_is_the_whole_slice_not_the_page_returned(db):
    """"Showing first N of M matching runs · every panel and tab is computed on
    all M" — the artboard's own line. A `matched` capped at the limit breaks
    it, and the SELECT-list placeholder that once bound to the wrong parameter
    made the count 0 while the list stayed full."""
    conn = get_conn(db)
    o = slices.outliers(delta=1, limit=3, conn=conn)
    conn.close()
    assert o["shown"] == 3
    assert o["matched"] > 3
    assert o["of_runs"] == 16
    assert o["truncated"] is True


def test_a_repeat_offender_is_named_because_one_run_is_a_story(db):
    conn = get_conn(db)
    o = slices.outliers(delta=1, conn=conn)
    conn.close()
    # HORSE 8 runs in both meetings and is out of line in both.
    repeats = {r["horse_name"] for r in o["runs"] if r["repeat"]}
    assert o["repeat_horses"] == len(repeats)
    assert all(r["appearances"] > 1 for r in o["runs"] if r["repeat"])


# ── the line every page carries ──────────────────────────────────────────────

def test_the_corpus_line_quantifies_what_is_missing(db):
    """"every page carries this line — it states what is current and
    quantifies what is missing". A page that slices by running style over a
    corpus that is 60% unlabelled is slicing something other than it says."""
    conn = get_conn(db)
    c = slices.corpus(conn=conn)
    conn.close()
    assert c["runs"] == 16 and c["races"] == 2 and c["trials"] == 2
    assert c["pace_labelled"] == 8
    assert c["pace_share"] == 0.5
    assert c["latest"] == "2026-06-01"

"""jobs/project_card — scoring a card that has not been run.

Two things had to be true before a speed map could be built on this, and both
are pinned here: that the projection uses ONLY data older than the card (there
are no results to leak, but the query that fetches the card is the file where
somebody would one day add them), and that a runner it cannot place is written
as an absence rather than as a confident zero.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from hkrd.jobs import project_card
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIRST_CALLS = ("1 2 3 4", "3 3 4 5", "6 6 5 6", "9 8 7 7", "11 10 9 8")


def _history(conn, weeks=16, field=10):
    """A run of finished meetings, so every horse on the card has form.

    Early sectionals vary by horse so the ESZ trait has something to rank, and
    the first-call positions vary with it so the styles are not all the same.
    """
    races, runners = [], []
    start = dt.date(2026, 1, 4)
    for d in range(weeks):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1200, "race_class": "4"})
        for h in range(field):
            early = 23.0 + h * 0.25          # horse 0 is habitually quickest away
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(h + 1),
                "finish_time": 100.0 + h * 0.2,
                "lengths_behind": "-" if h == 0 else f"{h}-1/4",
                "draw": h + 1, "actual_weight": 120 + h, "rating": 60 - h,
                "win_odds": str(3.0 + h * 2),
                "running_positions": FIRST_CALLS[h % len(FIRST_CALLS)],
                "section_times": f"{early:.1f}; 22.8; 23.6",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)


def _card(conn, date="2026-06-06", field=10, draws=None, names=None):
    """The card itself: declared, with no result columns at all."""
    races = [{"race_date": date, "race_no": 1, "venue": "ST", "course": "A",
              "surface": "Turf", "going": "G", "distance": 1200,
              "race_class": "4"}]
    runners = []
    for h in range(field):
        runners.append({
            "race_date": date, "race_no": 1, "horse_no": h + 1,
            "horse_name": (names or {}).get(h, f"HORSE {h}"),
            "draw": (draws or {}).get(h, h + 1),
            "actual_weight": 120 + h, "rating": 60 - h,
        })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)


@pytest.fixture()
def carded(tmp_path):
    path = tmp_path / "p.db"
    conn = get_conn(path)
    init_db(conn)
    _history(conn)
    _card(conn)
    conn.close()
    return path


def _rows(path, date="2026-06-06"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT p.*, n.horse_name FROM runner_projection p "
            "JOIN runners n USING (race_date, race_no, horse_no) "
            "WHERE p.race_date = ? ORDER BY p.horse_no", (date,))]
    finally:
        conn.close()


# ── it reaches the table ─────────────────────────────────────────────────────

def test_it_projects_a_card_that_has_no_results(carded):
    """The point of the job. Nothing in the card carries a finish time."""
    report = project_card.project("2026-06-06", carded)
    assert report.errors == []
    assert report.runners == 10
    assert report.projected == 10
    rows = _rows(carded)
    assert len(rows) == 10
    assert all(r["settle"] is not None for r in rows)
    assert all(r["settle_band"] in ("LEAD", "PACE", "MID", "BACK") for r in rows)


def test_the_quickest_beginner_projects_in_front(carded):
    """HORSE 0 has the fastest first section in every prior run and the inside
    gate; HORSE 9 has the slowest and the widest."""
    project_card.project("2026-06-06", carded)
    rows = {r["horse_no"]: r for r in _rows(carded)}
    assert rows[1]["settle"] < rows[10]["settle"]
    assert rows[1]["esz_rank"] < rows[10]["esz_rank"]


def test_the_projection_is_idempotent(carded):
    project_card.project("2026-06-06", carded)
    first = _rows(carded)
    project_card.project("2026-06-06", carded)
    second = _rows(carded)
    assert first == second


def test_a_rerun_after_a_scratching_renormalises_the_whole_card(carded):
    """field_size scales both axes, so a projection built before a scratching
    and shown after is wrong for every runner, not just the one that came out.
    """
    project_card.project("2026-06-06", carded)
    before = {r["horse_no"]: r["ndraw"] for r in _rows(carded)}
    conn = get_conn(carded)
    with transaction(conn):
        conn.execute("DELETE FROM runners WHERE race_date = ? AND horse_no = 10",
                     ("2026-06-06",))
    conn.close()
    project_card.project("2026-06-06", carded)
    after = {r["horse_no"]: r["ndraw"] for r in _rows(carded)}
    assert 10 not in after
    assert after[9] != before[9]
    assert after[9] == pytest.approx(1.0)
    assert all(r["field_size"] == 9 for r in _rows(carded))


# ── what it refuses to invent ────────────────────────────────────────────────

def test_a_runner_with_no_gate_is_written_without_a_projection(tmp_path):
    """A horse with no gate cannot be placed on a gate ladder at all.
    `derive/draw` may fall back to 0.0; this must not."""
    path = tmp_path / "g.db"
    conn = get_conn(path)
    init_db(conn)
    _history(conn)
    _card(conn, draws={3: None})
    conn.close()
    report = project_card.project("2026-06-06", path)
    assert report.no_gate == 1
    assert report.projected == 9
    row = next(r for r in _rows(path) if r["horse_no"] == 4)
    assert row["settle"] is None and row["settle_band"] is None
    assert row["ndraw"] is None


def test_a_first_starter_is_written_without_a_projection(tmp_path):
    """A season opener is full of them. Named, not counted, and never a zero:
    a zero bar reads as "breaks at field average"."""
    path = tmp_path / "f.db"
    conn = get_conn(path)
    init_db(conn)
    _history(conn)
    _card(conn, names={2: "DEBUTANT"})
    conn.close()
    report = project_card.project("2026-06-06", path)
    assert report.no_history == 1
    row = next(r for r in _rows(path) if r["horse_name"] == "DEBUTANT")
    assert row["settle"] is None
    assert row["esz"] is None and row["esz_rank"] is None


def test_a_runner_without_form_is_left_out_of_the_rank_denominator(tmp_path):
    """esz_rank is a percentile over the runners that HAVE a trait.

    A first-starter counted in the denominator would be treated as though it
    had a measured early speed of some sort -- and with nine ranked runners the
    percentiles must span 0 to 1 across those nine, not across ten.
    """
    path = tmp_path / "b.db"
    conn = get_conn(path)
    init_db(conn)
    _history(conn)
    _card(conn, names={9: "DEBUTANT"})
    conn.close()
    project_card.project("2026-06-06", path)
    rows = _rows(path)

    debutant = next(r for r in rows if r["horse_name"] == "DEBUTANT")
    assert debutant["esz_rank"] is None

    ranked = sorted(r["esz_rank"] for r in rows if r["esz_rank"] is not None)
    assert len(ranked) == 9
    assert ranked[0] == pytest.approx(0.0)
    assert ranked[-1] == pytest.approx(1.0)
    # Evenly spaced over nine runners, not ten: 1/8 apart, not 1/9.
    assert ranked[1] == pytest.approx(1 / 8)


# ── walk-forward ─────────────────────────────────────────────────────────────

def test_history_after_the_card_cannot_reach_the_projection(tmp_path):
    """A rating that has seen the race it is rating tells you nothing. The card
    has no results here, so the guard is on the HISTORY query: a later meeting
    must not inform an earlier card."""
    path = tmp_path / "w.db"
    conn = get_conn(path)
    init_db(conn)
    _history(conn)
    _card(conn, date="2026-02-07")           # a card in the MIDDLE of the series
    conn.close()
    early = project_card.project("2026-02-07", path)
    assert early.errors == []
    # Every run used is older than the card.
    conn = sqlite3.connect(path)
    later = conn.execute(
        "SELECT COUNT(*) FROM runners WHERE race_date > '2026-02-07'"
        " AND finish_time IS NOT NULL").fetchone()[0]
    conn.close()
    assert later > 0, "the fixture must have meetings after the card to be a test"
    assert early.history_runs == 50          # only the five meetings before it


def test_the_card_query_selects_no_result_columns():
    """Belt and braces on the file somebody will one day be tempted to widen."""
    sql = project_card.CARD_SQL.lower()
    for banned in ("place", "finish_time", "section_times", "running_positions"):
        assert banned not in sql


def test_a_card_that_is_not_stored_is_an_error_not_an_empty_table(carded):
    report = project_card.project("2026-12-25", carded)
    assert report.errors and "no card stored" in report.errors[0]
    assert _rows(carded, "2026-12-25") == []

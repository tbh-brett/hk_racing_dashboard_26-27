"""query/speedmap — the gate ladder as the page receives it.

The contract this pins is mostly about absence. A speed map is read as a
complete picture of a race, so a runner the model will not place has to arrive
NAMED, with the reason, rather than dropped or defaulted. Dropping it makes a
14-runner field look like an 11-runner one; defaulting it draws a bar that says
"breaks at field average" about a horse with no runs at all.
"""
from __future__ import annotations

import sqlite3

import pytest

from hkrd.query import speedmap as sm
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-06"


def _db(tmp_path, rows, *, field_size=4):
    """rows: (horse_no, draw, esz, esz_rank, settle, band, style, n_prior)."""
    path = tmp_path / "s.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        conn.execute(
            "INSERT INTO races (race_date, race_no, venue, surface, going, distance)"
            " VALUES (?, 1, 'ST', 'Turf', 'G', 1200)", (DATE,))
        for (no, draw, esz, rank, settle, band, style, n_prior) in rows:
            conn.execute(
                "INSERT INTO runners (race_date, race_no, horse_no, horse_name,"
                " draw, jockey, trainer) VALUES (?, 1, ?, ?, ?, 'J Moreira', 'C Fownes')",
                (DATE, no, f"HORSE {no}", draw))
            conn.execute(
                "INSERT INTO runner_projection (race_date, race_no, horse_no, esz,"
                " esz_rank, ndraw, draw_score, settle, settle_band, style, n_prior,"
                " field_size, derive_version)"
                " VALUES (?, 1, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, 'settle-1.0')",
                (DATE, no, esz, rank,
                 None if draw is None else (draw - 1) / (field_size - 1),
                 settle, band, style, n_prior, field_size))
    conn.close()
    return path


FULL = [(1, 1, -0.4, 0.0, 0.20, "LEAD", "Leader", 8),
        (2, 2, -0.1, 0.33, 0.35, "PACE", "On-Pace", 6),
        (3, 3, 0.1, 0.66, 0.50, "MID", "Midfield", 9),
        (4, 4, 0.4, 1.0, 0.72, "BACK", "Closer", 5)]


def _map(path, race_no=1):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return sm.speed_map(DATE, race_no, conn=conn)
    finally:
        conn.close()


# ── the ladder ───────────────────────────────────────────────────────────────

def test_the_ladder_is_ordered_by_gate(tmp_path):
    """The gate is the one thing on the page that is not a projection, so it is
    the axis everything else is read against."""
    path = _db(tmp_path, list(reversed(FULL)))
    out = _map(path)
    assert [r["draw"] for r in out["runners"]] == [1, 2, 3, 4]


def test_it_carries_the_bar_and_the_band_as_separate_readings(tmp_path):
    """A horse can be quick away and still settle midfield from a wide gate.
    Collapsing the two into one number is the thing the page is against."""
    out = _map(_db(tmp_path, FULL))
    first = out["runners"][0]
    assert first["esz_rank"] == 0.0          # quickest away
    assert first["settle_band"] == "LEAD"
    assert first["style"] == "Leader"
    assert "esz" in first and "settle" in first


def test_a_runner_with_no_gate_sorts_last_rather_than_first(tmp_path):
    """NULL sorts before every number in SQLite, so an ungated horse would head
    the ladder unless the ordering says otherwise."""
    rows = FULL + [(5, None, None, None, None, None, None, 3)]
    out = _map(_db(tmp_path, rows, field_size=5))
    assert [r["draw"] for r in out["runners"]] == [1, 2, 3, 4, None]


# ── what it will not hide ────────────────────────────────────────────────────

def test_an_unprojected_runner_is_returned_not_dropped(tmp_path):
    """Dropping it makes a five-runner field look like a four-runner one."""
    rows = FULL + [(5, 5, None, None, None, None, None, 1)]
    out = _map(_db(tmp_path, rows, field_size=5))
    assert len(out["runners"]) == 5
    assert out["projected"] == 4


def test_an_unprojected_runner_is_named_with_a_reason(tmp_path):
    rows = FULL + [(5, 5, None, None, None, None, None, 1)]
    out = _map(_db(tmp_path, rows, field_size=5))
    assert [u["horse_name"] for u in out["unprojected"]] == ["HORSE 5"]
    assert out["unprojected"][0]["reason"] == "fewer than two prior runs"


def test_a_missing_gate_is_reported_as_the_reason_it_is(tmp_path):
    """Two different absences with two different fixes: one is a scrape gap and
    the other is a horse that has simply never run."""
    rows = FULL + [(5, None, None, None, None, None, None, 9)]
    out = _map(_db(tmp_path, rows, field_size=5))
    assert out["unprojected"][0]["reason"] == "no gate declared"


def test_a_projected_runner_carries_no_reason(tmp_path):
    out = _map(_db(tmp_path, FULL))
    assert all(r["reason"] is None for r in out["runners"])
    assert out["unprojected"] == []


def test_it_never_substitutes_a_zero_for_a_missing_projection(tmp_path):
    """A zero settle would read as "leads", and a zero bar as "breaks at field
    average". Both are claims about a horse we know nothing about."""
    rows = FULL + [(5, 5, None, None, None, None, None, 0)]
    out = _map(_db(tmp_path, rows, field_size=5))
    absent = next(r for r in out["runners"] if r["horse_no"] == 5)
    assert absent["settle"] is None
    assert absent["esz_rank"] is None
    assert absent["settle_band"] is None


# ── the meeting, and the precision statement ─────────────────────────────────

def test_the_meeting_view_returns_every_race(tmp_path):
    path = _db(tmp_path, FULL)
    conn = get_conn(path)
    with transaction(conn):
        conn.execute("INSERT INTO races (race_date, race_no, venue, surface,"
                     " going, distance) VALUES (?, 2, 'ST', 'Turf', 'G', 1400)", (DATE,))
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no,"
                     " horse_name, draw) VALUES (?, 2, 1, 'OTHER', 1)", (DATE,))
        conn.execute("INSERT INTO runner_projection (race_date, race_no, horse_no,"
                     " esz_rank, ndraw, settle, settle_band, n_prior, field_size,"
                     " derive_version) VALUES (?, 2, 1, 0.0, 0.0, 0.3, 'PACE', 4, 1,"
                     " 'settle-1.0')", (DATE,))
    conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        out = sm.meeting_speed_map(DATE, conn=conn)
    finally:
        conn.close()
    assert [r["race_no"] for r in out["races"]] == [1, 2]


def test_the_error_bar_travels_with_the_map(tmp_path):
    """A speed map that does not say how wrong it can be is lying, so the
    figure is part of the payload rather than something the page invents."""
    conn = sqlite3.connect(_db(tmp_path, FULL))
    conn.row_factory = sqlite3.Row
    try:
        out = sm.meeting_speed_map(DATE, conn=conn)
    finally:
        conn.close()
    assert out["mae"] == pytest.approx(sm.MAE)
    assert "positions" in out["mae_note"]
    assert out["derive_version"] == "settle-1.0"


def test_a_date_with_no_projection_returns_no_races(tmp_path):
    conn = sqlite3.connect(_db(tmp_path, FULL))
    conn.row_factory = sqlite3.Row
    try:
        out = sm.meeting_speed_map("2026-01-01", conn=conn)
    finally:
        conn.close()
    assert out["races"] == []

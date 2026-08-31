"""Early speed z — how fast away a run was, inside its own race.

The old dashboard's ESZ column and the design's "JUMP z -0.89 slow away". It
did not exist in the rebuild: `model/sarr.py` carries an `esz` WEIGHT, but that
is a different quantity and reusing it would have been the wrong answer.
"""
from __future__ import annotations

import pytest

from hkrd.query import pace as pace_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-07-15"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "esz.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1800}
            for n in (1, 2)])
        # Race 1: horse 1 is clearly quickest away, horse 6 slowest.
        for i, early in enumerate([22.0, 23.0, 23.5, 24.0, 24.5, 25.5], start=1):
            conn.execute(
                "INSERT INTO runner_pace (race_date, race_no, horse_no, "
                "early_pace, derive_version) VALUES (?, 1, ?, ?, 'test')",
                (DATE, i, early))
        # Race 2: only three priced runners — not a distribution.
        for i, early in enumerate([23.0, 24.0, 25.0], start=1):
            conn.execute(
                "INSERT INTO runner_pace (race_date, race_no, horse_no, "
                "early_pace, derive_version) VALUES (?, 2, ?, ?, 'test')",
                (DATE, i, early))
    conn.close()
    return path


def _esz(path, keys):
    conn = get_conn(path)
    try:
        return pace_q.early_speed_z(keys, conn=conn)
    finally:
        conn.close()


def test_a_quick_beginning_is_positive(db):
    """early_pace is a TIME, so the sign is flipped: fast away reads high."""
    out = _esz(db, [(DATE, 1)])
    assert out[(DATE, 1, 1)] > 0
    assert out[(DATE, 1, 6)] < 0


def test_the_scale_is_within_the_race(db):
    """Standardised inside the race, so the values are z-scores about zero."""
    out = _esz(db, [(DATE, 1)])
    values = [v for (d, n, _), v in out.items() if n == 1]
    assert abs(sum(values)) < 0.01
    assert max(values) < 3 and min(values) > -3


def test_a_field_too_small_to_standardise_returns_nothing(db):
    """A z-score over three runners is arithmetic, not a reading, and it would
    render on screen looking exactly like one."""
    out = _esz(db, [(DATE, 2)])
    assert not [k for k in out if k[1] == 2]


def test_only_the_races_asked_for_are_computed(db):
    out = _esz(db, [(DATE, 1)])
    assert {k[1] for k in out} == {1}


def test_it_is_not_the_sarr_component(db):
    """SARR's esz is a weighted mean of a horse's early deviation across its
    HISTORY — a trait of the horse. This is one observation of one run.
    Merging them would make a horse that usually begins well read as fast away
    in the run where it missed the kick."""
    from hkrd.model import sarr

    assert "esz" in sarr.COMPONENTS
    assert not hasattr(sarr, "early_speed_z")

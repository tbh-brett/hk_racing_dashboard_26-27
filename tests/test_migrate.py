"""jobs/migrate_legacy — the legacy flat table into the normalised schema."""
from __future__ import annotations

import sqlite3

import pytest

from hkrd.jobs import migrate_legacy
from hkrd.store.connect import get_conn


LEGACY_COLS = [
    "race_date", "race_number", "horse_name", "horse_number", "place",
    "finish_time_seconds", "lbw", "draw", "going", "running_positions",
    "sectiontimes", "gear", "rating", "distance", "jockey", "trainer",
    "actual_weight", "win_odds", "declared_weight", "race_course",
    "race_class", "race_track", "track_type",
]


def _legacy_db(tmp_path, rows):
    p = tmp_path / "legacy.db"
    c = sqlite3.connect(p)
    c.execute(f"CREATE TABLE results ({', '.join(f'{col} TEXT' for col in LEGACY_COLS)})")
    c.executemany(
        f"INSERT INTO results ({', '.join(LEGACY_COLS)}) "
        f"VALUES ({', '.join('?' for _ in LEGACY_COLS)})",
        [tuple(r.get(col) for col in LEGACY_COLS) for r in rows],
    )
    c.commit()
    c.close()
    return p


BASE = {
    "race_date": "2026-07-15", "race_number": "3", "horse_name": "FIREFOOT",
    "horse_number": "8", "place": "2", "finish_time_seconds": "108.39",
    "lbw": "3-1/4", "draw": "8", "going": "G", "running_positions": "10 9 6 3 2",
    "distance": "1800", "actual_weight": "124", "win_odds": "7.5",
    # Taken from the real hkjc.db, NOT from the migration's own mapping:
    # race_track is ST | HV (the venue), race_course is A | C+3 | AWT (the rail
    # configuration). An earlier fixture had these the wrong way round, which
    # is why the whole suite passed over a swap that broke SARR.
    "race_track": "HV", "race_class": "4", "race_course": "C", "track_type": "Turf",
}


def test_migration_splits_one_flat_row_into_race_and_runner(tmp_path):
    src = _legacy_db(tmp_path, [BASE, {**BASE, "horse_number": "10",
                                       "horse_name": "KYRUS TREASURE", "place": "1",
                                       "lbw": "-"}])
    report = migrate_legacy.migrate(src, tmp_path / "out.db")
    assert (report.races, report.runners) == (1, 2)
    assert not report.errors


def test_scratched_rows_are_skipped_and_counted_not_dropped_silently(tmp_path):
    """143 legacy rows have no horse_number. They must be reported, not vanish."""
    src = _legacy_db(tmp_path, [BASE, {**BASE, "horse_number": None, "place": "WV"}])
    report = migrate_legacy.migrate(src, tmp_path / "out.db")
    assert report.runners == 1
    assert report.skipped_no_horse_no == 1
    assert "skipped" in report.render()


def test_migration_is_idempotent(tmp_path):
    src = _legacy_db(tmp_path, [BASE])
    dest = tmp_path / "out.db"
    migrate_legacy.migrate(src, dest)
    migrate_legacy.migrate(src, dest)
    conn = get_conn(dest)
    assert conn.execute("SELECT count(*) FROM runners").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM races").fetchone()[0] == 1
    conn.close()


def test_fractional_margins_survive_the_migration(tmp_path):
    """The whole point: '3-1/4' must arrive as 3.25, not NULL."""
    src = _legacy_db(tmp_path, [BASE])
    dest = tmp_path / "out.db"
    migrate_legacy.migrate(src, dest)
    conn = get_conn(dest)
    assert conn.execute(
        "SELECT lengths_behind FROM runners").fetchone()[0] == pytest.approx(3.25)
    conn.close()


@pytest.mark.parametrize("course,track,expected", [
    ("ST", "ALL WEATHER TRACK", "AWT"),
    ("ST", "AWT", "AWT"),
    ("ST", "Turf", "Turf"),
    ("HV", "Turf", "Turf"),
])
def test_awt_is_never_labelled_turf(course, track, expected):
    """The v4 reference builder pooled AWT with Sha Tin turf, corrupting par times."""
    assert migrate_legacy._surface(course, track) == expected


def test_source_database_is_never_modified(tmp_path):
    src = _legacy_db(tmp_path, [BASE])
    before = src.read_bytes()
    migrate_legacy.migrate(src, tmp_path / "out.db")
    assert src.read_bytes() == before



def test_the_venue_and_course_columns_do_not_get_swapped(tmp_path):
    """race_track (ST|HV) becomes venue; race_course (A|C+3|AWT) becomes course.

    They were mapped the wrong way round for all 1,712 legacy races. Nothing
    errored: SARR's Happy Valley style modifier simply never fired across 648
    HV races, and its venue-mismatch weight penalised a change of RAIL rather
    than of TRACK. Fixing it moved 23.5% of rankings and the top pick in 139
    races.
    """
    src = _legacy_db(tmp_path, [
        {**BASE, "race_track": "ST", "race_course": "A+3"},
        {**BASE, "race_number": "4", "race_track": "ST", "race_course": "AWT",
         "track_type": "All Weather Track"},
    ])
    migrate_legacy.migrate(src, tmp_path / "out.db")

    conn = get_conn(tmp_path / "out.db")
    rows = [dict(r) for r in conn.execute(
        "SELECT race_no, venue, course, surface FROM races ORDER BY race_no")]
    conn.close()
    assert [r["venue"] for r in rows] == ["ST", "ST"]
    assert [r["course"] for r in rows] == ["A+3", "AWT"]
    # AWT is a surface as well as a rail setting, and must not pool with turf.
    assert [r["surface"] for r in rows] == ["Turf", "AWT"]


def test_a_swapped_source_is_refused_rather_than_stored(tmp_path):
    """The store checks the domain, so this class of bug cannot land silently
    again — whatever a future loader believes it is mapping."""
    import pytest

    from hkrd.store import upsert
    from hkrd.store.connect import init_db, transaction

    conn = get_conn(tmp_path / "guard.db")
    init_db(conn)
    with pytest.raises(ValueError, match="swapped"):
        with transaction(conn):
            upsert.upsert_races(conn, [{
                "race_date": "2026-07-15", "race_no": 1,
                "venue": "C+3", "course": "HV", "surface": "Turf"}])
    conn.close()

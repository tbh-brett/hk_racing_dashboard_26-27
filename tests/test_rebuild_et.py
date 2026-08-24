"""jobs/rebuild_et — wiring the ET model to the normalised schema.

The model's own behaviour is covered by tests/test_et.py (42 tests, carried over
unchanged). These cover the job: that it reads the new schema, writes runner_et,
and preserves the two invariants that made the v4 model incoherent.
"""
from __future__ import annotations

import pytest

from hkrd.derive import et
from hkrd.jobs import rebuild_et
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _seed(db, n_races: int = 90):
    """Enough runs for reference cells to form.

    ET refuses to build references from fewer than 500 runs, which is the right
    guard -- a par time fitted on a handful of races is noise. 90 races x 8
    runners clears it.
    """
    import datetime as dt

    conn = get_conn(db)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2025, 1, 4)
    for d in range(n_races):
        date = (start + dt.timedelta(days=d * 4)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1800, "race_class": "4"})
        for h in range(8):
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(h + 1),
                # Times spread within a race and drift slightly across meetings,
                # so figures have something real to rank.
                "finish_time": 108.0 + h * 0.25 + (d % 5) * 0.1,
                "lengths_behind": "-" if h == 0 else f"{h}-1/4",
                "actual_weight": 120 + h, "draw": h + 1, "win_odds": "5.0",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()


def test_rebuild_writes_runner_et(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    report = rebuild_et.rebuild(db, window_months=0)
    assert report.rows_written > 0
    assert not report.errors
    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM runner_et").fetchone()[0] == report.rows_written
    conn.close()


def test_every_row_carries_the_derive_version(tmp_path):
    """A formula change must not silently overwrite figures from the old one."""
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_et.rebuild(db, window_months=0)
    conn = get_conn(db)
    versions = {r[0] for r in conn.execute("SELECT DISTINCT derive_version FROM runner_et")}
    conn.close()
    assert versions == {et.DERIVE_VERSION}


def test_rebuild_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    first = rebuild_et.rebuild(db, window_months=0).rows_written
    second = rebuild_et.rebuild(db, window_months=0).rows_written
    conn = get_conn(db)
    total = conn.execute("SELECT count(*) FROM runner_et").fetchone()[0]
    conn.close()
    assert first == second == total


def test_one_par_per_race(tmp_path):
    """A par is a property of a race. v4 used weight_band as a lookup key and
    handed runners in the same race pars up to 1.98s apart."""
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_et.rebuild(db, window_months=0)
    conn = get_conn(db)
    rows = conn.execute("""
        SELECT e.race_date, e.race_no, count(DISTINCT round(r.finish_time + e.sec_vs_par, 4)) p
        FROM runner_et e JOIN runners r USING (race_date, race_no, horse_no)
        WHERE e.sec_vs_par IS NOT NULL GROUP BY 1, 2""").fetchall()
    conn.close()
    assert rows and all(r["p"] == 1 for r in rows)


def test_faster_time_always_gives_a_better_figure(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rebuild_et.rebuild(db, window_months=0)
    conn = get_conn(db)
    bad = conn.execute("""
        SELECT count(*) FROM runner_et a
        JOIN runner_et b USING (race_date, race_no)
        JOIN runners ra ON ra.race_date=a.race_date AND ra.race_no=a.race_no
                       AND ra.horse_no=a.horse_no
        JOIN runners rb ON rb.race_date=b.race_date AND rb.race_no=b.race_no
                       AND rb.horse_no=b.horse_no
        WHERE ra.finish_time < rb.finish_time AND a.figure < b.figure""").fetchone()[0]
    conn.close()
    assert bad == 0


def test_empty_database_reports_rather_than_crashing(tmp_path):
    db = tmp_path / "empty.db"
    conn = get_conn(db)
    init_db(conn)
    conn.close()
    report = rebuild_et.rebuild(db, window_months=0)
    assert report.rows_written == 0
    assert report.errors and "nothing to build" in report.errors[0]


def test_et_uses_the_shared_margin_parser(tmp_path):
    """One definition of a beaten length. The module originally carried its own
    copy; it now delegates to store.coerce, and this pins that."""
    from hkrd.store import coerce
    assert et.parse_lbw("3-1/4") == pytest.approx(coerce.parse_lbw("3-1/4"))
    assert et.parse_lbw("HD") == pytest.approx(coerce.parse_lbw("HD"))
    import math
    assert math.isnan(et.parse_lbw("-"))          # pandas needs NaN
    assert coerce.parse_lbw("-") is None          # the store speaks None

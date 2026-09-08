"""The shapes that make a page slow, pinned so they cannot come back.

None of these assert a wall-clock time — a test that fails on a loaded machine
is a test people learn to ignore. They assert the SHAPE that caused the cost,
which is the thing that regresses.

Two were real. The Lookup insight panel ran a correlated subquery per row and
one of them twice, so an unfiltered slice did ~43,000 scans of a race's runners
to answer a question about 21,493 rows: 650 ms on the machine, 270 ms on a warm
local copy, against 20 ms for the same arithmetic grouped once. And nothing
compressed a response, on a server in Singapore read from Hong Kong, where a
Lookup answer is 478 KB of the most compressible thing there is — JSON, with
the same field names repeated on every row.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from hkrd.query import lookup
from hkrd.store import connect

ROOT = Path(__file__).resolve().parent.parent


def test_the_insight_panel_does_not_scan_a_race_per_row() -> None:
    """Field size and the race's book are constant within a race.

    Computed as correlated subqueries in the SELECT list they were recomputed
    per row per use; grouped once into a CTE they are one pass. The check is on
    the source, because the difference is invisible in the answer — both give
    exactly the same numbers, one of them thirteen times slower.
    """
    src = (ROOT / "hkrd" / "query" / "lookup.py").read_text(encoding="utf-8")
    body = src[src.index("def insight("):]
    body = body[:body.index("\ndef ")] if "\ndef " in body else body
    assert "WITH field AS" not in body, "the CTE belongs at module level"
    assert "_FIELD_CTE" in body, "insight must join the grouped aggregate"
    # The exact subqueries that cost 650 ms.
    assert "SELECT count(*) FROM runners f" not in body
    assert "SELECT sum(1.0 / f.win_odds) FROM runners f" not in body


def test_the_same_arithmetic_survives_the_rewrite(tmp_path) -> None:
    """Grouped once must be the same answer as computed per row.

    Built rather than mocked: three races of different field sizes, so both the
    seven-runner place rule and the book divide something real.
    """
    from hkrd.store import upsert
    from hkrd.store.connect import get_conn, init_db, transaction

    conn = get_conn(tmp_path / "p.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-05-01", "race_no": n, "venue": "ST",
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200}
            for n in (1, 2, 3)])
        for race_no, size in ((1, 12), (2, 6), (3, 9)):
            upsert.upsert_runners(conn, [
                {"race_date": "2026-05-01", "race_no": race_no, "horse_no": i,
                 "horse_name": f"H{race_no}{i}", "place": str(i),
                 "win_odds": 2.0 + i} for i in range(1, size + 1)])
    try:
        got = lookup.insight(conn=conn)
        # 12 + 6 + 9 runners; three place in the two big fields, two in the six.
        assert got["runs"] == 27
        assert got["wins"] == 3
        assert got["places"] == 3 + 2 + 3
        # Every runner is priced, so the expected wins over the whole slice is
        # one per race — the book always sums to itself.
        assert got["expected_wins"] == pytest.approx(3.0, abs=1e-6)
    finally:
        conn.close()


def test_responses_are_compressed() -> None:
    """JSON with the same field names on every row is the best case gzip has:
    measured, a race card is 7.3x smaller and a Lookup answer 7.9x."""
    src = (ROOT / "hkrd" / "api" / "app.py").read_text(encoding="utf-8")
    assert "GZipMiddleware" in src
    assert re.search(r"add_middleware\(\s*GZipMiddleware", src)


def test_json_of_this_shape_actually_compresses() -> None:
    """The reason the middleware is worth its CPU, checked rather than assumed.

    A row of a Lookup answer, repeated. If a future payload stopped compressing
    — because it became opaque, or already-compressed, or a blob — the
    middleware would be pure cost and this says so.
    """
    row = {"race_date": "2026-09-06", "race_no": 1, "horse_no": 4,
           "horse_name": "SUNNY Q", "jockey": "Z Purton", "trainer": "J Size",
           "venue": "ST", "going": "G", "distance": 1200, "place": 6,
           "win_odds": 2.3, "et_figure": 100.4, "pace_style": "Closer"}
    raw = json.dumps([row] * 500).encode()
    assert len(raw) / len(gzip.compress(raw, 6)) > 5


def test_the_page_cache_is_sized_for_the_database() -> None:
    """SQLite's default is 2 MB, against an archive of 38 MB — so a query
    touching a fifth of it re-read most of it from disk every time."""
    assert connect.CACHE_KIB >= 32_000
    assert connect.MMAP_BYTES >= 64 * 1024 * 1024


def test_a_connection_really_gets_them(tmp_path) -> None:
    """The pragmas are per CONNECTION, not per database, so a constant nobody
    applies is a comment."""
    conn = connect.get_conn(tmp_path / "c.db")
    try:
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -connect.CACHE_KIB
        assert conn.execute("PRAGMA mmap_size").fetchone()[0] == connect.MMAP_BYTES
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_only_the_derived_tables_a_filter_uses_are_joined(tmp_path) -> None:
    """Three LEFT JOINs over 21,493 runners is 64,000 index lookups, and on an
    unfiltered slice two of them are for tables nothing reads.

    Which to join is decided by reading the WHERE fragment for the alias, not
    from a second list of which filter uses which table — a list like that is
    one filter away from being wrong, and being wrong is a hard SQL error at
    read time on exactly the filter nobody tested. So this drives EVERY filter
    that touches a derived table and asserts each one answers.
    """
    from hkrd.store import upsert
    from hkrd.store.connect import get_conn, init_db, transaction

    conn = get_conn(tmp_path / "j.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-05-01", "race_no": 1, "venue": "ST",
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200,
             "race_class": "4"}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-05-01", "race_no": 1, "horse_no": i,
             "horse_name": f"H{i}", "place": str(i), "win_odds": 2.0 + i,
             "draw": i} for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, "
            "pace_style, early_dev, derive_version) VALUES (?,?,?,?,?,'t')",
            [("2026-05-01", 1, i, "Closer" if i > 4 else "Leader", 0.1)
             for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_et (race_date, race_no, horse_no, figure, "
            "derive_version) VALUES (?,?,?,?,'t')",
            [("2026-05-01", 1, i, 100.0 + i) for i in range(1, 9)])
        conn.executemany(
            "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, "
            "sarr_rank, derive_version) VALUES (?,?,?,?,?,'t')",
            [("2026-05-01", 1, i, 1.0, i) for i in range(1, 9)])
    try:
        # One per alias, plus the combinations, plus none at all.
        for filters in ({},
                        {"pace_style": "Closer"},
                        {"race_pace": "Neutral"},
                        {"sarr_rank_max": 3},
                        {"et_min": 102},
                        {"pace_style": "Closer", "sarr_rank_max": 8},
                        {"venue": "ST", "et_max": 200, "pace_style": "Leader"}):
            got = lookup.insight(conn=conn, **filters)
            assert got["runs"] >= 0
            assert "by_style" in got
    finally:
        conn.close()


def test_the_join_picker_reads_the_clause_it_is_given() -> None:
    from hkrd.query.lookup import _joins
    assert "runner_pace" not in _joins("1 = 1")
    assert "runner_sarr" not in _joins("1 = 1")
    assert "runner_et" in _joins("1 = 1"), "the insight always averages a figure"
    assert "runner_pace" in _joins("1 = 1 AND p.pace_style = ?")
    assert "runner_sarr" in _joins("1 = 1 AND s.sarr_rank <= ?")
    # The by-style query groups on pace whether or not a filter mentions it.
    assert "runner_pace" in _joins("1 = 1", always="ep")

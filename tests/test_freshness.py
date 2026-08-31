"""The freshness strip, and what "stale" means for each source.

The strip was reading `/api/status` — the MODEL status endpoint — and rendering
its `tables` key, so it showed how much of the archive had been DERIVED rather
than how current the data is. `/api/ops/status` had the real answer and nothing
called it.

The rule these tests pin is that each source is judged against its OWN normal.
Odds go stale in minutes; barrier trials are published weekly. One shared
threshold calls odds fine and trials broken, or the reverse.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.query import freshness as fq
from hkrd.store.connect import get_conn, init_db, transaction

NOW = dt.datetime(2026, 7, 15, 16, 22, tzinfo=dt.timezone.utc)


def _ago(minutes: int) -> str:
    return (NOW - dt.timedelta(minutes=minutes)).isoformat()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "f.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        for job, mins in (("scrape_meeting:card", 122),
                          ("scrape_odds", 47),
                          ("scrape_trials", 4320),
                          ("scrape_meeting:vet", 118)):
            conn.execute(
                "INSERT INTO job_runs (job, started_at, finished_at, ok, detail) "
                "VALUES (?, ?, ?, 1, ?)",
                (job, _ago(mins + 1), _ago(mins), f"{job} wrote rows"))
    conn.close()
    return path


def _by_key(path):
    conn = get_conn(path)
    try:
        return {s["key"]: s for s in fq.strip(conn=conn, now=NOW)["sources"]}
    finally:
        conn.close()


def test_each_source_is_judged_against_its_own_normal(db):
    """47 minutes is stale for odds and current for everything else."""
    s = _by_key(db)
    assert s["odds"]["stale"] is True        # normal 15m
    assert s["card"]["stale"] is False       # normal 12h, age 2h
    assert s["vet"]["stale"] is False        # normal 12h, age ~2h


def test_three_days_is_current_for_trials(db):
    """Trials are published weekly. A shared threshold would call this broken."""
    s = _by_key(db)
    assert s["trials"]["age"] == "3d"
    assert s["trials"]["stale"] is False


def test_a_source_that_never_ran_reads_as_neither_fresh_nor_stale(db):
    """Never-run and overdue need different answers: the fix differs."""
    s = _by_key(db)
    assert s["results"]["minutes"] is None
    assert s["results"]["mark"] == "—"
    assert s["results"]["stale"] is False


def test_the_mark_carries_what_the_run_wrote(db):
    """A job that ran and stored nothing is the failure this strip is for."""
    assert _by_key(db)["card"]["detail"]


def test_age_reads_in_the_coarsest_honest_unit():
    assert fq.age_label(47) == "47m"
    assert fq.age_label(122) == "2h"
    assert fq.age_label(4320) == "3d"
    assert fq.age_label(None) == "—"


def test_odds_prefer_their_own_capture_time(db):
    """Odds rows carry a capture stamp, which beats "when the job ran"."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute(
            "INSERT INTO odds_snapshots (race_date, race_no, horse_no, "
            "captured_at, win_odds) VALUES ('2026-07-15', 1, 1, ?, 3.0)",
            (_ago(5),))
    conn.close()
    s = _by_key(db)
    assert s["odds"]["age"] == "5m"
    assert s["odds"]["stale"] is False


def test_a_tz_naive_job_row_does_not_crash_the_strip(db):
    """job_log writes UTC; a row without an offset must still subtract."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute(
            "INSERT INTO job_runs (job, started_at, finished_at, ok, detail) "
            "VALUES ('scrape_meeting:results', '2026-07-15T14:00:00', "
            "'2026-07-15T14:02:00', 1, '9 races')")
    conn.close()
    assert _by_key(db)["results"]["minutes"] == 140

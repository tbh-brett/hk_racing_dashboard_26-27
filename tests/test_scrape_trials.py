"""jobs/scrape_trials — one trial day, stored.

Separate from the meeting job because trials are held on their own days. The
rows it writes carry two fields the archive has never had: `distance`, which
HKJC publishes in the batch header and the legacy import dropped, and `going`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.jobs import scrape_trials
from hkrd.store.connect import get_conn

TRIALS_HTML = (Path(__file__).parent / "fixtures" / "trials_day.html").read_text(
    encoding="utf-8")


class _Resp:
    def __init__(self, status, text="", url=""):
        self.status_code, self.text, self.url = status, text, url


class _Session:
    def get(self, url, params=None, timeout=None):
        return _Resp(200, TRIALS_HTML)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr("hkrd.ingest._client.MIN_INTERVAL", 0.0)


def test_a_trial_day_is_stored_with_its_batches(tmp_path):
    db = tmp_path / "t.db"
    report = scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    assert report.batches == 2
    assert report.runners == 4
    assert report.ok

    conn = get_conn(db)
    rows = conn.execute(
        "SELECT horse_name, trial_no, distance, going, venue, surface, draw, "
        "lengths_behind, place FROM trials ORDER BY trial_no, place").fetchall()
    conn.close()
    assert len(rows) == 4
    first = dict(rows[0])
    assert first["horse_name"] == "CALA DEI MORI"
    assert first["distance"] == 1200
    assert first["going"] == "GOOD"
    assert first["venue"] == "ST" and first["surface"] == "AWT"
    assert first["draw"] == 3
    assert first["place"] == 1


def test_the_batch_conditions_reach_every_runner(tmp_path):
    """The trials table is one row per RUN. A reader asking what going a horse
    trialled on should not have to join back to a batch table that does not
    exist."""
    db = tmp_path / "t.db"
    scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    conn = get_conn(db)
    goings = conn.execute(
        "SELECT DISTINCT trial_no, going, distance FROM trials "
        "ORDER BY trial_no").fetchall()
    conn.close()
    assert [(r["trial_no"], r["going"], r["distance"]) for r in goings] == [
        (1, "GOOD", 1200), (2, "GOOD TO FIRM", 1000)]


def test_running_it_twice_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    conn = get_conn(db)
    n = conn.execute("SELECT count(*) FROM trials").fetchone()[0]
    conn.close()
    assert n == 4


def test_a_day_with_no_trials_reports_it_rather_than_succeeding_quietly(tmp_path):
    class Empty:
        def get(self, *a, **k):
            return _Resp(200, "<html><body>No trials.</body></html>")

    report = scrape_trials.scrape("2026-08-22", db=tmp_path / "t.db",
                                  session=Empty())
    assert not report.ok
    assert report.errors and report.batches == 0


def test_the_report_counts_what_carries_a_distance(tmp_path):
    """So the gap between what the archive has and what a scrape adds is
    visible rather than assumed."""
    report = scrape_trials.scrape("2026-08-21", db=tmp_path / "t.db",
                                  session=_Session())
    assert report.with_distance == report.runners

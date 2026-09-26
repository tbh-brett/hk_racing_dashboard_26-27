"""jobs/scrape_trials — one trial day, stored.

Separate from the meeting job because trials are held on their own days. The
rows it writes carry two fields the archive has never had: `distance`, which
HKJC publishes in the batch header and the legacy import dropped, and `going`.
"""
from __future__ import annotations

import re
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


# ── which days to fetch ──────────────────────────────────────────────────────
#
# Measured over the 2025-26 archive (159 trial days): Tue, Thu and Fri 26.4%
# each, Mon 16.4%, Sat 3.1%, Wed 1.3%. A crontab guessing Tuesday and Thursday
# would miss 47% of trial days and say nothing. HKJC publishes the list, so the
# job asks instead of guessing.

def test_the_day_list_is_read_from_the_page(tmp_path):
    from hkrd.ingest import trials as ingest
    html = ('<select id="selectId">'
            '<option value="">Select date</option>'
            '<option value="21/08/2026">21/08/2026</option>'
            '<option value="18/08/2026">18/08/2026</option></select>')
    assert ingest.parse_day_list(html) == ["2026-08-21", "2026-08-18"]


def test_a_missing_selector_raises_rather_than_returning_nothing():
    """Otherwise the scheduled scrape stops finding trials forever and the
    only symptom is an archive that quietly stops growing."""
    from hkrd.ingest import trials as ingest
    with pytest.raises(ingest.TrialsError):
        ingest.parse_day_list("<html><body>no selector here</body></html>")


def test_a_selector_with_no_parseable_dates_raises():
    from hkrd.ingest import trials as ingest
    with pytest.raises(ingest.TrialsError):
        ingest.parse_day_list(
            '<select id="selectId"><option value="Aug 21">x</option></select>')


def test_only_the_days_the_database_lacks_are_fetched(tmp_path):
    db = tmp_path / "t.db"
    scrape_trials.scrape("2026-08-21", db=db, session=_Session())

    listed = ["2026-08-25", "2026-08-21", "2026-08-18"]
    assert scrape_trials.outstanding(listed, db=db) == ["2026-08-18",
                                                        "2026-08-25"]


def test_catch_up_takes_the_most_recent_and_runs_them_oldest_first(tmp_path):
    """Oldest first so a run cut short leaves the gap at the recent end, where
    the next run will find it, rather than a hole in the middle."""
    listed = [f"2026-08-{d:02d}" for d in (25, 21, 18, 14, 11, 7, 4)]
    got = scrape_trials.outstanding(listed, db=tmp_path / "empty.db", limit=3)
    assert got == ["2026-08-18", "2026-08-21", "2026-08-25"]


# ── a day HKJC had not finished ──────────────────────────────────────────────
#
# HKJC puts the times up first and the running positions and comments later.
# 2026-09-19 and 2026-09-25 were read in between, counted as HAD, and never
# asked about again: 116 runners in production with a time and no position, no
# placing and no comment, long after HKJC had published all three.

# The same page read early: every running position and every remark blank.
EARLY_HTML = re.sub(r"<td>[^<]*\.</td>", "<td></td>",
                    re.sub(r"<td>\d+(?: \d+)+</td>", "<td></td>", TRIALS_HTML))


class _Page:
    def __init__(self, html):
        self.html = html

    def get(self, url, params=None, timeout=None):
        return _Resp(200, self.html)


def _stored(db):
    conn = get_conn(db)
    rows = conn.execute(
        "SELECT horse_name, place, running_positions, comment_text "
        "FROM trials ORDER BY horse_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_a_day_read_before_hkjc_finished_it_is_fetched_again(tmp_path):
    db = tmp_path / "t.db"
    early = scrape_trials.scrape("2026-08-21", db=db, session=_Page(EARLY_HTML))
    assert early.runners == 4
    assert early.with_positions == early.with_comment == 0
    assert early.unfinished == 2
    assert "NOT YET PUBLISHED" in early.render()

    listed = ["2026-08-25", "2026-08-21", "2026-08-18"]
    assert scrape_trials.outstanding(listed, db=db) == [
        "2026-08-18", "2026-08-21", "2026-08-25"]

    whole = scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    assert whole.unfinished == 0 and whole.with_comment == 4
    assert all(r["place"] and r["comment_text"] for r in _stored(db))
    assert scrape_trials.outstanding(listed, db=db) == [
        "2026-08-18", "2026-08-25"]


def test_one_runner_without_a_position_is_a_non_finisher_not_an_early_read(
        tmp_path):
    """Every complete day in production has one to three of these. Treating
    them as unfinished would fetch every day again for a fortnight."""
    db = tmp_path / "t.db"
    one_short = (TRIALS_HTML.replace("<td>5 6 9</td>", "<td></td>")
                 .replace("Weakened halfway; gave ground thereafter.", ""))
    report = scrape_trials.scrape("2026-08-21", db=db, session=_Page(one_short))
    assert report.with_positions == 3 and report.unfinished == 0
    assert scrape_trials.outstanding(["2026-08-21"], db=db) == []


def test_an_unfinished_day_is_only_rechecked_while_it_is_recent(tmp_path):
    """A day HKJC never completes must stop costing a request."""
    db = tmp_path / "t.db"
    scrape_trials.scrape("2026-08-21", db=db, session=_Page(EARLY_HTML))
    listed = ["2026-08-28", "2026-08-25", "2026-08-21"]
    for newer in listed[:2]:
        scrape_trials.scrape(newer, db=db, session=_Session())
    assert scrape_trials.outstanding(listed, db=db, recheck=3) == ["2026-08-21"]
    assert scrape_trials.outstanding(listed, db=db, recheck=2) == []


def test_fetching_a_stored_day_again_never_blanks_it(tmp_path):
    """The upsert keeps a stored value against NULL and not against "", so an
    empty cell has to arrive as NULL now that stored days are re-read."""
    db = tmp_path / "t.db"
    scrape_trials.scrape("2026-08-21", db=db, session=_Session())
    before = _stored(db)
    scrape_trials.scrape("2026-08-21", db=db, session=_Page(EARLY_HTML))
    assert _stored(db) == before


def test_a_day_hkjc_does_not_publish_is_not_a_failure(tmp_path):
    """Asked by hand for a known trial day, an empty result is a fault. Asked
    every morning by cron, it is most mornings."""
    class Missing:
        def get(self, *a, **k):
            return _Resp(404, "")

    report = scrape_trials.scrape("2026-08-22", db=tmp_path / "t.db",
                                  session=Missing())
    assert report.ok and report.no_such_day and report.batches == 0
    assert "none published" in report.render()

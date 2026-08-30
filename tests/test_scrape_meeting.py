"""jobs/scrape_meeting — the orchestrator that replaces code generation.

run_meeting.generate_analysis_script() wrote a Python file per meeting by
copying a 4,536-line template and executing it. Those files were committed and
froze at whatever template version was current, which is why race_day_report
reached 3 schemas with 17 inconsistent keys while every dedicated scraper held
exactly one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.jobs import scrape_meeting
from hkrd.store.connect import get_conn

FIXTURES = Path(__file__).parent / "fixtures"
RACE_HTML = (FIXTURES / "results_race.html").read_text(encoding="utf-8")
CORUN_HTML = (FIXTURES / "corunning_4col.html").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, status, text="", url=""):
        self.status_code, self.text, self.url = status, text, url


class _Session:
    """Serves `races` races of results, and corunning for the same card."""

    def __init__(self, races=3):
        self.races = races
        self.seen: list[str] = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        no = int(params.get("RaceNo") or params.get("raceno") or 0)
        self.seen.append(url)
        if no > self.races:
            return _Resp(404, url=f"{url}?{no}")
        return _Resp(200, CORUN_HTML if "corunning" in url else RACE_HTML)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr("hkrd.ingest._client.MIN_INTERVAL", 0.0)


def test_scrapes_a_card_and_stores_it(tmp_path):
    db = tmp_path / "m.db"
    report = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db,
                                           session=_Session(3))
    assert report.races == 3
    assert report.runners == 9          # 3 runners x 3 races
    assert report.ok

    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM races").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM runners").fetchone()[0] == 9
    conn.close()


def test_values_are_coerced_on_the_way_in(tmp_path):
    db = tmp_path / "m.db"
    scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=_Session(1))
    conn = get_conn(db)
    row = conn.execute("SELECT * FROM runners WHERE horse_no = 11").fetchone()
    conn.close()
    assert row["finish_time"] == pytest.approx(100.13)     # '1:40.13'
    assert row["lengths_behind"] == pytest.approx(1.25)    # '1-1/4'
    assert row["place"] == 2
    assert row["horse_name"] == "TELECOM POWER"


def test_the_winner_has_no_margin_rather_than_a_wrong_one(tmp_path):
    db = tmp_path / "m.db"
    scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=_Session(1))
    conn = get_conn(db)
    row = conn.execute("SELECT lengths_behind FROM runners WHERE place = 1").fetchone()
    conn.close()
    assert row["lengths_behind"] is None      # '---'


def test_running_the_same_meeting_twice_gives_identical_counts(tmp_path):
    db = tmp_path / "m.db"
    first = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=_Session(2))
    second = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=_Session(2))
    assert (first.races, first.runners) == (second.races, second.runners)
    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM runners").fetchone()[0] == first.runners
    conn.close()


def test_post_race_also_fetches_comments_on_running(tmp_path):
    """Comments are only published after the race is run, so they are not
    fetched on a pre-race pass."""
    db = tmp_path / "m.db"
    pre = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=_Session(2))
    assert pre.comments == 0

    post = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, post_race=True,
                                         session=_Session(2))
    assert post.comments > 0
    assert post.lane_tags > 0


def test_report_carries_counts_per_table(tmp_path):
    """A job that reports nothing looks the same whether it worked or silently
    did nothing -- which is how the pace column sat empty for weeks."""
    db = tmp_path / "m.db"
    report = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db,
                                           post_race=True, session=_Session(2))
    text = report.render()
    for label in ("races", "runners", "comments", "lane tags"):
        assert label in text


def test_a_failed_fetch_reports_and_stores_nothing(tmp_path):
    class Dead:
        def get(self, *a, **k):
            return _Resp(500, url="x")

    db = tmp_path / "m.db"
    report = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db, session=Dead())
    assert report.errors and not report.ok
    assert report.races == 0


def test_no_code_generation_anywhere_in_the_job():
    """The single thing that must not survive the rebuild."""
    src = Path("hkrd/jobs/scrape_meeting.py").read_text(encoding="utf-8")
    for forbidden in ("exec(", "eval(", "compile(", "generate_analysis_script"):
        assert forbidden not in src.replace("generate_analysis_script(), which", "")


# ── the four parsers wired in ────────────────────────────────────────────────

RACECARD_HTML = (FIXTURES / "racecard.html").read_text(encoding="utf-8")
DIVIDENDS_HTML = (FIXTURES / "dividends.html").read_text(encoding="utf-8")
VET_HTML = (FIXTURES / "vet_race.html").read_text(encoding="utf-8")


class _FullSession:
    """Serves whichever page the URL asks for, for `races` races."""

    def __init__(self, races=3):
        self.races = races

    def get(self, url, params=None, timeout=None):
        params = params or {}
        no = int(params.get("RaceNo") or params.get("raceno") or 0)
        if no > self.races:
            return _Resp(404, url=f"{url}?{no}")
        if "racecard" in url:
            return _Resp(200, RACECARD_HTML)
        if "corunning" in url:
            return _Resp(200, CORUN_HTML)
        if "veterinaryrecord" in url:
            return _Resp(200, VET_HTML)
        if "localresults" in url:
            # The results page carries the dividends below the finishing order.
            return _Resp(200, RACE_HTML + DIVIDENDS_HTML)
        return _Resp(200, RACE_HTML)


def test_the_declared_card_is_stored_before_the_race_is_run(tmp_path):
    """The card is the only source for rating, gear and days-since-last, and a
    meeting can be scraped ahead of time — the results pass upserts onto the
    same rows rather than making a second set."""
    db = tmp_path / "m.db"
    report = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db,
                                           session=_FullSession(3))
    assert report.declared > 0
    conn = get_conn(db)
    rated = conn.execute(
        "SELECT count(*) FROM runners WHERE rating IS NOT NULL").fetchone()[0]
    conn.close()
    assert rated > 0


def test_a_missing_card_is_a_warning_not_a_failed_scrape(tmp_path):
    """A source that is absent is not a scrape that failed. The card is not
    published for every meeting this package can reach, and a results-only
    scrape is legitimate."""
    db = tmp_path / "m.db"
    report = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db,
                                           session=_Session(3))
    assert report.warnings          # the fake pages have no race card
    assert not report.errors
    assert report.ok                # results came through, so the job is fine


def test_dividends_and_vet_records_are_post_race_only(tmp_path):
    """Both only exist after the race, so a pre-race scrape must not report
    their absence as a fault."""
    db = tmp_path / "pre.db"
    pre = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db,
                                        session=_FullSession(3))
    assert pre.dividends == 0 and pre.vet_records == 0

    db2 = tmp_path / "post.db"
    post = scrape_meeting.scrape_meeting("2026-07-15", "HV", db=db2,
                                         post_race=True,
                                         session=_FullSession(3))
    assert post.dividends > 0
    assert post.vet_records > 0
    conn = get_conn(db2)
    assert conn.execute("SELECT count(*) FROM dividends").fetchone()[0] > 0
    assert conn.execute("SELECT count(*) FROM vet_records").fetchone()[0] > 0
    conn.close()


def test_an_unreachable_host_is_not_retried_once_per_race(tmp_path):
    """Transport, not content. If the host will not answer for race 1 it will
    not answer for race 11, and retrying eleven times turned one unreachable
    meeting into a minute of backoff."""
    import time

    class Dead:
        def __init__(self):
            self.calls = 0

        def get(self, *a, **k):
            self.calls += 1
            return _Resp(500, url="x")

    dead = Dead()
    started = time.monotonic()
    scrape_meeting.scrape_meeting("2026-07-15", "HV", db=tmp_path / "d.db",
                                  session=dead)
    # 3 attempts for the card + 3 for the results, not 3 x 11 for each.
    assert dead.calls <= 8, f"{dead.calls} fetches for one unreachable meeting"
    assert time.monotonic() - started < 30

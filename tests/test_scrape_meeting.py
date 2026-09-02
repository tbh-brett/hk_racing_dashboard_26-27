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


# ── a meeting that has not been run yet ──────────────────────────────────────
#
# The only kind the Card button exists for. Its card is up; its results do not
# exist. Every signal said the scrape had failed: `ok` required races > 0, the
# results failure was filed as an error, the early return skipped the source
# log entirely, and the API reported the `races`/`runners` counters — which a
# card scrape never touches — so a card that had just stored a full field
# reported "wrote nothing" and the strip kept saying "no successful run on
# record".

_CARD = {
    "races": [{
        "race": {"race_date": "2099-01-01", "race_no": 1, "venue": "ST",
                 "course": "A", "surface": "Turf", "going": "G",
                 "distance": 1200, "race_class": "4"},
        "runners": [{"race_no": 1, "horse_no": i, "horse_name": f"H{i}",
                     "draw": i, "gear": "B", "rating": 50} for i in range(1, 13)],
    }],
    "errors": [],
}


@pytest.fixture()
def pre_race(monkeypatch, tmp_path):
    """A card that is up, for a meeting nobody has run."""
    from hkrd.ingest._client import FetchError
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: _CARD)
    monkeypatch.setattr(
        scrape_meeting.results_ingest, "fetch_meeting",
        lambda *a, **k: (_ for _ in ()).throw(FetchError("404 not published")))
    return scrape_meeting.scrape_meeting("2099-01-01", "ST", db=tmp_path / "t.db")


def test_a_card_scraped_before_the_race_is_a_success(pre_race):
    """There is nothing else to fetch until the meeting is run."""
    assert pre_race.declared == 12
    assert pre_race.ok is True
    assert pre_race.errors == []


def test_results_that_do_not_exist_yet_are_not_an_error(pre_race):
    assert any("not published yet" in w for w in pre_race.warnings)


def test_the_card_is_recorded_even_though_results_ended_the_run(pre_race, tmp_path):
    """The early return skipped the source log, so the strip said the card had
    never succeeded no matter how many times it did."""
    conn = get_conn(tmp_path / "t.db")
    logged = dict(conn.execute("SELECT job, ok FROM job_runs").fetchall())
    conn.close()
    assert logged.get("scrape_meeting:card") == 1
    # Results are not claimed either way before the meeting has been run.
    assert "scrape_meeting:results" not in logged


def test_the_declared_field_is_actually_stored(pre_race, tmp_path):
    conn = get_conn(tmp_path / "t.db")
    n = conn.execute("SELECT count(*) FROM runners WHERE race_date='2099-01-01'"
                     ).fetchone()[0]
    gear = conn.execute("SELECT gear FROM runners WHERE horse_no=1").fetchone()[0]
    conn.close()
    assert n == 12
    assert gear == "B", "the card is the only source of gear"


def test_missing_results_after_the_race_is_still_an_error(monkeypatch, tmp_path):
    """The rule reads the calendar, not the mood: a meeting that HAS been run
    and has no results is a real failure and must stay one."""
    from hkrd.ingest._client import FetchError
    past = dict(_CARD)
    past = {"races": [{**_CARD["races"][0],
                       "race": {**_CARD["races"][0]["race"],
                                "race_date": "2020-01-01"}}],
            "errors": []}
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: past)
    monkeypatch.setattr(
        scrape_meeting.results_ingest, "fetch_meeting",
        lambda *a, **k: (_ for _ in ()).throw(FetchError("404 not published")))
    r = scrape_meeting.scrape_meeting("2020-01-01", "ST", db=tmp_path / "t.db")
    assert r.errors and "results" in r.errors[0]
    assert r.ok is False


def test_sources_that_only_exist_after_a_race_are_not_claimed_before_it(
        pre_race, tmp_path):
    """Results and dividends do not exist until the meeting is run, so a
    pre-race scrape has neither succeeded nor failed at them. Recording them
    as successful with "0 rows" is the same silent zero the freshness strip
    exists to catch.

    The vet record is NOT one of those: it is each declared runner's
    veterinary history, published with the card, so it is claimed either way.
    """
    conn = get_conn(tmp_path / "t.db")
    logged = dict(conn.execute("SELECT job, ok FROM job_runs").fetchall())
    conn.close()
    assert set(logged) == {"scrape_meeting:card", "scrape_meeting:vet"}
    assert logged["scrape_meeting:card"] == 1


# ── another meeting's results must never land under this date ────────────────
#
# Asked for the results of a meeting that has not been run, HKJC does not
# answer 404 — it serves a page. `_store_race` stamps the date we ASKED for
# onto whatever came back, so the 6 Sep card showed 15 Jul's twelve horses at
# 15 Jul's prices, with the two genuinely new runners left underneath. Nothing
# about the stored rows said they were from elsewhere.

_OTHER_MEETING = [{
    "race_no": 1, "venue": "HV", "course": "C", "going": "G", "distance": 1650,
    "race_class": "5",
    "runners": [{"horse_no": i, "horse_name": f"OLD HORSE {i}",
                 "place": str(i), "win_odds": 7.0 + i} for i in range(1, 13)],
}]


def test_post_race_sources_are_not_fetched_before_the_race(monkeypatch, tmp_path):
    """The fix is to not ask. Nothing post-race exists yet."""
    asked = []
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: _CARD)
    monkeypatch.setattr(scrape_meeting.results_ingest, "fetch_meeting",
                        lambda *a, **k: asked.append("results") or _OTHER_MEETING)
    r = scrape_meeting.scrape_meeting("2099-01-01", "ST", db=tmp_path / "t.db",
                                      post_race=True)
    assert asked == [], "results were fetched for a meeting nobody has run"
    assert r.declared == 12 and r.ok is True


def test_results_for_a_field_this_meeting_never_declared_are_refused(
        monkeypatch, tmp_path):
    """The second line of defence, for a date in the past where we do ask."""
    past_card = {"races": [{**_CARD["races"][0],
                            "race": {**_CARD["races"][0]["race"],
                                     "race_date": "2020-01-01"}}],
                 "errors": []}
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: past_card)
    monkeypatch.setattr(scrape_meeting.results_ingest, "fetch_meeting",
                        lambda *a, **k: _OTHER_MEETING)
    r = scrape_meeting.scrape_meeting("2020-01-01", "ST", db=tmp_path / "t.db")

    assert any("did not declare" in e for e in r.errors)
    conn = get_conn(tmp_path / "t.db")
    names = [x[0] for x in conn.execute(
        "SELECT horse_name FROM runners WHERE race_date='2020-01-01'").fetchall()]
    conn.close()
    assert not any(n.startswith("OLD HORSE") for n in names), \
        "another meeting's runners were stored under this date"
    assert r.ok is False


def test_the_real_field_is_still_stored_when_it_matches(monkeypatch, tmp_path):
    """The guard asks for a majority overlap, so scratchings and reserves do
    not make a genuine card look like a substitution."""
    past_card = {"races": [{**_CARD["races"][0],
                            "race": {**_CARD["races"][0]["race"],
                                     "race_date": "2020-01-01"}}],
                 "errors": []}
    real = [{"race_no": 1, "venue": "ST", "course": "A", "going": "G",
             "distance": 1200, "race_class": "4",
             # Ten of the twelve declared, plus one late reserve.
             "runners": ([{"horse_no": i, "horse_name": f"H{i}", "place": str(i)}
                          for i in range(1, 11)]
                         + [{"horse_no": 13, "horse_name": "RESERVE",
                             "place": "11"}])}]
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: past_card)
    monkeypatch.setattr(scrape_meeting.results_ingest, "fetch_meeting",
                        lambda *a, **k: real)
    r = scrape_meeting.scrape_meeting("2020-01-01", "ST", db=tmp_path / "t.db")
    assert r.races == 1 and r.runners == 11
    assert not any("did not declare" in e for e in r.errors)


def test_the_vet_record_is_fetched_before_the_race(monkeypatch, tmp_path):
    """It is a history attached to today's field, not an account of the race,
    and it is the single most useful thing to know before backing anything."""
    asked = []
    monkeypatch.setattr(scrape_meeting.racecard_ingest, "fetch_meeting",
                        lambda *a, **k: _CARD)
    monkeypatch.setattr(scrape_meeting.results_ingest, "fetch_meeting",
                        lambda *a, **k: asked.append("results") or [])
    monkeypatch.setattr(
        scrape_meeting.vet_ingest, "fetch_meeting",
        lambda *a, **k: asked.append("vet") or {1: [
            {"race_date": "2099-01-01", "race_no": 1, "horse_no": 1,
             "horse_name": "H1", "record_date": "2026-06-12",
             "detail": "lameness in the near fore", "passed_date": None,
             "category": "PHYSICAL"}]})
    r = scrape_meeting.scrape_meeting("2099-01-01", "ST", db=tmp_path / "t.db",
                                      post_race=True)
    assert "vet" in asked, "the vet record was not fetched for a live card"
    assert "results" not in asked
    assert r.vet_records == 1

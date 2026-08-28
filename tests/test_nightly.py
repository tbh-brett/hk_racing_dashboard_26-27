"""jobs/nightly — what the scheduled run decides, and what it says when it fails.

The point of these is the DECISION, not the fetching. A scheduled scrape that
picks the wrong dates is invisible: the dashboard keeps serving last week and
looks exactly as it did when it was right.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.ingest._client import FetchError, NotFound
from hkrd.ingest import racecard
from hkrd.jobs import nightly
from hkrd.store import job_log, upsert
from hkrd.store.connect import get_conn, init_db, transaction

TODAY = dt.date(2026, 8, 26)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "nightly.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    conn = get_conn(path)
    init_db(conn)
    conn.close()
    return path


def _meeting(db, date: str, venue: str, *, races: int = 3,
             results: bool = True, dividends: bool = True) -> None:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": date, "race_no": n, "venue": venue, "distance": 1200,
             "course": "A", "surface": "Turf", "going": "G", "race_class": "4"}
            for n in range(1, races + 1)])
        upsert.upsert_runners(conn, [
            {"race_date": date, "race_no": n, "horse_no": h,
             "horse_name": f"HORSE {n}{h}",
             "place": str(h) if results else None}
            for n in range(1, races + 1) for h in (1, 2)])
        if dividends:
            upsert.upsert_dividends(conn, [
                {"race_date": date, "race_no": n, "pool": "WIN",
                 "combination": "1", "dividend": 25.0}
                for n in range(1, races + 1)])
    conn.close()


def _plans(db, **kw) -> dict[str, nightly.Plan]:
    return {p.date: p for p in nightly.plan_window(db, today=TODAY, **kw)}


# ── the decision ─────────────────────────────────────────────────────────────

def test_a_settled_meeting_is_left_alone(db):
    """Results and dividends for every race means it will never change again.
    Re-fetching it is eleven requests of noise against a public site."""
    _meeting(db, "2026-08-23", "ST")
    p = _plans(db)["2026-08-23"]
    assert not p.act and p.venue == "ST" and "settled" in p.reason


def test_a_meeting_missing_dividends_is_picked_up(db):
    _meeting(db, "2026-08-24", "HV", dividends=False)
    p = _plans(db)["2026-08-24"]
    assert p.act and "without dividends" in p.reason


def test_a_card_with_no_results_is_picked_up(db):
    _meeting(db, "2026-08-25", "ST", results=False, dividends=False)
    p = _plans(db)["2026-08-25"]
    assert p.act and "without results" in p.reason


def test_tomorrows_card_is_refetched_because_horses_get_scratched(db):
    _meeting(db, "2026-08-27", "HV", results=False, dividends=False)
    p = _plans(db)["2026-08-27"]
    assert p.act and p.venue == "HV"


def test_a_date_the_database_has_never_seen_has_no_venue_so_both_are_probed(db):
    p = _plans(db)["2026-08-22"]
    assert p.act and p.venue is None


def test_the_window_is_the_days_asked_for(db):
    plans = _plans(db, back=2, ahead=1)
    assert sorted(plans) == ["2026-08-24", "2026-08-25", "2026-08-26",
                             "2026-08-27"]


def test_planning_makes_no_requests(db, monkeypatch):
    """A plan is a database read. If it reaches the network, a dry run is a
    scrape and the operator has no way to ask what tonight would do."""
    def explode(*a, **k):
        raise AssertionError("plan_window made an HTTP request")
    monkeypatch.setattr(racecard, "fetch_race", explode)
    monkeypatch.setattr(racecard, "fetch_meeting", explode)
    _meeting(db, "2026-08-24", "HV", dividends=False)
    assert nightly.run(db, today=TODAY, dry_run=True).plans


# ── the probe ────────────────────────────────────────────────────────────────

def test_a_404_means_no_meeting(db, monkeypatch):
    monkeypatch.setattr(racecard, "fetch_race",
                        lambda *a, **k: (_ for _ in ()).throw(NotFound("404")))
    assert nightly.probe("2026-08-22", "ST")[0] == "none"


def test_a_page_that_will_not_parse_is_not_the_same_as_no_meeting(db, monkeypatch):
    """HKJC's behaviour on a non-race day could not be measured where this was
    written, so both shapes are handled — but they stay distinguishable."""
    monkeypatch.setattr(racecard, "fetch_race", lambda *a, **k: (
        _ for _ in ()).throw(racecard.RacecardError("no race card table found")))
    assert nightly.probe("2026-08-22", "ST")[0] == "unparsed"


def test_an_empty_card_table_is_no_meeting(db, monkeypatch):
    monkeypatch.setattr(racecard, "fetch_race",
                        lambda *a, **k: {"race": {}, "runners": []})
    assert nightly.probe("2026-08-22", "ST")[0] == "none"


def test_a_card_with_runners_is_a_meeting(db, monkeypatch):
    monkeypatch.setattr(racecard, "fetch_race",
                        lambda *a, **k: {"race": {}, "runners": [{}, {}, {}]})
    verdict, detail = nightly.probe("2026-08-22", "ST")
    assert verdict == "card" and "3 declared" in detail


# ── what it says when it goes wrong ──────────────────────────────────────────

def test_a_window_where_nothing_parsed_is_reported_not_swallowed(db, monkeypatch):
    """Five quiet nights and a changed layout look identical from here. The
    job says so rather than exiting 0 on an empty window."""
    monkeypatch.setattr(racecard, "fetch_race", lambda *a, **k: (
        _ for _ in ()).throw(racecard.RacecardError("no race card table found")))
    report = nightly.run(db, today=TODAY, derive=False)
    assert not report.ok
    assert "would not parse" in report.errors[0]


def test_a_quiet_night_is_not_an_error(db, monkeypatch):
    monkeypatch.setattr(racecard, "fetch_race",
                        lambda *a, **k: (_ for _ in ()).throw(NotFound("404")))
    report = nightly.run(db, today=TODAY, derive=False)
    assert report.ok
    assert report.one_line() == "nothing outstanding"


def test_a_meeting_the_database_knows_about_that_returns_nothing_is_an_error(
        db, monkeypatch):
    """This is the failure that must never be quiet: the card was scraped on
    Tuesday, so we KNOW Wednesday raced, and Wednesday night came back empty."""
    _meeting(db, "2026-08-25", "ST", results=False, dividends=False)

    from hkrd.jobs import scrape_meeting as scrape_job
    monkeypatch.setattr(scrape_job, "scrape_meeting",
                        lambda date, venue, **k: scrape_job.ScrapeReport(
                            date=date, venue=venue,
                            errors=["results: HTTP 503"]))
    monkeypatch.setattr(racecard, "fetch_race",
                        lambda *a, **k: (_ for _ in ()).throw(NotFound("404")))

    report = nightly.run(db, today=TODAY, derive=False)
    assert not report.ok
    assert any("2026-08-25 ST" in e and "no races" in e for e in report.errors)


def test_a_transport_failure_does_not_read_as_no_meeting(db, monkeypatch):
    monkeypatch.setattr(racecard, "fetch_race", lambda *a, **k: (
        _ for _ in ()).throw(FetchError("failed after 3 attempts")))
    assert nightly.probe("2026-08-22", "ST")[0] == "unparsed"


# ── the record it leaves ─────────────────────────────────────────────────────

def test_the_run_is_recorded_before_it_finishes(db):
    """An interrupted run and a run that never started must not look alike."""
    conn = get_conn(db)
    init_db(conn)
    try:
        with job_log.running("nightly", db):
            mid = job_log.last_run(conn, "nightly")
            assert mid is not None
            assert mid["ok"] is None and mid["finished_at"] is None
    finally:
        conn.close()


def test_a_failure_is_recorded_as_a_failure(db):
    conn = get_conn(db)
    init_db(conn)
    try:
        with job_log.running("nightly", db) as outcome:
            outcome["ok"] = False
            outcome["detail"] = "1 error(s): results: HTTP 503"
        row = job_log.last_run(conn, "nightly")
        assert row["ok"] is False and "503" in row["detail"]
    finally:
        conn.close()


def test_a_raised_exception_still_closes_the_row(db):
    conn = get_conn(db)
    init_db(conn)
    try:
        with pytest.raises(ValueError):
            with job_log.running("nightly", db):
                raise ValueError("scraper blew up")
        row = job_log.last_run(conn, "nightly")
        assert row["ok"] is False and "scraper blew up" in row["detail"]
    finally:
        conn.close()

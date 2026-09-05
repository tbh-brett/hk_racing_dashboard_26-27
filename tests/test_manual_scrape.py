"""Running one source by hand, from the strip that says it is stale.

Design brief 07 §6 argues against a page of scrape buttons: it makes the
mechanism the interface and leaves the user remembering what to run and when.
The freshness strip is the inversion — it already names the stale source — so
the button is the strip itself.

What these protect is the honesty of the answer. A scrape reaches the network
and sometimes a browser, so it fails for reasons that are about the HOST, and
the person clicking is the one who can fix them. A bare 500 tells them nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hkrd.api.app import app

client = TestClient(app)


def test_an_unknown_source_is_refused_by_name() -> None:
    r = client.post("/api/jobs/scrape", json={"source": "everything"})
    assert r.status_code == 400
    # The five it does know, so the caller can correct themselves.
    for known in ("card", "odds", "results", "trials", "vet"):
        assert known in r.json()["detail"]


def test_a_meeting_scrape_will_not_guess_the_meeting() -> None:
    """Guessing a venue would scrape the wrong track and store it as this
    one's card — silently, because both tracks return a valid page."""
    r = client.post("/api/jobs/scrape", json={"source": "card",
                                              "date": "2026-07-15"})
    assert r.status_code == 400
    assert "venue" in r.json()["detail"]


@pytest.mark.parametrize("source", ["card", "results", "vet"])
def test_the_three_meeting_sources_share_one_fetch(source: str) -> None:
    """The strip reports them separately because they FAIL separately, not
    because they are separate requests to HKJC."""
    from hkrd.api.routes.jobs import _SCRAPE_JOBS
    assert _SCRAPE_JOBS[source] == "meeting"


def test_every_source_on_the_strip_can_be_run() -> None:
    """A strip that says a source is stale and offers no way to fetch it is
    the diagnosis without the fix — which is the state this replaced."""
    from hkrd.api.routes.jobs import _SCRAPE_JOBS
    from hkrd.query.freshness import SOURCES
    missing = [s["key"] for s in SOURCES if s["key"] not in _SCRAPE_JOBS]
    assert not missing, f"the strip shows {missing} with no way to fetch them"


def test_a_refused_odds_capture_reaches_the_caller(monkeypatch) -> None:
    """The odds no longer need a browser — this used to check the 503 for a
    host without one, which was the reason the capture could not be automated.

    What can still go wrong is HKJC answering about a different meeting than
    the one asked for, which `ingest/odds` refuses. That refusal is an ANSWER,
    not a fault: the run happened, it stored nothing, and it knows why. So it
    comes back 200 with the reason in the payload rather than as a status code
    the strip has to translate.
    """
    from hkrd.jobs import scrape_odds

    class Report:
        races, attempted, win_place, pairs = 0, 11, 0, 0
        notes: list = []
        skipped = ["HKJC lists no meeting for 2026-09-06 ST"]

    monkeypatch.setattr(scrape_odds, "run", lambda *a, **k: Report())
    r = client.post("/api/jobs/scrape", json={"source": "odds"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["wrote"] == {"races": 0, "win_place": 0, "pairs": 0}
    assert "lists no meeting" in body["warnings"][0]


def test_no_source_asks_the_owner_to_install_a_browser(monkeypatch) -> None:
    """A capture that needs Chromium cannot run on the deploy image, so it
    cannot be automated — which is exactly why the odds cron line spent a
    season commented out."""
    from hkrd.api.routes import jobs as route

    src = Path(route.__file__).read_text(encoding="utf-8").lower()
    for banned in ("playwright", "chromium", "selenium"):
        assert banned not in src


def test_an_unreachable_hkjc_is_not_reported_as_our_bug(monkeypatch) -> None:
    from hkrd.jobs import scrape_trials

    def offline(*a, **k):
        raise OSError("HTTPSConnectionPool: Max retries exceeded")

    monkeypatch.setattr(scrape_trials, "scrape", offline)
    r = client.post("/api/jobs/scrape", json={"source": "trials",
                                              "date": "2026-08-21"})
    assert r.status_code == 503
    assert "could not reach HKJC" in r.json()["detail"]


def test_a_run_reports_what_it_wrote_not_that_it_ran(monkeypatch) -> None:
    """A scrape that succeeded and stored nothing is the failure the whole
    freshness design exists to make visible."""
    from hkrd.jobs import scrape_trials

    class Report:
        date, batches, runners, with_distance = "2026-08-21", 0, 0, 0
        errors: list = []
        no_such_day = True

    monkeypatch.setattr(scrape_trials, "scrape", lambda *a, **k: Report())
    body = client.post("/api/jobs/scrape",
                       json={"source": "trials", "date": "2026-08-21"}).json()
    assert body["wrote"] == {"batches": 0, "runners": 0, "days": 1}
    assert body["total"] == 1        # the day it looked at, not rows written
    assert body["warnings"] == ["2026-08-21: none published"]

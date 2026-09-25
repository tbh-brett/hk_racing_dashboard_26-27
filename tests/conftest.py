"""Test-wide setup.

The API refuses to start without `HKRD_PASSWORD` — deliberately, because the
dashboard serves the whole betting ledger and a deploy that forgot its secret
must fail rather than publish it. The suite is a local instance nothing else
can reach, so it takes the explicit opt-out; `test_auth.py` sets a password of
its own where the point is the password.
"""
from __future__ import annotations

import os

os.environ.setdefault("HKRD_ALLOW_NO_AUTH", "1")

import pytest


@pytest.fixture(autouse=True)
def _no_standards_fetch(monkeypatch):
    """The nightly job refreshes HKJC's standard times once a week, and the
    suite must never reach HKJC. Every test sees a copy that is fresh, so the
    refresh asks nothing; tests of the refresh itself import the real function
    at module load, before this runs."""
    from hkrd.jobs import scrape_standards
    monkeypatch.setattr(scrape_standards, "refresh", lambda *a, **k: None)

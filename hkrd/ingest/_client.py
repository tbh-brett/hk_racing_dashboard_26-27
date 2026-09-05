"""Shared HTTP session for every HKJC scraper.

One session, one User-Agent, one retry policy, one rate limit. The old repo had
each scraper build its own, so politeness and timeout behaviour varied by
whichever file you happened to be in.

Almost everything here is HTML scraping against the public site, which is why
the parsers are written to fail loudly on a layout change rather than quietly
return nothing. The one exception is odds: `bet.hkjc.com` is a single-page app
and it reads its prices from a JSON endpoint, so `ingest/odds.py` reads the
same endpoint. See `fetch_json` below.
"""
from __future__ import annotations

import json as _json
import threading
import time
from collections.abc import Mapping
from typing import Any

import requests

__all__ = ["BASE_URL", "FetchError", "NotFound", "get_session", "fetch_html",
           "fetch_json", "urls"]

BASE_URL = "https://racing.hkjc.com"


class urls:
    """Endpoint templates, in one place."""

    localresults = f"{BASE_URL}/en-us/local/information/localresults"
    resultsall = f"{BASE_URL}/en-us/local/information/resultsall"
    sectional = f"{BASE_URL}/en-us/local/information/displaysectionaltime"
    corunning = f"{BASE_URL}/en-us/local/information/corunning"
    racecard = f"{BASE_URL}/en-us/local/information/racecard"
    vet = f"{BASE_URL}/en-us/local/information/veterinaryrecord"
    trials = f"{BASE_URL}/en-us/local/information/btresult"
    # Odds, and only odds. A different host from everything above because it
    # belongs to the betting site rather than the information site; it is the
    # endpoint bet.hkjc.com's own front end reads, declared in its
    # /Config/GlobalConfig.js.
    graphql = "https://info.cld.hkjc.com/graphql/base/"


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# One request every 1.2s across all threads. HKJC is a public site run for
# punters, not an API with a quota; the courtesy is the point.
MIN_INTERVAL = 1.2
_last_request = 0.0
_lock = threading.Lock()
_session: requests.Session | None = None


class FetchError(RuntimeError):
    """A page could not be retrieved. Carries the URL and what went wrong."""


class NotFound(FetchError):
    """The page does not exist (404).

    Kept distinct from every other failure because callers walking ?raceno=1..N
    need "the card ended here" to mean something different from "the request
    failed". Collapsing the two is how a broken scraper looks like a short
    meeting.
    """


def get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _session = s
    return _session


def _throttle() -> None:
    global _last_request
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def fetch_html(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 1.5,
) -> str:
    """GET a page, returning its body.

    Raises FetchError rather than returning None: a scraper that returns nothing
    on failure produces an empty table three days later with no way to tell
    whether the race had no incidents or the fetch broke.
    """
    session = session or get_session()
    last: Exception | None = None

    for attempt in range(1, retries + 1):
        _throttle()
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = e
        else:
            if resp.status_code == 200:
                return resp.text
            # 404 is a real answer: that page does not exist. Do not retry it.
            if resp.status_code == 404:
                raise NotFound(f"404 for {resp.url}")
            last = FetchError(f"HTTP {resp.status_code} for {resp.url}")
        if attempt < retries:
            time.sleep(backoff * attempt)

    raise FetchError(f"failed after {retries} attempts: {url} — {last}")


def fetch_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 1.5,
) -> dict[str, Any]:
    """POST a JSON body and return the decoded reply.

    Same session, throttle and retry policy as `fetch_html`, so the one
    endpoint that speaks JSON is as polite as the pages are.

    GraphQL answers 200 with an `errors` key rather than an HTTP status, so a
    reply carrying errors is raised here as a FetchError. A caller that only
    checked the status code would read `data: null` as "no odds" and store a
    successful capture of nothing, which is the failure this whole module is
    written to prevent.
    """
    session = session or get_session()
    last: Exception | None = None

    for attempt in range(1, retries + 1):
        _throttle()
        try:
            resp = session.post(url, json=dict(payload), timeout=timeout,
                                headers={"Content-Type": "application/json"})
        except requests.RequestException as e:
            last = e
        else:
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except _json.JSONDecodeError as e:
                    raise FetchError(
                        f"{url} answered 200 with something that is not JSON "
                        f"— {resp.text[:200]!r}") from e
                if body.get("errors"):
                    messages = "; ".join(
                        str(e.get("message", e)) for e in body["errors"][:3])
                    raise FetchError(f"{url} returned errors: {messages}")
                return body
            if resp.status_code == 404:
                raise NotFound(f"404 for {url}")
            last = FetchError(f"HTTP {resp.status_code} for {url}")
        if attempt < retries:
            time.sleep(backoff * attempt)

    raise FetchError(f"failed after {retries} attempts: {url} — {last}")

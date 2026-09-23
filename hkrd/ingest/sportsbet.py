"""Sportsbet: fixed odds, and the Racing & Sports words, for HK races.

The endpoints are the ones sportsbet.com.au's own pages read. They answer a
home connection and refuse the Fly machine (403, measured 2026-09-23), so
this is called from the PC — `tools/harvest_sportsbet.py` — and what it
finds reaches the dashboard as a tips payload, like the YouTube sources.

    AllRacing/{date}                   every meeting that day, with its races'
                                       event ids
    Events/{id}/RacecardWithContext    one race: the "Win or Place" market,
                                       whose "L" price is the fixed win and
                                       place price; `raceCommentary`, the
                                       Racing & Sports preview; its four
                                       tips as selection ids, in order; and
                                       on each runner `statistics.overview`,
                                       a Racing & Sports line on its form

Returns plain dicts. Numbers and names are Sportsbet's; `jobs/import_tips`
checks both against the HKJC card before anything is stored.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from hkrd.ingest._client import HEADERS, FetchError, fetch_html

__all__ = ["API", "VENUES", "session", "race_ids", "fetch_race", "prices",
           "tips", "comment", "runner_comments", "page_url", "provider",
           "SportsbetError", "FetchError"]

API = "https://www.sportsbet.com.au/apigw/sportsbook-racing/Sportsbook/Racing"
VENUES = {"HV": "Happy Valley", "ST": "Sha Tin"}
_R_AND_S = "R_AND_S"


class SportsbetError(RuntimeError):
    """Sportsbet answered, but not with what a race record looks like."""


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({**HEADERS, "Accept": "application/json"})
    return s


def _get(url: str, *, session: requests.Session | None) -> Any:
    try:
        return json.loads(fetch_html(url, session=session))
    except ValueError as exc:
        raise SportsbetError(f"{url}: not JSON — {exc}") from exc


def race_ids(date: str, venue: str | None = None, *,
             session: requests.Session | None = None
             ) -> tuple[str | None, dict[int, int]]:
    """(venue, race number -> event id) for the HK meeting on `date`.

    Hong Kong races one meeting a day, so with no venue the one at Happy
    Valley or Sha Tin is it."""
    body = _get(f"{API}/AllRacing/{date}", session=session)
    wanted = [VENUES[venue]] if venue in VENUES else list(VENUES.values())
    for day in body.get("dates") or []:
        for section in day.get("sections") or []:
            for m in section.get("meetings") or []:
                if m.get("name") in wanted:
                    code = next(k for k, v in VENUES.items()
                                if v == m["name"])
                    return code, {int(e["raceNumber"]): int(e["id"])
                                  for e in m.get("events") or []
                                  if e.get("raceNumber") and e.get("id")}
    return None, {}


def fetch_race(event_id: int, *, session: requests.Session | None = None
               ) -> dict[str, Any]:
    body = _get(f"{API}/Events/{event_id}/RacecardWithContext",
                session=session)
    rec = body.get("racecardEvent") if isinstance(body, dict) else None
    if not isinstance(rec, dict) or not rec.get("markets"):
        raise SportsbetError(f"event {event_id}: no race card in the reply")
    return rec


def _field(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The runners, from the Win or Place market."""
    for m in rec.get("markets") or []:
        if m.get("name") == "Win or Place":
            return m.get("selections") or []
    raise SportsbetError(f"event {rec.get('id')}: no Win or Place market")


def prices(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per runner: number, name, fixed win and place ("L" price —
    "MID" is Sportsbet's mid-tote, a different bet)."""
    out = []
    for s in _field(rec):
        fixed = next((p for p in s.get("prices") or []
                      if p.get("priceCode") == "L"), {})
        out.append({"horse_no": s.get("runnerNumber"),
                    "name": (s.get("name") or "").upper(),
                    "win": fixed.get("winPrice"),
                    "place": fixed.get("placePrice"),
                    "scratched": bool(s.get("isOut"))})
    return out


def provider(rec: dict[str, Any]) -> str | None:
    return rec.get("tippedSelectionProvider")


def tips(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The four tips in order, as {"horse_no", "name"}. Empty unless they
    are Racing & Sports' — another provider would be another source."""
    if provider(rec) != _R_AND_S:
        return []
    by_id = {s.get("id"): s for s in _field(rec)}
    out = []
    for k in range(1, 5):
        s = by_id.get(rec.get(f"tippedSelection{k}"))
        if s:
            out.append({"horse_no": s.get("runnerNumber"),
                        "name": (s.get("name") or "").upper()})
    return out


def comment(rec: dict[str, Any]) -> str:
    if provider(rec) != _R_AND_S:
        return ""
    return rec.get("raceCommentary") or ""


def runner_comments(rec: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in _field(rec):
        stats = s.get("statistics") or {}
        if stats.get("overviewProvider") == _R_AND_S and stats.get("overview"):
            out.append({"horse_no": s.get("runnerNumber"),
                        "name": (s.get("name") or "").upper(),
                        "comment": stats["overview"]})
    return out


def page_url(rec: dict[str, Any]) -> str:
    venue = (rec.get("competitionName") or "").lower().replace(" ", "-")
    return (f"https://www.sportsbet.com.au/horse-racing/international/"
            f"{venue}/race-{rec.get('raceNumber')}-{rec.get('id')}")

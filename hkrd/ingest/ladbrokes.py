"""Ladbrokes (Australia): fixed odds, and its race-by-race tips, for HK races.

Ladbrokes' public affiliates feed (api.ladbrokes.com.au/affiliates/v1) is what
its own site reads — no account, no key — and it answers the Fly machine in
Singapore as well as a home PC (measured 2026-09-23). For each HK race:

  race.comment   a written preview naming its top four with the reasons:
                 "SUPERB KING (4) produced a terrific first-up effort …"
  race.tips      those four as runner ids, IN ORDER — the ranking is data,
                 not something to infer from the prose

Both are Racing & Sports' work, the same words Sportsbet carries under that
name, and are stored as that source (`ingest.racing_sports`).
  runners[]      fixed win and place prices now

Returns plain dicts, as ingest/ does. The numbers are Ladbrokes' own and its
names are its own spelling; `jobs/scrape_fixed_odds` checks both against the
HKJC card before anything is stored.
"""
from __future__ import annotations

import json
from typing import Any

from hkrd.ingest._client import FetchError, fetch_html
from hkrd.ingest.racing_sports import reasons

__all__ = ["API", "VENUES", "race_ids", "fetch_race", "prices", "tips",
           "comment", "page_url", "LadbrokesError"]

API = "https://api.ladbrokes.com.au/affiliates/v1/racing"
VENUES = {"HV": "Happy Valley", "ST": "Sha Tin"}


class LadbrokesError(RuntimeError):
    """Ladbrokes answered, but not with what a race record looks like."""


def _get(url: str, *, session=None) -> dict[str, Any]:
    body = json.loads(fetch_html(url, session=session))
    if not isinstance(body, dict) or body.get("data") is None:
        raise LadbrokesError(f"{url}: no data in the reply — {str(body)[:160]}")
    return body["data"]


def race_ids(date: str, venue: str, *, session=None) -> dict[int, str]:
    """race number -> Ladbrokes event id, for one HK meeting. Empty when
    Ladbrokes does not list that meeting."""
    data = _get(f"{API}/meetings?date_from={date}&date_to={date}"
                f"&category=T&country=HK", session=session)
    want = VENUES.get(venue, venue).lower()
    for m in data.get("meetings") or []:
        if (m.get("name") or "").lower() == want:
            return {int(r["race_number"]): r["id"]
                    for r in m.get("races") or [] if r.get("race_number")}
    return {}


def fetch_race(event_id: str, *, session=None) -> dict[str, Any]:
    rec = _get(f"{API}/events/{event_id}", session=session)
    if not isinstance(rec.get("runners"), list):
        raise LadbrokesError(f"event {event_id}: no runners in the record")
    rec["_event_id"] = event_id
    return rec


def prices(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per runner: number, Ladbrokes' name, fixed win and place."""
    out = []
    for r in rec.get("runners") or []:
        odds = r.get("odds") or {}
        out.append({"horse_no": r.get("runner_number"),
                    "name": (r.get("name") or "").upper(),
                    "win": odds.get("fixed_win"),
                    "place": odds.get("fixed_place"),
                    "scratched": bool(r.get("is_scratched"))})
    return out


def comment(rec: dict[str, Any]) -> str:
    return (rec.get("race") or {}).get("comment") or ""


def page_url(rec: dict[str, Any]) -> str:
    race = rec.get("race") or {}
    return (f"https://www.ladbrokes.com.au/racing/"
            f"{(race.get('meeting_name') or '').lower().replace(' ', '-')}/"
            f"{rec.get('_event_id') or race.get('event_id')}")


def tips(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The race's picks, first pick first, each with its reason."""
    race = rec.get("race") or {}
    by_id = {r.get("entrant_id"): r for r in rec.get("runners") or []}
    said = reasons(comment(rec))
    out = []
    for rank, entrant in enumerate(race.get("tips") or [], start=1):
        runner = by_id.get(entrant)
        if not runner:
            continue
        no = runner.get("runner_number")
        why = said.get(no, {})
        out.append({"horse_no": no, "rank": rank,
                    "name": (why.get("name") or runner.get("name") or "").upper(),
                    "reason": why.get("text"), "url": page_url(rec)})
    return out


__all__ += ["FetchError"]

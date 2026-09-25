"""HKJC's course standard times and reference sectionals.

https://racing.hkjc.com/en-us/local/page/racing-course-time -- "the time that
could be expected to be achieved by a winner in the class on good going", for
every course, distance and class, and the same broken into the sections HKJC
times a race in.

It is the reference a race's pace should be read against. The dashboard used
to read pace against the average of every race at the same DISTANCE, which
pools Sha Tin's straight 1000m with Happy Valley's (a 0.6s difference over the
first 200m, before class), Class 5 with Group races (0.45s over the first
400m at Sha Tin 1200m) and good going with yielding. On 23 Sep 2026 that read
a Happy Valley 1000m run exactly to its class standard as "Fast", and a 1200m
run 0.4s SLOWER than standard to the 800m as "Fast" too.

The page renders its tables in the browser, but the numbers ship inside the
HTML as Next.js flight data -- a JSON string inside a JSON array -- so one
plain request reads them. Returns plain dicts.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from hkrd.ingest._client import BASE_URL, fetch_html

__all__ = ["URL", "StandardsError", "parse_standards", "fetch_standards"]

URL = f"{BASE_URL}/en-us/local/page/racing-course-time"

# The section each key ends at, in race order: "start12800M" is the section
# ending at the 800m-to-go mark, whichever marker it started from.
_SECTION_KEYS = ("start2000M", "start201600M", "start161200M", "start12800M",
                 "start8400M", "start400M")
_HK = dt.timezone(dt.timedelta(hours=8))
_TRACKS = (("sha tin", "turf", "ST", "Turf"), ("happy valley", "turf", "HV", "Turf"),
           ("sha tin", "all weather", "ST", "AWT"))


class StandardsError(ValueError):
    """The page no longer carries the standards where this parser looks."""


def _seconds(text: str | None) -> float | None:
    """"1.07.95" / "0.55.95" / "1:07.95" / "23.45" -> seconds. "-" -> None."""
    t = (text or "").strip()
    if not t or t == "-":
        return None
    parts = re.split(r"[.:]", t)
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 100
        return float(t)
    except ValueError:
        raise StandardsError(f"unreadable time {text!r}") from None


def _class_key(label: str) -> str:
    low = label.lower()
    if "group" in low:
        return "G"
    if "griffin" in low:
        return "Griffin Race"
    m = re.search(r"class\s*(\d)", low)
    if not m:
        raise StandardsError(f"unknown class label {label!r}")
    return m.group(1)


def _track(title: str) -> tuple[str, str]:
    low = " ".join(title.lower().split())
    for venue, surface, v, s in _TRACKS:
        if venue in low and surface in low:
            return v, s
    raise StandardsError(f"unknown track {title!r}")


def _flight_objects(html: str):
    for m in re.finditer(r"<script>self\.__next_f\.push\((\[.*?\])\)</script>", html, re.S):
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str) and "sectionalData" in arr[1]:
            payload = arr[1]
            yield json.loads(payload[payload.index(":") + 1:])


def _find(obj: Any, key: str):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for v in obj.values():
            yield from _find(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find(v, key)


def parse_standards(html: str) -> list[dict[str, Any]]:
    """`[{venue, surface, distance, class_key, standard_time, sections,
    updated}]`, one per cell HKJC publishes a number for."""
    rows: list[dict[str, Any]] = []
    for obj in _flight_objects(html):
        for data in _find(obj, "sectionalData"):
            stamp = (data.get("updateDate") or {}).get("dateValue")
            # A Hong Kong date: in UTC, HKJC's "25-8-2026" reads as the 24th.
            updated = (dt.datetime.fromtimestamp(stamp / 1000, _HK)
                       .date().isoformat() if stamp else None)
            for track in data.get("children", []):
                venue, surface = _track(track["displayTitle"]["value"])
                for dist in track.get("children", []):
                    distance = int(dist["distance"]["value"])
                    for cell in dist.get("children", []):
                        total = _seconds(cell["standardTimes"]["value"])
                        if total is None:
                            continue
                        raw = [(cell.get(k) or {}).get("value", "") for k in _SECTION_KEYS]
                        sections = [_seconds(x) for x in raw if x.strip()]
                        rows.append({
                            "venue": venue, "surface": surface, "distance": distance,
                            "class_key": _class_key(
                                cell["class"]["targetItem"]["displayLabel"]["value"]),
                            "standard_time": total,
                            "sections": (None if any(s is None for s in sections)
                                         else ";".join(f"{s:.2f}" for s in sections)),
                            "updated": updated})
    if not rows:
        raise StandardsError("no standard times found on the page — the layout "
                             "has changed")
    return rows


def fetch_standards(*, session=None) -> list[dict[str, Any]]:
    """One request. Raises `FetchError` or `StandardsError`; never an empty list."""
    return parse_standards(fetch_html(URL, {}, session=session))

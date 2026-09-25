"""Every race header of a meeting, in one request.

HKJC's "all results" page prints each race of the meeting under one line:

    Race 9 Class 3 (Restricted) - 1600M - (85-60) - TURF - "C+3" Course - TIN SHUI WAI HANDICAP

which carries what the archive most often lost -- the class as HKJC writes
it, the distance, the race's name. A repair that needs only those reads one
page per meeting instead of eleven results pages, or the thirty requests a
full re-scrape costs.

Returns plain dicts. The class phrase is returned as written; `store/coerce`
reads it.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["parse_meeting_headers", "fetch_meeting_headers"]

# Each header is matched on its own, as one of HKJC's five class phrasings
# and a distance -- nothing after it. The first version also required every
# race's trailing text to run on to the next "Race N" or to the end of the
# page, and `.` does not cross a newline: the text after a meeting's LAST race
# carries one, so the last race of 518 meetings never matched at all.
_LINE = re.compile(
    r"\bRace\s+(\d{1,2})\s+"
    r"(Class\s*\d(?:\s*\(Restricted\))?|Group\s+(?:One|Two|Three|\d)"
    r"|Griffin(?:\s+Race)?|4\s*Years?\s*Olds?|Listed(?:\s+Race)?)"
    r"\s*-\s*(\d{3,4})M\b", re.IGNORECASE)
_NAME = re.compile(r"Course\s*-\s*(.+?)(?:\s+Multi\b|\s+Pla\.|\s+Horse\b|$)")


def parse_meeting_headers(html: str) -> list[dict[str, Any]]:
    """`[{race_no, race_class, distance, race_name}]` from an all-results page."""
    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    out: dict[int, dict[str, Any]] = {}
    for m in _LINE.finditer(text):
        no = int(m.group(1))
        name = _NAME.search(text[m.end():m.end() + 250])
        out.setdefault(no, {"race_no": no, "race_class": m.group(2).strip(),
                            "distance": int(m.group(3)),
                            "race_name": name.group(1).strip() if name else None})
    return [out[k] for k in sorted(out)]


def fetch_meeting_headers(date: str, *, session=None) -> list[dict[str, Any]]:
    """One request: every race's class, distance and name for `date`."""
    html = fetch_html(urls.resultsall, {"racedate": date.replace("-", "/")},
                      session=session)
    return parse_meeting_headers(html)

"""Comments on Running — HKJC's objective per-runner account of each race.

    https://racing.hkjc.com/en-us/local/information/corunning?date=YYYYMMDD&raceno=N

This is the honest source for where a horse actually travelled: which lane, how
wide, when it improved. The previous system read lane position by OCR-ing
photographs, which was inaccurate and expensive, while this text sat unused --
because the parser that was supposed to read it had been silently broken since
it was written.

The bug is worth recording, because it dictates how this module is built. The
old parse_corunning indexed columns by position assuming three of them:

    horse_no = tds[0];  horse_cell = tds[1];  comment = tds[2]

The table has four -- place, horse number, horse name (code), comment. So every
field read one column to the left and tds[3], the actual comment, was never read
at all. The output stayed structurally valid: 10,690 records across 87 meetings,
every one carrying a horse number where the name belonged and a horse name where
the comment belonged, and not one carrying any comment prose. Nothing failed
loudly, so nobody looked.

Columns here are therefore mapped by HEADER TEXT, never by position. A layout
change then produces a missing-column error naming what it could not find,
instead of silently shifting every field by one.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import NotFound, fetch_html, urls

__all__ = ["CoRunningError", "parse_corunning", "url_for", "extract_lane_notes",
           "fetch", "fetch_meeting", "comment_rows", "lane_tag_rows"]

CORUNNING_URL = urls.corunning

# Header text -> the field it feeds. Matched case-insensitively on a substring,
# because HKJC varies the exact wording between seasons.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "place": ("pla.", "place", "position", "finish"),
    "horse_no": ("horse no", "horse no.", "no.", "number"),
    "horse": ("horse",),
    "comment": ("comment", "running", "remarks"),
}

_HORSE_CODE = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[A-Z]\d{3})\)\s*$")


class CoRunningError(ValueError):
    """Raised with the URL and the field that failed. Never a silent None."""


def url_for(date: str, race_no: int) -> str:
    """date is YYYY-MM-DD or YYYYMMDD."""
    return f"{CORUNNING_URL}?date={date.replace('-', '')}&raceno={race_no}"


def _header_map(cells: list[str]) -> dict[str, int]:
    """Map field name -> column index, from the header row's text."""
    found: dict[str, int] = {}
    for idx, raw in enumerate(cells):
        text = raw.strip().lower()
        if not text:
            continue
        for field, aliases in _COLUMN_ALIASES.items():
            if field in found:
                continue
            if any(a in text for a in aliases):
                found[field] = idx
                break
    return found


def parse_corunning(html: str, *, source: str = "") -> list[dict[str, Any]]:
    """Rows of {horse_no, horse_name, horse_code, place, comment}.

    Returns plain dicts. Knows nothing about the database.
    """
    if not html or not html.strip():
        raise CoRunningError(f"empty document{f' from {source}' if source else ''}")

    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        cols = _header_map(header)
        if "comment" not in cols or "horse" not in cols:
            continue

        out: list[dict[str, Any]] = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if not cells or len(cells) <= cols["comment"]:
                continue

            horse_cell = cells[cols["horse"]]
            m = _HORSE_CODE.match(horse_cell)
            name = (m.group("name") if m else horse_cell).strip().upper()
            code = m.group("code") if m else None
            if not name:
                continue

            out.append({
                "place": _int(cells[cols["place"]]) if "place" in cols else None,
                "horse_no": _int(cells[cols["horse_no"]]) if "horse_no" in cols else None,
                "horse_name": name,
                "horse_code": code,
                "comment": cells[cols["comment"]].strip(),
            })
        if out:
            return out

    raise CoRunningError(
        f"no comments-on-running table found{f' at {source}' if source else ''} — "
        f"expected a table with 'Horse' and 'Comment' headers"
    )


def _int(text: str) -> int | None:
    t = (text or "").strip()
    return int(t) if t.isdigit() else None


# ── lane and trip language ───────────────────────────────────────────────────
#
# Corunning is where lane position is stated plainly, which is what makes the
# photo-OCR approach unnecessary. These are read straight out of the prose
# rather than inferred from an image.

_LANE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rail", r"\b(on|against) the (rail|fence)\b|\brailed\b"),
    ("one_off_rail", r"\bone (off|out from) the (rail|fence)\b"),
    ("two_off_rail", r"\btwo (off|out from) the (rail|fence)\b"),
    ("three_wide", r"\bthree (wide|deep)\b"),
    ("four_wide", r"\bfour (wide|deep)\b"),
    ("wide", r"\bwide\b"),
    ("without_cover", r"\bwithout cover\b|\bno cover\b"),
    ("inside", r"\binside\b|\binner\b"),
    ("outside", r"\boutside\b|\bouter\b"),
)


def extract_lane_notes(comment: str) -> tuple[str, ...]:
    """Lane descriptors present in one running comment.

    Deliberately literal: it reports what the text says, not an inferred lane
    number. An inferred number would carry false precision, which is the fault
    the OCR approach had.
    """
    if not comment:
        return ()
    text = comment.lower()
    return tuple(tag for tag, pat in _LANE_PATTERNS if re.search(pat, text))


# ── fetching ─────────────────────────────────────────────────────────────────
#
# There is no HKJC API; this is HTML scraping against the public site. These
# functions return plain dicts and know nothing about the database.


def fetch(date: str, race_no: int, *, session=None) -> list[dict[str, Any]]:
    """One race's comments on running.

    date is YYYY-MM-DD or YYYYMMDD.
    """
    compact = date.replace("-", "")
    html = fetch_html(CORUNNING_URL, {"date": compact, "raceno": race_no},
                      session=session)
    return parse_corunning(html, source=url_for(date, race_no))


def fetch_meeting(date: str, *, max_races: int = 11,
                  session=None) -> dict[int, list[dict[str, Any]]]:
    """Every race on a card, keyed by race number.

    Walks 1..max_races and stops at the first race that does not exist -- a
    meeting is a contiguous block, so a 404 means the card has ended.

    Only a 404 stops the walk. Any other failure propagates, because "the card
    ended" and "the fetch broke" are different facts and a scraper that treats a
    500 as the end of the card reports a short meeting instead of an outage.
    Likewise a race that exists but cannot be parsed raises: that is a layout
    change and must be seen, not skipped.
    """
    out: dict[int, list[dict[str, Any]]] = {}
    for race_no in range(1, max_races + 1):
        try:
            html = fetch_html(CORUNNING_URL, {"date": date.replace("-", ""),
                                              "raceno": race_no}, session=session)
        except NotFound:
            break
        rows = parse_corunning(html, source=url_for(date, race_no))
        out[race_no] = rows
    return out


def comment_rows(date: str, race_no: int,
                 rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape parsed rows for store.upsert_comments.

    source='corunning' keeps these distinct from the stewards' incident report:
    they are different accounts of the same race and the form guide shows both.
    """
    return [{
        "race_date": date, "race_no": race_no, "horse_no": r["horse_no"],
        "comment_text": r["comment"], "source": "corunning",
    } for r in rows if r.get("horse_no") and r.get("comment")]


def lane_tag_rows(date: str, race_no: int,
                  rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lane descriptors as runner_tags rows.

    Confidence is 1.0 because these are not inferred: the comment states the
    horse raced three wide, so the tag records exactly that. Contrast the OCR
    approach, which produced a lane number with no way to check it.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("horse_no"):
            continue
        for tag in extract_lane_notes(r.get("comment", "")):
            out.append({"race_date": date, "race_no": race_no,
                        "horse_no": r["horse_no"], "tag": f"lane:{tag}",
                        "confidence": 1.0})
    return out

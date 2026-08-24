"""HKJC race results — finishing order, times, sectionals.

Adapted from the old scrape_hkjc_results.py, which has been schema-stable for
eight months. Two things change.

First, this returns plain dicts of scraped fact and nothing else. The old
scraper also computed pace labels, going adjustments and deviation figures and
wrote them into the same JSON, which put derived values in the ingest layer
where they could drift away from the ones derive/pace.py computes. Pace belongs
to derive; here we only report what the page said.

Second, columns are located by header text with positional fallback, and the
result is validated before it is returned. The old parser indexed cells[0]
through cells[11] with no check that they held what it assumed -- which is
exactly how parse_corunning read a four-column table as three and produced
10,690 records of nothing for 87 meetings without failing once.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["ResultsError", "parse_race_header", "parse_results_table",
           "parse_sectional_table", "fetch_race", "fetch_meeting"]


class ResultsError(ValueError):
    """A results page could not be read. Names the URL and the field."""


GOING_ABBREV = {
    "FAST": "F", "GOOD TO FIRM": "GF", "GOOD": "G", "GOOD TO YIELDING": "GY",
    "YIELDING": "Y", "YIELDING TO SOFT": "YS", "SOFT": "S", "HEAVY": "H",
    "WET SLOW": "WS", "WET FAST": "WF", "SLOW": "SL",
}

# Header text -> field. Substring match, case-insensitive.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "place": ("pla.", "place"),
    "horse_no": ("horse no", "no."),
    "horse_name": ("horse",),
    "jockey": ("jockey",),
    "trainer": ("trainer",),
    "actual_weight": ("act. wt", "actual wt", "act wt"),
    "declared_weight": ("declar", "decl. horse wt", "horse wt"),
    "draw": ("draw",),
    "lbw": ("lbw", "margin"),
    "running_position": ("running position", "position"),
    "finish_time": ("finish time", "time"),
    "win_odds": ("win odds", "odds"),
}
# The order the table has used for eight months, used only when no header row
# can be found at all.
_POSITIONAL = ("place", "horse_no", "horse_name", "jockey", "trainer",
               "actual_weight", "declared_weight", "draw", "lbw",
               "running_position", "finish_time", "win_odds")


def parse_race_header(html: str) -> dict[str, Any]:
    """Race conditions from the page header."""
    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    info: dict[str, Any] = {}

    if m := re.search(r"Class\s*(\d+)\s*-\s*(\d+)M", text):
        info["race_class"], info["distance"] = m.group(1), int(m.group(2))
    elif m := re.search(r"(Griffin\s+Race|Group\s+\d+|Listed\s+Race)\s*-\s*(\d+)M",
                        text, re.I):
        info["race_class"], info["distance"] = m.group(1).strip(), int(m.group(2))
    elif m := re.search(r"-\s*(\d{3,4})M", text):
        info["distance"] = int(m.group(1))

    if m := re.search(r"Going\s*:\s*(.+?)\s+Course\s*:", text):
        segment = re.sub(r"\s+", " ", m.group(1).strip())
        upper = segment.upper()
        for full in sorted(GOING_ABBREV, key=len, reverse=True):
            if upper == full or upper.startswith(full + " "):
                info["going"] = GOING_ABBREV[full]
                if name := segment[len(full):].strip():
                    info["race_name"] = name
                break

    if m := re.search(r"Course\s*:\s*(.+?)(?:\s+Class|\s+Race|\s*$)", text):
        raw = m.group(1).strip()
        # AWT is its own surface and must never be pooled with Sha Tin turf.
        if "ALL WEATHER" in raw.upper() or "AWT" in raw.upper():
            info["course"], info["surface"] = "AWT", "AWT"
        else:
            vm = re.search(r"[\"']?([A-C](?:\+\d)?)[\"']?", raw)
            info["course"], info["surface"] = (vm.group(1) if vm else raw), "Turf"
    return info


def _map_columns(header_cells: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx, raw in enumerate(header_cells):
        text = raw.strip().lower()
        if not text:
            continue
        for field, aliases in _COLUMNS.items():
            if field not in found and any(a in text for a in aliases):
                found[field] = idx
                break
    return found


# What each field must look like if the columns are aligned. A shift moves a
# jockey's name into horse_no or a time into running_position, and every one of
# those violates a shape below.
_SHAPES: dict[str, "re.Pattern[str]"] = {
    "horse_no": re.compile(r"^\d{1,2}$"),
    "draw": re.compile(r"^(\d{1,2})?$"),
    "finish_time": re.compile(r"^(\d+:)?\d{1,2}\.\d{1,2}$|^$"),
    "win_odds": re.compile(r"^[\d.]+$|^-+$|^$"),
    "actual_weight": re.compile(r"^\d{2,3}$|^$"),
}


def _validate(rows: list[dict[str, Any]], source: str) -> None:
    """Reject a plausible-looking but misaligned parse.

    This is the check the old parser lacked, and the reason it is written by
    shape rather than by one symptom: parse_corunning shifted so that a horse
    NUMBER landed in the name, but a shift the other way puts a jockey's NAME
    there instead. Testing for "the name is numeric" catches only the first.
    Testing that every field still looks like itself catches both.

    A column shift produces rows that are structurally fine and semantically
    nonsense, which is how 10,690 records of it survived 87 meetings.
    """
    if not rows:
        return

    broken: list[str] = []
    for field, pattern in _SHAPES.items():
        values = [str(r.get(field, "")).strip() for r in rows if field in r]
        if not values:
            continue
        bad = sum(1 for v in values if not pattern.match(v))
        if bad > len(values) / 2:
            example = next(v for v in values if not pattern.match(v))
            broken.append(f"{field} holds {example!r}")

    # One odd column is a quirk; two or more is a layout change.
    if len(broken) >= 2:
        raise ResultsError(
            f"{source}: columns look misaligned — " + "; ".join(broken))


def parse_results_table(html: str, *, source: str = "") -> list[dict[str, Any]]:
    """One race's finishing order."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.f_tac.table_bd.draggable") or soup.find("table")
    if table is None:
        raise ResultsError(f"{source or 'results'}: no results table found")

    all_rows = table.find_all("tr")
    header = [c.get_text(" ", strip=True) for c in all_rows[0].find_all(["th", "td"])] \
        if all_rows else []
    cols = _map_columns(header)
    body = table.select("tbody tr") or all_rows[1:]

    out: list[dict[str, Any]] = []
    for tr in body:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 8:
            continue
        if cols:
            row = {f: cells[i] for f, i in cols.items() if i < len(cells)}
        else:
            row = {f: cells[i] for i, f in enumerate(_POSITIONAL) if i < len(cells)}
        # The name cell carries "HORSE NAME (CODE)".
        if name := row.get("horse_name"):
            row["horse_name"] = name.split("(")[0].strip().upper()
        if row.get("horse_no") or row.get("horse_name"):
            out.append(row)

    _validate(out, source or "results")
    return out


def parse_sectional_table(html: str) -> dict[str, dict[str, Any]]:
    """Per-runner sectional splits, keyed by horse number."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict[str, Any]] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        head = " ".join(c.get_text(" ", strip=True)
                        for c in rows[0].find_all(["th", "td"])).lower()
        if "sectional" not in head and "section" not in head:
            continue
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) < 3 or not cells[0].strip().isdigit():
                continue
            splits = [c for c in cells[2:] if re.fullmatch(r"\d+\.\d+", c or "")]
            if splits:
                out[cells[0].strip()] = {"section_times": "; ".join(splits)}
        if out:
            break
    return out


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_race(date: str, venue: str, race_no: int, *, session=None) -> dict[str, Any]:
    """One race: header, runners, sectionals. Raw fact only, no derived values."""
    params = {"racedate": date, "Racecourse": venue, "RaceNo": str(race_no)}
    html = fetch_html(urls.localresults, params, session=session)
    source = f"{urls.localresults} {date} {venue} R{race_no}"

    header = parse_race_header(html)
    runners = parse_results_table(html, source=source)
    sections = parse_sectional_table(html)
    for r in runners:
        extra = sections.get(str(r.get("horse_no", "")).strip())
        if extra:
            r.update(extra)
    return {"race_date": date, "race_no": race_no, "venue": venue,
            **header, "runners": runners}


def fetch_meeting(date: str, venue: str, *, max_races: int = 11,
                  session=None) -> list[dict[str, Any]]:
    """Every race on a card. Stops when a race does not exist."""
    from hkrd.ingest._client import NotFound

    out: list[dict[str, Any]] = []
    for race_no in range(1, max_races + 1):
        try:
            out.append(fetch_race(date, venue, race_no, session=session))
        except NotFound:
            break
    return out

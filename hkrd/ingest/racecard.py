"""HKJC race cards — the field as declared, before the race is run.

Adapted from the old `scrape_hkjc_racecard.py`, which was 968 lines and did
three jobs. This does one: read the page and return what it said. The old
module also fetched with Playwright, classified running styles and wrote
derived figures into the same JSON, which put derive/ values in the ingest
layer where they could drift from the ones derive/ computes.

Columns are located by HEADER TEXT with no positional fallback for the fields
that matter. The old module had a 27-column hardcoded map and fell back to it
whenever header detection found fewer than ten fields -- which is the shape of
the bug that made `parse_corunning` read a four-column table as three and
produce 10,690 records of nothing across 87 meetings. A layout change here
raises, naming the meeting, the race and the column it could not find.

VENUE AND COURSE ARE NOT THE SAME FIELD, and the legacy scraper conflated
them: it put the rail position (A, B, C+3) in a key called `race_course` and
the surface in `surface`. In this package `venue` is ST or HV and `course` is
the rail. Migrating the archive with those two swapped had already put the
rail in `venue` for all 1,712 races, so the naming here is deliberate.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["RacecardError", "parse_race_header", "parse_racecard",
           "fetch_race", "fetch_meeting"]


class RacecardError(ValueError):
    """A race card could not be read. Names the race and the field."""


# Header text -> field. Substring match on a lowercased header, longest alias
# first within each field so "horse no." is not matched as "horse".
_COLUMNS: dict[str, tuple[str, ...]] = {
    "horse_no": ("horse no", "no."),
    "last_6": ("last 6 runs", "last 6"),
    "horse_name": ("horse",),
    "brand_no": ("brand no", "brand"),
    "actual_weight": ("wt.", "weight"),
    "jockey": ("jockey",),
    "over_weight": ("over wt", "overwt"),
    "draw": ("draw",),
    "trainer": ("trainer",),
    "rating": ("rtg.", "rating"),
    "rating_change": ("rtg.+/-", "rtg +/-"),
    "declared_weight": ("horse wt", "declar"),
    "weight_change": ("wt.+/-", "wt +/-"),
    "best_time": ("best time",),
    "age": ("age",),
    "sex": ("sex",),
    "season_stakes": ("season stakes",),
    "priority": ("priority",),
    "days_since_last": ("days since", "days"),
    "gear": ("gear",),
    "owner": ("owner",),
    "sire": ("sire",),
    "dam": ("dam",),
}

# Without these the row is not a runner. There is no positional fallback for
# them: guessing where the horse number is, is how a card becomes nonsense
# that still validates.
_REQUIRED = ("horse_no", "horse_name")

_INT_FIELDS = ("horse_no", "draw", "actual_weight", "declared_weight",
               "rating", "age", "days_since_last")

# What each field must look like when the columns are aligned. A shift puts a
# jockey's name in horse_no or a rating in draw, and both violate a shape here.
_SHAPES: dict[str, "re.Pattern[str]"] = {
    "horse_no": re.compile(r"^\d{1,2}$"),
    "draw": re.compile(r"^(\d{1,2})?$"),
    "actual_weight": re.compile(r"^\d{2,3}$|^$"),
    "rating": re.compile(r"^-?\d{1,3}$|^$|^-+$"),
}

_SCRATCH = re.compile(r"scratch|withdraw", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _to_int(text: str) -> int | None:
    match = re.match(r"^-?\d+", _clean(text).replace(",", ""))
    return int(match.group(0)) if match else None


# ── the race header ──────────────────────────────────────────────────────────

_GOING = ("Good to Firm", "Good to Yielding", "Yielding to Soft",
          "Wet Fast", "Wet Slow", "Firm", "Good", "Yielding", "Soft", "Heavy")
_GOING_CODE = {"Firm": "F", "Good to Firm": "GF", "Good": "G",
               "Good to Yielding": "GY", "Yielding": "Y",
               "Yielding to Soft": "YS", "Soft": "S", "Heavy": "H",
               "Wet Slow": "WS", "Wet Fast": "WF"}


def parse_race_header(html: str, race_no: int, *,
                      source: str = "") -> dict[str, Any]:
    """Race-level facts from the block above the field.

    Going is matched LONGEST FIRST: "Good to Firm" contains "Good", and a
    shortest-first scan records every GF meeting as G. That is not a rounding
    error -- ET pars are computed per going band, so the whole race lands in
    the wrong reference.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    out: dict[str, Any] = {"race_no": race_no}

    name = re.search(
        r"Race\s+\d+\s*[-–—]\s*(.+?)"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|,\s*\d{4})",
        text, re.IGNORECASE)
    out["race_name"] = _clean(name.group(1).rstrip(" ,")) if name else None

    cls = re.search(r"\bClass\s+(\d)\b", text)
    if cls:
        out["race_class"] = cls.group(1)
    else:
        grp = re.search(r"\bGroup\s+(One|Two|Three|1|2|3)\b", text, re.IGNORECASE)
        out["race_class"] = f"Group {grp.group(1)}" if grp else None

    dist = re.search(r"(\d{3,4})\s*M\b", text, re.IGNORECASE)
    out["distance"] = int(dist.group(1)) if dist else None

    # venue is ST or HV; course is the rail. Not the same field.
    out["venue"] = ("ST" if re.search(r"\bSha\s*Tin\b", text, re.IGNORECASE)
                    else "HV" if re.search(r"\bHappy\s*Valley\b", text,
                                           re.IGNORECASE) else None)
    if re.search(r"All\s*Weather", text, re.IGNORECASE):
        out["surface"] = "AWT"
        out["course"] = "AWT"
    else:
        out["surface"] = "Turf"
        rail = re.search(r'["“]([A-C](?:\+\d)?)["”]', text) \
            or re.search(r"\bCourse\s*[:\-]?\s*([A-C](?:\+\d)?)\b", text)
        out["course"] = rail.group(1) if rail else None

    out["going"] = None
    for label in _GOING:
        if re.search(re.escape(label), text, re.IGNORECASE):
            out["going"] = _GOING_CODE[label]
            break

    off = re.search(r"\b([012]?\d:[0-5]\d)\b", text)
    out["off_time"] = off.group(1) if off else None

    prize = re.search(r"Prize\s*Money\s*[:\-]?\s*\$?([\d,]+)", text,
                      re.IGNORECASE)
    out["prize"] = _to_int(prize.group(1)) if prize else None

    if out["distance"] is None or out["venue"] is None:
        raise RacecardError(
            f"{source or f'R{race_no}'}: race header unreadable — "
            f"distance={out['distance']!r} venue={out['venue']!r}")
    return out


# ── the field ────────────────────────────────────────────────────────────────

def _map_columns(cells: list[str], source: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx, raw in enumerate(cells):
        text = _clean(raw).lower()
        if not text:
            continue
        for field, aliases in _COLUMNS.items():
            if field in found:
                continue
            if any(alias in text for alias in aliases):
                found[field] = idx
                break
    missing = [f for f in _REQUIRED if f not in found]
    if missing:
        raise RacecardError(
            f"{source}: race card header has no {', '.join(missing)} column — "
            f"saw {[_clean(c) for c in cells if _clean(c)][:12]}")
    return found


def _header_row(table) -> list | None:
    """The first row naming at least three known columns.

    HKJC sometimes puts a colspan title row above the real header, so the
    first <tr> is not reliably it.
    """
    for tr in table.find_all("tr")[:4]:
        cells = tr.find_all(["th", "td"])
        text = " ".join(_clean(c.get_text()) for c in cells).lower()
        hits = sum(1 for aliases in _COLUMNS.values()
                   if any(a in text for a in aliases))
        if hits >= 3:
            return tr
    return None


def _find_table(soup: BeautifulSoup):
    for finder in (lambda: soup.find("table", class_="starter"),
                   lambda: soup.find("table", id="racecardlist")):
        table = finder()
        if table is not None:
            return table
    # Content match, in the same spirit as the header mapping: a table whose
    # own header names the columns a race card has.
    for table in soup.find_all("table"):
        if _header_row(table) is not None:
            return table
    return None


def _validate(rows: list[dict[str, Any]], source: str) -> None:
    """Reject a plausible-looking but misaligned parse.

    Written by SHAPE rather than by one symptom, for the reason the results
    parser gives: a shift one way puts a number where a name belongs, and a
    shift the other way puts a name where a number belongs. Testing that every
    field still looks like itself catches both.
    """
    if not rows:
        raise RacecardError(f"{source}: race card table found but no runner "
                            "parsed — the layout has changed")
    mostly_bad: list[str] = []
    never_right: list[str] = []
    for field, pattern in _SHAPES.items():
        values = [str(r.get(f"{field}_raw", r.get(field) or "")).strip()
                  for r in rows if field in r or f"{field}_raw" in r]
        values = [v for v in values if v != "None"]
        if not values:
            continue
        bad = [v for v in values if not pattern.match(v)]
        if not bad:
            continue
        if len(bad) > len(values) / 2:
            mostly_bad.append(f"{field} holds {bad[0]!r}")
        # A column wrong in EVERY row is not a quirk, it is the wrong column.
        # Requiring two such fields before raising misses the single
        # catastrophic case: shift the name column into the weight column and
        # only one shape breaks, while every row now carries a horse's name
        # where its weight belongs.
        if len(bad) == len(values) and len(values) >= 2:
            never_right.append(f"{field} holds {bad[0]!r} in every row")

    if never_right or len(mostly_bad) >= 2:
        detail = "; ".join(never_right or mostly_bad)
        raise RacecardError(f"{source}: columns look misaligned — {detail}")


def parse_racecard(html: str, race_no: int, *,
                   source: str = "") -> list[dict[str, Any]]:
    """The declared field for one race.

    A scratched runner is RETURNED and marked, not dropped. The card is the
    record of what was declared, and a horse that came out of a race is a fact
    about that race — the Race Day page shows it struck through rather than
    silently renumbering the field.
    """
    label = source or f"R{race_no}"
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table(soup)
    if table is None:
        raise RacecardError(f"{label}: no race card table found")

    header = _header_row(table)
    if header is None:
        raise RacecardError(f"{label}: race card table has no header row")
    columns = _map_columns(
        [c.get_text() for c in header.find_all(["th", "td"])], label)

    out: list[dict[str, Any]] = []
    started = False
    for tr in table.find_all("tr"):
        if tr is header:
            started = True
            continue
        if not started:
            continue
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        raw = [_clean(c.get_text()) for c in cells]
        row_text = " ".join(raw)

        runner: dict[str, Any] = {"race_no": race_no}
        for field, idx in columns.items():
            if idx >= len(raw):
                continue
            value = raw[idx]
            runner[f"{field}_raw"] = value
            runner[field] = _to_int(value) if field in _INT_FIELDS else (value or None)

        if not runner.get("horse_no") or not runner.get("horse_name"):
            continue
        # HKJC writes the brand number into the name cell as "NAME (V123)".
        brand = re.search(r"\(([A-Z]\d{3})\)\s*$", str(runner["horse_name"]))
        if brand:
            runner["brand_no"] = runner.get("brand_no") or brand.group(1)
            runner["horse_name"] = _clean(
                str(runner["horse_name"])[:brand.start()])
        runner["horse_name"] = str(runner["horse_name"]).upper()
        runner["scratched"] = bool(
            _SCRATCH.search(row_text)
            or "scratch" in " ".join(tr.get("class") or []).lower())
        out.append(runner)

    _validate(out, label)
    return [{k: v for k, v in r.items() if not k.endswith("_raw")} for r in out]


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_race(date: str, venue: str, race_no: int, *,
               session=None) -> dict[str, Any]:
    """One race's card: the header and the declared field."""
    query = date.replace("-", "/")
    html = fetch_html(urls.racecard,
                      {"racedate": query, "Racecourse": venue,
                       "RaceNo": str(race_no)}, session=session)
    label = f"{date} {venue} R{race_no}"
    header = parse_race_header(html, race_no, source=label)
    header["race_date"] = date
    return {"race": header,
            "runners": parse_racecard(html, race_no, source=label)}


def fetch_meeting(date: str, venue: str, *, max_races: int = 11,
                  session=None) -> dict[str, Any]:
    """Every race on the card, with the ones that failed named.

    A meeting that is nine races long and returns eight is a fact the caller
    needs; returning eight silently is the failure this package removes.
    """
    from hkrd.ingest._client import FetchError, NotFound

    races: list[dict[str, Any]] = []
    errors: list[str] = []
    for race_no in range(1, max_races + 1):
        try:
            races.append(fetch_race(date, venue, race_no, session=session))
        except NotFound:
            break
        except FetchError as exc:
            # Transport, not content. If the host will not answer for race 1
            # it will not answer for race 11 either, and retrying eleven times
            # turns one unreachable meeting into a minute of backoff.
            errors.append(f"R{race_no}: {exc}")
            break
        except RacecardError as exc:
            errors.append(f"R{race_no}: {exc}")
    return {"race_date": date, "venue": venue, "races": races,
            "errors": errors}

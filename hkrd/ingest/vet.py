"""HKJC veterinary records — what the vets said about a horse, and when.

Adapted from the old `scrape_hkjc_vet.py` (678 lines). What is kept is the
parsing and the injury vocabulary, which is genuinely good: 32 patterns across
five categories, built against the real prose.

What is dropped is everything the old module did AFTER parsing. It computed a
"concern flag" and a "concern score" from the category, the days elapsed and a
clearance date, then filtered rows out of its own output according to those
scores -- so the scraper decided what the interface was allowed to see, using
thresholds (400 days for a physical note, 90 for a performance one) that lived
nowhere else. A record that existed on the page and did not survive that
filter simply was not there, and nothing said so.

Here, ingest reports. Every record on the page comes back, with its category
and its age in days, and the decision about what is worth showing belongs to
the layer that knows what it is being asked -- which for a vet record is not
the same question on the Race Day card as on a horse's own history.

Columns are located by header text, and the vet table is the one case in this
package where a row can be a CONTINUATION: one horse's cell is filled once and
its later records leave it blank. That state carries down the table, and a
continuation is only accepted while a horse is open -- the same rule as the
dividends parser, for the same reason.
"""
from __future__ import annotations

import re
from datetime import date as Date
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["VetError", "classify", "parse_vet_table", "fetch_race",
           "fetch_meeting", "CATEGORIES"]


class VetError(ValueError):
    """A veterinary record page could not be read."""


# Ordered: the first category whose pattern matches wins, so the more specific
# families are declared before the general ones. Carried across from the old
# module, which built them against the real prose and got them right.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "RESPIRATORY": (
        r"blood\s+in\s+trachea", r"substantial\s+blood",
        r"post[\s\-]?race\s+scop", r"bleed(?:er|ing)", r"roarer",
        r"epiglottic", r"throat\s+surgery", r"laryngeal",
        r"exercise[\s\-]induced", r"respiratory",
    ),
    "CARDIAC": (
        r"heart\s+(?:irregularity|condition|murmur)",
        r"atrial\s+fibrillation", r"cardiac",
    ),
    "PHYSICAL": (
        r"lame", r"withdrawn\s+from\s+racing",
        r"muscle\s+(?:injury|tear|strain)", r"shin\s+soreness",
        r"joint\s+(?:injury|effusion|inflammation)", r"knee",
        r"swelling", r"wound", r"abscess",
    ),
    "PERFORMANCE": (
        r"unacceptable\s+performance", r"disappointing\s+performance",
        r"racing\s+manners", r"rider\s+concerned", r"erratic",
    ),
    "PROCEDURAL": (
        r"castration", r"inadvertent\s+treatment", r"inappetence",
        r"fever", r"vaccin", r"dental", r"infection",
    ),
}
_COMPILED = {name: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
             for name, patterns in CATEGORIES.items()}

_COLUMNS: dict[str, tuple[str, ...]] = {
    "horse_no": ("horse no", "no."),
    "horse_name": ("horse",),
    "record_date": ("date",),
    "detail": ("detail", "record", "remark"),
    "passed_date": ("passed", "clear", "fit to race"),
}
_REQUIRED = ("horse_name", "detail")

# Below the runners, the page repeats itself for stand-by starters. Their vet
# records are real but they are not in the race, and folding them into the
# field silently adds horses that will not run.
_STANDBY = re.compile(r"stand[\s\-]?by.*start", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def classify(detail: str) -> str:
    """Which family a vet note belongs to. UNKNOWN when none matches.

    UNKNOWN rather than a guess: the categories drive how a note is read, and
    filing an unrecognised one under PROCEDURAL because that is the mildest
    would understate it in exactly the cases nobody has seen before.
    """
    text = detail or ""
    for name, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            return name
    return "UNKNOWN"


def _hkjc_date(text: str) -> str | None:
    """HKJC writes dd/mm/yyyy. Parsed strictly, because 03/04/2026 read as
    March 4th rather than April 3rd moves a vet note a month."""
    raw = _clean(text)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _days_before(record: str | None, race_date: str | None) -> int | None:
    if not record or not race_date:
        return None
    try:
        return (Date.fromisoformat(race_date) - Date.fromisoformat(record)).days
    except ValueError:
        return None


def _map_columns(cells: list[str], source: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx, raw in enumerate(cells):
        text = _clean(raw).lower()
        if not text:
            continue
        for field, aliases in _COLUMNS.items():
            if field not in found and any(a in text for a in aliases):
                found[field] = idx
                break
    missing = [f for f in _REQUIRED if f not in found]
    if missing:
        raise VetError(
            f"{source}: vet table header has no {', '.join(missing)} column — "
            f"saw {[_clean(c) for c in cells if _clean(c)][:10]}")
    return found


def _header_row(table):
    for tr in table.find_all("tr")[:3]:
        cells = tr.find_all(["th", "td"])
        text = " ".join(_clean(c.get_text()) for c in cells).lower()
        hits = sum(1 for aliases in _COLUMNS.values()
                   if any(a in text for a in aliases))
        if hits >= 3:
            return tr
    return None


def _find_table(soup: BeautifulSoup):
    table = soup.find("table", id="OVR1")
    if table is not None:
        return table
    for candidate in soup.find_all("table"):
        if _header_row(candidate) is not None:
            return candidate
    return None


def parse_vet_table(html: str, *, race_date: str | None = None,
                    source: str = "") -> list[dict[str, Any]]:
    """Every veterinary record on the page, with nothing filtered out.

    The old module scored each record and dropped the ones below a threshold,
    so the scraper decided what the interface could see. A record that existed
    on the page and did not survive that filter was simply not there. Here the
    category and the age come back and the caller decides.
    """
    label = source or "vet"
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table(soup)
    if table is None:
        raise VetError(f"{label}: no veterinary record table found")

    header = _header_row(table)
    if header is None:
        raise VetError(f"{label}: vet table has no header row")
    columns = _map_columns(
        [c.get_text() for c in header.find_all(["th", "td"])], label)

    out: list[dict[str, Any]] = []
    horse_no: int | None = None
    horse_name: str | None = None
    started = False
    for tr in table.find_all("tr"):
        if tr is header:
            started = True
            continue
        if not started:
            continue
        cells = tr.find_all(["td", "th"])
        raw = [_clean(c.get_text()) for c in cells]
        # Checked BEFORE the cell-count guard: HKJC writes the section marker
        # as a single colspan cell, so a `len(cells) < 2` skip steps straight
        # over it and folds the reserves into the field.
        if _STANDBY.search(" ".join(raw)):
            break                       # the stand-by section; not in the race
        if len(cells) < 2:
            continue

        def cell(field: str) -> str:
            idx = columns.get(field)
            return raw[idx] if idx is not None and idx < len(raw) else ""

        name = cell("horse_name")
        if name:
            horse_name = name.upper()
            number = cell("horse_no")
            horse_no = int(number) if number.isdigit() else None
        elif horse_name is None:
            # A continuation before any horse has been named is not a
            # continuation; it is furniture.
            continue

        detail = cell("detail")
        if not detail:
            continue
        record_date = _hkjc_date(cell("record_date"))
        out.append({
            "horse_no": horse_no,
            "horse_name": horse_name,
            "record_date": record_date,
            "detail": detail,
            "passed_date": _hkjc_date(cell("passed_date")),
            "category": classify(detail),
            # How long before the race it was recorded. Returned rather than
            # used to filter: what counts as recent is the reader's question,
            # not the scraper's.
            "days_before_race": _days_before(record_date, race_date),
        })

    if not out:
        raise VetError(f"{label}: vet table found but no record parsed — "
                       "the layout has changed")
    return out


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_race(date: str, venue: str, race_no: int, *,
               session=None) -> list[dict[str, Any]]:
    """Veterinary records for one race's field."""
    html = fetch_html(urls.vet,
                      {"racedate": date.replace("-", "/"), "Racecourse": venue,
                       "RaceNo": str(race_no)}, session=session)
    records = parse_vet_table(html, race_date=date,
                              source=f"{date} {venue} R{race_no}")
    for r in records:
        r["race_date"] = date
        r["race_no"] = race_no
    return records


def fetch_meeting(date: str, venue: str, *, max_races: int = 11,
                  session=None) -> dict[int, list[dict[str, Any]]]:
    """Every race on the card. A race with no vet records is absent from the
    result rather than present and empty — the two are different, and only one
    of them means the page was read."""
    from hkrd.ingest._client import FetchError, NotFound

    out: dict[int, list[dict[str, Any]]] = {}
    for race_no in range(1, max_races + 1):
        try:
            out[race_no] = fetch_race(date, venue, race_no, session=session)
        except NotFound:
            break
        except FetchError:
            # Transport, not content: one unreachable host, not eleven.
            break
        except VetError:
            continue
    return out

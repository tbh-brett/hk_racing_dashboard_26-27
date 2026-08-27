"""HKJC barrier trials — the batches, as run.

Adapted from the old `scrape_hkjc_trials.py`. One thing changes and it is the
same thing that changes everywhere in this layer: `_parse_data_table` indexed
`cells[0]` through `cells[9]` with no check that they held what it assumed.
That is the corunning bug exactly -- a parser confident about positions it
never verified -- and here it is worse than usual, because a trial table has
no numeric column in its first three cells, so a shift produces a jockey where
the horse belongs and nothing about the output looks wrong.

Columns are located by header text. A layout change raises, naming the date,
the batch and the column it could not find.

TWO THINGS THE ARCHIVE DOES NOT HAVE that this returns:

`distance` is published, in the batch header ("Batch 1 - SHA TIN ALL WEATHER
TRACK - 1200m"). The legacy import dropped it, so no trial in the 7,750-row
archive carries one -- and `query/trials` said HKJC published none, which was
wrong about the source rather than about the data. New scrapes carry it.

`place` is NOT published. The page's own RESULT column is empty on every row
in the archive, which settled open question C2: it was never populated at
source rather than merely unwired in the interface. The finishing position is
therefore derived from the LAST running position, and that derivation lives
here rather than in three callers.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["TrialsError", "parse_trial_day", "parse_batch", "fetch_day"]


class TrialsError(ValueError):
    """A trials page could not be read. Names the batch and the field."""


_COLUMNS: dict[str, tuple[str, ...]] = {
    "horse_name": ("horse",),
    "jockey": ("jockey",),
    "trainer": ("trainer",),
    "draw": ("draw", "barrier"),
    "gear": ("gear",),
    "lbw": ("lbw", "margin"),
    "running_positions": ("running position", "position"),
    "finish_time": ("time",),
    "result": ("result",),
    "comment": ("comment", "trial remark", "remark"),
}

# Without a horse name the row is not a runner, and there is no positional
# fallback for it.
_REQUIRED = ("horse_name",)

_BATCH = re.compile(r"Batch\s+(\d+)", re.IGNORECASE)
_COURSE_DIST = re.compile(r"-\s*(.+?)\s*-\s*(\d{3,4})\s*m", re.IGNORECASE)
_GOING = re.compile(r"Going\s*:\s*([A-Za-z ]+?)(?:\s{2,}|$|Time)", re.IGNORECASE)
_TIME = re.compile(r"Time\s*:\s*([\d.:]+)", re.IGNORECASE)
_SECTIONAL = re.compile(r"Sectional\s+Time\s*:\s*(.+)", re.IGNORECASE)
_HORSE_CODE = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[A-Z]\d{3})\)\s*$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _positions(text: str) -> list[int]:
    return [int(p) for p in re.findall(r"\d+", _clean(text))]


def _lbw(text: str) -> float | None:
    """Lengths behind, from HKJC's mixed vulgar fractions.

    "1-1/2" is one and a half lengths, "-" is the winner, "3/4" is three
    quarters. `float()` on any of them raises, and `pd.to_numeric` -- which
    the old package used -- silently discards all three.
    """
    raw = _clean(text).replace("N", "").strip()
    if not raw or raw in {"-", "--", "---"}:
        return 0.0
    match = re.match(r"^(\d+)?[-\s]*(\d+)/(\d+)$", raw)
    if match:
        whole = int(match.group(1) or 0)
        return round(whole + int(match.group(2)) / int(match.group(3)), 3)
    try:
        return float(raw)
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
        raise TrialsError(
            f"{source}: trial table header has no {', '.join(missing)} "
            f"column — saw {[_clean(c) for c in cells if _clean(c)][:10]}")
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


def parse_batch(header_text: str, meta_text: str, table, *,
                source: str = "") -> dict[str, Any]:
    """One batch: its conditions and its runners."""
    batch_no = _BATCH.search(header_text or "")
    course_dist = _COURSE_DIST.search(header_text or "")
    course = _clean(course_dist.group(1)) if course_dist else ""
    label = source or (f"batch {batch_no.group(1)}" if batch_no else "trial batch")

    going = _GOING.search(meta_text or "")
    overall = _TIME.search(meta_text or "")
    sectional = _SECTIONAL.search(meta_text or "")

    head = _header_row(table)
    if head is None:
        raise TrialsError(f"{label}: trial table has no header row")
    columns = _map_columns([c.get_text() for c in head.find_all(["th", "td"])],
                           label)

    runners: list[dict[str, Any]] = []
    started = False
    for tr in table.find_all("tr"):
        if tr is head:
            started = True
            continue
        if not started:
            continue
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        raw = [_clean(c.get_text()) for c in cells]

        row: dict[str, Any] = {}
        for field, idx in columns.items():
            if idx < len(raw):
                row[field] = raw[idx] or None
        name = row.get("horse_name")
        if not name:
            continue
        coded = _HORSE_CODE.match(name)
        if coded:
            row["horse_name"] = coded.group("name")
            row["horse_code"] = coded.group("code")
        row["horse_name"] = str(row["horse_name"]).upper()
        row["draw"] = int(row["draw"]) if str(row.get("draw") or "").isdigit() else None
        row["lbw"] = _lbw(row.get("lbw") or "")
        positions = _positions(row.get("running_positions") or "")
        row["running_positions"] = positions
        # RESULT is empty at source on every row in the archive. The finishing
        # position is the LAST running position, derived here so three callers
        # do not each derive it slightly differently.
        row["place"] = positions[-1] if positions else None
        runners.append(row)

    _validate(runners, label)
    return {
        "trial_no": int(batch_no.group(1)) if batch_no else None,
        "course": course,
        "venue": ("ST" if "SHA TIN" in course.upper()
                  else "HV" if "HAPPY VALLEY" in course.upper()
                  else "CH" if "CONGHUA" in course.upper() else None),
        "surface": "AWT" if "ALL WEATHER" in course.upper() else "Turf",
        # Published in the batch header. The legacy import dropped it, which is
        # why no trial in the archive has one.
        "distance": int(course_dist.group(2)) if course_dist else None,
        "going": _clean(going.group(1)) if going else None,
        "finish_time": _clean(overall.group(1)) if overall else None,
        "section_times": (_clean(sectional.group(1)).split()
                          if sectional else []),
        "runners": runners,
    }


def _validate(runners: list[dict[str, Any]], source: str) -> None:
    """A trial table with no runner, or with a name that is not a name.

    The shape check here is the reverse of the results parser's: a trial table
    has no numeric column in its first cells, so a shift shows up as a NUMBER
    where a horse's name belongs rather than the other way round.
    """
    if not runners:
        raise TrialsError(f"{source}: trial table found but no runner parsed — "
                          "the layout has changed")
    numeric_names = [r["horse_name"] for r in runners
                     if re.fullmatch(r"[\d\s./-]+", str(r["horse_name"]))]
    if numeric_names:
        raise TrialsError(
            f"{source}: columns look misaligned — horse_name holds "
            f"{numeric_names[0]!r}")


def parse_trial_day(html: str, *, source: str = "") -> list[dict[str, Any]]:
    """Every batch on one trial day.

    A batch is a header line, a conditions line and a table. HKJC nests them
    loosely, so the batches are found by their HEADER TEXT and each takes the
    next runner table after it — rather than by assuming a fixed structure of
    three tables per batch, which the old parser did and which breaks whenever
    a batch has an extra note.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    batches: list[dict[str, Any]] = []

    pending_header = ""
    pending_meta = ""
    for table in tables:
        text = _clean(table.get_text(" ", strip=True))
        if _BATCH.search(text) and _header_row(table) is None:
            pending_header = text
            pending_meta = ""
            continue
        if _header_row(table) is None:
            if pending_header and ("Going" in text or "Time" in text):
                pending_meta = text
            continue
        if not pending_header:
            continue
        label = f"{source} batch {_BATCH.search(pending_header).group(1)}" \
            if source and _BATCH.search(pending_header) else source
        batches.append(parse_batch(pending_header, pending_meta, table,
                                   source=label or "trial batch"))
        pending_header = ""
        pending_meta = ""

    if not batches:
        raise TrialsError(
            f"{source or 'trials'}: no trial batch found on the page")
    return batches


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_day(date: str, *, session=None) -> list[dict[str, Any]]:
    """Every batch on one trial day, with the date stamped on each."""
    html = fetch_html(urls.trials, {"Date": date.replace("-", "/")},
                      session=session)
    batches = parse_trial_day(html, source=date)
    for batch in batches:
        batch["trial_date"] = date
    return batches

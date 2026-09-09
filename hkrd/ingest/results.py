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

from hkrd.ingest._client import FetchError, fetch_html, urls

__all__ = ["ResultsError", "parse_race_header", "parse_results_table",
           "parse_sectional_table", "parse_sectional_page",
           "parse_incident_report", "fetch_sectionals",
           "fetch_race", "fetch_meeting", "meeting_venue"]


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
    # HKJC's header is "Dr.", not "Draw", and has been on every results page
    # in the archive. With only the long alias the column never mapped and the
    # draw was dropped from EVERY results scrape -- unnoticed because the
    # racecard supplies it too and covers 99.3% of the archive. It stops
    # covering anything the moment you look at an older meeting: HKJC keeps
    # results far longer than cards, so a backfilled season arrives with the
    # draw column empty and the draw term silently switched off for it.
    "draw": ("draw", "dr."),
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

    mostly_bad: list[str] = []
    never_right: list[str] = []
    for field, pattern in _SHAPES.items():
        values = [str(r.get(field, "")).strip() for r in rows if field in r]
        if not values:
            continue
        bad = [v for v in values if not pattern.match(v)]
        if not bad:
            continue
        if len(bad) > len(values) / 2:
            mostly_bad.append(f"{field} holds {bad[0]!r}")
        if len(bad) == len(values) and len(values) >= 2:
            never_right.append(f"{field} holds {bad[0]!r} in every row")

    # One odd column is a quirk; two or more is a layout change -- AND so is
    # one column that is wrong in every row. The two-field rule alone missed
    # the single catastrophic case: move the Horse column onto Act. Wt.'s
    # index and the parse comes back with horse_name='133',
    # jockey='FASHION LEGEND (J080)', actual_weight='D Eustace' -- the exact
    # shape of the corunning failure this validator was written to catch, and
    # it did not raise, because only one shape broke.
    if never_right or len(mostly_bad) >= 2:
        detail = "; ".join(never_right or mostly_bad)
        raise ResultsError(f"{source}: columns look misaligned — {detail}")


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


# ── the stewards' incident report ────────────────────────────────────────────
#
# It is a table ON THE RESULTS PAGE — `Pla. | Horse No. | Horse | Incident` —
# and nothing scraped it. `runner_comments` has carried a `source` column with
# 'incident' since the beginning and `query/race` PREFERS it over the objective
# comments-on-running text, but the only thing that ever wrote one was
# `jobs/import_legacy_reports`, run once over the archive.
#
# So live meetings had only the corunning endpoint, which for 2026-09-06
# answered "No Comments on Running information for this horse." for all 119
# runners while the incident report on the page beside it read "Approaching the
# 900 Metres, when racing keenly, was steadied when crowded between EMERGING
# STAR ... a substantial amount of blood in the horse's trachea". Every tag in
# `runner_tags` derives from this text, so the card had none.
_INCIDENT_HEAD = ("pla", "horse no", "incident")


def parse_incident_report(html: str, *, source: str | None = None
                          ) -> list[dict[str, Any]]:
    """The stewards' account of each runner, keyed by horse number.

    Returns [] when the page carries no incident table. A race can genuinely
    have no report — it is published with the result and occasionally lags it —
    and the caller walks races, so that must be an empty answer rather than an
    exception.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [" ".join(c.get_text(" ", strip=True).split()).lower()
                for c in rows[0].find_all(["th", "td"])]
        joined = " | ".join(head)
        if not all(want in joined for want in _INCIDENT_HEAD):
            continue
        # By HEADER, never by position: the column order has changed on this
        # site before and a parser confident about positions it never verified
        # put a trainer's name in the horse column for three days.
        idx = {name: i for i, name in enumerate(head)}
        no_at = next(i for name, i in idx.items() if "horse no" in name)
        inc_at = next(i for name, i in idx.items() if "incident" in name)
        out: list[dict[str, Any]] = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) <= max(no_at, inc_at):
                continue
            horse_no = cells[no_at].strip()
            text = " ".join(cells[inc_at].split()).strip()
            if not horse_no.isdigit() or not text:
                continue
            out.append({"horse_no": horse_no, "comment": text})
        if out:
            return out
    return []


# ── the sectional page ───────────────────────────────────────────────────────
#
# Sectionals used to be a table INSIDE the results page, and `parse_sectional_
# table` reads that. HKJC stopped putting them there: coverage runs 151 of 151
# on 2026-06-27 and 152 of 153 on 2026-07-12, then 0 of 107 on 2026-07-15 and
# 0 of 120 on 2026-09-06. Nothing failed — the table simply was not in the page
# any more, the parser found no header saying "sectional", and returned {}.
# Every meeting since mid-July has had no section times at all.
#
# They are still published, at their own endpoint, which wants the date the
# other way round: `displaysectionaltime?racedate=06/09/2026` is DD/MM/YYYY
# where `localresults?racedate=2026/09/06` is YYYY/MM/DD. Same site, same
# meeting, two formats.
#
# A section time is the FIRST time-shaped token in its cell. The cell reads
# "7 3 22.33 11.15 11.18": position, margin behind the leader, the section
# time, and then the 100m splits inside it. Margins are lengths — `2-3/4`, `N`,
# `SH`, `HD` — and never look like a time, which is what makes taking the first
# `nn.nn` safe rather than positional.
_SECTION_TIME = re.compile(r"^\d{1,3}\.\d{2}$")


def parse_sectional_page(html: str, *, source: str | None = None
                         ) -> dict[str, dict[str, Any]]:
    """Per-runner section times from the dedicated page, keyed by horse number.

    Returns {} when the page carries no sectional table — which is what HKJC
    serves for a race past the end of the card. The caller walks races and
    needs that to be an empty answer rather than an exception.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = " ".join(c.get_text(" ", strip=True)
                        for c in rows[0].find_all(["th", "td"])).lower()
        if "finishing order" not in head or "horse no" not in head:
            continue
        out: dict[str, dict[str, Any]] = {}
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            # place | horse no | horse (code) | 1st sec .. nth sec | time
            if len(cells) < 5 or not cells[0].strip().isdigit():
                continue
            times = []
            for cell in cells[3:-1]:
                got = next((tok for tok in cell.split()
                            if _SECTION_TIME.match(tok)), None)
                if got:
                    times.append(got)
            if times:
                out[cells[1].strip()] = {"section_times": "; ".join(times)}
        if out:
            return out
    return {}


def fetch_sectionals(date: str, race_no: int, *,
                     session=None) -> dict[str, dict[str, Any]]:
    """One race's section times, from the endpoint that still publishes them."""
    day, month, year = date.split("-")[::-1]
    html = fetch_html(urls.sectional,
                      {"racedate": f"{day}/{month}/{year}", "RaceNo": str(race_no)},
                      session=session)
    return parse_sectional_page(
        html, source=f"{urls.sectional} {date} R{race_no}")


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_race(date: str, venue: str, race_no: int, *, session=None) -> dict[str, Any]:
    """One race: header, runners, sectionals. Raw fact only, no derived values."""
    params = {"racedate": date, "Racecourse": venue, "RaceNo": str(race_no)}
    html = fetch_html(urls.localresults, params, session=session)
    source = f"{urls.localresults} {date} {venue} R{race_no}"

    header = parse_race_header(html)
    runners = parse_results_table(html, source=source)
    # The results page first, because where it still carries the table this
    # costs nothing. Only when it does not — every meeting since mid-July — is
    # the dedicated page fetched, which is one extra request per race.
    sections = parse_sectional_table(html)
    if not sections:
        try:
            sections = fetch_sectionals(date, race_no, session=session)
        except FetchError:
            # A race whose sectionals cannot be reached still has its result,
            # its dividends and its finishing time. Losing all of that over a
            # second page would be the trade the wrong way round.
            sections = {}
    for r in runners:
        extra = sections.get(str(r.get("horse_no", "")).strip())
        if extra:
            r.update(extra)
    return {"race_date": date, "race_no": race_no, "venue": venue,
            **header, "runners": runners,
            # The stewards' report, from the same page. Kept beside the runners
            # rather than merged into them: it is a different fact with a
            # different source, and `runner_comments` stores it as one.
            "incidents": parse_incident_report(html, source=source)}


# Sha Tin or Happy Valley, as the page writes them.
_VENUE_ON_PAGE = (("Sha Tin", "ST"), ("Happy Valley", "HV"))


def meeting_venue(date: str, *, session=None) -> str | None:
    """Which course raced on `date`, or None if nothing did.

    ASKED WITHOUT A COURSE, HKJC ANSWERS WITH THE ONE THAT RACED. That is the
    whole trick behind backfilling: the results page needs a `Racecourse` to
    return a race, but omitting it makes the site pick the meeting that
    actually happened and name the course in the body. So one request settles
    both "did they race" and "where", instead of two probes per candidate day.

    A day with no meeting returns the same "No information." page a wrong
    course does, which is why this reads the venue out of the body rather than
    trusting the status code -- HKJC serves 200 for both.

    The date picker on the page is populated by JavaScript, so there is no
    calendar to read and nothing here needs a browser to find one.
    """
    try:
        html = fetch_html(urls.localresults,
                          {"racedate": date, "RaceNo": "1"}, session=session)
    except FetchError:
        return None
    if "No information" in html:
        return None
    for label, code in _VENUE_ON_PAGE:
        if label in html:
            return code
    return None


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

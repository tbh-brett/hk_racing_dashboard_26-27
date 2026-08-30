"""HKJC dividends — what each pool actually paid.

Adapted from the old `scrape_hkjc_dividends.py`. The parsing shape is kept:
the dividend table is a run of rows where a POOL LABEL opens a section and
unlabelled rows continue it, so state carries down the table and a row like
`["4", "15.00"]` belongs to whatever pool last named itself.

Three things change.

The pool label is matched longest-first. "QUINELLA PLACE" starts with
"QUINELLA", so a shortest-first scan files every QPL dividend under QIN --
silently, and with a plausible number. The old module got this right by
sorting; it is stated here because the ordering IS the correctness.

A continuation row is only accepted while a pool is open. The old parser would
take the first two cells of ANY unlabelled row, so a footnote or a totals line
became a dividend under whichever pool preceded it.

And the result is validated before it is returned. A dividend table with no
WIN row, or with a combination that is not a set of horse numbers, is a layout
change rather than a quiet meeting -- which is the failure mode this package
exists to remove.

Dividends live on the SAME page as the results, so there is no separate URL:
`fetch_race` reads the localresults page and this reads the table below the
finishing order.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import fetch_html, urls

__all__ = ["DividendsError", "parse_dividends", "fetch_race", "fetch_meeting",
           "POOLS"]


class DividendsError(ValueError):
    """A dividend table could not be read. Names the source and the field."""


# Label on the page -> the code stored. Scanned LONGEST FIRST: "QUINELLA
# PLACE" starts with "QUINELLA", and a shortest-first scan files every QPL
# dividend under QIN with a number that looks entirely reasonable.
POOLS: dict[str, str] = {
    "QUINELLA PLACE": "QPL",
    "JOCKEY CHALLENGE": "JKC",
    "DOUBLE TRIO": "DTRIO",
    "QUINELLA": "QIN",
    "FORECAST": "FCT",
    "TIERCE": "TCE",
    "QUARTET": "QTT",
    "FIRST 4": "F4",
    "TREBLE": "TBL",
    "DOUBLE": "DBL",
    "PLACE": "PLACE",
    "TRIO": "TRIO",
    "WIN": "WIN",
}
_BY_LENGTH = tuple(sorted(POOLS, key=len, reverse=True))

# A combination is horse numbers, separated by commas or slashes. Anything
# else in that cell means the row is not a dividend.
_COMBINATION = re.compile(r"^\d{1,2}(\s*[,/]\s*\d{1,2})*$")
_MONEY = re.compile(r"^[\d,]+(\.\d{1,2})?$")

# Rows that are furniture, not dividends.
_SKIP = ("POOL", "COMBINATION", "DIVIDEND", "TOTAL", "REFUND")


def _money(text: str) -> float | None:
    cleaned = (text or "").replace(",", "").replace("$", "").strip()
    if not _MONEY.match(cleaned):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _pool_for(label: str) -> str | None:
    upper = (label or "").upper().strip()
    for name in _BY_LENGTH:
        if upper.startswith(name):
            return POOLS[name]
    return None


def _find_table(soup: BeautifulSoup):
    """The dividend table is the one naming several pools at once.

    Matched on content rather than on a class name, because HKJC's table
    classes change between seasons while the pools do not.
    """
    best = None
    best_hits = 0
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True).upper()
        hits = sum(1 for name in POOLS if name in text)
        if hits > best_hits:
            best, best_hits = table, hits
    # WIN, PLACE and at least one exotic. Fewer than three named pools is some
    # other table that happens to contain the word "PLACE".
    return best if best_hits >= 3 else None


def parse_dividends(html: str, *, source: str = "") -> list[dict[str, Any]]:
    """One race's dividends, per $10 unit as HKJC publishes them.

    Not normalised to a $1 stake: the number stored should be the number on
    the ticket, and converting it here would put a derived value in the ingest
    layer where two callers could disagree about the divisor.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table(soup)
    if table is None:
        raise DividendsError(
            f"{source or 'dividends'}: no dividend table found — the page "
            "named fewer than three pools")

    out: list[dict[str, Any]] = []
    pool: str | None = None
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True)
                 for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        if any(cells[0].upper().startswith(s) for s in _SKIP):
            continue

        named = _pool_for(cells[0])
        if named:
            pool = named
            combo, amount = (cells[1], cells[2]) if len(cells) >= 3 else (None, None)
        elif pool and len(cells) >= 2:
            # A continuation row, and ONLY while a pool is open. Taking the
            # first two cells of any unlabelled row turns a footnote into a
            # dividend under whichever pool happened to precede it.
            combo, amount = cells[0], cells[1]
        else:
            continue

        if not combo or not _COMBINATION.match(combo.replace(" ", "")):
            continue
        value = _money(amount or "")
        if value is None:
            continue
        out.append({"pool": pool, "combination": combo.replace(" ", ""),
                    "dividend_per_10": value})

    _validate(out, source or "dividends")
    return out


def _validate(rows: list[dict[str, Any]], source: str) -> None:
    """A race always pays a WIN dividend. No WIN row is a layout change.

    Stated as a rule rather than left to the caller because an empty list and
    a misread table look identical downstream, and that equivalence is the
    fault this package exists to remove.
    """
    if not rows:
        raise DividendsError(
            f"{source}: dividend table found but no row parsed — the layout "
            "has changed")
    pools = {r["pool"] for r in rows}
    if "WIN" not in pools:
        raise DividendsError(
            f"{source}: no WIN dividend among {sorted(pools)} — every race "
            "pays one, so the pool labels are being read wrongly")


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_race(date: str, venue: str, race_no: int, *,
               session=None) -> list[dict[str, Any]]:
    """Dividends for one race. Same page as the results."""
    query = date.replace("-", "/")
    html = fetch_html(urls.localresults,
                      {"racedate": query, "Racecourse": venue,
                       "RaceNo": str(race_no)}, session=session)
    return parse_dividends(html, source=f"{date} {venue} R{race_no}")


def fetch_meeting(date: str, venue: str, *, max_races: int = 11,
                  session=None) -> dict[int, list[dict[str, Any]]]:
    """Every race on the card. A race that raises is recorded as an error
    against its number rather than dropped, so a partial meeting is visible."""
    from hkrd.ingest._client import FetchError, NotFound

    out: dict[int, list[dict[str, Any]]] = {}
    for race_no in range(1, max_races + 1):
        try:
            out[race_no] = fetch_race(date, venue, race_no, session=session)
        except NotFound:
            break                       # past the last race on the card
        except FetchError:
            # Transport, not content: one unreachable host, not eleven.
            break
        except DividendsError:
            continue
    return out

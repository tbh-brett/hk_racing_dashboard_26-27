"""Doubles — one bet spanning two consecutive races.

Leg N couples race N with race N+1, so a card of R races offers R-1 legs and
`/dbl/<date>/<venue>/10` on a ten-race card redirects to leg 1 rather than
existing. Both legs must win; when the second-leg selection runs second a
consolation is paid.

WHY THIS POOL IS WORTH CAPTURING. It is the only market that prices a race
before that race's own win pool has matured. Divide a double by its first-leg
win odds and what is left is an implied conditional price for the second-leg
runner, funded by different money and available hours earlier than the win
market on that race. Two independent reads on the same race disagreeing is a
fact no single pool can produce.

Unlike the quinella matrices next door in `odds.py`, this one is an ordinary
rectangular table, so it is read by row and column rather than rebuilt from
bounding boxes. The shape:

    row 0        ['1st Leg', '1', '2', ... ]   first-leg horse numbers
    row 1        ['2nd Leg']                   a spanning label, skipped
    rows 2..n    ['<2nd leg horse>', odds ...]

The table is padded to the widest field the venue allows, so a race with 11
runners still renders rows 12, 13 and 14 — empty. Those blanks are padding, not
prices, and writing them would invent runners that were never declared.
"""
from __future__ import annotations

import re
from datetime import datetime
from collections.abc import Sequence
from typing import Any

__all__ = ["DoublesError", "parse_grid", "double_rows", "fetch_leg",
           "leg_races", "DISPLAY_CAP", "DBL_URL", "GRID_JS"]

DBL_URL = "https://bet.hkjc.com/en/racing/dbl/{date}/{venue}/{leg_no}"

# The page cannot show more than three digits, so every double priced at or
# beyond 999 renders as exactly "999". It is a ceiling, not a quotation: a
# 62/1 runner coupled with a 41/1 runner is worth some thousands and still
# prints 999. Stored as given so the capture stays faithful to the page, and
# named here so analysis can exclude capped cells rather than invert them into
# a probability that is simply wrong.
DISPLAY_CAP = 999.0

_NUMERIC = re.compile(r"^\d+(\.\d+)?$")


class DoublesError(ValueError):
    """A doubles grid could not be read. Names what was wrong."""


def leg_races(leg_no: int) -> tuple[int, int]:
    """The two races leg N couples. Named so the +1 lives in one place."""
    return int(leg_no), int(leg_no) + 1


def _horse_no(value: Any) -> int | None:
    s = str(value or "").strip()
    return int(s) if s.isdigit() and 1 <= int(s) <= 20 else None


def _odds(value: Any) -> float | None:
    s = str(value or "").strip()
    if not s or not _NUMERIC.match(s):
        return None
    v = float(s)
    return v if v > 0 else None


# Read straight off the rendered table. The quinella matrices need their grid
# rebuilt from cell geometry because they are triangular; this one is a plain
# rectangle, so reading rows and cells is both simpler and less to go wrong.
#
# The detector reads the TABLE's text, not the document's: '1st Leg' is present
# in `table.innerText` and absent from `document.body.innerText` on the live
# page, so a body-text check finds nothing while the table is sitting there.
GRID_JS = r"""
() => {
  const leaves = Array.from(document.querySelectorAll('table'))
    .filter(t => t.querySelectorAll('table').length === 0);
  const m = leaves.find(t => /1st\s*Leg/i.test(t.innerText || ''));
  if (!m) return null;
  return Array.from(m.rows).map(
    r => Array.from(r.cells).map(c => (c.innerText || '').trim()));
}
"""


def parse_grid(grid: Sequence[Sequence[str]], *, date: str, leg_no: int,
               captured_at: str, venue: str | None = None) -> dict[str, Any]:
    """Normalise the rendered grid into first-leg / second-leg prices.

    Raises when the grid is not the shape this was written for. Returning an
    empty list instead would be indistinguishable from a leg with no market,
    which is the failure this package exists to remove.
    """
    if not captured_at:
        raise DoublesError(f"{date} leg {leg_no}: grid has no captured_at")
    try:
        datetime.fromisoformat(captured_at)
    except ValueError:
        raise DoublesError(
            f"{date} leg {leg_no}: unparseable captured_at {captured_at!r}") from None

    if not grid or not grid[0]:
        raise DoublesError(
            f"{date} leg {leg_no}: no doubles grid on the page — the shape "
            "changed, or the page had not finished rendering")

    header = list(grid[0])
    if not re.search(r"1st\s*Leg", str(header[0] or ""), re.I):
        raise DoublesError(
            f"{date} leg {leg_no}: first cell of the grid is {header[0]!r}, "
            "expected the '1st Leg' corner label")

    # Column index -> first-leg horse number, taken from the header LABELS
    # rather than assumed to be 1..n. A field with a scratching still prints
    # its remaining numbers, and positional reading would shift every price by
    # one column from the gap onwards.
    first_by_col: dict[int, int] = {}
    for col, cell in enumerate(header[1:], start=1):
        no = _horse_no(cell)
        if no is not None:
            first_by_col[col] = no
    if not first_by_col:
        raise DoublesError(
            f"{date} leg {leg_no}: header row carries no first-leg horse "
            f"numbers (read: {header[:6]})")

    pairs: list[dict[str, Any]] = []
    capped = 0
    seen_label = False
    for row in grid[1:]:
        if not row:
            continue
        if len(row) == 1:
            # The '2nd Leg' spanning label. Noted so that a grid which never
            # carries it is still readable, but its absence is not silent.
            if re.search(r"2nd\s*Leg", str(row[0] or ""), re.I):
                seen_label = True
            continue
        second = _horse_no(row[0])
        if second is None:
            continue
        for col, cell in enumerate(row[1:], start=1):
            first = first_by_col.get(col)
            if first is None:
                continue
            price = _odds(cell)
            if price is None:
                # Padding: the table is drawn to the widest field the venue
                # allows, so a race of 11 still renders rows 12-14 blank.
                # Storing them would declare runners that do not exist.
                continue
            if price >= DISPLAY_CAP:
                capped += 1
            pairs.append({"first": first, "second": second, "odds": price})

    if not pairs:
        raise DoublesError(
            f"{date} leg {leg_no}: grid found but no priced combinations in it")

    race_first, race_second = leg_races(leg_no)
    notes: list[str] = []
    if not seen_label:
        notes.append("grid had no '2nd Leg' label row — layout may have changed")
    if capped:
        notes.append(f"{capped} of {len(pairs)} combinations are at the "
                     f"{DISPLAY_CAP:.0f} display cap, not a real price")

    return {
        "race_date": date, "venue": venue, "leg_no": int(leg_no),
        "race_first": race_first, "race_second": race_second,
        "captured_at": captured_at, "pairs": pairs, "capped": capped,
        "notes": notes,
    }


def double_rows(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per combination, ready for the store.

    horse_first and horse_second are NOT sorted. `odds.pair_rows` sorts its
    pair so that 3-with-7 and 7-with-3 cannot disagree, because a quinella is
    unordered. A double is ordered — 3 in the first leg then 7 is a different
    bet at a different price from 7 then 3 — so sorting here would collapse two
    prices into whichever was written last.
    """
    return [{
        "race_date": parsed["race_date"], "leg_no": parsed["leg_no"],
        "horse_first": p["first"], "horse_second": p["second"],
        "captured_at": parsed["captured_at"], "odds": p["odds"],
    } for p in parsed["pairs"]]


def fetch_leg(page, date: str, venue: str, leg_no: int) -> dict[str, Any]:
    """One leg's doubles grid, through an already-open Playwright page.

    Takes the caller's `page`: the odds scrape is already driving a browser and
    a second would double the memory a capture needs.
    """
    url = DBL_URL.format(date=date, venue=venue, leg_no=leg_no)
    notes: list[str] = []
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    try:
        page.wait_for_selector("text=1st Leg", timeout=15_000)
    except Exception as exc:                        # noqa: BLE001 - recorded
        # Not fatal on its own: the grid read below either finds the shape or
        # raises. What must not happen is this passing unrecorded.
        notes.append(f"doubles grid did not render within 15s "
                     f"({type(exc).__name__})")
    page.wait_for_timeout(1_500)

    captured = datetime.now().isoformat(timespec="seconds")
    grid = page.evaluate(GRID_JS)
    parsed = parse_grid(grid or [], date=date, leg_no=leg_no,
                        captured_at=captured, venue=venue)
    parsed["notes"] = notes + parsed["notes"]
    parsed["url"] = url
    return parsed

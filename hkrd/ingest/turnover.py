"""Pool turnover — how much money is in each pool, per race, over time.

Odds are a ratio. They say how the money is DIVIDED and nothing about how much
of it there is, so a price of 4.0 in a $91,000 double leg and a price of 4.0 in
a $4,300,000 win pool are the same number describing amounts forty times apart.
Turnover is the missing denominator, and it is what turns "shortened 4.0 to
3.5" into "$210,000 arrived on this horse".

It is also the thing most easily misread. On 2026-09-06 race 3 held $4,288,122
of win money against roughly $500,000 in every other race on the card — not
because the crowd had found value, but because KA YING RISING was 1.0 in a
six-horse field. Raw turnover follows field size, favourite shortness and race
profile; it is a denominator, never a tip. Nothing in this module ranks a horse
by the money on it.

The page lives at /turnover/<date>/<venue>/<race_no> and renders a plain text
block, so unlike the quinella matrices there is no grid to rebuild.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from hkrd.ingest.dividends import POOLS as _DIVIDEND_POOLS

__all__ = ["TurnoverError", "parse_turnover", "turnover_rows", "fetch_race",
           "POOL_LABELS", "TOTAL_POOLS", "TURNOVER_URL"]

TURNOVER_URL = "https://bet.hkjc.com/en/racing/turnover/{date}/{venue}/{race_no}"


class TurnoverError(ValueError):
    """A turnover block could not be read. Names what was wrong."""


# Label as the page prints it -> the code stored.
#
# The codes come from ingest/dividends.POOLS so the two sources agree on what
# "QIN" means; a second private vocabulary here is how the same pool ends up
# under two names in one database.
#
# Matched on the WHOLE label, not by prefix. dividends.py has to scan its
# labels longest-first because a section header there is a bare word and
# "QUINELLA PLACE" starts with "QUINELLA". Here each line carries its complete
# label, so an exact lookup avoids that trap outright rather than ordering
# around it.
POOL_LABELS: dict[str, str] = {
    **{label: code for label, code in _DIVIDEND_POOLS.items()},
    # Not in the dividends vocabulary: the turnover page merges these two into
    # a single pool line, and they pay out as one. Recording it as QTT would
    # claim a quartet-only figure the page never published.
    "QUARTET AND FIRST 4": "QTT_F4",
    # The page's own totals. Redundant with the sum of the pools above, which
    # is the point -- see TOTAL_POOLS.
    "RACE TOTAL (SINGLE RACE POOLS)": "RACE_TOTAL_SINGLE",
    "RACE TOTAL (ALL POOLS)": "RACE_TOTAL_ALL",
}

# The two rows that are totals rather than pools. Kept apart so that summing
# "the pools" never accidentally counts the total as one of them.
TOTAL_POOLS = frozenset({"RACE_TOTAL_SINGLE", "RACE_TOTAL_ALL"})

_ANCHOR = "Total Turnover"
# "Win\t$ 4,288,122" once innerText has flattened the row.
_LINE = re.compile(r"^(?P<label>[A-Za-z0-9 ()&/'-]+?)\s*\$\s*(?P<amount>[\d,]+)\s*$")


def _money(text: str) -> float | None:
    cleaned = (text or "").replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return float(cleaned)


def parse_turnover(body_text: str, *, date: str, race_no: int,
                   captured_at: str, venue: str | None = None) -> dict[str, Any]:
    """Read the `Total Turnover` block out of the rendered page.

    Raises when the anchor is missing or no pool line follows it. An empty
    result would be indistinguishable from a race whose pools hold nothing,
    which is not a state that exists — a race with a market always has win
    money in it.
    """
    if not captured_at:
        raise TurnoverError(f"{date} R{race_no}: turnover has no captured_at")
    try:
        datetime.fromisoformat(captured_at)
    except ValueError:
        raise TurnoverError(
            f"{date} R{race_no}: unparseable captured_at {captured_at!r}") from None

    start = body_text.find(_ANCHOR)
    if start < 0:
        raise TurnoverError(
            f"{date} R{race_no}: '{_ANCHOR}' not found in the rendered page — "
            "the shape changed, or the page had not finished rendering")

    pools: dict[str, float] = {}
    unknown: list[str] = []
    for raw in body_text[start + len(_ANCHOR):].splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            # The block ends at the first line that is not `label $ amount`.
            # Everything after it is page furniture (the bet slip, the
            # responsible-gambling notice), and scanning on would let a stray
            # dollar figure from the footer land in a pool.
            if pools:
                break
            continue
        label = " ".join(m.group("label").split()).upper()
        amount = _money(m.group("amount"))
        code = POOL_LABELS.get(label)
        if code is None:
            # Named, never swallowed: HKJC adding a pool must be visible as a
            # new name rather than as a total that quietly stops adding up.
            unknown.append(label)
            continue
        if amount is not None:
            pools[code] = amount

    if not pools:
        raise TurnoverError(
            f"{date} R{race_no}: found '{_ANCHOR}' but no pool lines under it")
    if "WIN" not in pools:
        raise TurnoverError(
            f"{date} R{race_no}: turnover block has no WIN pool — a race with a "
            f"market always has win money in it (read: {sorted(pools)})")

    notes: list[str] = []
    for label in unknown:
        notes.append(f"unrecognised pool label {label!r} — not stored")

    # The published total against the sum of what was recognised. This is the
    # only check that can notice a pool this parser has never heard of: the
    # label lands in `unknown`, and the arithmetic stops matching.
    total = pools.get("RACE_TOTAL_SINGLE")
    if total is not None:
        # Single-race pools only: DBL, TBL and DTRIO span races and are
        # excluded from the page's own single-race subtotal.
        cross_race = {"DBL", "TBL", "DTRIO"}
        summed = sum(v for k, v in pools.items()
                     if k not in TOTAL_POOLS and k not in cross_race)
        if summed and abs(summed - total) > max(1.0, total * 0.005):
            notes.append(
                f"single-race pools sum to {summed:,.0f} but the page publishes "
                f"{total:,.0f} — a pool is missing from POOL_LABELS")

    return {
        "race_date": date, "race_no": int(race_no), "venue": venue,
        "captured_at": captured_at, "pools": pools, "notes": notes,
        # Which pools the race actually OPERATES. A pool with no line is not
        # offered -- race 3 of 2026-09-06 had six starters, so no Quinella
        # Place pool existed and the page omitted the row entirely. That is a
        # fact about the race, not a failed read.
        "operated": sorted(k for k in pools if k not in TOTAL_POOLS),
    }


def turnover_rows(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per pool, ready for the store."""
    return [{
        "race_date": parsed["race_date"], "race_no": parsed["race_no"],
        "pool": pool, "captured_at": parsed["captured_at"], "turnover": amount,
    } for pool, amount in sorted(parsed["pools"].items())]


def fetch_race(page, date: str, venue: str, race_no: int) -> dict[str, Any]:
    """One race's pool turnover, through an already-open Playwright page.

    Takes the caller's `page` rather than opening its own browser: the odds
    scrape is already driving one, and a second would double the memory a
    capture needs for a page that is a dozen lines of text.
    """
    url = TURNOVER_URL.format(date=date, venue=venue, race_no=race_no)
    notes: list[str] = []
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    try:
        page.wait_for_selector(f"text={_ANCHOR}", timeout=15_000)
    except Exception as exc:                        # noqa: BLE001 - recorded
        notes.append(f"turnover block did not render within 15s "
                     f"({type(exc).__name__})")
    page.wait_for_timeout(500)

    captured = datetime.now().isoformat(timespec="seconds")
    parsed = parse_turnover(page.locator("body").inner_text(timeout=5_000),
                            date=date, race_no=race_no, captured_at=captured,
                            venue=venue)
    parsed["notes"] = notes + parsed["notes"]
    parsed["url"] = url
    return parsed

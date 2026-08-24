"""Live odds — win, place, and the quinella matrices.

Odds live on bet.hkjc.com and are rendered by JavaScript, so the fetch needs a
real browser. That is the one sanctioned use of Playwright in this codebase;
everything else is plain HTTP. Parsing is kept separate from fetching so the
shapes can be tested without one.

The rule that governs this module: NOTHING here ever deletes a snapshot. The
old scraper called prune_old_snapshots(keep=20) after every capture, and 17
meetings survived an entire season. Odds movement is the most informative
signal in the dataset -- the favourite changes between morning and post time in
44% of races -- and it is the only thing here that cannot be reconstructed after
the fact. A season is a few hundred megabytes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

__all__ = ["OddsError", "parse_snapshot", "snapshot_rows", "pair_rows",
           "BET_URL"]

BET_URL = "https://bet.hkjc.com/en/racing"

_NUMERIC = re.compile(r"^\d+(\.\d+)?$")


class OddsError(ValueError):
    """A snapshot could not be read. Names what was wrong."""


def _odds(value: Any) -> float | None:
    """A price, or None where none was offered.

    Scratched runners and pre-market races show '---' or blank; those are real
    answers and must not become zero.
    """
    s = str(value or "").strip()
    if not s or not _NUMERIC.match(s):
        return None
    v = float(s)
    return v if v > 0 else None


def _horse_no(value: Any) -> int | None:
    s = str(value or "").strip()
    return int(s) if s.isdigit() else None


def parse_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise one captured payload.

    Accepts the shape the live scraper produces: win/place under `odds`, and
    pair matrices under `qin_odds` / `qpl_odds`.
    """
    date = str(payload.get("date") or "").strip()
    race_no = payload.get("race_no")
    if not date or race_no is None:
        raise OddsError(f"snapshot missing date or race_no: {list(payload)[:6]}")

    captured = str(payload.get("scraped_at") or "").strip()
    if not captured:
        raise OddsError(f"{date} R{race_no}: snapshot has no scraped_at timestamp")
    # A snapshot without a trustworthy timestamp is worthless: the whole value
    # of this table is knowing WHEN a price was true.
    try:
        datetime.fromisoformat(captured)
    except ValueError:
        raise OddsError(f"{date} R{race_no}: unparseable scraped_at {captured!r}") from None

    return {
        "race_date": date,
        "race_no": int(race_no),
        "venue": payload.get("venue"),
        "captured_at": captured,
        "runners": payload.get("odds") or [],
        "qin": payload.get("qin_odds") or [],
        "qpl": payload.get("qpl_odds") or [],
    }


def snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Win and place prices, one row per runner."""
    out: list[dict[str, Any]] = []
    for r in snapshot["runners"]:
        no = _horse_no(r.get("no"))
        if no is None:
            continue
        out.append({
            "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
            "horse_no": no, "captured_at": snapshot["captured_at"],
            "win_odds": _odds(r.get("win")), "place_odds": _odds(r.get("place")),
        })
    return out


def pair_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Quinella and quinella-place matrices.

    Pairs are stored with horse_a < horse_b so a pair has one representation,
    not two that can disagree.
    """
    out: list[dict[str, Any]] = []
    for pool, entries in (("QIN", snapshot["qin"]), ("QPL", snapshot["qpl"])):
        for e in entries:
            a, b = _horse_no(e.get("a")), _horse_no(e.get("b"))
            if a is None or b is None or a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            out.append({
                "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
                "pool": pool, "horse_a": lo, "horse_b": hi,
                "captured_at": snapshot["captured_at"], "odds": _odds(e.get("odds")),
            })
    return out

"""A horse's own record in its gear: did it race keenly in it, and has it come off.

Asked for by the owner on 19 Nov 2026, after PRIVATE WEALTH: blinkers first
time on 25 Oct and keen in the middle stages; blinkers again on 11 Nov, keen
throughout and a warning on its racing manners; blinkers off on 18 Nov, settled
three places further back and beaten 0.3 L at 19.0. Every fact in that story
was in the archive. The card said only "B OFF", in the colour it gives every
removal, because nothing asked what the horse had done IN the blinkers.

Three readings, each attached to a gear piece `query/gear.py` already lists:

    keen_first      wearing blinkers / visor / cheek pieces again after racing
                    keenly the FIRST time it wore them. The caution: 83 such
                    runs returned 39 to 47 cents a dollar worse than their price
                    in WIN, PLACE and QPL.
    keen_in         the same after a keen run in gear it had worn before. NOT a
                    caution: those 577 runs placed 1.05x their price, the crowd
                    marking a keen run down further than it costs. Shown as the
                    horse's history, in a neutral colour. On the first card it
                    was read on (18 Nov 2026) STAR BROSE and GAZELEY won and
                    PRESTIGE GOOD ran second with it.
    off_after_keen  one of those taken off today, after it raced keenly the last
                    time it wore them. Private Wealth's case.
    hood_off        a hood taken off today: the strongest gear figure there is.

WHAT IS NOT CLAIMED. The archive figures in the tooltips were measured on two
seasons (Sep 2024, when HKJC's gear suffix starts, to 18 Nov 2026) with this
module's own definitions, by `claude/tools/insight-lab/gear_constants.py`. None
has been scored on races run after that. They say what happened, not what will.

A run counts only where its gear was recorded (a NULL gear column is "this
scrape did not carry gear", never "no gear") and keenness only where something
was written about the run (`derive.gear.raced_keen` returns None otherwise), so
every count here is "k of n runs we can read", never k of n runs.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.derive.gear import SWITCH_ON_CODES, parse_pieces, raced_keen
from hkrd.store.connect import Connection

__all__ = ["runs", "record", "annotate", "MEASURED"]

# Measured 19 Nov 2026, runs 11 Sep 2024 to 18 Nov 2026, "keen" as
# derive.gear.raced_keen reads it and the family as SWITCH_ON_CODES.
MEASURED: dict[str, Any] = {
    "span": "Sep 2024 to Nov 2026",
    "keen_all": 8.5,                    # % of all runs read
    # kept on after a keen run in the family
    "kept_n": 577, "kept_again": 15, "kept_win_ae": 1.05, "kept_place_ae": 1.05,
    # ... where that keen run was its FIRST in it
    "kept_first_n": 83, "kept_first_again": 18, "kept_first_place_ae": 0.97,
    "kept_first_cents": "39 to 47",
    # taken off after a keen run in it
    "off_n": 137, "off_place_ae": 1.19, "off_lengths": "half a length",
    # first time in the family
    "first_n": 1083, "first_keen": 10, "first_keen_before": 6,
    "first_win_ae": 0.86, "first_place_ae": 0.94,
    # hood off
    "hood_n": 222, "hood_win_ae": 1.91, "hood_place_ae": 1.16,
    "hood_by_season": "2.41× in 2024-25, 1.44× in 2025-26",
}
_FAMILY = "blinkers, a visor or cheek pieces"


def runs(conn: Connection, names: list[str], before: str) -> dict[str, list[dict[str, Any]]]:
    """Each horse's runs before `before` that have their gear on record, oldest
    first: the date, the codes worn (and those worn for the first time on
    record, HKJC's `1`), and whether it raced keenly (None = nothing written).
    Withdrawn runners (WV, WX ...) did not run and are left out."""
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT r.horse_name, r.race_date, r.race_no, r.gear, c.comment_text "
        f"FROM runners r LEFT JOIN runner_comments c "
        f"  ON c.race_date = r.race_date AND c.race_no = r.race_no AND c.horse_no = r.horse_no "
        f"WHERE r.horse_name IN ({marks}) AND r.race_date < ? AND r.gear IS NOT NULL "
        f"  AND (r.place IS NOT NULL OR (r.place_code IS NOT NULL AND r.place_code NOT LIKE 'W%')) "
        f"ORDER BY r.horse_name, r.race_date, r.race_no",
        [*names, before]).fetchall()
    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in rows:
        key = (r["horse_name"], r["race_date"], r["race_no"])
        run = grouped.setdefault(key, {"race_date": r["race_date"], "gear": r["gear"], "texts": []})
        run["texts"].append(r["comment_text"])
    out: dict[str, list[dict[str, Any]]] = {}
    for (name, _, _), run in grouped.items():          # insertion order is date order
        pieces = parse_pieces(run["gear"])
        out.setdefault(name, []).append({
            "race_date": run["race_date"],
            "worn": {p.code for p in pieces if p.worn},
            "first": {p.code for p in pieces if p.state == "first"},
            "keen": raced_keen(run["texts"]),
        })
    return out


def record(history: list[dict[str, Any]], code: str) -> dict[str, Any]:
    """The horse's runs in `code` and without it: how many could be read, how
    many were keen, and whether the LAST run in it was (None if unread)."""
    inn = [r for r in history if code in r["worn"]]
    out = [r for r in history if code not in r["worn"]]
    read_in = [r for r in inn if r["keen"] is not None]
    read_out = [r for r in out if r["keen"] is not None]
    return {
        "runs": len(inn), "read": len(read_in),
        "keen": sum(1 for r in read_in if r["keen"]),
        "keen_dates": [r["race_date"] for r in read_in if r["keen"]],
        "last_date": inn[-1]["race_date"] if inn else None,
        "last_keen": inn[-1]["keen"] if inn else None,
        # the last run in it was its first ever (HKJC's `1`)
        "last_first": bool(inn) and code in inn[-1].get("first", set()),
        "without_read": len(read_out),
        "without_keen": sum(1 for r in read_out if r["keen"]),
    }


def _day(iso: str, year: int) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b}" if d.year == year else f"{d.day} {d:%b} {d.year}"


def _own(rec: dict[str, Any], name: str, year: int) -> str:
    dates = ", ".join(_day(x, year) for x in rec["keen_dates"])
    text = f"Raced keenly in {rec['keen']} of its {rec['read']} runs in {name} on record ({dates})"
    if rec["without_read"]:
        text += f"; without them, {rec['without_keen']} of {rec['without_read']}"
    return text + "."


def annotate(pieces: list[dict[str, Any]], history: list[dict[str, Any]], *, before: str) -> None:
    """Add the horse's own record to the gear pieces `query/gear.py` built for
    it, in place. A piece gains `history` (its record) where there is one, and
    `signal` where the record says something a chip should show."""
    m = MEASURED
    year = dt.date.fromisoformat(before[:10]).year
    for p in pieces:
        p.setdefault("signal", None)
        code, state = p["code"], p["state"]
        if code == "H" and state == "off":
            p["signal"] = "hood_off"
            p["notable"] = True
            p["detail"] += (f". Hood off: {m['hood_n']} runs won {m['hood_win_ae']}× and placed "
                            f"{m['hood_place_ae']}× their price ({m['hood_by_season']}). "
                            f"Measured {m['span']}, not yet scored forward.")
            continue
        if code not in SWITCH_ON_CODES:
            continue
        rec = record(history, code)
        p["history"] = rec
        if state == "first":
            p["detail"] += (f". First time in {_FAMILY}: {m['first_keen']}% raced keenly that day "
                            f"against {m['first_keen_before']}% the run before ({m['first_n']:,} runs); "
                            f"they won {m['first_win_ae']}× and placed {m['first_place_ae']}× their price.")
            if rec["without_keen"]:
                p["detail"] += (f" This horse raced keenly in {rec['without_keen']} of its "
                                f"{rec['without_read']} runs on record.")
            continue
        if not rec["last_keen"]:
            continue
        if state == "off":
            p["signal"] = "off_after_keen"
            p["notable"] = True
            p["detail"] += (f". {_own(rec, p['name'], year)} Taken off after a keen run in {_FAMILY}, "
                            f"{m['off_n']} runs placed {m['off_place_ae']}× their price and finished "
                            f"{m['off_lengths']} better than it said; kept on, {m['kept_n']} placed "
                            f"{m['kept_place_ae']}×. Measured {m['span']}, not yet scored forward.")
        elif rec["last_first"]:
            p["signal"] = "keen_first"
            p["notable"] = True
            p["detail"] += (f". {_own(rec, p['name'], year)} Kept on after a keen FIRST run in "
                            f"{_FAMILY}, horses raced keenly again {m['kept_first_again']}% of the time "
                            f"({m['keen_all']}% of all runners) and placed {m['kept_first_place_ae']}× their "
                            f"price; those {m['kept_first_n']} runs returned {m['kept_first_cents']} cents "
                            f"a dollar worse than their price in WIN, PLACE and QPL. Measured {m['span']}.")
        else:
            p["signal"] = "keen_in"
            p["notable"] = True
            p["detail"] += (f". {_own(rec, p['name'], year)} Kept on after a keen run in gear they had "
                            f"worn before, horses raced keenly again {m['kept_again']}% of the time "
                            f"({m['keen_all']}% of all runners) but won {m['kept_win_ae']}× and placed "
                            f"{m['kept_place_ae']}× their price ({m['kept_n']} runs): the crowd marks a keen "
                            f"run down further than it costs. A history, not a caution. Measured {m['span']}.")

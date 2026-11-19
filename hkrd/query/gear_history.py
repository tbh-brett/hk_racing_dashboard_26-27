"""A horse's own record in its gear: did it race keenly in it, and has it come off.

Asked for by the owner on 19 Nov 2026, after PRIVATE WEALTH: blinkers first
time on 25 Oct and keen in the middle stages; blinkers again on 11 Nov, keen
throughout and a warning on its racing manners; blinkers off on 18 Nov, settled
three places further back and beaten 0.3 L at 19.0. Every fact in that story
was in the archive. The card said only "B OFF", in the colour it gives every
removal, because nothing asked what the horse had done IN the blinkers.

WHAT WAS CONFIRMED, and so what the chips claim (claude/tools/insight-lab/
gear_validate.py and gear_validate2.py; rules written before the results):

  * Blinkers switch horses on. First time in them, 11.4% of runs were keen
    against 7.7% for no-change runs by horses with the same keen history
    (+3.7 points, 95% +1.3 to +6.3, 559 runs). The race call and the stewards'
    report each show it on their own; the horses also sat nearer the lead than
    their habit at the first call; it held its sign in every season; and it
    lasts three runs (11.2% in runs 1-3, 7.5% from the 4th). Taking blinkers
    off is the mirror: 3.3 points CALMER (-5.0 to -1.6). Regression to the
    mean runs against this result, not for it: blinkers go on horses calmer
    than average (6.5% keen before).
  * No other piece moves keenness once the control is matched: a hood or a
    nose band "calms" a horse only because they are fitted to keen horses.
  * A horse keen in its gear last time is keen again 14.6% of the time if the
    gear stays on (575 runs) against 8% of all runs.

WHAT WAS NOT, and so what the chips do NOT claim: that any of this changes the
result against the price. Blinker runs 1-3 placed 0.96x (0.87 to 1.04); taken
off after a keen run, 1.21x against 1.06x kept on, a gap whose range spans
zero and which 2024-25 did not show. Hood off (1.72x to win, 1.22 to 2.31) and
cheek pieces off (1.47x, 1.11 to 1.85) cleared 1.00 in both full seasons but
came out of testing eight pieces; they are labelled leads, scored forward
from 19 Nov 2026, and drawn without the colour of a claim.

The readings, each attached to a gear piece `query/gear.py` already lists:

    keen_in         wearing blinkers / visor / cheek pieces, and keen the last
                    time it wore them. A caution about how it will RUN.
    off_after_keen  one of those off today, after a keen run in it.
    lead_off        a hood or cheek pieces off: a lead, with its record.

Every piece in that family, and the hood, also carries `timeline`: its last
runs with gear on record, each with the token it wore, which source called it
keen, where it sat at the first call and how it finished. That is the reading
itself, so the owner can disagree with it.

A run counts only where its gear was recorded (a NULL gear column is "this
scrape did not carry gear", never "no gear") and keenness only where something
was written about the run (`derive.gear.raced_keen` returns None otherwise), so
every count is "k of n runs we can read", never k of n runs.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.derive.gear import SWITCH_ON_CODES, parse_pieces, raced_keen
from hkrd.store.connect import Connection

__all__ = ["runs", "record", "annotate", "MEASURED", "TIMELINE_RUNS"]

TIMELINE_RUNS = 6
SOURCE_NAMES = {"corunning": "race call", "incident": "stewards"}
_FAMILY = "blinkers, a visor or cheek pieces"

# Measured 19 Nov 2026 on runs 8 Sep 2024 to 18 Nov 2026 (gear_validate*.py).
MEASURED: dict[str, Any] = {
    "span": "Sep 2024 to Nov 2026",
    "keen_all": 8.0,
    "b1_n": 559, "b1_keen": 11.4, "b1_ctl": 7.7,
    "b13_keen": 11.2, "b4_keen": 7.5, "b13_n": 1464, "b13_place_ae": 0.96,
    "boff_n": 674, "boff_keen": 5.2, "boff_ctl": 8.5,
    "kept_n": 575, "kept_again": 14.6, "off_n": 134, "off_again": 11.2,
    "off_place_ae": 1.21, "kept_place_ae": 1.06,
    "other_first": "cheek pieces +1.3 points (-1.2 to +4.3), visor -0.7 (-4.3 to +3.2)",
    "lead": {
        "H": ("Hood", 247, 1.72, "1.22 to 2.31", 1.18, "2.07× in 2024-25, 1.54× in 2025-26, 1.00× on 24 runs this season"),
        "CP": ("Cheek pieces", 530, 1.47, "1.11 to 1.85", 1.07, "1.19× in 2024-25, 1.60× in 2025-26, 1.81× on 53 runs this season"),
    },
}


def runs(conn: Connection, names: list[str], before: str) -> dict[str, list[dict[str, Any]]]:
    """Each horse's runs before `before` with their gear on record, oldest
    first: the date, each code's token (`B1`, `B`, `B-`), the codes worn and
    worn for the first time, keen or not (None = nothing written) and which
    source said so, the first call, the finish and the price. Withdrawn runners
    (WV, WX ...) did not run and are left out."""
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT r.horse_name, r.race_date, r.race_no, r.gear, r.place, r.place_code, "
        f"       r.win_odds, r.running_positions, c.source, c.comment_text "
        f"FROM runners r LEFT JOIN runner_comments c "
        f"  ON c.race_date = r.race_date AND c.race_no = r.race_no AND c.horse_no = r.horse_no "
        f"WHERE r.horse_name IN ({marks}) AND r.race_date < ? AND r.gear IS NOT NULL "
        f"  AND (r.place IS NOT NULL OR (r.place_code IS NOT NULL AND r.place_code NOT LIKE 'W%')) "
        f"ORDER BY r.horse_name, r.race_date, r.race_no",
        [*names, before]).fetchall()
    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in rows:
        key = (r["horse_name"], r["race_date"], r["race_no"])
        run = grouped.setdefault(key, {"row": r, "texts": {}})
        if r["source"]:
            run["texts"][r["source"]] = r["comment_text"]
    out: dict[str, list[dict[str, Any]]] = {}
    for (name, _, _), run in grouped.items():          # insertion order is date order
        r = run["row"]
        pieces = parse_pieces(r["gear"])
        calls = (r["running_positions"] or "").split()
        out.setdefault(name, []).append({
            "race_date": r["race_date"],
            "token": {p.code: p.raw for p in pieces},
            "worn": {p.code for p in pieces if p.worn},
            "first": {p.code for p in pieces if p.state == "first"},
            "keen": raced_keen(run["texts"].values()),
            "keen_by": [SOURCE_NAMES.get(s, s) for s, t in sorted(run["texts"].items())
                        if raced_keen([t])],
            "first_call": int(calls[0]) if calls and calls[0].isdigit() else None,
            "finish": r["place"] if r["place"] is not None else r["place_code"],
            "odds": r["win_odds"],
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
        "last_first": bool(inn) and code in inn[-1]["first"],
        "without_read": len(read_out),
        "without_keen": sum(1 for r in read_out if r["keen"]),
    }


def timeline(history: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    """The last runs on record, newest first, read for one piece of gear."""
    return [{
        "race_date": r["race_date"],
        "token": r["token"].get(code),
        "keen": r["keen"], "keen_by": r["keen_by"],
        "first_call": r["first_call"], "finish": r["finish"], "odds": r["odds"],
    } for r in reversed(history[-TIMELINE_RUNS:])]


def _day(iso: str, year: int) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b}" if d.year == year else f"{d.day} {d:%b} {d.year}"


def _own(rec: dict[str, Any], name: str, year: int) -> str:
    dates = ", ".join(_day(x, year) for x in rec["keen_dates"])
    text = f"Raced keenly in {rec['keen']} of its {rec['read']} runs in {name} on record ({dates})"
    if rec["without_read"]:
        text += f"; without them, {rec['without_keen']} of {rec['without_read']}"
    return text + "."


def _first_text(code: str) -> str:
    m = MEASURED
    if code == "B":
        return (f" First time in blinkers, horses raced keenly in {m['b1_keen']}% of runs against "
                f"{m['b1_ctl']}% for comparable horses ({m['b1_n']} runs; the race call and the "
                f"stewards' report each show it), sat nearer the lead early, and stayed keener for "
                f"their first three runs in them ({m['b13_keen']}%, back to {m['b4_keen']}% from the "
                f"4th). The price allows for it: runs 1-3 placed {m['b13_place_ae']}× their price "
                f"({m['b13_n']:,} runs). Measured {m['span']}.")
    return (f" Not shown to change how keenly a horse races, unlike blinkers: {m['other_first']} "
            f"points on first runs against comparable horses. Measured {m['span']}.")


def annotate(pieces: list[dict[str, Any]], history: list[dict[str, Any]], *, before: str) -> None:
    """Add the horse's own record to the gear pieces `query/gear.py` built for
    it, in place: `history` (its record in the piece), `timeline` (the runs
    behind it) and `signal` where the record says something a chip should."""
    m = MEASURED
    year = dt.date.fromisoformat(before[:10]).year
    for p in pieces:
        p.setdefault("signal", None)
        code, state = p["code"], p["state"]
        if code not in SWITCH_ON_CODES and code != "H":
            continue
        rec = record(history, code)
        p["history"] = rec
        p["timeline"] = timeline(history, code)
        lead = m["lead"].get(code)
        if state == "off" and lead:
            label, n, win, rng, place, seasons = lead
            p["detail"] += (f". {label} off: {n} runs won {win}× their price ({rng}) and placed "
                            f"{place}× ({seasons}). A lead from testing eight pieces, scored forward "
                            f"from 19 Nov 2026, not a confirmed edge.")
            p["signal"] = "lead_off"
            p["notable"] = True
            if code == "H":
                continue
        if code not in SWITCH_ON_CODES:
            continue
        if state == "first":
            p["detail"] += "." + _first_text(code)
            if rec["without_keen"]:
                p["detail"] += (f" This horse raced keenly in {rec['without_keen']} of its "
                                f"{rec['without_read']} runs on record.")
            continue
        if state == "second" and code == "B" and not rec["last_keen"]:
            p["detail"] += (f". Still inside the first three runs in blinkers, when horses race "
                            f"keener ({m['b13_keen']}% against {m['b4_keen']}% from the 4th).")
        if not rec["last_keen"]:
            continue
        if state == "off":
            p["signal"] = "off_after_keen"
            p["notable"] = True
            p["detail"] += (f". {_own(rec, p['name'], year)} Taken off after a keen run in {_FAMILY}, "
                            f"horses were keen again {m['off_again']}% of the time against "
                            f"{m['kept_again']}% kept on ({m['off_n']} and {m['kept_n']} runs)"
                            + (f"; blinkers off left horses {m['boff_ctl'] - m['boff_keen']:.1f} points "
                               f"calmer than comparable horses ({m['boff_n']} runs)" if code == "B" else "")
                            + f". Whether it helps the result is not settled: placed {m['off_place_ae']}× "
                            f"against {m['kept_place_ae']}× kept on, a gap 2024-25 did not show. "
                            f"Measured {m['span']}.")
        else:
            p["signal"] = "keen_in"
            p["notable"] = True
            p["detail"] += (f". {_own(rec, p['name'], year)} Kept on after a keen run in {_FAMILY}, "
                            f"horses raced keenly again {m['kept_again']}% of the time ({m['kept_n']} runs) "
                            f"against {m['keen_all']}% of all runs. A caution about how it will run; "
                            f"not shown to cost it against the price. Measured {m['span']}.")

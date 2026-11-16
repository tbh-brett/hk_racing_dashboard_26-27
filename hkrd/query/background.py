"""Where each import came from, and how the horses from each background ran.

Two reads. `for_horses` is the background line the Briefing and Race Day show
for a horse in its first EARLY_STARTS Hong Kong starts — after that its own
form says more than where it came from (Brett, 2026-09-28). `record` is the
background record: per agent, where it was trained, import type, sale price
and pre-import trial, how the horses ran against what the market expected.

THE TEST IS THE HORSES STILL TO COME. The idea was looked at on the 2025/26
imports (docs/background.md): agents named in the profile, David Price's
above all, looked better than the market thought; nothing else did. Numbers
looked at to form an idea cannot also test it, so `record` keeps the eras
apart — horses profiled from 2026/27 on are the test, 2025/26 is where the
idea came from — and every figure carries its sample.

PER GROUP: horses, their runs in Hong Kong, wins, A/E against the closing
tote (wins against the de-vigged chances of the same runs: above 1, the
market underrated them), the median change in rating from first run to last,
and how many rose ten points or more — Price's 2025/26 horses won races no
more often than other imports, but three of fourteen rose ten or more.

THE PROFILE'S TRIP VERDICT is a third read (Brett, 16 Nov 2026). The
profile's closing sentence says how far the horse should get, and
`derive/distance_verdict` reads it as stayer, miler or sprinter. Imports
called stayers had won at 1600 m and over about one and a half times as often
as their price said — in the WIN pool only; placed, they paid what anything
does. `distance_verdicts` puts the verdict on a runner where the trip bears on
it: 1600 m and over, or further than the horse has run here.
`record()["distance"]` scores it by RUN date rather than profile date, because
the idea was formed on every run to 16 Nov and the runs after it are the
test, whoever the horse.
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any, NamedTuple

from hkrd.derive import distance_verdict
from hkrd.query.screen_inputs import RAN
from hkrd.store.connect import Connection, get_conn

__all__ = ["for_horses", "record", "distance_verdicts", "EARLY_STARTS",
           "TEST_FROM", "DISTANCE_TEST_FROM", "SHOWN_FROM_M"]

EARLY_STARTS = 5          # the background shows for starts one to five
TEST_FROM = "2026-08-01"  # horses profiled from here on test the idea
DISTANCE_TEST_FROM = "2026-11-17"  # runs from here test the trip verdict
SHOWN_FROM_M = 1600       # the trip verdict shows at this trip and over
ROSE = 10                 # rating points that make "a horse that got better"
_BANDS = ((60e3, "under A$60k"), (150e3, "A$60-150k"), (300e3, "A$150-300k"),
          (float("inf"), "A$300k+"))
_COLS = ("horse_name", "brand_no", "profiled_for", "url", "origin",
         "import_type", "sire", "dam", "sire_hk_starters", "sire_hk_winners",
         "sale_kind", "sale_ccy", "sale_amount", "sale_aud", "prev_name",
         "prev_trainer", "prev_owner", "buyer", "prev_country", "agents",
         "overseas_starts", "overseas_wins", "trial_won", "about")


def _row(r: Any) -> dict[str, Any]:
    out = {k: r[k] for k in _COLS}
    out["agents"] = [a for a in (r["agents"] or "").split(",") if a]
    out["trial_won"] = None if r["trial_won"] is None else bool(r["trial_won"])
    return out


def for_horses(names: list[str], *, conn: Connection | None = None
               ) -> dict[str, dict[str, Any]]:
    """name -> its background, for the names that have one."""
    if not names:
        return {}
    own = conn is None
    conn = conn or get_conn()
    try:
        marks = ",".join("?" * len(names))
        return {r["horse_name"]: _row(r) for r in conn.execute(
            f"SELECT {', '.join(_COLS)} FROM horse_background "
            f"WHERE horse_name IN ({marks})", list(names))}
    finally:
        if own:
            conn.close()


def distance_verdicts(names: list[str], *, distance: int | None, before: str,
                      conn: Connection | None = None) -> dict[str, dict[str, Any]]:
    """name -> what its profile says of the trip, for the runners today's
    trip bears on: SHOWN_FROM_M and over, or longer than any race the horse
    has started in here before `before`. A horse whose profile names no trip
    is not in it.

    {"verdict", "ceiling_m", "sentence", "url", "longest_m", "step_up"}"""
    if not names or not distance:
        return {}
    own = conn is None
    conn = conn or get_conn()
    try:
        marks = ",".join("?" * len(names))
        profiles = conn.execute(
            f"SELECT horse_name, about, url FROM horse_background "
            f"WHERE horse_name IN ({marks})", list(names)).fetchall()
        if not profiles:
            return {}
        longest = {r[0]: r[1] for r in conn.execute(
            "SELECT u.horse_name, MAX(ra.distance) FROM runners u "
            "  JOIN races ra ON ra.race_date = u.race_date AND ra.race_no = u.race_no "
            f"WHERE u.horse_name IN ({marks}) AND u.race_date < ? "
            f"  AND (u.place IS NOT NULL OR u.place_code IS NOT NULL) AND {RAN.format(t='u')} "
            "GROUP BY u.horse_name", [*names, before])}
    finally:
        if own:
            conn.close()
    out = {}
    for r in profiles:
        v = distance_verdict.read(r["about"])
        top = longest.get(r["horse_name"])
        step_up = top is not None and distance > top
        if v is None or not (step_up or distance >= SHOWN_FROM_M):
            continue
        out[r["horse_name"]] = {**v, "url": r["url"], "longest_m": top, "step_up": step_up}
    return out


# ── the record ──────────────────────────────────────────────────────────────

class _Run(NamedTuple):
    key: tuple                  # (race_date, race_no)
    horse_no: int
    place: int | None
    rating: int | None
    p: float | None             # the de-vigged closing chance
    distance: int | None


def _settled(conn: Connection) -> dict[str, list[_Run]]:
    """Every settled run by a profiled horse, with its chance de-vigged from
    its whole field's closing prices."""
    field: dict[tuple, list] = defaultdict(list)
    for r in conn.execute(
            "WITH mine AS (SELECT DISTINCT u.race_date, u.race_no FROM runners u "
            "  JOIN horse_background b ON b.horse_name = u.horse_name "
            "  WHERE u.place IS NOT NULL) "
            "SELECT u.race_date, u.race_no, u.horse_no, u.horse_name, u.place, "
            "       u.win_odds, u.rating, ra.distance FROM runners u JOIN mine m "
            "    ON m.race_date = u.race_date AND m.race_no = u.race_no "
            "  JOIN races ra ON ra.race_date = u.race_date AND ra.race_no = u.race_no "
            f" WHERE {RAN.format(t='u')}"):
        field[(r["race_date"], r["race_no"])].append(r)
    profiled = {r[0] for r in conn.execute("SELECT horse_name FROM horse_background")}
    runs: dict[str, list[_Run]] = defaultdict(list)
    for key, f in field.items():
        priced = [y for y in f if y["win_odds"] and y["win_odds"] > 1.0]
        total = sum(1 / y["win_odds"] for y in priced)
        for y in f:
            if y["horse_name"] in profiled:
                p = (1 / y["win_odds"] / total) if (
                    y["win_odds"] and y["win_odds"] > 1.0 and total) else None
                runs[y["horse_name"]].append(
                    _Run(key, y["horse_no"], y["place"], y["rating"], p, y["distance"]))
    for rs in runs.values():
        rs.sort()
    return runs


def _band(aud: float | None) -> str:
    if not aud:
        return "no sale price"
    return next(label for top, label in _BANDS if aud < top)


def _groups(b: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (dimension, group) a horse belongs to. A horse with two agents
    counts under each: they are two names on one horse, not two horses."""
    out = [("agent", a) for a in b["agents"]] or [("agent", "no agent named")]
    out += [("trained in", b["prev_country"] or "not stated"),
            ("import type", b["import_type"]),
            ("sale price", _band(b["sale_aud"]))]
    if b["trial_won"] is not None:
        out.append(("pre-import trial", "won a trial or jump-out" if b["trial_won"]
                    else "trialled, did not win"))
    return out


_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 600.0


def record(*, conn: Connection | None = None) -> dict[str, Any]:
    """The background record, both eras. Cached ten minutes per database:
    it moves only when a result lands."""
    own = conn is None
    conn = conn or get_conn()
    try:
        key = conn.execute("PRAGMA database_list").fetchone()["file"] or ""
        hit = _CACHE.get(key)
        if key and hit and time.monotonic() - hit[0] < _TTL:
            return hit[1]
        out = _record(conn)
        if key:
            _CACHE[key] = (time.monotonic(), out)
        return out
    finally:
        if own:
            conn.close()


def _record(conn: Connection) -> dict[str, Any]:
    bg = {r["horse_name"]: _row(r) for r in conn.execute(
        f"SELECT {', '.join(_COLS)} FROM horse_background")}
    runs = _settled(conn)

    eras = {"test": "profiled from 2026/27 on — the test",
            "origin": "profiled in 2025/26 — where the idea came from"}
    table: dict[str, dict[tuple[str, str], list[str]]] = {e: defaultdict(list) for e in eras}
    for name, b in bg.items():
        era = "test" if b["profiled_for"] >= TEST_FROM else "origin"
        for g in _groups(b):
            table[era][g].append(name)

    def line(dim: str, group: str, names: list[str]) -> dict[str, Any]:
        n = w = 0
        expected = 0.0
        changes, ran = [], 0
        for h in names:
            rs = runs.get(h, [])
            if not rs:
                continue
            ran += 1
            for r in rs:
                if r.p is None:
                    continue
                n += 1
                w += r.place == 1
                expected += r.p
            rated = [r.rating for r in rs if r.rating]
            if len(rated) >= 2:
                changes.append(rated[-1] - rated[0])
        return {"dimension": dim, "group": group, "horses": len(names),
                "ran": ran, "runs": n, "won": w,
                "ae": round(w / expected, 2) if expected else None,
                "rating_change": statistics.median(changes) if changes else None,
                "rated": len(changes),
                "rose": sum(c >= ROSE for c in changes)}

    order = ("agent", "trained in", "import type", "sale price", "pre-import trial")
    out_eras = []
    for era, label in eras.items():
        lines = [line(d, g, names) for (d, g), names in table[era].items()]
        lines.sort(key=lambda x: (order.index(x["dimension"]), -x["horses"], x["group"]))
        out_eras.append({"key": era, "label": label,
                         "horses": len({h for names in table[era].values() for h in names}),
                         "lines": lines})
    return {"test_from": TEST_FROM, "rose_by": ROSE, "early_starts": EARLY_STARTS,
            "eras": out_eras, "distance": _distance_record(conn, bg, runs)}


_VERDICT_ORDER = (*distance_verdict.VERDICTS, None)
# The long runs are also split by whether the horse had already run that far
# here: when its own HK form at the trip takes over from the profile is a
# question for the runs from DISTANCE_TEST_FROM, not one to settle by guess
# (Brett, 16 Nov 2026).
_LONG, _SHORT = f"{SHOWN_FROM_M} m and over", f"under {SHOWN_FROM_M} m"
_FIRST, _AGAIN = f"first time at {SHOWN_FROM_M} m+ here", f"had run {SHOWN_FROM_M} m+ here"
_TRIPS = (_LONG, _FIRST, _AGAIN, _SHORT)


def _distance_record(conn: Connection, bg: dict[str, dict],
                     runs: dict[str, list[_Run]]) -> dict[str, Any]:
    """Per trip verdict and trip band: how those runs did against the win
    price, and what a $1 place bet returned at the tote — the place pool is
    where a stayer's edge was NOT, so the record says so every time."""
    verdict = {h: (distance_verdict.read(b["about"]) or {}).get("verdict")
               for h, b in bg.items()}
    # Place dividends for the races these horses ran in. A race with none
    # stored (HKJC's page was never read) is left out of the place return
    # rather than counted as a loss.
    keys = {r.key for rs in runs.values() for r in rs}
    paid: dict[tuple, dict[int, float]] = defaultdict(dict)
    if keys:
        for r in conn.execute(
                "SELECT race_date, race_no, combination, dividend_per_10 FROM dividends "
                "WHERE pool = 'PLACE' AND race_date >= ?", [min(keys)[0]]):
            key = (r["race_date"], r["race_no"])
            if key in keys and str(r["combination"]).strip().isdigit() and r["dividend_per_10"]:
                paid[key][int(r["combination"])] = r["dividend_per_10"]

    def cell() -> dict[str, Any]:
        return {"horses": set(), "runs": 0, "won": 0, "expected": 0.0, "placed": 0,
                "paid_runs": 0, "paid": 0.0}

    table: dict[str, dict[tuple, dict]] = {e: defaultdict(cell) for e in ("test", "origin")}
    for h, rs in runs.items():
        been_long = False           # an earlier start here at SHOWN_FROM_M or over
        for r in rs:                # in date order
            long_ = bool(r.distance) and r.distance >= SHOWN_FROM_M
            if r.p is not None and r.distance:
                era = "test" if r.key[0] >= DISTANCE_TEST_FROM else "origin"
                for trip in ((_LONG, _AGAIN if been_long else _FIRST) if long_ else (_SHORT,)):
                    c = table[era][(verdict.get(h), trip)]
                    c["horses"].add(h)
                    c["runs"] += 1
                    c["won"] += r.place == 1
                    c["expected"] += r.p
                    c["placed"] += r.place is not None and r.place <= 3
                    if r.key in paid:
                        c["paid_runs"] += 1
                        c["paid"] += paid[r.key].get(r.horse_no, 0.0)
            # An unpriced start still counts as having been that far.
            been_long = been_long or long_

    labels = {"test": f"runs from {DISTANCE_TEST_FROM} — the test",
              "origin": f"runs before {DISTANCE_TEST_FROM} — where the idea came from"}
    out = []
    for era, label in labels.items():
        lines = []
        for v in _VERDICT_ORDER:
            for trip in _TRIPS:
                c = table[era].get((v, trip))
                if not c:
                    continue
                lines.append({
                    "verdict": v or "no trip named", "trip": trip,
                    "sub": trip in (_FIRST, _AGAIN),
                    "horses": len(c["horses"]), "runs": c["runs"], "won": c["won"],
                    "expected": round(c["expected"], 1),
                    "ae": round(c["won"] / c["expected"], 2) if c["expected"] else None,
                    "placed": c["placed"], "paid_runs": c["paid_runs"],
                    "place_return": (round(c["paid"] / (10 * c["paid_runs"]), 2)
                                     if c["paid_runs"] else None)})
        out.append({"key": era, "label": label, "lines": lines,
                    "runs": sum(x["runs"] for x in lines if not x["sub"])})
    return {"test_from": DISTANCE_TEST_FROM, "shown_from_m": SHOWN_FROM_M, "eras": out}

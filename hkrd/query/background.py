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
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any

from hkrd.query.screen_inputs import RAN
from hkrd.store.connect import Connection, get_conn

__all__ = ["for_horses", "record", "EARLY_STARTS", "TEST_FROM"]

EARLY_STARTS = 5          # the background shows for starts one to five
TEST_FROM = "2026-08-01"  # horses profiled from here on test the idea
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


# ── the record ──────────────────────────────────────────────────────────────

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
    # Every settled race one of these horses ran in, with its whole field,
    # so each run's chance can be de-vigged from the closing prices.
    field: dict[tuple, list] = defaultdict(list)
    for r in conn.execute(
            "WITH mine AS (SELECT DISTINCT u.race_date, u.race_no FROM runners u "
            "  JOIN horse_background b ON b.horse_name = u.horse_name "
            "  WHERE u.place IS NOT NULL) "
            "SELECT u.race_date, u.race_no, u.horse_name, u.place, u.win_odds, "
            "       u.rating FROM runners u JOIN mine m "
            "    ON m.race_date = u.race_date AND m.race_no = u.race_no "
            f" WHERE {RAN.format(t='u')}"):
        field[(r["race_date"], r["race_no"])].append(r)
    runs: dict[str, list] = defaultdict(list)
    for key, f in field.items():
        priced = [y for y in f if y["win_odds"] and y["win_odds"] > 1.0]
        total = sum(1 / y["win_odds"] for y in priced)
        for y in f:
            if y["horse_name"] in bg:
                p = (1 / y["win_odds"] / total) if (
                    y["win_odds"] and y["win_odds"] > 1.0 and total) else None
                runs[y["horse_name"]].append((key, y["place"], y["win_odds"], y["rating"], p))

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
            rs = sorted(runs.get(h, []))
            if not rs:
                continue
            ran += 1
            for _, place, _, _, p in rs:
                if p is None:
                    continue
                n += 1
                w += place == 1
                expected += p
            rated = [r for _, _, _, r, _ in rs if r]
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
            "eras": out_eras}

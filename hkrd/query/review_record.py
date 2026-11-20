"""What the banker review adds up to (docs/banker-review.md).

He said on 2026-11-20 that the review is worth his time if, three months on, he
knows which of his reasons to trust. Until the fortnightly review sorts his
reasons into kinds (`banker_review.claim_kind`), the record can already say:

  VERDICTS     how his judged bankers split: read right or wrong, beaten
               fairly, circumstances, misread.
  HOW SURE     placed against the price at each confidence -- the claims made
               before the off apart from the ones written after, because a
               confidence remembered after the result is not a forecast.
  WHOSE PICK   Kelvin's tickets carry legs other people chose.
  THE RECORD   how often the stewards' report or the comments on running name
               a circumstance for the beaten bankers he put down to
               circumstances, beside the ones he called a misread or beaten
               fairly. Blaming luck is the bias a review like this invites;
               this is the check on it, and it reads the same text for both
               sides, so it measures his calls and not the record's coverage.
  BY ACCOUNT   placed against the price for every key horse with a result,
               judged or not -- the yardstick the rest sits beside.

A RECORD, NOT A RANKING, as everywhere on this page: every line carries its
count and an interval, and one under `THIN` is marked thin.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from hkrd.derive.circumstances import CIRCUMSTANCES, FROM_RECORD
from hkrd.derive.probability import actual_over_expected
from hkrd.query.banker_review import (CONFIDENCE, PICKED_BY, SINCE, VERDICTS, key_horses,
                                      rows)
from hkrd.store.connect import Connection, get_conn

__all__ = ["THIN", "line", "summary"]

THIN = 30                      # bet_analysis.THIN_BETS


def line(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """Placed against the price's chance of placing, over rows that have one."""
    priced = [r for r in rs if r["run"]["chance"] is not None]
    placed = sum(1 for r in priced if r["run"]["placed"])
    ae = actual_over_expected(sum(r["run"]["chance"] for r in priced), placed, len(priced))
    return {"n": len(rs), "priced": len(priced), "placed": placed,
            "expected": ae["expected_wins"], "ae": ae["ae"], "ae_lo": ae["ae_lo"],
            "ae_hi": ae["ae_hi"], "thin": len(priced) < THIN}


def _named(rs: list[dict[str, Any]]) -> int:
    return sum(1 for r in rs if any(x["key"] in FROM_RECORD for x in r["record"]))


def summary(*, account: str | None = None, conn: Connection | None = None) -> dict[str, Any]:
    """The review's record over every key horse since `SINCE`."""
    own = conn is None
    conn = conn or get_conn()
    try:
        every = rows(conn, key_horses(conn, SINCE, "9999-12-31", account=account),
                     evidence=False)
    finally:
        if own:
            conn.close()
    run = [r for r in every if r["race"]["result"] and not r["run"]["withdrawn"]]
    judged = [r for r in run if r["review"]["verdict"]]
    beaten = [r for r in judged if r["run"]["placed"] is False]

    by_verdict = Counter(r["review"]["verdict"] for r in judged)
    record_side = []
    for key, label in (("circumstances", "You said circumstances"),
                       ("mistake", "You said you misread it"),
                       ("right_beaten", "You said beaten fairly"),
                       ("unsure", "You couldn't tell")):
        side = [r for r in beaten if r["review"]["verdict"] == key]
        record_side.append({"verdict": key, "label": label, "n": len(side),
                            "record_names_one": _named(side)})
    circ = [r for r in beaten if r["review"]["verdict"] == "circumstances"]
    named = []
    for key, label in CIRCUMSTANCES:
        said = [r for r in circ if key in r["review"]["circumstances"]]
        named.append({"key": key, "label": label, "you": len(said),
                      "from_record": key in FROM_RECORD,
                      "record_agrees": (sum(1 for r in said
                                            if any(x["key"] == key for x in r["record"]))
                                        if key in FROM_RECORD else None)})

    sure = []
    for key, label in (*CONFIDENCE, (None, "Not given")):
        for timing in ("before", "after"):
            rs = [r for r in run if r["review"]["confidence"] == key
                  and (r["review"]["claim_timing"] or "after") == timing]
            if rs or key is not None:
                sure.append({"confidence": key, "label": label, "timing": timing, **line(rs)})

    picks = [{"picked_by": key, "label": label,
              **line([r for r in run if "kelvin" in r["accounts"]
                      and (r["review"]["picked_by"] or None) == key])}
             for key, label in (*PICKED_BY, (None, "Not said"))]
    kinds = sorted({r["review"]["claim_kind"] for r in run if r["review"]["claim_kind"]})
    return {
        "since": SINCE, "account": account,
        "horses": len(every), "run": len(run), "judged": len(judged),
        "claimed": sum(1 for r in run if r["review"]["claim_timing"]),
        "claimed_before": sum(1 for r in run if r["review"]["claim_timing"] == "before"),
        "verdicts": [{"key": k, "label": label, "n": by_verdict.get(k, 0)}
                     for k, label, _ in VERDICTS],
        "record": {"beaten_judged": len(beaten), "sides": record_side, "named": named},
        "confidence": sure,
        "kelvin_picks": picks,
        "kinds": [{"kind": k, **line([r for r in run if r["review"]["claim_kind"] == k]),
                   "verdicts": dict(Counter(r["review"]["verdict"] for r in run
                                            if r["review"]["claim_kind"] == k
                                            and r["review"]["verdict"]))}
                  for k in kinds],
        "accounts": [{"account": a, **line([r for r in run if a in r["accounts"]])}
                     for a in ("brett", "personal", "kelvin")
                     if any(a in r["accounts"] for r in run)],
        "overall": line(run), "thin": THIN}

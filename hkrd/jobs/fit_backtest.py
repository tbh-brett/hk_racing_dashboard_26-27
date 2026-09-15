"""Recompute the published backtest figures — `model/backtest.MEASURED`.

    python -m hkrd.jobs.fit_backtest --db /data/hkrd.db

WHY A JOB AND NOT A COMMENT. `MEASURED` is rendered on the Model Analysis page
beside the live walk-forward, so a reader can see whether a rerun agrees with
what was published. That only works if the published block can be REGENERATED
rather than hand-edited: a constant nobody can reproduce is a claim, and this
project does not publish those. `jobs/fit_blend` does the same for the blend's
own constants.

IT WRITES NOTHING. It prints a block to paste into `model/backtest.py`, because
a job that edits source is a job that can be wrong in a file nobody rereads.
The figures it prints came from the database it was pointed at, and the header
says which selection produced them — the whole reason this exists is that the
selection changed on 2026-09-15 and every figure moved with it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hkrd.model import backtest as bt
from hkrd.store.connect import db_path, get_conn

__all__ = ["measure", "render"]

# The weight and edge pairs the published table carries. Each is a different
# question -- "how much model, and how much disagreement before we act" -- and
# the finding is that every one of them scores worse, so the grid is the
# evidence rather than an illustration of it.
GRID = ((0.05, 0.10), (0.10, 0.10), (0.10, 0.25), (0.25, 0.10), (1.00, 0.10))


def measure(db: Path | None = None) -> dict:
    """The `MEASURED` block, as the current archive produces it."""
    conn = get_conn(db if db is not None else db_path())
    try:
        out = bt.walk_forward(conn=conn)
        if not out.get("usable"):
            return {"error": "no usable races", "races": out.get("races", 0)}
        cov, cal = out["coverage"], out["calibration"]
        races = bt.races_for_backtest(conn=conn)
        test = [r for r in races if r["race_date"] >= out["split_date"]]

        by_weight = []
        for weight, edge in GRID:
            v = bt.value_bets(test, weight=weight, edge=edge)
            by_weight.append({
                "weight": weight, "edge": edge, "bets": v["bets"],
                "strike": v["strike_rate"], "roi": v["roi"]})
        at_fitted = bt.value_bets(test, weight=out["weight"], edge=0.0)

        partial = cov["races_with_an_unrated_runner"]
        return {
            "population": (
                f"every race with a complete book and two rated runners; "
                f"{partial:,} of {cov['races']:,} test races carry a runner "
                f"SARR did not rate"),
            "races": out["races"],
            "train_races": out["train_races"],
            "test_races": out["test_races"],
            "split_date": out["split_date"],
            "test_runners": cal["runners"],
            "brier": cal["brier"],
            "log_loss": cal["log_loss"],
            "off_bins": cal["off_bins"],
            "value_at_fitted_weight": {"bets": at_fitted["bets"],
                                       "roi": at_fitted["roi"]},
            "value_by_weight": by_weight,
            "reading": bt.MEASURED["reading"],
        }
    finally:
        conn.close()


def render(block: dict) -> str:
    """The block as Python, ready to replace `MEASURED` verbatim."""
    body = json.dumps(block, indent=4)
    # JSON's null is not Python's, and the value rows read better on one line
    # each -- which is how the constant is already written.
    body = body.replace(": null", ": None")
    return "MEASURED = " + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    block = measure(a.db)
    if "error" in block:
        print(f"  {block['error']} ({block['races']} races)")
        return 1

    print()
    print(f"  races usable       {block['races']:>7,}")
    print(f"  walk-forward split {block['split_date']}"
          f"  (train {block['train_races']:,} / test {block['test_races']:,})")
    print(f"  test runners       {block['test_runners']:>7,}")
    print(f"  brier              {block['brier']:>7.5f}")
    print(f"  log loss           {block['log_loss']:>7.4f}")
    print(f"  bins off the curve {block['off_bins']:>7,}")
    print(f"  population         {block['population']}")
    print()
    print("  value by weight and required edge")
    for row in block["value_by_weight"]:
        roi = "-" if row["roi"] is None else f"{row['roi']:+.3f}"
        print(f"    w={row['weight']:<5} edge={row['edge']:<5} "
              f"bets {row['bets']:>6,}  roi {roi}")
    print()
    print("  Paste this over MEASURED in hkrd/model/backtest.py:")
    print()
    print(render(block))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

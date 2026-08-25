"""Re-derive the blend calibration in hkrd/model/blend.py.

    python -m hkrd.jobs.fit_blend

Fits the softmax temperature and the blend weight on an earlier window and
reports the log loss on a later one, so the published numbers are out-of-sample
rather than the fit congratulating itself. Prints; it writes nothing. The
constants live in blend.py where they can be read next to what they mean, and
this job exists so nobody has to take them on trust.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from hkrd.model.blend import blend, fundamental_probability, market_probability
from hkrd.store.connect import db_path, get_conn

# Fractions of the fundamental stream to report beside the fitted one: zero,
# the handoff's own grid-search result, the artboard's placeholder, and the
# weight the old FUSE actually ran.
REPORT_WEIGHTS = (0.0, 0.1, 0.32, 1.0)


def _races(conn) -> list[tuple[str, np.ndarray, np.ndarray, np.ndarray]]:
    """Races where every finisher scored and every finisher was priced.

    A partial field breaks the arithmetic in both directions: a softmax over
    some of the runners and a de-vig over some of the prices are each
    normalised against a denominator that is missing terms.
    """
    scored: dict[tuple[str, int], list] = {}
    for r in conn.execute("""
        SELECT s.race_date, s.race_no, s.sarr, u.place, u.win_odds
        FROM runner_sarr s JOIN runners u USING (race_date, race_no, horse_no)
        WHERE u.place IS NOT NULL
    """):
        scored.setdefault((r["race_date"], r["race_no"]), []).append(
            (r["sarr"], r["place"], r["win_odds"]))

    sizes = {(r["race_date"], r["race_no"]): r["n"] for r in conn.execute(
        "SELECT race_date, race_no, count(*) n FROM runners "
        "WHERE place IS NOT NULL GROUP BY 1, 2")}

    out = []
    for key, runners in sorted(scored.items()):
        if len(runners) != sizes.get(key):
            continue
        if not any(p == 1 for _, p, _ in runners):
            continue
        if not all(o for _, _, o in runners):
            continue
        out.append((key[0],
                    np.array([x[0] for x in runners], dtype=float),
                    np.array([x[1] for x in runners]),
                    np.array([x[2] for x in runners], dtype=float)))
    return out


def _log_loss(races, probability) -> float:
    """Mean negative log likelihood of the horse that actually won."""
    return float(-np.mean([
        np.log(max(probability(r)[r[2] == 1][0], 1e-12)) for r in races]))


def run(db: Path | None = None, *, train_fraction: float = 0.6) -> dict:
    conn = get_conn(db if db is not None else db_path())
    try:
        races = _races(conn)
    finally:
        conn.close()
    if len(races) < 20:
        return {"races": len(races), "error": "not enough complete races to fit"}

    split = races[int(len(races) * train_fraction)][0]
    train = [r for r in races if r[0] < split]
    test = [r for r in races if r[0] >= split]

    beta = float(min(np.arange(0.25, 10.01, 0.25),
                     key=lambda b: _log_loss(
                         train, lambda r, b=b: fundamental_probability(r[1], b))))

    fund = lambda r: fundamental_probability(r[1], beta)
    mkt = lambda r: market_probability(r[3])
    weight = float(min(np.arange(0.0, 1.001, 0.01),
                       key=lambda w: _log_loss(
                           train, lambda r, w=w: blend(fund(r), mkt(r), w))))

    return {
        "races": len(races), "train_races": len(train), "test_races": len(test),
        "split_date": split, "beta": beta, "fitted_weight": round(weight, 2),
        "log_loss": {
            "uniform": _log_loss(test, lambda r: np.full(r[1].size, 1 / r[1].size)),
            "fundamental": _log_loss(test, fund),
            "market": _log_loss(test, mkt),
        },
        "log_loss_by_weight": {
            f"{w:.2f}": _log_loss(test, lambda r, w=w: blend(fund(r), mkt(r), w))
            for w in sorted({*REPORT_WEIGHTS, round(weight, 2)})},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    out = run(a.db)
    if "error" in out:
        print(f"  {out['error']} ({out['races']} races)")
        return 1
    print(f"  complete races     {out['races']:>7,}")
    print(f"  walk-forward split {out['split_date']}"
          f"  (train {out['train_races']:,} / test {out['test_races']:,})")
    print(f"  softmax beta       {out['beta']:>7.2f}")
    print(f"  fitted weight      {out['fitted_weight']:>7.2f}  (on the fundamental)")
    print("  test log loss")
    for name, value in out["log_loss"].items():
        print(f"    {name:<16} {value:.4f}")
    print("  test log loss by blend weight")
    for w, value in sorted(out["log_loss_by_weight"].items()):
        print(f"    w={w:<6} {value:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

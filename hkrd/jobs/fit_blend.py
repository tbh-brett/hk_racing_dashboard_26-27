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
    """Races with a settled result and a COMPLETE BOOK. SARR may be short.

    The de-vig genuinely needs every price -- the overround is the gap between
    the book and 100%, so a book missing a runner has a gap that is partly the
    missing runner -- and an unpriced field is dropped for that reason.

    The fundamental stream used to impose the same requirement, which quietly
    made this a fit on a DIFFERENT POPULATION from the one the page runs on.
    SARR wants two prior runs before it rates a horse, so requiring a fully
    scored field selects for races without debutants: 660 of the archive's
    1,712, and the other 1,052 were never in the window the weight was chosen
    in. An unrated runner now arrives as NaN and is carried, so the fit sees
    the cards the reader sees.
    """
    scored: dict[tuple[str, int], list] = {}
    for r in conn.execute("""
        SELECT u.race_date, u.race_no, s.sarr, u.place, u.win_odds
        FROM runners u LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
        WHERE u.place IS NOT NULL
    """):
        scored.setdefault((r["race_date"], r["race_no"]), []).append(
            (np.nan if r["sarr"] is None else r["sarr"], r["place"], r["win_odds"]))

    out = []
    for key, runners in sorted(scored.items()):
        if not any(p == 1 for _, p, _ in runners):
            continue
        if not all(o for _, _, o in runners):
            continue
        sarr = np.array([x[0] for x in runners], dtype=float)
        # Two rated runners is the least that makes a softmax mean anything.
        if int((~np.isnan(sarr)).sum()) < 2:
            continue
        out.append((key[0], sarr,
                    np.array([x[1] for x in runners]),
                    np.array([x[2] for x in runners], dtype=float)))
    return out


def _complete(race) -> bool:
    """Every runner rated -- the population the published figures were fitted
    on before this job could carry a partial field."""
    return not bool(np.isnan(race[1]).any())


def _fund(race, beta: float) -> np.ndarray:
    """The fundamental stream for one race, scaled to the market's own share of
    the runners it rated. On a fully rated field that share is 1.0 and this is
    the plain softmax, which is why the published beta survives the change."""
    mkt = market_probability(race[3])
    rated = ~np.isnan(race[1])
    return fundamental_probability(race[1], beta, mass=float(mkt[rated].sum()))


def _log_loss(races, probability) -> float:
    """Mean negative log likelihood of the horse that actually won.

    A DEAD HEAT HAS TWO OF THEM and neither is more the winner than the other,
    so the race contributes the mean of their log likelihoods. It used to
    contribute whichever one came first in the row order, which made a
    published constant depend on a join: reading the archive through `runners`
    rather than through `runner_sarr` moved the market's test loss from 2.0466
    to 2.0545 with no model change at all. Three of the archive's 1,712 races
    are dead heats and two of them fall after the split, which is enough to
    move the fourth decimal on a 266-race mean.
    """
    return float(-np.mean([
        np.mean(np.log(np.maximum(probability(r)[r[2] == 1], 1e-12)))
        for r in races]))


def run(db: Path | None = None, *, train_fraction: float = 0.6) -> dict:
    conn = get_conn(db if db is not None else db_path())
    try:
        races = _races(conn)
    finally:
        conn.close()
    full = [r for r in races if _complete(r)]
    if len(full) < 20:
        return {"races": len(full), "error": "not enough complete races to fit"}

    # The split is taken on the COMPLETE races so the headline figures stay
    # comparable to the ones they replace, and the same date then splits the
    # wider set -- one boundary, not two.
    split = full[int(len(full) * train_fraction)][0]
    before = lambda rs: [r for r in rs if r[0] < split]
    after = lambda rs: [r for r in rs if r[0] >= split]
    train, test = before(full), after(full)

    # beta is fitted on fully rated fields only. It is the temperature of the
    # fundamental stream ALONE, and on a partial field that stream borrows the
    # market for the runners it cannot rate -- fitting it there would tune a
    # constant of one model against a number the other model supplied.
    beta = float(min(np.arange(0.25, 10.01, 0.25),
                     key=lambda b: _log_loss(
                         train, lambda r, b=b: fundamental_probability(r[1], b))))

    fund = lambda r: _fund(r, beta)
    mkt = lambda r: market_probability(r[3])
    grid = np.arange(0.0, 1.001, 0.01)
    fit_on = lambda rs: float(min(grid, key=lambda w: _log_loss(
        rs, lambda r, w=w: blend(fund(r), mkt(r), w))))
    losses = lambda rs, w: _log_loss(rs, lambda r: blend(fund(r), mkt(r), w))

    weight = fit_on(train)
    weight_all = fit_on(before(races))

    return {
        "races": len(full), "train_races": len(train), "test_races": len(test),
        "split_date": split, "beta": beta, "fitted_weight": round(weight, 2),
        "log_loss": {
            "uniform": _log_loss(test, lambda r: np.full(r[1].size, 1 / r[1].size)),
            "fundamental": _log_loss(test, fund),
            "market": _log_loss(test, mkt),
        },
        "log_loss_by_weight": {
            f"{w:.2f}": losses(test, w)
            for w in sorted({*REPORT_WEIGHTS, round(weight, 2)})},
        # The same grid over every race with a complete book, rated field or
        # not. This is the population the page actually runs on and it is
        # nearly three times the size of the one above; if the two disagree
        # about the weight, the disagreement is the finding.
        "partial": {
            "races": len(races),
            "test_races": len(after(races)),
            "fitted_weight": round(weight_all, 2),
            "log_loss_by_weight": {
                f"{w:.2f}": losses(after(races), w)
                for w in sorted({*REPORT_WEIGHTS, round(weight_all, 2)})},
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    out = run(a.db)
    if "error" in out:
        print(f"  {out['error']} ({out['races']} races)")
        return 1
    print(f"  complete races     {out['races']:>7,}"
          f"   of {out['partial']['races']:,} with a complete book")
    print(f"  walk-forward split {out['split_date']}"
          f"  (train {out['train_races']:,} / test {out['test_races']:,})")
    print(f"  softmax beta       {out['beta']:>7.2f}")
    print(f"  fitted weight      {out['fitted_weight']:>7.2f}  (on the fundamental)")
    print("  test log loss")
    for name, value in out["log_loss"].items():
        print(f"    {name:<16} {value:.4f}")
    print("  test log loss by blend weight, fully rated fields")
    for w, value in sorted(out["log_loss_by_weight"].items()):
        print(f"    w={w:<6} {value:.4f}")
    part = out["partial"]
    print(f"  every race with a complete book  ({part['races']:,} races, "
          f"test {part['test_races']:,})")
    print(f"    fitted weight    {part['fitted_weight']:>7.2f}")
    for w, value in sorted(part["log_loss_by_weight"].items()):
        print(f"    w={w:<6} {value:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Whether a meeting's bias carries from its early races to its later ones -> `day_bias_record`.

    python -m hkrd.jobs.fit_day_bias

On every settled meeting from 2019 (model/day_bias): each meeting's first half against its second,
on all going and on rain-affected going, and the day's read carried into the next race's chances
against the price alone. About forty seconds; run when the record should take in a new stretch of
the season. The Speed Map's TODAY'S RACES box shows the newest row beside the day's own read.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np

from hkrd.jobs.fit_shape import history
from hkrd.model import day_bias
from hkrd.store import day as store
from hkrd.store import job_log
from hkrd.store.connect import Connection, get_conn, init_db, transaction

__all__ = ["frame", "run"]

FIRST_SEASON = 2019


def frame(conn: Connection, *, before: str):
    """Every settled run since 2019 with its covariates: the realized front, the draw, the rail
    turning in from the Comments on Running, and the expected front from habitual early speed."""
    ex = history(conn, before=before)
    d = ex[(ex.season >= FIRST_SEASON) & ex.p_mkt.notna() & ex.early_frac.notna() & ex.place.notna()].copy()
    d["rail_obs"] = np.where(d.lane_turn.notna(), (d.lane_turn <= 1).astype(float), np.nan)
    rank = d.groupby("race_id").hab_early.rank(method="average")
    d["front_exp"] = (1 - (rank - 1) / (d.field - 1).clip(lower=1)).fillna(0.5)
    d["won"] = (d.place == 1).astype(float)
    d = d[d.groupby("race_id").won.transform("sum") == 1]          # a dead heat has two winners
    d["race_no"] = d.race_no.astype(int)
    return day_bias.covariates(d)


def run(db: Path | None = None, *, today: str | None = None) -> str:
    today = today or dt.date.today().isoformat()
    conn = get_conn(db)
    try:
        init_db(conn)
        d = frame(conn, before=today)
        rec = day_bias.record(d)
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        with transaction(conn):
            store.save_record(conn, fitted_at=now, through=str(d.race_date.max()), record=rec)
    finally:
        conn.close()
    p, q = rec["persistence"], rec["prediction"]
    return (f"fit_day_bias: {p['meetings']} meetings; first half vs second r = "
            + ", ".join(f"{c} {p[c]['r']:+.3f}" for c in day_bias.COLS)
            + f"; the day's read on the next race {q['gain_per_race']:+.4f} log loss a race (+/- {q['se']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    with job_log.running("fit_day_bias", a.db) as outcome:
        outcome["detail"] = run(a.db)
        outcome["ok"] = True
    print(outcome["detail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

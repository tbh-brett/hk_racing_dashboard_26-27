"""Score a stored card with the newest shape models -> `runner_shape` (racesim-SPEC §6).

    python -m hkrd.jobs.score_shape --date 2026-10-25
    python -m hkrd.jobs.score_shape --pending          # every card still to run

Beside `score_gbm`, from `project_card` and `raceday_card`. The card's declared facts and the
model are fingerprinted first, so a card that has not changed costs one query and builds nothing;
a scratching or a new rider rescores it. A race that has gone off is never scored again: what the
simulation read at the off is its record (jobs/sim_card).

Each horse's slow-start rate is its record in the Comments on Running -- runs with a comment only,
HKJC's placeholder is not one -- shrunk towards the population's by eight runs (card_inputs.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from hkrd.model import shape, shape_frame, sim_tables
from hkrd.store import gbm as gbm_store
from hkrd.store import shape as store
from hkrd.store.connect import Connection, get_conn, init_db

__all__ = ["score", "score_pending", "main", "ScoreReport"]

_FACTS = ("race_no", "horse_no", "horse_name", "draw", "jockey", "gear", "venue", "surface",
          "course", "distance", "race_class")


@dataclass
class ScoreReport:
    scored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  shape scored     {s}" for s in self.scored]
        lines += [f"  shape unchanged  {d}" for d in self.unchanged]
        lines += [f"  shape note       {n}" for n in self.notes]
        lines += [f"  shape ERROR      {e}" for e in self.errors]
        return "\n".join(lines) or "  shape: no card waiting to be scored"


def _key(card: pd.DataFrame, version: str) -> str:
    facts = card[list(_FACTS)].astype(object).where(card[list(_FACTS)].notna(), None).values.tolist()
    blob = json.dumps([version, facts], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _score(conn: Connection, date: str, report: ScoreReport) -> None:
    version = store.latest_shape_version(conn)
    if version is None:
        report.notes.append(f"{date}: no shape model fitted yet -- run jobs.fit_shape")
        return
    unrun = {r["race_no"] for r in gbm_store.unrun_races(conn, date)}
    card = store.card_runs(conn, date)
    card = card[card.race_no.isin(unrun)].reset_index(drop=True)
    if card.empty:
        return
    key = _key(card, version)
    if store.shape_keys(conn, date) == {key}:
        report.unchanged.append(date)
        return
    t = shape_frame.build(store.load_runs(conn, before=date))
    t["is_card"] = 0
    ex = shape.features(pd.concat([t, shape.card_rows(card)], ignore_index=True, sort=False))
    C = ex[ex.is_card == 1]
    models = store.shape_models(conn, version)               # twenty boosters: only when scoring
    boosters = {k: [lgb.Booster(model_str=s) for s in models[k]["model_text"]] for k in shape.KINDS}
    P = shape.predict(boosters, C).merge(C[["race_id", "horse_no", "horse_name", "n_prior"]], on=["race_id", "horse_no"])
    season = int(shape_frame.season_of(pd.Series(pd.to_datetime([date])))[0])
    base = sim_tables.slow_base(t, last_season=season - 1)
    rec = t[t.has_comment == 1].groupby("horse_name").slow_bad.agg(["sum", "size"])
    P = P.merge(rec, left_on="horse_name", right_index=True, how="left").fillna({"sum": 0, "size": 0})
    k = sim_tables.SLOW_PRIOR_RUNS
    P["slow_rate"] = (k * base + P["sum"]) / (k + P["size"])
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows = [dict(race_date=date, race_no=int(r.race_no), horse_no=int(r.horse_no), pred_early=float(r.pred_early),
                 p_lead=float(r.p_lead), exp_lane=float(r.exp_lane), p_nocover=float(r.p_nocover),
                 slow_rate=float(r.slow_rate), n_prior=int(r.n_prior), model_version=version,
                 inputs_key=key, scored_at=now) for r in P.itertuples()]
    n = store.write_shape_scores(conn, date, sorted(unrun), rows)
    report.scored.append(f"{date}: {n} runners in {len(set(P.race_no))} races with {version}")


def score(date: str, db: Path | None = None) -> ScoreReport:
    """Score one meeting's unrun races, if anything they depend on has changed."""
    report = ScoreReport()
    conn = get_conn(db)
    try:
        init_db(conn)
        _score(conn, date, report)
    except Exception as exc:                   # noqa: BLE001 - recorded, per date
        report.errors.append(f"{date}: {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return report


def score_pending(db: Path | None = None, *, today: str | None = None) -> ScoreReport:
    """Every meeting from today on with a race still to run."""
    report = ScoreReport()
    conn = get_conn(db)
    try:
        init_db(conn)
        dates = gbm_store.unrun_dates(conn, today=today or dt.date.today().isoformat())
    finally:
        conn.close()
    for date in dates:
        one = score(date, db)
        for k in ("scored", "unchanged", "notes", "errors"):
            getattr(report, k).extend(getattr(one, k))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--date", help="YYYY-MM-DD")
    which.add_argument("--pending", action="store_true", help="every card still to run")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    report = score_pending(a.db) if a.pending else score(a.date, a.db)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Refit the four shape models after a settled meeting -> `shape_models` (racesim-SPEC §6).

    python -m hkrd.jobs.fit_shape --pending      # only when a meeting has settled since the last fit
    python -m hkrd.jobs.fit_shape                # now

TWO FITS, SO A MEETING COSTS A THIRD OF THE LAB'S. The lab fitted every model three times a card:
five seeds early-stopped on the last settled season to find their rounds, then refitted to date for
the card, and again to the season before for the check (3 min of two threads on the 25 Oct card).
Within a season the first and the last never change, so they are fitted once a season and kept as
'shape-1.0+check-<season before>': each seed's rounds, and boosters that stop at the season before.
After each settled meeting only the card models are refitted (to date, with those rounds), and the
season so far is scored with the kept check boosters -- §6's record, on every fit's rows.

WHEN. Its own crontab line before dawn, at nice 19, so a fit never shares the CPU with a race
night's scrape and derive (Fly's baseline is 12.5% of one core; ops/crontab). `--pending` is one
query on a day with nothing new. After a fit every stored card is rescored (jobs/score_shape).
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from hkrd.jobs import score_shape
from hkrd.model import shape, shape_frame
from hkrd.store import gbm as gbm_store
from hkrd.store import job_log
from hkrd.store import shape as store
from hkrd.store.connect import Connection, get_conn, init_db, transaction

__all__ = ["pending", "run", "main", "FitReport", "history"]


@dataclass
class FitReport:
    version: str = ""
    check: str = ""             # "fitted" | "kept <version>"
    runs: int = 0
    rounds: dict[str, list[int]] = field(default_factory=dict)
    record: dict = field(default_factory=dict)
    seconds: float = 0.0
    skipped: str = ""

    def render(self) -> str:
        if self.skipped:
            return f"fit_shape: {self.skipped}"
        e = self.record.get("early", {})
        vs = (f"; 26/27 first call off {e['mae_model']} places against the Speed Map's {e['mae_speed_map']} "
              f"({e['runners']} runners, z {e['z']})") if e.get("runners") else ""
        return (f"fit_shape {self.version}: {self.runs:,} runs, check fit {self.check}, rounds "
                + ", ".join(f"{k} {sum(v) // len(v)}" for k, v in self.rounds.items()) + vs
                + f" ({self.seconds:.0f}s)")


def _day_after(date: str) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()


def history(conn: Connection, *, before: str) -> pd.DataFrame:
    """Every settled run before a date as the shape models read it: the trip frame and its
    pre-race features."""
    t = shape_frame.build(store.load_runs(conn, before=before))
    t["is_card"] = 0
    return shape.features(t)


def pending(db: Path | None = None, *, today: str | None = None) -> bool:
    """Has a meeting settled since the newest card fit was trained?"""
    today = today or dt.date.today().isoformat()
    conn = get_conn(db)
    try:
        init_db(conn)
        dates = gbm_store.settled_dates(conn, through=today, today=today)
        version = store.latest_shape_version(conn)
        return bool(dates) and (version is None or dates[-1] > version.split("+", 1)[1])
    finally:
        conn.close()


def _rows(version: str, through: str, boosters: dict[str, list[lgb.Booster]], rounds: dict[str, list[int]],
          record: dict, now: str) -> list[dict]:
    params = {"early": shape.REG, "lane": shape.REG, "nocover": shape.REG, "lead": shape.LEAD}
    return [dict(version=version, kind=k, trained_through=through, created_at=now, record=record.get(k, {}),
                 params=dict(params=params[k], seeds=list(shape.SEEDS), rounds=rounds[k],
                             lightgbm=lgb.__version__, features=shape.RUNNER),
                 model_text=[b.model_to_string() for b in boosters[k]]) for k in shape.KINDS]


def run(db: Path | None = None, *, today: str | None = None) -> FitReport:
    t0 = time.time()
    report = FitReport()
    today = today or dt.date.today().isoformat()
    conn = get_conn(db)
    try:
        init_db(conn)
        dates = gbm_store.settled_dates(conn, through=today, today=today)
        if not dates:
            report.skipped = "no settled meeting to fit on"
            return report
        last = dates[-1]
        season = int(shape_frame.season_of(pd.Series(pd.to_datetime([last])))[0])
        hist = history(conn, before=_day_after(last))
        if not ({season - 1, season - 2} <= set(hist.season.unique())):
            report.skipped = f"not enough history to fit: it needs settled runs in {season - 2} and {season - 1}"
            return report
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        check_version = f"{shape.VERSION}+check-{season - 1}"
        check = store.shape_models(conn, check_version)
        if len(check) == len(shape.KINDS) and all(check[k]["model_text"] for k in shape.KINDS):
            report.check = f"kept {check_version}"
            rounds = {k: check[k]["params"]["rounds"] for k in shape.KINDS}
            check_b = {k: [lgb.Booster(model_str=s) for s in check[k]["model_text"]] for k in shape.KINDS}
        else:
            report.check = "fitted"
            rounds = {k: shape.fit_rounds(hist, k, season=season) for k in shape.KINDS}
            check_b = {k: shape.fit_final(hist, k, rounds[k], train_to=season - 1) for k in shape.KINDS}
            with transaction(conn):
                store.save_shape_models(conn, _rows(check_version, f"{season - 1} season", check_b, rounds, {}, now))
        boosters = {k: shape.fit_final(hist, k, rounds[k], train_to=season) for k in shape.KINDS}
        rows = hist[hist.season == season]
        if rows.early_frac.notna().any():
            lane_train = hist[hist.lane_s4.notna() & (hist.season >= shape.FIRST_SEASON) & (hist.season <= season - 1)]
            report.record = shape.checks(rows, shape.predict(check_b, rows), store.speed_map(conn), lane_train)
        report.version, report.rounds = f"{shape.VERSION}+{last}", rounds
        report.runs = int(((hist.season >= shape.FIRST_SEASON) & (hist.season <= season)).sum())
        with transaction(conn):
            store.save_shape_models(conn, _rows(report.version, last, boosters, rounds, report.record, now))
    finally:
        conn.close()
    report.seconds = time.time() - t0
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pending", action="store_true", help="only if a meeting has settled since the last fit")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.pending and not pending(a.db):
        print("fit_shape: no meeting has settled since the last fit")
        return 0
    with job_log.running("fit_shape", a.db) as outcome:
        report = run(a.db)
        # every stored card is rescored with the new fit at once, so the simulation's next tick
        # reads it rather than waiting for project_card
        scored = score_shape.score_pending(a.db) if report.version else None
        outcome["ok"] = not (scored and scored.errors)
        outcome["detail"] = "; ".join([report.render(), *(scored.scored + scored.errors if scored else [])])
    print(report.render())
    if scored:
        print(scored.render())
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

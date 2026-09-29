"""Train the fundamental model on every settled meeting; promote it only through the gate.

    python -m hkrd.jobs.fit_gbm [--db PATH] [--through YYYY-MM-DD] [--record]
    python -m hkrd.jobs.fit_gbm --promote VERSION      # by hand, after review

One fit a night after a meeting settles (gbm-SPEC §5, §14.8: a model retrained
before each meeting beat one frozen in July by 0.031 a race). In order:

1. The inputs for every settled run (`derive/features`).
2. How many trees: early stopping on the most recent season not in progress,
   trained on the seasons before it, plus a tenth -- the lab's rule, which
   gave 412 on the 26/27 replay.
3. The fit on everything from 2020-21 to the last settled meeting.
4. The walk-forward record, rebuilt only when the recipe or the seasons it
   covers change (a minute of work); otherwise carried from the last fit.
5. The gate (`model/gbm_record.gate`): the new recipe fitted on races before
   the last eight meetings, against the current model's recipe fitted the same
   way. Once `runner_gbm` holds what the page showed for those meetings, the
   benchmark becomes that (step 3 of the build). The first fit is promoted.

Every run reports its counts, and a fit that is not promoted says why: that is
a normal night, and the page keeps the model it has.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from hkrd.derive import features
from hkrd.model import gbm, gbm_record
from hkrd.store import gbm as store
from hkrd.store import job_log
from hkrd.store.connect import get_conn, init_db, transaction

__all__ = ["run", "main", "FitReport"]


@dataclass
class FitReport:
    version: str = ""
    trained_through: str = ""
    runs: int = 0
    races: int = 0
    rounds: int = 0
    record: str = ""            # "built" | "carried from <version>"
    promoted: bool = False
    reason: str = ""
    seconds: float = 0.0
    peak_mb: float | None = None
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.errors:
            return "fit_gbm: " + "; ".join(self.errors)
        mem = f", peak {self.peak_mb:.0f} MB" if self.peak_mb else ""
        return (f"fit_gbm {self.version}: {self.runs:,} runs in {self.races:,} races, "
                f"{self.rounds} trees, record {self.record}; "
                f"{'PROMOTED' if self.promoted else 'not promoted'} -- {self.reason} "
                f"({self.seconds:.0f}s{mem})")


def peak_mb() -> float | None:
    """This process's peak resident memory (Linux; None elsewhere)."""
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            return next(int(x.split()[1]) / 1024 for x in f if x.startswith("VmHWM:"))
    except (OSError, StopIteration):
        return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _same_recipe(a: dict, b: dict) -> bool:
    keys = ("recipe", "params", "lightgbm", "first_train")
    return all(a.get(k) == b.get(k) for k in keys)


def _gate(frame: pd.DataFrame, dates: list[str], rounds: int, live: dict | None) -> dict:
    """The candidate recipe against the live model's, on the last eight meetings."""
    if live is None:
        return {"promote": True, "reason": "the first model", "benchmark": None}
    window = dates[-gbm_record.GATE_MEETINGS:]
    rd = frame["race_date"].to_numpy()
    before = (frame["season"].to_numpy() >= gbm.FIRST_TRAIN) & (rd < window[0])
    test = np.isin(rd, window)
    if live["features_version"] != features.DERIVE_VERSION or \
            live["params"].get("lightgbm") != gbm.recipe(0)["lightgbm"]:
        return {"promote": False, "benchmark": None,
                "reason": "the live model's inputs or library differ and no shown forecasts "
                          "cover the window yet: review, then --promote by hand"}
    part = frame.loc[test, ["race_id", "y", "won"]].reset_index(drop=True)
    races = gbm.Races(part["race_id"].to_numpy())
    p = gbm.predict(gbm.fit(frame, before, rounds=rounds), frame, test)
    # the trees count here, unlike for carrying the record (which picks its own)
    same = _same_recipe(gbm.recipe(rounds), live["params"]) and rounds == live["rounds"]
    q = p if same else gbm.predict(
        gbm.fit(frame, before, rounds=live["rounds"], params=live["params"]["params"]),
        frame, test)
    y = part["y"].to_numpy()
    verdict = gbm_record.gate(races.nll(p, y), races.nll(q, y), p, part["won"].to_numpy(),
                              benchmark=f"the live recipe ({live['version']})")
    return {**verdict, "window": [window[0], window[-1]], "same_recipe": same}


def run(db: Path | None = None, *, through: str | None = None, rebuild_record: bool = False,
        today: str | None = None) -> FitReport:
    t0 = time.time()
    report = FitReport()
    conn = get_conn(db)
    try:
        init_db(conn)
        today = today or dt.date.today().isoformat()
        dates = store.settled_dates(conn, through=through or today, today=today)
        if not dates:
            report.errors.append("no settled meeting to train on")
            return report
        last = dates[-1]
        frame = features.build(store.load_runs(conn, through=last))
        frame = frame[~frame["is_card"]].reset_index(drop=True)
        season = frame["season"].to_numpy()
        now = int(season.max())
        inner = (season >= gbm.FIRST_TRAIN) & (season <= now - 2)
        rounds = gbm.choose_rounds(frame, inner, season == now - 1)
        train = season >= gbm.FIRST_TRAIN
        booster = gbm.fit(frame, train, rounds=rounds)
        report.runs = int(train.sum())
        report.races = int(frame.loc[train, "race_id"].nunique())
        report.rounds, report.trained_through = rounds, last
        report.version = f"{gbm.RECIPE}+{last}"
        recipe = gbm.recipe(rounds)

        live = store.live_model(conn)
        prior = live or store.model_row(conn)
        seasons = list(range(2021, now))
        carry = (prior is not None and not rebuild_record
                 and _same_recipe(recipe, prior["params"])
                 and prior["features_version"] == features.DERIVE_VERSION
                 and prior["record"].get("walk_forward", {}).get("test_seasons")
                 == [f"{s}-{(s + 1) % 100:02d}" for s in seasons])
        if carry:
            wf = prior["record"]["walk_forward"]
            report.record = f"carried from {prior['version']}"
        else:
            wf = gbm_record.record(gbm_record.walk_forward(frame, seasons))
            report.record = "built"
        verdict = _gate(frame, dates, rounds, live)
        report.promoted, report.reason = bool(verdict["promote"]), verdict["reason"]
        at = _now()
        with transaction(conn):
            store.save_model(conn, {
                "version": report.version, "kind": gbm.KIND, "trained_through": last,
                "features_version": features.DERIVE_VERSION, "params": recipe,
                "rounds": rounds, "model_text": booster.model_to_string(),
                "record": {"walk_forward": wf, "gate": verdict, "meetings": []},
                "created_at": at})
            if report.promoted:
                store.promote(conn, report.version, at=at)
    finally:
        conn.close()
    report.seconds, report.peak_mb = time.time() - t0, peak_mb()
    return report


def promote_by_hand(version: str, db: Path | None = None) -> str:
    conn = get_conn(db)
    try:
        init_db(conn)
        if store.model_row(conn, version) is None:
            return f"no fit {version}"
        with transaction(conn):
            store.promote(conn, version, at=_now())
        return f"{version} promoted by hand"
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--through", help="train on meetings up to this date (YYYY-MM-DD)")
    ap.add_argument("--record", action="store_true", help="rebuild the walk-forward record")
    ap.add_argument("--promote", metavar="VERSION", help="make a stored fit live, by hand")
    a = ap.parse_args(argv)
    if a.promote:
        print(promote_by_hand(a.promote, a.db))
        return 0
    with job_log.running("fit_gbm", a.db) as outcome:
        report = run(a.db, through=a.through, rebuild_record=a.record)
        outcome["ok"] = not report.errors
        outcome["detail"] = report.render()
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

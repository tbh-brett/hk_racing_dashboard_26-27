"""Replay meetings as the model would have seen them: the leak test that runs the whole path.

    python -m hkrd.jobs.replay_gbm --db COPY.db [--dates 2026-09-06,2026-09-09,...]

gbm-SPEC §14.2. For each meeting: cut a copy of the database at the day
(`store/asof`), rebuild every input from that copy alone, choose the trees and
fit exactly as `jobs/fit_gbm` would that morning, and score the card twice --
race morning (body weights and going published) and the night before
(neither). Then check each card's inputs against the full archive's, and score
both reads against what happened.

Run it on a COPY (`ops/pull-db.ps1`); it writes nothing to the database it is
given. The six 26/27 meetings the lab replayed must give R² 0.145 (morning) and
0.143 (night before), 16 top picks and 29 winners in the top three, within
0.005 R².
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from hkrd.derive import features
from hkrd.model import gbm
from hkrd.store import asof
from hkrd.store import gbm as store
from hkrd.store.connect import get_conn

__all__ = ["replay", "score", "main", "REPLAY_DATES"]

REPLAY_DATES = ["2026-09-06", "2026-09-09", "2026-09-13", "2026-09-16", "2026-09-23",
                "2026-09-27"]


def _inputs(db: Path, date: str | None = None) -> pd.DataFrame:
    conn = get_conn(db)
    try:
        return features.build(store.load_runs(conn, through=date or "9999-12-31"),
                              card_dates=(date,) if date else ())
    finally:
        conn.close()


def _card(db: Path, cut: Path, date: str, *, night: bool) -> tuple[pd.DataFrame, dict]:
    checks = asof.cut_copy(db, cut, date, night_before=night)
    frame = _inputs(cut, date)
    for side in ("", "-wal", "-shm"):
        Path(f"{cut}{side}").unlink(missing_ok=True)
    return frame, checks


def replay(db: Path, dates: list[str]) -> pd.DataFrame:
    """Per runner: the morning and night-before chances, as each would have
    been produced on the day, plus the audit columns."""
    full = _inputs(db)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        cut = Path(tmp) / "asof.db"
        for date in dates:
            morning, checks = _card(db, cut, date, night=False)
            season = morning["season"].to_numpy()
            hist = ~morning["is_card"].to_numpy()
            now = int(season[~hist].max())
            rounds = gbm.choose_rounds(morning, hist & (season >= gbm.FIRST_TRAIN)
                                       & (season <= now - 2), hist & (season == now - 1))
            booster = gbm.fit(morning, hist & (season >= gbm.FIRST_TRAIN), rounds=rounds)
            card = morning.loc[~hist].reset_index(drop=True)
            ref = full[full["race_date"] == date].set_index(["race_id", "horse_no"])
            mine = card.set_index(["race_id", "horse_no"])[gbm.INPUTS]
            theirs = ref.loc[mine.index, gbm.INPUTS]
            same = (mine.astype(str) == theirs.astype(str)) | (mine.isna() & theirs.isna())
            night, _ = _card(db, cut, date, night=True)
            filled = gbm.assume_body_weight(night)
            night = night.loc[night["is_card"]].reset_index(drop=True)
            if not night[["race_id", "horse_no"]].equals(card[["race_id", "horse_no"]]):
                raise ValueError(f"{date}: the night-before card is not the morning's field")
            card["p_morning"] = gbm.predict(booster, card)
            card["p_night"] = gbm.predict(booster, night)
            res = ref.loc[mine.index, ["y", "won", "p_mkt"]].reset_index(drop=True)
            out.append(card[["race_id", "race_date", "race_no", "horse_no", "horse_name",
                             "p_morning", "p_night"]].join(res).assign(
                rounds=rounds, trained_through=morning.loc[hist, "race_date"].max(),
                inputs_differ=int((~same.all(axis=0)).sum()), body_weight_assumed=filled,
                result_cells=checks["result_cells_on_card"], after=checks["runners_after"]))
    return pd.concat(out, ignore_index=True)


def score(x: pd.DataFrame) -> dict:
    races = gbm.Races(x["race_id"].to_numpy())
    y, unif = x["y"].to_numpy(), np.log(races.counts).sum()
    out: dict = {"races": len(races.starts), "runners": len(x)}
    for col, name in (("p_morning", "morning"), ("p_night", "night"), ("p_mkt", "market")):
        rank = x.groupby("race_id")[col].rank(ascending=False, method="first")[x["won"] == 1]
        out[f"r2_{name}"] = 1 - races.nll(x[col].to_numpy(), y).sum() / unif
        out[f"top1_{name}"], out[f"top3_{name}"] = int((rank == 1).sum()), int((rank <= 3).sum())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True, help="a COPY of the database")
    ap.add_argument("--dates", default=",".join(REPLAY_DATES))
    ap.add_argument("--out", type=Path, help="write every runner's chances to this CSV")
    a = ap.parse_args(argv)
    x = replay(a.db, a.dates.split(","))
    if a.out:
        x.to_csv(a.out, index=False)
    rows = [{"meeting": d, **score(g)} for d, g in x.groupby("race_date")]
    rows.append({"meeting": "all", **score(x)})
    audit = x.groupby("race_date")[["rounds", "inputs_differ", "body_weight_assumed",
                                    "result_cells", "after"]].first()
    pd.set_option("display.width", 200)
    print(audit.to_string(), "\n")
    print(pd.DataFrame(rows).round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

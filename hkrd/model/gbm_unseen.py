"""What the fundamental model cannot see, named beside its number and never in it.

The Briefing shows these under a runner, each with its measured record against
the model's own chance -- read from the live model's walk-forward record
(`model/gbm_record`, the `unseen` table), never a typed number:

- a barrier trial since the last run, rated STANDOUT, POSITIVE or NEGATIVE by
  `derive/trial_quality`. The model reads no trials (gbm-SPEC §14.9): the
  archive holds them from 2025-26 only, one season in five.
- a new stable: a different trainer from the last start. The model reads each
  stable's last year, not the move between two of them.

WHY THESE AND NOT THE OLD SCREEN'S OTHER FACTS. Tested 30 Sep 2026 over 3,365
races, each season scored by a model that had never seen it: a STANDOUT trial
since the last run won 2.0x what the model gave (192 runs), POSITIVE 1.31x,
NEGATIVE 0.63x, a new stable 1.36x (743 runs). The rest of the old Screen's
named facts -- first-up, a wide trip, an excuse, a draw or rating move, class,
first-time gear -- came out at about 1.00 against the model: it already carries
them. Mixing the old Screen into the model's number gained nothing (+0.0006 a
race, se 0.0036), so the facts are said beside the number, not added to it.

Against the closing price every one of them is near 1.00: the market knows
them. They are context, not bets. And the trial bands' phrases were chosen on
the same 2025-26 trials the figures come from, so until 26/27 has settled the
trial figures are flattered; the record says how many runs each rests on.

Pure Python on the page path. `mark` (pandas) is for the fit job only.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

__all__ = ["UNSEEN", "TRIAL_WINDOW_DAYS", "trial_since", "new_stable", "facts", "mark"]

# A trial counts when it came after the last run AND within this many days of
# the race -- the old Screen's window, kept so its measurements carry over.
TRIAL_WINDOW_DAYS = 60

# In the order the Briefing lists them.
UNSEEN: dict[str, dict[str, str]] = {
    "trial_standout": {"label": "STANDOUT trial",
                       "note": "a trial rated STANDOUT since its last run; the model reads no trials"},
    "trial_positive": {"label": "POSITIVE trial",
                       "note": "a trial rated POSITIVE since its last run; the model reads no trials"},
    "trial_negative": {"label": "NEGATIVE trial",
                       "note": "a trial rated NEGATIVE since its last run; the model reads no trials"},
    "new_stable": {"label": "new stable",
                   "note": "a different trainer from its last start; the model reads each "
                           "stable's year, not the move between them"},
}
_BY_BAND = {"STANDOUT": "trial_standout", "POSITIVE": "trial_positive",
            "NEGATIVE": "trial_negative"}


def _day(value: Any) -> str:
    return str(value)[:10]


def trial_since(race_date: str, last_run: str | None,
                trials: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The newest trial after the last run and within the window, or None.
    `trials` newest first, each with a `trial_date`."""
    floor = (dt.date.fromisoformat(_day(race_date))
             - dt.timedelta(days=TRIAL_WINDOW_DAYS)).isoformat()
    if last_run and _day(last_run) > floor:
        floor = _day(last_run)
    return next((t for t in trials if floor < _day(t["trial_date"]) < _day(race_date)), None)


def _name(s: str | None) -> str:
    return " ".join((s or "").upper().split())


def new_stable(trainer: str | None, last_trainer: str | None) -> bool:
    """A different trainer from the last start. Unknown on either side is not a move."""
    return bool(_name(trainer) and _name(last_trainer) and _name(trainer) != _name(last_trainer))


def facts(race_date: str, last_run: str | None, trainer: str | None,
          last_trainer: str | None, trials: Sequence[Mapping[str, Any]]
          ) -> list[tuple[str, Mapping[str, Any] | None]]:
    """The facts that hold for one runner, in `UNSEEN` order, each with the
    trial it rests on (None for a new stable). `trials` newest first, rated
    (`band`)."""
    out: list[tuple[str, Mapping[str, Any] | None]] = []
    t = trial_since(race_date, last_run, trials)
    key = _BY_BAND.get((t or {}).get("band"))
    if key:
        out.append((key, t))
    if new_stable(trainer, last_trainer):
        out.append(("new_stable", None))
    return out


def mark(starts: pd.DataFrame, trials: Mapping[str, Sequence[Mapping[str, Any]]]
         ) -> pd.DataFrame:
    """One boolean column per fact, for every starter in `starts` (race_date,
    race_no, horse_no, horse_name, trainer; scratched runners already out),
    keyed on (race_date, race_no, horse_no). The same `facts` the page calls,
    so the record measures exactly what the Briefing shows."""
    s = starts.sort_values(["horse_name", "race_date", "race_no"], kind="stable")
    g = s.groupby("horse_name", sort=False)
    last_date, last_trainer = g["race_date"].shift(), g["trainer"].shift()
    on = {k: [] for k in UNSEEN}
    for date, name, trainer, ld, lt in zip(s["race_date"], s["horse_name"], s["trainer"],
                                            last_date, last_trainer):
        held = {k for k, _ in facts(date, None if pd.isna(ld) else ld, trainer,
                                    None if pd.isna(lt) else lt, trials.get(name, ()))}
        for k in UNSEEN:
            on[k].append(k in held)
    out = s[["race_date", "race_no", "horse_no"]].copy()
    for k, v in on.items():
        out[k] = v
    return out.reset_index(drop=True)

"""What the fundamental model reads from a horse's barrier trials (recipe gbm-1.1).

Measured 9 Nov 2026 over seven seasons, with the trial archive fetched back to
May 2019 (25,381 starts had a trial since their previous run): the closing
price already knows what a trial says about WHO went well -- trial winners win
at 1.00x their price -- but the model read no trials, and gave trial winners
34% too little and debutants the least of all. Walk-forward, the live recipe
with trial inputs added scored +0.014 to +0.019 a race better in every season
(t 5 to 6); 2025-26 R2 0.135 to 0.144. docs/gbm.md has the measurement.

ONE TRIAL: the latest since the previous start and within `WINDOW_DAYS`, plus
how many there were. Ex ante, like every other input: a trial counts only when
it was run on an EARLIER day than the race. A race-morning trial is on no card
scored that day, and `store/asof` cuts trials at the day as it cuts runs.

What is read, and why:
    tr_days        days from the trial to the race. Fresher is better: about
                   1% of the field per ten days, in both eras.
    tr_n           trials since the last start.
    tr_early       where it sat at the first call, as a share of the batch.
    tr_fin         where it finished, as a share of the batch.
    tr_late_gain   places made from the second-last call to the line.
    tr_behind      seconds behind the batch's fastest.
    tr_vs_morning  seconds against the median runner that morning over the same
                   course and trip. The time read that held in both eras: a
                   second faster finished 1.5% of the field better than its
                   price. Against the morning rather than a course standard,
                   so no later trial sets the bar.
    tr_effort      0 not asked, 1 asked, 2 hard ridden -- HKJC's comment.
    tr_finish      0 weak, 1 fair, 2 strong finishing words -- HKJC's comment.
    tr_at_ch       trialled at Conghua: 1.16x the price over seven seasons,
                   and the model missed it as badly.
    tr_at_hv       trialled at Happy Valley.
Missing is NaN, as everywhere in the model: no trial since the last start, a
comment that says nothing about effort, a run with no time.

The Speed Map's shape models (model/shape, shape-1.1) read the same trial, for
where a horse will sit: `tr_early`, `tr_days`, `tr_n` and one more read, the
comment's word for the break, `tr_break` (1 good, 0 average, -1 slow, -2
awkward) -- not a model input here, so the fundamental model is unchanged.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

__all__ = ["INPUTS", "WINDOW_DAYS", "READS", "reads", "attach", "effort", "finish_words",
           "break_word", "venue_of"]

WINDOW_DAYS = 120
INPUTS = ["tr_days", "tr_n", "tr_early", "tr_fin", "tr_late_gain", "tr_behind",
          "tr_vs_morning", "tr_effort", "tr_finish", "tr_at_ch", "tr_at_hv"]
# What is read off each trial run; the first two inputs are about the gap to the race.
READS = [*INPUTS[2:], "tr_break"]


# ── HKJC's comment: was it asked, and what did it find ────────────────────────
#
# The vocabulary is the 9 Nov study's, read over 48,120 comments. Matched on
# word boundaries, so "hard ridden" is not read inside "not hard ridden" and
# "impressive" is not "unimpressive". The strongest named wins: a comment that
# says both "under a hold" and "hard ridden" was ridden.

def _rx(words: tuple[str, ...]) -> re.Pattern:
    return re.compile("|".join(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])" for w in words))


_HARD = _rx(("hard ridden", "under whip", "vigorously ridden", "under pressure", "strongly ridden",
             "ridden out", "driven", "rousted", "under strong urging", "shown the whip",
             "under the whip", "vigorously", "full stretch", "hard-ridden", "stick"))
_ASKED = _rx(("urged", "asked", "given reminders", "reminders", "niggled", "persuaded",
              "pushed along", "ridden along", "hands and heels", "shaken up", "nudged",
              "encouraged", "let go", "ridden for", "ridden in the straight", "ridden to"))
_NOT_ASKED = _rx(("under a hold", "not tested", "not fully tested", "just to stretch out",
                  "to stretch", "stretching out", "own steam", "ridden conservatively",
                  "under restraint", "just to pass trial", "to pass trial", "eased", "not asked",
                  "restrained", "under a tight hold", "not extended", "without being asked",
                  "not pushed", "not ridden out", "hard held", "cruised", "on the bit"))
# Urging to LEAD is about the start, not the finish; and a denial is not an ask.
_EARLY = re.compile(r"urged to lead|ridden along early|niggled along early|pushed along early|"
                    r"urged early|ridden early|urged along early|ridden along at start")
_DENIED = re.compile(r"not asked|without being asked|not ridden out|not pushed|not hard ridden")

_STRONG = _rx(("drew away", "quickened", "ran on strongly", "stayed on strongly", "ran on well",
               "stayed on well", "ran on nicely", "ran on smoothly", "ran on comfortably",
               "stayed on comfortably", "won cozily", "won comfortably", "won easily",
               "won convincingly", "dashed on", "hit the line strongly", "made rapid progress",
               "accelerated", "finished strongly", "kept on well", "strong finish",
               "good turn of foot", "sprinted", "finished off well", "ran on impressively",
               "kept on strongly", "ran on resolutely", "ran on gamely", "finished off strongly",
               "made good progress", "ran home well", "quickened nicely", "stayed on nicely",
               "kept on nicely", "won well", "scored easily", "ran on boldly", "ran on keenly"))
_WEAK = _rx(("limited response", "failed to respond", "ran on ordinary", "weakened", "gave ground",
             "tailed off", "finished last", "never in contention", "not much progress",
             "one-paced", "one paced", "no response", "faded", "dropped out", "tired",
             "struggled", "outpaced", "no progress", "found little", "made no progress",
             "dropped back", "lost touch", "ran on ordinarily", "finished off ordinary",
             "plodded", "never threatened", "not much response", "little response",
             "flattened out", "dropped away"))
_FAIR = _rx(("kept on", "stayed on fairly", "ran on fairly", "ran on ok", "ran on evenly",
             "kept on evenly", "made progress", "held ground", "not beaten far",
             "finished off evenly", "made steady progress", "made minor inroads", "plugged on",
             "stayed on", "ran on", "held on", "battled on", "kept going", "finished off fairly",
             "came from behind", "hit the front", "kept up"))


_BREAK = {
    -2.0: _rx(("jumped awkwardly", "stumbled", "jumped clumsily", "bounded on jumping", "blundered",
               "reared", "awkward at start", "awkward start", "began awkwardly", "jumped out",
               "jumped in", "hit the gate", "bumped at start", "jumped inwards", "jumped outwards",
               "dived", "propped")),
    -1.0: _rx(("slightly slow to begin", "slow to begin", "tardy start", "last to begin",
               "slowly away", "missed the start", "missed the kick", "dwelt", "began slowly",
               "slow away", "slow start", "slow to jump", "jumped slowly", "slow into stride",
               "lost ground at start", "lost ground at the start", "tardy")),
    1.0: _rx(("jumped well", "good early speed", "first to begin", "began well", "began fast",
              "quickly away", "smartly away", "jumped smartly", "broke well", "jumped fast",
              "fast to begin", "quick to begin", "quickly into stride", "began quickly",
              "jumped quickly", "began smartly", "good start", "jumped sharply", "began sharply")),
    0.0: _rx(("average to begin", "began fairly", "jumped fairly", "began ok", "fair start")),
}


def break_word(comment: object) -> float:
    """1 a good break, 0 average, -1 slow, -2 awkward, NaN when unstated; an
    awkward or slow start outranks a good word elsewhere in the comment."""
    if not isinstance(comment, str):
        return np.nan
    low = comment.lower()
    return next((v for v, rx in _BREAK.items() if rx.search(low)), np.nan)


def effort(comment: object) -> float:
    """2 hard ridden, 1 asked, 0 not asked, NaN when the comment says nothing."""
    if not isinstance(comment, str):
        return np.nan
    low = _EARLY.sub(" ", comment.lower())
    asked = _DENIED.sub(" ", low)
    if _HARD.search(asked):
        return 2.0
    if _ASKED.search(asked):
        return 1.0
    return 0.0 if _NOT_ASKED.search(low) else np.nan


def finish_words(comment: object) -> float:
    """2 strong, 0 weak, 1 fair (or both said), NaN when it says nothing."""
    if not isinstance(comment, str):
        return np.nan
    low = comment.lower()
    strong, weak = bool(_STRONG.search(low)), bool(_WEAK.search(low))
    if strong != weak:
        return 2.0 if strong else 0.0
    if strong or _FAIR.search(low):
        return 1.0
    return np.nan


# ── one row per trial run ─────────────────────────────────────────────────────

def venue_of(course: pd.Series, venue: pd.Series) -> pd.Series:
    """ST, HV or CH from the course name HKJC prints, the stored venue where
    the name says none. The legacy import stored Conghua trials as 'HV'."""
    c = course.fillna("").str.upper()
    out = venue.astype(object).where(venue.notna(), "?").copy()
    out[c.str.contains("SHA TIN")] = "ST"
    out[c.str.contains("HAPPY VALLEY")] = "HV"
    out[c.str.contains("CONGHUA")] = "CH"
    return out


def reads(trials: pd.DataFrame) -> pd.DataFrame:
    """What the model reads from each trial run: `horse_name`, `trial_date`
    (Timestamp) and the per-trial inputs. `trials` holds the table's columns
    (trial_date, trial_no, horse_name, venue, course, distance, finish_time,
    running_positions, comment_text)."""
    if trials.empty:
        return pd.DataFrame(columns=["horse_name", "trial_date", *READS])
    t = trials.reset_index(drop=True)
    venue = venue_of(t["course"], t["venue"])
    batch = [t["trial_date"], venue, t["trial_no"]]
    field = t.groupby(batch)["horse_name"].transform("size")
    span = (field - 1).clip(lower=1)
    calls = t["running_positions"].fillna("").str.split()
    first = pd.to_numeric(calls.str[0], errors="coerce")
    last = pd.to_numeric(calls.str[-1], errors="coerce")
    second_last = pd.to_numeric(calls.map(lambda p: p[-2] if len(p) >= 2 else None),
                                errors="coerce")
    ft = pd.to_numeric(t["finish_time"], errors="coerce")
    ft = ft.where((ft > 30) & (ft < 200))         # a time is a time, not a parse error
    morning = [t["trial_date"], t["course"].fillna("").str.upper(), t["distance"]]
    return pd.DataFrame({
        "horse_name": t["horse_name"],
        "trial_date": pd.to_datetime(t["trial_date"]),
        "tr_early": (first - 1) / span,
        "tr_fin": (last - 1) / span,
        "tr_late_gain": second_last - last,
        "tr_behind": ft - ft.groupby(batch).transform("min"),
        "tr_vs_morning": ft - ft.groupby(morning).transform("median"),
        "tr_effort": t["comment_text"].map(effort).astype(float),
        "tr_finish": t["comment_text"].map(finish_words).astype(float),
        "tr_at_ch": (venue == "CH").astype(float),
        "tr_at_hv": (venue == "HV").astype(float),
        "tr_break": t["comment_text"].map(break_word).astype(float),
    })


# ── the trial each run had ────────────────────────────────────────────────────

def attach(horse: pd.Series, date: pd.Series, prev_date: pd.Series,
           trial_reads: pd.DataFrame) -> pd.DataFrame:
    """For each run (or declared runner): the latest trial strictly before
    `date`, after `prev_date` (the horse's previous start, NaT for a debut) and
    within `WINDOW_DAYS`; and how many such trials it had. One row per input
    row, in its order, with `INPUTS` and `tr_break` -- NaN where there was no trial."""
    ns = "datetime64[ns]"            # one resolution on both sides of every as-of join
    rows = pd.DataFrame({"horse_name": horse.to_numpy(),
                         "date": pd.to_datetime(date).astype(ns).to_numpy(),
                         "prev": pd.to_datetime(prev_date).astype(ns).to_numpy(),
                         "row": np.arange(len(horse))})
    out = pd.DataFrame(np.nan, index=np.arange(len(horse)), columns=["tr_days", "tr_n", *READS])
    if trial_reads.empty or rows.empty:
        return out
    t = trial_reads.assign(trial_date=pd.to_datetime(trial_reads["trial_date"]).astype(ns))
    t = t.sort_values(["horse_name", "trial_date"], kind="stable").reset_index(drop=True)
    t["k"] = t.groupby("horse_name").cumcount()         # the horse's trials, oldest first
    rows["floor"] = (rows["date"] - pd.Timedelta(days=WINDOW_DAYS)).where(
        rows["prev"].isna() | (rows["prev"] < rows["date"] - pd.Timedelta(days=WINDOW_DAYS)),
        rows["prev"])
    by_date = rows.sort_values("date", kind="stable")
    latest = pd.merge_asof(by_date, t.sort_values("trial_date", kind="stable"),
                           left_on="date", right_on="trial_date", by="horse_name",
                           direction="backward", allow_exact_matches=False)
    # the last trial on or before the floor: everything after it, up to the
    # latest, is inside the window
    floor = pd.merge_asof(by_date[["row", "horse_name", "floor"]].sort_values("floor"),
                          t[["horse_name", "trial_date", "k"]].sort_values("trial_date"),
                          left_on="floor", right_on="trial_date", by="horse_name",
                          direction="backward", allow_exact_matches=True)
    latest = latest.set_index("row").sort_index()
    below = floor.set_index("row")["k"].reindex(latest.index).fillna(-1)
    ok = latest["trial_date"].notna() & (latest["trial_date"] > latest["floor"])
    out.loc[ok.to_numpy(), READS] = latest.loc[ok, READS].to_numpy(dtype=float)
    out.loc[ok.to_numpy(), "tr_days"] = (latest.loc[ok, "date"]
                                         - latest.loc[ok, "trial_date"]).dt.days.to_numpy()
    out.loc[ok.to_numpy(), "tr_n"] = (latest.loc[ok, "k"] - below[ok]).to_numpy(dtype=float)
    return out

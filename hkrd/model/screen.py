"""The Screen — who is a chance in a race, read before there is a price.

Brett, 2026-09-24: parse the whole dashboard for the manual information and
say which horses are a chance, "without me going through every single race
every selection", before the odds are out. Gate, pace crowding, a good trial,
better circumstances than last time, an excuse for the last run, a swing
against a horse that beat it.

EVERY FACTOR HERE WAS MEASURED, AND MOST OF THE OBVIOUS ONES FAILED. Each was
tested as a multiplier on a horse's chance ON TOP OF its SARR rating, so a
factor earns a place only by saying something the form figures do not. The
fit is an exploded (top-three) logit over 2020-21 to 2025-26 and was scored
walk-forward: trained on the seasons before, tested on the next, every season.
What survived, with what did not beside it:

  kept   the jockey (the largest thing SARR cannot see), a trial rated
         POSITIVE or better since the last run, being the ONLY habitual
         leader, finishing in the back 60% last start (SARR under-reads it),
         second-up after running 7th or worse first-up, raced wide last start,
         a real veterinary finding, rating moved by the handicapper, a better
         or worse draw than last time, a new stable
  gone   class DROP (x1.02 -- a drop is the handicapper saying the horse is
         struggling), a trip change, "finished off well" and "weakened" once
         the finishing position is counted, first-time gear, a trial under
         the same jockey or in the same gear, weight relief on its own

`FACTORS` holds each one's fitted weight and how many runs carried it, and
`jobs/fit_screen` re-derives every number here from the same code path the
page runs, so nobody has to take a multiplier on trust.

WHAT IT IS NOT. A value finder. Walk-forward, where the screen rates a horse
well above the closing tote, the tote has been right: A/E 0.91 where the
screen says 1.25-2x the tote's chance and 0.86 beyond that, over 20,600
runners. The screen replaces reading every race to find the four or
five horses with a case. Once prices exist, a big gap between the two is more
often the market knowing something than the screen finding something.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from hkrd.derive.probability import place_from_win

__all__ = ["VERSION", "Factor", "FACTORS", "BY_KEY", "FIT", "SPELL_DAYS",
           "TRIAL_WINDOW_DAYS", "JOCKEY_WINDOW_DAYS", "class_number", "stage",
           "jockey_rate", "call_flags", "features", "race_context", "score_race",
           "places_paid"]

VERSION = "screen-1.0"

# A gap this long starts a new campaign. The season diagnostic's definition,
# and the second-up finding holds at 45, 60 and 90 days alike.
SPELL_DAYS = 60
# A trial counts when it came after the last run, or within this many days
# when there has been no run since (first-up).
TRIAL_WINDOW_DAYS = 60
# "Beaten" last start: finished behind this share of its field.
BEATEN_SHARE = 0.4
DRAW_MOVE = 4          # gates, relative to the last start
RATING_MOVE = 3        # handicap points since the last start
SECOND_UP_BAD_PLACE = 7
# The jockey's win rate over the year before the race, shrunk toward the
# year's overall rate by this many rides so a rider on his tenth ride of the
# season is not a 30% jockey.
JOCKEY_WINDOW_DAYS = 365
JOCKEY_PRIOR_RIDES = 50
JOCKEY_REFERENCE = 0.08

# The stewards' own words for a run that was compromised by others.
HARD_TROUBLE = frozenset({"held_up", "checked", "hampered", "short_of_room",
                          "crowded", "steadied"})
WIDE_TAGS = frozenset({"wide", "without_cover"})
# Findings about the horse, not about the trip. `bled` is deliberately absent:
# measured, a bleeder's next run is no worse than its form says (x1.07).
VET_BAD = frozenset({"roarer", "lame_fore", "lame_hind", "vet_finding",
                     "arrhythmia"})

# HKJC's race call writes about trouble in words the stewards' vocabulary
# (`derive/tags`) never needed: "blocked near 200M", "3 wide no cover".
_CALL_BLOCKED = re.compile(
    r"\bblocked\b|\bno room\b|\bheld up\b|\bdenied\b|\bshort of room\b"
    r"|\bno clear run\b|\bin traffic\b|\bpocketed\b|\bboxed\b", re.I)
_CALL_WIDE = re.compile(r"\bwide\b|\bdeep\b|\bwithout cover\b", re.I)


@dataclass(frozen=True)
class Factor:
    """One measured reason. `weight` is the fitted log-multiplier on the
    horse's strength; `runs` is how many runs in the fit carried it."""

    key: str
    group: str        # FORM RIDER CAMPAIGN LAST-START PACE TRIAL CHANGE CONTROL
    label: str        # what the page says when it fires
    weight: float
    runs: int
    caveat: str | None = None

    @property
    def multiplier(self) -> float:
        return math.exp(self.weight)

    @property
    def shown(self) -> bool:
        """Controls correct the fit for gaps in the archive and are scored,
        but are not a reason anybody would read."""
        return self.group != "CONTROL"


_ONE_SEASON = "one season of trials in the archive; refit as 26/27 accrues"
_TWO_SEASONS = "HKJC ratings are in the archive from 2024-25 only"

# jobs/fit_screen, 2026-09-24: every settled race from 2020-21 to 23 Sep 2026.
# The walk-forward figures are the seasons 2022-23 to 26/27 so far, each
# scored by weights fitted only on the seasons before it.
FIT: dict[str, Any] = {
    "fitted": "2026-09-24",
    "seasons": "2020-21 to 2026-27",
    "races": 5019,
    "runs": 61007,
    "test_races": 3366,
    "top3_has_winner": {"screen": 0.521, "form": 0.487, "market": 0.621},
    "top4_has_winner": {"screen": 0.624, "form": 0.586, "market": 0.710},
    # A/E against the closing tote where the screen rates a horse 1.25-2x and
    # 2x+ what the tote does: the tote has been right.
    "above_tote_ae": {"x1.25-2": 0.910, "x2+": 0.855, "runs": 20600},
}

FACTORS: tuple[Factor, ...] = (
    Factor("form", "FORM", "SARR rating, per standard deviation above the field", 0.466, 0),
    Factor("jockey", "RIDER", "Jockey's strike rate over the last year", 0.489, 0),
    Factor("unrated", "CONTROL", "No SARR rating yet", -0.147, 6024),
    Factor("prev_no_comment", "CONTROL", "No race comments on the last start", 0.092, 9933),
    Factor("debut", "CAMPAIGN", "Debut", -0.396, 3020),
    Factor("first_up", "CAMPAIGN", "First-up from a spell", -0.119, 7985),
    Factor("second_up_bad", "CAMPAIGN", "Second-up after running 7th or worse first-up", -0.322, 4746),
    Factor("deep_campaign", "CAMPAIGN", "Fifth run or later this campaign", 0.070, 27332),
    Factor("prev_beaten", "LAST-START", "Beaten out of the frame last start", -0.318, 32660),
    Factor("prev_excuse_beaten", "LAST-START", "Beaten last start, but held up, checked or blocked", 0.056, 6574),
    Factor("prev_wide", "LAST-START", "Raced wide last start", 0.104, 17923),
    Factor("prev_vet", "LAST-START", "Veterinary finding after the last start", -0.277, 606),
    Factor("leader_alone", "PACE", "The only habitual leader in the race", 0.246, 1297),
    Factor("leader_pair", "PACE", "One of two habitual leaders", 0.215, 2938),
    Factor("leader_crowd", "PACE", "One of three or more habitual leaders", 0.017, 5537),
    Factor("on_pace", "PACE", "Habitually races on the pace", 0.088, 7431),
    Factor("closer", "PACE", "Habitually races at the back", -0.095, 26674),
    Factor("trial_good", "TRIAL", "Trial rated POSITIVE or STANDOUT since the last run", 0.513, 832, _ONE_SEASON),
    Factor("trial_bad", "TRIAL", "Trial rated NEGATIVE since the last run", -0.308, 682, _ONE_SEASON),
    Factor("rating_up", "CHANGE", "Rating up 3+ since the last start", 0.161, 1584, _TWO_SEASONS),
    Factor("rating_down", "CHANGE", "Rating down 3+ since the last start", -0.253, 908, _TWO_SEASONS),
    Factor("class_rise", "CHANGE", "Up in class", 0.083, 2692),
    Factor("draw_in", "CHANGE", "Drawn 4+ gates further in than last start", 0.105, 15059),
    Factor("draw_out", "CHANGE", "Drawn 4+ gates further out than last start", -0.119, 15111),
    Factor("venue_change", "CHANGE", "Other course from last start", -0.086, 15480),
    Factor("trainer_change", "CHANGE", "New stable since last start", 0.244, 1119),
)
BY_KEY: dict[str, Factor] = {f.key: f for f in FACTORS}


# ── pieces ──────────────────────────────────────────────────────────────────

def class_number(race_class: str | None) -> float | None:
    """HKJC class as a number where LOWER is better: Group races 0, Class 1-5,
    Griffin 6. None for anything else (an international, a missing header)."""
    if race_class is None:
        return None
    c = str(race_class).strip()
    if c in {"1", "2", "3", "4", "5"}:
        return float(c)
    if c == "0" or "group" in c.lower():
        return 0.0
    if "griffin" in c.lower():
        return 6.0
    return None


def _days(later: str, earlier: str) -> int:
    return (dt.date.fromisoformat(str(later)[:10])
            - dt.date.fromisoformat(str(earlier)[:10])).days


def stage(race_date: str, prior_dates: Sequence[str]) -> int:
    """Run number in the current campaign: 0 debut, 1 first-up, 2 second-up.
    `prior_dates` newest first, strictly before `race_date`."""
    if not prior_dates:
        return 0
    n, later = 1, race_date
    for earlier in prior_dates:
        if _days(later, earlier) >= SPELL_DAYS:
            break
        n, later = n + 1, earlier
    return n


def jockey_rate(wins: int, rides: int, base: float) -> float:
    """The rider's year, shrunk toward the year's overall rate."""
    return (wins + JOCKEY_PRIOR_RIDES * base) / (rides + JOCKEY_PRIOR_RIDES)


def call_flags(text: str | None) -> dict[str, bool]:
    """What HKJC's race call said about trouble, in its own words."""
    t = text or ""
    return {"blocked": bool(_CALL_BLOCKED.search(t)), "wide": bool(_CALL_WIDE.search(t))}


def places_paid(field_size: int) -> int:
    """Three places in a field of seven or more, two below that."""
    return 3 if field_size >= 7 else 2


def _logit(p: float) -> float:
    p = min(max(p, 0.01), 0.5)
    return math.log(p / (1 - p))


# ── one runner ─────────────────────────────────────────────────────────────

def features(r: Mapping[str, Any], race: Mapping[str, Any]
             ) -> tuple[dict[str, float], dict[str, str]]:
    """One runner's factor values, and a sentence for each one that fired.

    `r` is `query/screen_inputs`' runner: the card line (draw, jockey,
    trainer, rating), `sarr`, `style`, the rider's year (`j_wins`, `j_rides`,
    `j_base`), `prior_dates` newest first, `prev` (the last run, or None) and
    `trials` newest first. `race` carries the card's date, venue and class
    and the field's SARR mean and spread and habitual-leader count.

    Nothing here reads a result or a price. `place` and `win_odds` travel on
    the same dict for the fit job and are never touched.
    """
    v: dict[str, float] = {f.key: 0.0 for f in FACTORS}
    why: dict[str, str] = {}
    date = race["race_date"]

    sarr, sd = r.get("sarr"), race.get("sarr_sd")
    if sarr is None:
        v["unrated"] = 1.0
        why["unrated"] = "fewer than two runs with sectionals"
    elif sd:
        v["form"] = -(sarr - race["sarr_mean"]) / sd

    rate = jockey_rate(r.get("j_wins") or 0, r.get("j_rides") or 0,
                       race.get("j_base") or JOCKEY_REFERENCE)
    v["jockey"] = _logit(rate) - _logit(JOCKEY_REFERENCE)
    why["jockey"] = (f"{r.get('jockey') or 'rider'} {100 * rate:.0f}% "
                     f"({r.get('j_wins') or 0} from {r.get('j_rides') or 0} last year)")

    prior = r.get("prior_dates") or []
    run_no = stage(date, prior)
    prev = r.get("prev")
    if run_no == 0:
        v["debut"] = 1.0
        why["debut"] = "first start"
    elif run_no == 1:
        v["first_up"] = 1.0
        why["first_up"] = f"{_days(date, prior[0])} days since the last run"
    elif run_no >= 5:
        v["deep_campaign"] = 1.0
        why["deep_campaign"] = f"run {run_no} of this campaign"
    if run_no == 2 and prev and (prev.get("place") or 0) >= SECOND_UP_BAD_PLACE:
        v["second_up_bad"] = 1.0
        why["second_up_bad"] = f"ran {prev['place']} of {prev.get('field_size')} first-up"

    if prev:
        _last_start(v, why, r, prev)

    _pace(v, why, r.get("style"), race.get("n_leaders") or 0)
    _trial(v, why, date, prev, r.get("trials") or [])
    return v, why


def _last_start(v: dict, why: dict, r: Mapping[str, Any],
                prev: Mapping[str, Any]) -> None:
    tags = set(prev.get("tags") or ())
    call = prev.get("running_comment")
    flags = call_flags(call)
    when = prev["race_date"]
    if not call and not prev.get("incident_comment") and not tags:
        v["prev_no_comment"] = 1.0
    place, field = prev.get("place"), prev.get("field_size") or 0
    beaten = bool(place and field > 1 and (place - 1) / (field - 1) > BEATEN_SHARE)
    if beaten:
        v["prev_beaten"] = 1.0
        why["prev_beaten"] = f"{place} of {field} on {when}"
        hard = sorted(tags & HARD_TROUBLE)
        if hard or flags["blocked"]:
            v["prev_excuse_beaten"] = 1.0
            # Tags are read off both HKJC texts (`jobs/rebuild_tags`), so the
            # sentence names HKJC and not the stewards.
            why["prev_excuse_beaten"] = (
                "HKJC: " + ", ".join(t.replace("_", " ") for t in hard)
                if hard else "race call: blocked for a run")
    wide = sorted(tags & WIDE_TAGS)
    if wide or flags["wide"]:
        v["prev_wide"] = 1.0
        why["prev_wide"] = ("HKJC: " + ", ".join(t.replace("_", " ") for t in wide)
                            if wide else "race call: raced wide")
    vet = sorted(tags & VET_BAD)
    if vet:
        v["prev_vet"] = 1.0
        why["prev_vet"] = ", ".join(t.replace("_", " ") for t in vet) + f" on {when}"

    if r.get("rating") is not None and prev.get("rating") is not None:
        move = r["rating"] - prev["rating"]
        if move >= RATING_MOVE:
            v["rating_up"], why["rating_up"] = 1.0, f"{prev['rating']} to {r['rating']}"
        elif move <= -RATING_MOVE:
            v["rating_down"], why["rating_down"] = 1.0, f"{prev['rating']} to {r['rating']}"
    now, then = class_number(r.get("race_class")), class_number(prev.get("race_class"))
    if now is not None and then is not None and now < then:
        v["class_rise"] = 1.0
        why["class_rise"] = f"class {prev.get('race_class')} to {r.get('race_class')}"
    if r.get("draw") is not None and prev.get("draw") is not None:
        move = r["draw"] - prev["draw"]
        if move <= -DRAW_MOVE:
            v["draw_in"], why["draw_in"] = 1.0, f"gate {prev['draw']} to {r['draw']}"
        elif move >= DRAW_MOVE:
            v["draw_out"], why["draw_out"] = 1.0, f"gate {prev['draw']} to {r['draw']}"
    if prev.get("venue") and r.get("venue") and prev["venue"] != r["venue"]:
        v["venue_change"] = 1.0
        why["venue_change"] = f"{prev['venue']} last time"
    if prev.get("trainer") and r.get("trainer") and prev["trainer"] != r["trainer"]:
        v["trainer_change"] = 1.0
        why["trainer_change"] = f"from {prev['trainer']}"


def _pace(v: dict, why: dict, style: str | None, leaders: int) -> None:
    if style == "Leader":
        key = ("leader_alone" if leaders <= 1 else
               "leader_pair" if leaders == 2 else "leader_crowd")
        v[key] = 1.0
        why[key] = ("no other habitual leader" if leaders <= 1 else
                    f"{leaders} habitual leaders in the race")
    elif style == "On-Pace":
        v["on_pace"], why["on_pace"] = 1.0, "habitually races handy"
    elif style == "Closer":
        v["closer"], why["closer"] = 1.0, "habitually races at the back"


def _trial(v: dict, why: dict, date: str, prev: Mapping[str, Any] | None,
           trials: Sequence[Mapping[str, Any]]) -> None:
    floor = (dt.date.fromisoformat(date[:10])
             - dt.timedelta(days=TRIAL_WINDOW_DAYS)).isoformat()
    if prev and prev["race_date"] > floor:
        floor = prev["race_date"]
    recent = [t for t in trials if floor < str(t["trial_date"]) < date]
    if not recent:
        return
    t = recent[0]
    band = t.get("band")
    text = f"{band} trial {t['trial_date']}" + (f" ({t['place']} of {t['field_size']})"
                                                 if t.get("place") else "")
    if band in ("STANDOUT", "POSITIVE"):
        v["trial_good"], why["trial_good"] = 1.0, text
    elif band == "NEGATIVE":
        v["trial_bad"], why["trial_bad"] = 1.0, text


# ── one race ───────────────────────────────────────────────────────────────

def race_context(race: Mapping[str, Any], runners: Sequence[Mapping[str, Any]]
                 ) -> dict[str, Any]:
    """The field-level numbers every runner's features are measured against:
    SARR's mean and spread over the rated runners, and the habitual leaders."""
    rated = [x["sarr"] for x in runners if x.get("sarr") is not None]
    out = dict(race)
    out["sarr_mean"] = statistics.fmean(rated) if rated else None
    out["sarr_sd"] = statistics.stdev(rated) if len(rated) >= 2 else None
    out["n_leaders"] = sum(1 for x in runners if x.get("style") == "Leader")
    return out


def score_race(race: Mapping[str, Any], runners: Sequence[Mapping[str, Any]]
               ) -> list[dict[str, Any]]:
    """Every runner's factors, strength, and chance to win and to place.

    Chances are within THIS field: a softmax over the strengths for the win,
    Harville-Henery for the places, never a linear scaling.
    """
    ctx = race_context(race, runners)
    out = []
    for r in runners:
        values, why = features(r, ctx)
        parts = {k: BY_KEY[k].weight * x for k, x in values.items() if x}
        out.append({"horse_no": r.get("horse_no"), "values": values, "why": why,
                    "parts": parts, "score": sum(parts.values())})
    if not out:
        return out
    s = np.array([x["score"] for x in out])
    win = np.exp(s - s.max())
    win = win / win.sum()
    place = place_from_win(win, places=places_paid(len(out)))
    for x, w, p in zip(out, win, place):
        x["win"], x["place"] = float(w), float(p)
    return out

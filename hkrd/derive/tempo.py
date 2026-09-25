"""How fast a race was run, read against HKJC's own standard for it.

One value per RACE (AGENTS.md: pace, style and trend are three different
things). Pure functions; `jobs/derive_all` writes the `race_tempo` table from
them and every page reads that table.

WHAT IS MEASURED. The leader's time at each of HKJC's section marks -- the
fastest cumulative time any finisher recorded there, which is how HKJC's own
race sectional is built -- to the 800m-to-go mark: the part of a race before
the home turn, where the pace is set. At 1000m the first section is only the
200m out of the gates, so the reading runs to the 400m instead.

WHAT IT IS READ AGAINST. HKJC's reference sectionals for that course,
distance and class (`ingest/standards`), not the average of every race at the
distance. The old reading pooled Sha Tin's straight 1000m with Happy Valley's,
Class 5 with Group races and good going with yielding, and on 23 Sep 2026 it
called a Happy Valley 1000m run exactly to its class standard "Fast" and a
1200m run 0.4s slower than standard to the 800m "Fast" as well.

THE DAY'S TRACK. HKJC's standards are for good going. A yielding track slows
every section, and read against a good-going standard every race on it would
be "Slow". So the standard is scaled by the meeting's own track speed first:
the median, over that day's races on that surface, of how far the winning time
was from its standard. A race is Fast when its leader went faster than THAT
day's class standard, not faster than a dry day's.

Negative deviations are faster, like every time in the archive.
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["DERIVE_VERSION", "BANDS", "CUTS", "standard_class", "pick_standard",
           "leader_splits", "early_sections", "meeting_variant", "tempo",
           "band"]

DERIVE_VERSION = "tempo-1.0"
BANDS = ("Very Fast", "Fast", "Neutral", "Slow", "Very Slow")

# Seconds per 400m of the early reading against the day's class standard.
# Set at the archive's 11.5th, 31st, 69th and 88.5th percentiles (4,865 races
# from 2020-21, sd 0.343): the proportions the old reading's +-0.5 and +-1.2
# standard deviations gave, so a band is as rare as it was.
#
# WHY TO THE 800m AND NOT THE 400m. Tested on the same races, by how often
# the winner led at the first call, fastest fifth to slowest: to the 800m
# 21.0 / 23.9 / 26.4 / 30.0 / 38.2%, to the 400m 25.1 / 24.9 / 24.7 / 28.0 /
# 37.0%. A pace reading should decide whether the front runners last; the
# 800m one does at every step, the 400m one only in the slowest two fifths.
CUTS = (-0.37, -0.15, 0.17, 0.42)

# A class's neighbours, nearest first, for a cell HKJC leaves blank ("-").
_LADDER = ("G", "1", "2", "3", "4", "5")
MIN_VARIANT_RACES = 3


def standard_class(race_class: str | None) -> str | None:
    """Which row of HKJC's table a race is read against."""
    if race_class is None:
        return None
    c = str(race_class)
    if c in ("1", "2", "3", "4", "5"):
        return c
    if c == "Griffin Race":
        return c
    # Group races of every grade, Listed, the 4YO series and the legacy "0".
    if c in ("G1", "G2", "G3", "Listed", "4YO", "0") or c.lower().startswith("group"):
        return "G"
    return None


def pick_standard(standards: Mapping[tuple, Mapping[str, Any]], venue: str,
                  surface: str, distance: int, race_class: str | None
                  ) -> tuple[Mapping[str, Any] | None, bool]:
    """The standard row, and whether it is the race's own class.

    HKJC publishes no figure where it has too few races (Group races at Happy
    Valley 1200m, Class 1 at the 2000m); the nearest class on the ladder
    stands in and the row says so. A race of unknown class is read against
    Class 3, the middle of the ladder, and says so too.
    """
    want = standard_class(race_class)
    order: list[str]
    if want == "Griffin Race":
        order = ["Griffin Race", "5", "4"]
    elif want in _LADDER:
        i = _LADDER.index(want)
        order = sorted(_LADDER, key=lambda c: (abs(_LADDER.index(c) - i),
                                               _LADDER.index(c) < i))
    else:
        order = ["3", "4", "2", "5", "1", "G"]
    for c in order:
        row = standards.get((venue, surface, int(distance), c))
        if row and row.get("sections"):
            return row, c == want
    return None, False


def leader_splits(sections_by_runner: Sequence[Sequence[float]]) -> list[float] | None:
    """The leader's time for each section: the fastest cumulative time at each
    mark, differenced. Only runners with every section count."""
    full = [s for s in sections_by_runner if s]
    if not full:
        return None
    n = max(len(s) for s in full)
    full = [s for s in full if len(s) == n]
    cum = []
    for s in full:
        total, c = 0.0, []
        for x in s:
            total += x
            c.append(total)
        cum.append(c)
    lead = [min(c[i] for c in cum) for i in range(n)]
    return [lead[0]] + [lead[i] - lead[i - 1] for i in range(1, n)]


def early_sections(distance: int, n: int) -> int:
    """How many sections the early reading covers: to the 800m-to-go mark,
    or to the 400m at 1000m, where the first section is the 200m jump."""
    return 2 if int(distance) == 1000 else max(1, n - 2)


def meeting_variant(races: Sequence[Mapping[str, Any]]) -> float | None:
    """The day's track speed on one surface: the median of winning time over
    its standard, less one. None when too few races ran to say."""
    ratios = [r["winner_time"] / r["standard_time"] - 1 for r in races
              if r.get("winner_time") and r.get("standard_time")]
    if len(ratios) < MIN_VARIANT_RACES:
        return None
    return statistics.median(ratios)


def band(dev_per_400: float | None) -> str | None:
    if dev_per_400 is None:
        return None
    for cut, name in zip(CUTS, BANDS):
        if dev_per_400 < cut:
            return name
    return BANDS[-1]


def tempo(race: Mapping[str, Any], sections_by_runner: Sequence[Sequence[float]],
          standard: Mapping[str, Any] | None, exact: bool,
          variant: float | None) -> dict[str, Any] | None:
    """One race's `race_tempo` row, or None when it cannot be read.

    `standard` is `pick_standard`'s row: its `sections` a ';'-joined string.
    """
    if not standard:
        return None
    lead = leader_splits(sections_by_runner)
    std = [float(x) for x in str(standard["sections"]).split(";")]
    if not lead or len(lead) != len(std):
        return None
    k = early_sections(race["distance"], len(std))
    early_len = sum(_lengths(race["distance"], len(std))[:k])
    scale = 1.0 + (variant or 0.0)
    early_time, early_std = sum(lead[:k]), sum(std[:k]) * scale
    late_time, late_std = lead[-1], std[-1] * scale
    dev = early_time - early_std
    per_400 = dev * 400.0 / early_len
    return {
        "race_date": race["race_date"], "race_no": race["race_no"],
        "standard_class": standard["class_key"], "standard_exact": int(exact),
        "leader_sections": ";".join(f"{x:.2f}" for x in lead),
        "early_to": 400 if int(race["distance"]) == 1000 else 800,
        "early_time": round(early_time, 2), "early_std": round(early_std, 2),
        "variant": None if variant is None else round(variant, 5),
        "early_dev": round(dev, 2), "early_dev_400": round(per_400, 3),
        "late_time": round(late_time, 2), "late_std": round(late_std, 2),
        "late_dev": round(late_time - late_std, 2),
        "band": band(per_400), "derive_version": DERIVE_VERSION,
    }


def _lengths(distance: int, n: int) -> list[int]:
    """Metres in each section: the first takes whatever is left over."""
    first = int(distance) - 400 * (n - 1)
    return [first] + [400] * (n - 1)

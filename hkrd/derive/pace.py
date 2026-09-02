"""Pace and running style. Pure functions — rows in, dicts out.

Three quantities that the previous system kept collapsing into one another, and
which are kept strictly separate here:

  race pace   one value per RACE  -- how the race was run overall
  style       one value per RUNNER per run -- where it sat in the field
  trend       direction of a horse's recent figures (lives in query/, not here)

A "Fast" race says nothing about which style will suit it. The 15 Jul R3 example
was run Fast and was won by a Leader, with a Closer third and another Closer
eleventh. They are independent axes and get separate columns.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import median
from typing import Any

from hkrd.store.coerce import parse_running_positions, parse_section_times

__all__ = ["DERIVE_VERSION", "PaceError", "classify_style", "STYLE_ORDER",
           "style_ordinal", "section_lengths", "per_400", "race_pace_rows"]

DERIVE_VERSION = "pace-2.0"


class PaceError(ValueError):
    """Malformed input, raised with the identifying key. Never returns None."""


# ── running style ────────────────────────────────────────────────────────────
#
# Decision A2. Four implementations existed in the old repo; two were identical,
# so there were three distinct behaviours. Measured across all 21,130 legacy runs
# with positions, the fixed-threshold version in pace_utils.py disagreed with the
# others on 663 runs -- every single one of them first-call position 8 in a 13-
# or 14-runner field, where a fixed "> 7 is a Closer" fires but a field-scaled
# cutoff of max(8, field * 0.7) = 9 does not.
#
# The scaled version is canonical because it is what SARR was built and
# backtested against, and because it is right on the merits: position 8 of 14 is
# mid-division, while position 8 of 9 is a closer. Adopting it changes 3.1% of
# displayed styles; the legacy display over-called Closer by 8%.

STYLE_ORDER = ("Leader", "On-Pace", "Midfield", "Closer")


def style_ordinal(style: str) -> int:
    """Positional sort key, front of the field to the back.

    Sorting these as strings gives Closer, Leader, Midfield, On-Pace, which is
    meaningless. Any sort must go through this.
    """
    try:
        return STYLE_ORDER.index(style)
    except ValueError:
        return len(STYLE_ORDER)


def classify_style(positions: Sequence[int] | str | None, field_size: int) -> str:
    """Running style from the first-call position, scaled to the field."""
    pos = (parse_running_positions(positions)
           if not isinstance(positions, (list, tuple)) else tuple(positions))
    if not pos or field_size <= 0:
        return "Unknown"
    first = pos[0]
    if first <= 2:
        return "Leader"
    if first <= max(4, int(field_size * 0.3)):
        return "On-Pace"
    if first >= max(8, int(field_size * 0.7)):
        return "Closer"
    return "Midfield"


# ── sectionals ───────────────────────────────────────────────────────────────
#
# HKJC splits are not equal lengths. The first section of an 1800m race is 200m
# and the rest are 400m, so comparing raw split times across distances -- or
# comparing a first split to a later one -- is comparing different things.

SECTION_LENGTHS: dict[int, tuple[int, ...]] = {
    1000: (200, 400, 400),
    1200: (400, 400, 400),
    1400: (200, 400, 400, 400),
    1600: (400, 400, 400, 400),
    1650: (450, 400, 400, 400),
    1800: (200, 400, 400, 400, 400),
    2000: (400, 400, 400, 400, 400),
    2200: (200, 400, 400, 400, 400, 400),
    2400: (400, 400, 400, 400, 400, 400),
}


def section_lengths(distance: int) -> tuple[int, ...]:
    try:
        return SECTION_LENGTHS[int(distance)]
    except (KeyError, TypeError, ValueError):
        raise PaceError(f"no section layout for distance {distance!r}") from None


def per_400(splits: Sequence[float], distance: int) -> tuple[float, ...]:
    """Normalise each split to seconds per 400m so they are comparable."""
    lengths = section_lengths(distance)
    if not splits:
        return ()
    if len(splits) != len(lengths):
        raise PaceError(
            f"distance {distance} expects {len(lengths)} sections, got {len(splits)}"
        )
    return tuple(s * 400.0 / m for s, m in zip(splits, lengths))


def _early_late(norm: Sequence[float]) -> tuple[float | None, float | None]:
    """Early = the opening 400m-equivalent; late = the closing one."""
    if not norm:
        return None, None
    return norm[0], norm[-1]


def race_pace_rows(runners: Sequence[dict[str, Any]], distance: int) -> list[dict[str, Any]]:
    """Compute runner_pace rows for one race.

    early_dev and late_dev are deviations from the RACE MEDIAN, not from a par.
    That is deliberate: it removes the day's track speed, so the figure answers
    "did this horse go faster than its own field" rather than "was it a fast
    day", which is what the ET par figure already answers.
    """
    if not runners:
        return []
    field_size = len(runners)

    lengths = section_lengths(distance)      # raises if the distance is unknown

    parsed: list[dict[str, Any]] = []
    for r in runners:
        key = f"{r.get('race_date')} R{r.get('race_no')} #{r.get('horse_no')}"
        try:
            splits = parse_section_times(r.get("section_times"))
        except Exception as e:
            raise PaceError(f"{key}: {e}") from None

        # A runner with FEWER splits than the distance calls for did not
        # complete the race — pulled up, fell, unseated. It has no pace figure
        # and never will, but it must not take the rest of the field with it.
        # It did once: a single non-finisher raised out of this loop and voided
        # the pace for all fourteen runners, which is how 377 runners across 30
        # races ended up with no pace at all. AGENTS.md, Error handling: a
        # missing minor input must never void a whole result.
        #
        # MORE splits than expected is a different thing entirely. That means
        # the section layout for this distance is wrong, which is a fault in
        # SECTION_LENGTHS rather than in one horse's race, and it still raises.
        if splits and len(splits) > len(lengths):
            raise PaceError(
                f"{key}: distance {distance} expects {len(lengths)} sections, "
                f"got {len(splits)} — the section layout is wrong, not the horse")

        if splits and len(splits) < len(lengths):
            parsed.append({
                "race_date": r.get("race_date"), "race_no": r.get("race_no"),
                "horse_no": r.get("horse_no"),
                "sec_400": None, "early_pace": None, "late_pace": None,
                "pace_style": classify_style(r.get("running_positions"), field_size),
                "incomplete": True,
            })
            continue

        norm = per_400(splits, distance) if splits else ()
        early, late = _early_late(norm)
        parsed.append({
            "race_date": r.get("race_date"), "race_no": r.get("race_no"),
            "horse_no": r.get("horse_no"),
            "sec_400": ";".join(f"{v:.3f}" for v in norm) or None,
            "early_pace": early, "late_pace": late,
            "pace_style": classify_style(r.get("running_positions"), field_size),
            "incomplete": False,
        })

    med_early = _median_of([p["early_pace"] for p in parsed])
    med_late = _median_of([p["late_pace"] for p in parsed])

    for p in parsed:
        # Negative means faster than the field, which is the intuitive direction.
        p["early_dev"] = (p["early_pace"] - med_early
                          if p["early_pace"] is not None and med_early is not None else None)
        p["late_dev"] = (p["late_pace"] - med_late
                         if p["late_pace"] is not None and med_late is not None else None)
        # Sustained Speed Index: how much a runner gave back over the closing
        # section relative to its own early effort, against the field.
        p["ssi"] = (p["late_dev"] - p["early_dev"]
                    if p["early_dev"] is not None and p["late_dev"] is not None else None)
        p["derive_version"] = DERIVE_VERSION
    return parsed


def _median_of(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return median(present) if present else None

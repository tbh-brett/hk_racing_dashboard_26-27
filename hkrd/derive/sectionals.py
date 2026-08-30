"""Where a race was actually won — section by section.

`derive/pace` already normalises the splits and reads the first and last of
them. What it does not do is look at the MIDDLE, or pair a section's time with
the places changed over it, and those two together are the whole point of a
sectional.

    A horse that ran the fastest closing 400m and gained six places did
    something. A horse that ran the fastest closing 400m from last and gained
    nothing was simply left behind, and the two are indistinguishable from the
    time alone.

So every section here carries both: how fast it was against the field, and how
many places it changed hands over. The section lengths come from
`derive/pace.SECTION_LENGTHS` rather than a second copy -- one table, and it is
the one SARR and the pace bands are already built on.

WHAT THE SEGMENTATION IS, and it was checked against the archive rather than
assumed: HKJC splits from the FINISH backwards in 400m sections, and the
OPENING section carries the remainder. Measured over every winner in the
21,280-run archive, the implied opening lengths come out at round numbers
(200m, 400m, 450m) and every opening section is SLOWER per metre than the rest
-- 15.4 m/s against 18.3 at 1000m, 13.5 against 16.5 at 2200m -- which is what
a standing start requires and what the reverse assignment would contradict.

Running positions align with sections exactly: 21,075 runners in the archive
carry both, and not one has a different count of each. `positions[i]` is the
position held at the END of section `i`.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import median
from typing import Any

from hkrd.derive.pace import PaceError, per_400, section_lengths
from hkrd.store.coerce import parse_running_positions, parse_section_times

__all__ = ["DERIVE_VERSION", "decompose", "race_sections", "SectionError"]

DERIVE_VERSION = "sectionals-1.0"


class SectionError(PaceError):
    """A run's sectionals could not be decomposed."""


# A section a runner beat the field's median by more than this, in seconds per
# 400m, is where it made its run. The value is measured, not chosen: binned
# over the 20,755 archived runs that carry a closing deviation, against a
# baseline win rate of 8.1%,
#
#     closing dev (s/400m)      n     won      95% CI
#          -1.00 and faster    210   43.8%  [37.3%, 50.6%]
#          -1.00 to -0.50     2157   24.2%  [22.4%, 26.0%]
#          -0.50 to -0.25     3040   14.9%  [13.7%, 16.2%]
#          -0.25 to  0.00     4743    8.6%  [ 7.8%,  9.4%]   <- the baseline
#           0.00 to +0.25     4461    4.4%  [ 3.8%,  5.0%]
#          +0.25 and slower   6144    0.3%
#
# 0.25 is where the band stops separating from the field: at -0.25 to 0.00 the
# interval contains the 8.1% baseline, and one band faster it clearly does not.
#
# WHAT THIS DOES NOT CLAIM. That table is within-race and close to tautological
# -- winning a race generally requires finishing fast in it. It fixes what
# counts as a distinguishable section, and nothing more. It is NOT evidence
# that a fast closing section predicts the NEXT start; model/backtest measured
# that question directly and the answer was no.
NOTABLE_SECONDS = 0.25


def _median_of(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return median(present) if present else None


def decompose(splits: Sequence[float], positions: Sequence[int],
              distance: int) -> list[dict[str, Any]]:
    """One runner's sections, before the field is known.

    `places_gained` is None for the opening section: every horse starts level,
    so there is no earlier position to have gained on. Reporting a gain from
    the draw would confuse a wide barrier with a bad beginning.
    """
    lengths = section_lengths(distance)
    if splits and len(splits) != len(lengths):
        raise SectionError(
            f"distance {distance} expects {len(lengths)} sections, "
            f"got {len(splits)}")
    normalised = per_400(splits, distance) if splits else ()

    out: list[dict[str, Any]] = []
    for i, length in enumerate(lengths):
        seconds = splits[i] if i < len(splits) else None
        at_end = positions[i] if i < len(positions) else None
        before = positions[i - 1] if 0 < i <= len(positions) - 1 else None
        out.append({
            "index": i,
            # Counted from the finish, which is how a sectional is spoken
            # about: "the last 400" is section -1, not section 4.
            "from_finish": len(lengths) - i,
            "length_m": length,
            "seconds": seconds,
            "per_400": normalised[i] if i < len(normalised) else None,
            "speed_ms": round(length / seconds, 3) if seconds else None,
            "position_at_end": at_end,
            "places_gained": (before - at_end
                              if before is not None and at_end is not None
                              else None),
        })
    return out


def race_sections(runners: Sequence[dict[str, Any]],
                  distance: int) -> list[dict[str, Any]]:
    """Every runner in one race, decomposed and ranked against its own field.

    The comparison is to the RACE median, not to a par, for the same reason
    `derive/pace` gives: it removes the day's track speed, so the figure
    answers "did this horse go faster than its own field" rather than "was it
    a fast day", which the ET figure already answers.
    """
    if not runners:
        return []

    parsed: list[dict[str, Any]] = []
    for r in runners:
        key = (f"{r.get('race_date')} R{r.get('race_no')} "
               f"#{r.get('horse_no')}")
        try:
            splits = parse_section_times(r.get("section_times"))
            positions = parse_running_positions(r.get("running_positions"))
        except Exception as exc:
            raise SectionError(f"{key}: {exc}") from None
        if not splits:
            continue
        try:
            sections = decompose(splits, positions, distance)
        except SectionError as exc:
            raise SectionError(f"{key}: {exc}") from None
        parsed.append({
            "race_date": r.get("race_date"), "race_no": r.get("race_no"),
            "horse_no": r.get("horse_no"),
            "horse_name": r.get("horse_name"),
            "sections": sections,
        })

    if not parsed:
        return []

    count = len(parsed[0]["sections"])
    for i in range(count):
        column = [p["sections"][i]["per_400"] for p in parsed]
        par = _median_of(column)
        # Rank within the section, fastest first. Ties share a rank, so two
        # runners on the same split are both "equal third" rather than third
        # and fourth — inventing an order the clock did not measure.
        ordered = sorted(v for v in column if v is not None)
        for p in parsed:
            cell = p["sections"][i]
            value = cell["per_400"]
            cell["field_per_400"] = round(par, 3) if par is not None else None
            # Negative is faster than the field, which is the direction every
            # other deviation in this package uses.
            cell["dev"] = (round(value - par, 3)
                           if value is not None and par is not None else None)
            cell["rank"] = (ordered.index(value) + 1
                            if value is not None else None)
            cell["notable"] = bool(cell["dev"] is not None
                                   and cell["dev"] <= -NOTABLE_SECONDS)

    for p in parsed:
        p.update(_summarise(p["sections"]))
        p["derive_version"] = DERIVE_VERSION
    return parsed


def _where(section: dict[str, Any], count: int) -> str:
    """Name a section the way a race is called.

    Hong Kong markers count distance REMAINING, so a section is named by the
    marker it runs to: the opening section of an 1650m race ends at the 1200m,
    and calling it "1600m out" — which its position from the finish would
    suggest — puts it 450 metres from where it was run.
    """
    if section["from_finish"] == 1:
        return f"the last {section['length_m']}m"
    if section["index"] == 0:
        return f"the opening {section['length_m']}m"
    # Racing names a move by the marker it began from: "made its run from the
    # 800m". The marker is where the section ENDS, distance remaining.
    return f"the {(section['from_finish'] - 1) * 400}m"


def _summarise(sections: list[dict[str, Any]]) -> dict[str, Any]:
    """The one-line read of a run: where it was made, and whether it counted.

    The pairing is the point. A fastest closing section is not a run if the
    horse gained nothing over it, and the summary says which happened rather
    than leaving a rank to imply the wrong one.
    """
    priced = [s for s in sections if s["dev"] is not None]
    closing = sections[-1] if sections else None

    best = min(priced, key=lambda s: s["dev"], default=None)
    worst = max(priced, key=lambda s: s["dev"], default=None)
    gains = [s for s in sections if s["places_gained"] is not None]
    total_gained = sum(s["places_gained"] for s in gains) if gains else None

    read = None
    if best is not None and best["notable"]:
        moved = best["places_gained"]
        where = _where(best, len(sections))
        # The opening section has no earlier position to have gained on, so
        # it can only ever be reported as a time.
        if moved is None:
            read = f"fastest section was {where}"
        elif moved > 0:
            preposition = "over" if best["from_finish"] == 1 else "from"
            read = f"made its run {preposition} {where} — {moved} places"
        else:
            # Fastest section, no places. Being quickest from the back of a
            # field that had already gone is not a run at the race.
            read = (f"fastest over {where} but gained nothing — the race had "
                    "gone without it")
    elif closing is not None and closing["rank"] == 1:
        read = "fastest home, though no section beat the field by much"

    return {
        "closing_rank": closing["rank"] if closing else None,
        "closing_dev": closing["dev"] if closing else None,
        "best_section": best["index"] if best else None,
        "best_section_label": _where(best, len(sections)) if best else None,
        "worst_section": worst["index"] if worst else None,
        "places_gained": total_gained,
        "read": read,
    }

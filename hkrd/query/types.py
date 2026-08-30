"""The shared runner line. One type, returned by every query function.

A horse's past run in the form guide, the same run in race lookup, and the same
run on the results page are literally the same object with the same ET figure,
the same pace style and the same tags. They cannot disagree, because there is
only one of them.

That is the whole architectural idea: the four subsystems that were four
pipelines reading eight sources become four calls returning this.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["RunnerLine", "RaceLine", "FormGuide", "format_race_time",
           "format_figure", "STYLE_ORDER"]

STYLE_ORDER = ("Leader", "On-Pace", "Midfield", "Closer")


def format_race_time(seconds: float | None) -> str | None:
    """Cumulative race times render m:ss.xx; segments stay plain seconds.

    The rule, stated so it cannot be misapplied: if a value could exceed 60
    seconds it is a race time and gets the colon form. A 22.91s sectional split
    is a segment and must stay as it is -- converting it would be actively wrong.
    """
    if seconds is None:
        return None
    minutes, rest = divmod(float(seconds), 60.0)
    return f"{int(minutes)}:{rest:05.2f}" if minutes >= 1 else f"{rest:.2f}"


def format_figure(figure: float | None, len_vs_par: float | None,
                  n_eff: int | None, confidence: str | None) -> str | None:
    """A figure never renders as a bare number.

    Every figure carries what it means and how much evidence sits behind it:
    "103.7 (+3.9L vs par, n=41, medium)".
    """
    if figure is None:
        return None
    bits = []
    if len_vs_par is not None:
        bits.append(f"{len_vs_par:+.1f}L vs par")
    if n_eff is not None:
        bits.append(f"n={n_eff}")
    if confidence:
        bits.append(confidence)
    return f"{figure:.1f}" + (f" ({', '.join(bits)})" if bits else "")


@dataclass(frozen=True)
class RunnerLine:
    """One runner's line in one race."""

    # identity
    race_date: str
    race_no: int
    horse_no: int
    horse_name: str
    draw: int | None = None
    jockey: str | None = None
    trainer: str | None = None
    actual_weight: int | None = None
    declared_weight: int | None = None
    gear: str | None = None

    # race context — identical for every line in a race
    venue: str | None = None
    course: str | None = None
    surface: str | None = None
    going: str | None = None
    distance: int | None = None
    race_class: str | None = None
    field_size: int = 0

    # result
    place: int | None = None
    place_code: str | None = None
    dead_heat: bool = False
    finish_time: float | None = None
    lengths_behind: float | None = None
    running_positions: tuple[int, ...] = ()
    section_times: tuple[float, ...] = ()

    # derived — one definition, used everywhere
    et_figure: float | None = None
    et_len_vs_par: float | None = None
    et_len_vs_race: float | None = None
    et_n_eff: int | None = None
    et_confidence: str | None = None
    pace_style: str | None = None
    early_dev: float | None = None
    late_dev: float | None = None
    sarr: float | None = None
    sarr_rank: int | None = None
    tags: tuple[str, ...] = ()
    # Where the horse actually travelled, read from HKJC's comments on
    # running rather than inferred from a photograph.
    lane_notes: tuple[str, ...] = ()
    running_comment: str | None = None
    incident_comment: str | None = None

    # market — always post-time unless stated
    win_odds: float | None = None
    place_odds: float | None = None
    p_place: float | None = None

    @property
    def finish_time_display(self) -> str | None:
        return format_race_time(self.finish_time)

    @property
    def figure_display(self) -> str | None:
        return format_figure(self.et_figure, self.et_len_vs_par,
                             self.et_n_eff, self.et_confidence)

    @property
    def style_ordinal(self) -> int:
        """Leader -> On-Pace -> Midfield -> Closer. Never sort these as strings."""
        try:
            return STYLE_ORDER.index(self.pace_style or "")
        except ValueError:
            return len(STYLE_ORDER)

    def to_dict(self) -> dict[str, Any]:
        """JSON shape for the API. Tuples become lists; displays are precomputed
        so the browser never reimplements a formatting rule."""
        d = asdict(self)
        d["running_positions"] = list(self.running_positions)
        d["section_times"] = list(self.section_times)
        d["tags"] = list(self.tags)
        d["lane_notes"] = list(self.lane_notes)
        d["finish_time_display"] = self.finish_time_display
        d["figure_display"] = self.figure_display
        d["style_ordinal"] = self.style_ordinal
        return d


@dataclass(frozen=True)
class RaceLine:
    """Race-level facts, including the ones that are properties of the race and
    not of any runner — pace tempo and market concentration."""

    race_date: str
    race_no: int
    venue: str | None = None
    course: str | None = None
    surface: str | None = None
    going: str | None = None
    distance: int | None = None
    race_class: str | None = None
    race_name: str | None = None
    off_time: str | None = None
    field_size: int = 0
    concentration: float | None = None
    concentration_band: str | None = None
    runners: tuple[RunnerLine, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["runners"] = [r.to_dict() for r in self.runners]
        return d


@dataclass(frozen=True)
class FormGuide:
    """One race's lines, plus the last N lines for each horse in it.

    Two query calls, not a pipeline: get_race, then get_horse_form per runner.
    The history entries are the same RunnerLine type as the card entries.
    """

    race: RaceLine
    history: dict[str, tuple[RunnerLine, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"race": self.race.to_dict(),
                "history": {k: [r.to_dict() for r in v] for k, v in self.history.items()}}

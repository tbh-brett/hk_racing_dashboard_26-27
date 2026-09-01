"""How far back a figure is measured over.

The owner asked for daily, weekly, monthly, seasonal and lifetime on the Bets
and Blackbook analyses. The temptation is a date filter per page, which is how
the old dashboard ended up with a strike rate on one tab disagreeing with the
strike rate on another — both right, over windows nobody had written down.

THE SEASON IS THE ONE THAT BITES. A Hong Kong season runs early September to
mid-July. A calendar year cuts one in half and mixes two together, and nothing
on screen would show it: the figure just quietly means something else.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.query import period


def test_the_five_windows_the_interface_offers() -> None:
    assert period.PERIODS == ("day", "week", "month", "season", "lifetime")


def test_a_season_is_named_by_the_year_it_opened_in() -> None:
    """July 2026 is the END of the 2025/26 season, not the start of 2026/27."""
    assert period.season_of("2026-07-15") == 2025
    assert period.season_of("2026-08-31") == 2025      # still the close-season
    assert period.season_of("2026-09-01") == 2026      # the new one opens
    assert period.season_of("2026-01-04") == 2025      # midwinter, same season


def test_the_season_window_spans_september_to_the_anchor() -> None:
    w = period.resolve("season", anchor="2026-07-15")
    assert (w.since, w.until) == ("2025-09-01", "2026-07-15")
    assert w.label == "SEASON 2025/26"


def test_a_calendar_year_would_have_split_that_season() -> None:
    """The bug this module exists to prevent, stated as a test: a January
    boundary would drop eight months of the season being asked about."""
    w = period.resolve("season", anchor="2026-07-15")
    assert w.since < "2026-01-01", (
        "the season must reach back into the previous calendar year")


def test_a_week_is_seven_days_ending_on_the_anchor() -> None:
    """Not the ISO week — that makes "this week" mean two days on a Tuesday."""
    w = period.resolve("week", anchor="2026-07-15")
    assert (w.since, w.until) == ("2026-07-09", "2026-07-15")
    span = dt.date.fromisoformat(w.until) - dt.date.fromisoformat(w.since)
    assert span.days == 6


def test_a_day_is_one_day_at_both_ends() -> None:
    w = period.resolve("day", anchor="2026-07-15")
    assert w.since == w.until == "2026-07-15"


def test_lifetime_has_no_lower_bound_rather_than_an_early_one() -> None:
    """A hard-coded early date would silently exclude anything imported from
    before it, and the archive's first meeting is a fact about the data."""
    w = period.resolve("lifetime")
    assert w.since is None and w.until is None
    assert period.clause(w) == ("1 = 1", [])


def test_an_explicit_range_is_never_rounded_to_a_named_window() -> None:
    """The interface offers a custom range beside the five. A range someone
    typed must come back as they typed it."""
    w = period.resolve("month", since="2025-11-01", until="2025-11-30")
    assert w.period == "custom"
    assert (w.since, w.until) == ("2025-11-01", "2025-11-30")


def test_an_unknown_period_is_refused_rather_than_defaulted() -> None:
    """Falling back to lifetime would answer a different question than the one
    asked and look like it answered the right one."""
    with pytest.raises(ValueError, match="unknown period"):
        period.resolve("fortnight")


def test_the_label_names_the_bounds_not_just_the_word() -> None:
    """"SEASON" is a word; "SEASON 2025/26" is checkable, and a figure copied
    off the page can be checked again later against the same dates."""
    for name in ("day", "week", "month", "season"):
        w = period.resolve(name, anchor="2026-07-15")
        assert w.label and w.label != name.upper()


def test_the_sql_fragment_binds_both_ends() -> None:
    w = period.resolve("season", anchor="2026-07-15")
    frag, params = period.clause(w, "r.race_date")
    assert frag == "r.race_date >= ? AND r.race_date <= ?"
    assert params == ["2025-09-01", "2026-07-15"]


def test_the_column_is_the_caller_s_to_name() -> None:
    """Bets filter on the bet's race_date and the blackbook on the RUN's, and
    they are different tables in the same query."""
    w = period.resolve("day", anchor="2026-07-15")
    assert period.clause(w, "b.race_date")[0].startswith("b.race_date")
    assert period.clause(w, "r.race_date")[0].startswith("r.race_date")

"""How far back a figure is measured over — one definition, every page.

The owner asked for daily, weekly, monthly, seasonal and lifetime on the Bets
and Blackbook analyses. The temptation is a date filter per page. That is how
the old dashboard ended up with a strike rate on one tab disagreeing with the
strike rate on another: both were right, over windows nobody had written down.

So a window is a named thing resolved HERE, and every query that accepts one
accepts the same five names and computes the same bounds.

THE SEASON IS THE INTERESTING ONE. A Hong Kong racing season runs from early
September to mid-July, not January to December, so a calendar year cuts one in
half and mixes two together. "This season" in July means the previous
September onward; in October it means six weeks ago. Getting that wrong makes
a seasonal figure quietly meaningless, and nothing on screen would show it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

__all__ = ["PERIODS", "Window", "resolve", "season_of", "season_label"]

# The five the interface offers, in the order it offers them.
PERIODS: tuple[str, ...] = ("day", "week", "month", "season", "lifetime")

# HKJC's season opens in early September and closes in mid-July. The first of
# September is the boundary that puts every meeting in exactly one season
# without needing the exact opening date, which moves year to year.
SEASON_START_MONTH = 9


@dataclass(frozen=True)
class Window:
    """A closed date range, or an open one when `since` is None.

    `since` and `until` are inclusive ISO dates, because every table this is
    applied to stores `race_date` as an ISO string and comparing strings is
    exact for that format. `label` is what the interface shows, and it names
    the actual bounds rather than the word — "SEASON" alone is not checkable.
    """
    period: str
    since: str | None
    until: str | None
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"period": self.period, "since": self.since,
                "until": self.until, "label": self.label}


def season_of(date: str) -> int:
    """The season a date belongs to, named by the year it STARTED in.

    2026-07-15 is in the season that opened in September 2025, so it is 2025 —
    calling it 2026 would put the last month of one season with the first
    month of the next.
    """
    d = dt.date.fromisoformat(str(date)[:10])
    return d.year if d.month >= SEASON_START_MONTH else d.year - 1


def season_label(start_year: int) -> str:
    return f"{start_year}/{str(start_year + 1)[2:]}"


def _anchor(anchor: str | dt.date | None) -> dt.date:
    if anchor is None:
        return dt.date.today()
    if isinstance(anchor, dt.date):
        return anchor
    return dt.date.fromisoformat(str(anchor)[:10])


def resolve(period: str | None = "lifetime", *,
            anchor: str | dt.date | None = None,
            since: str | None = None,
            until: str | None = None) -> Window:
    """Turn a period name into dates.

    An explicit `since`/`until` overrides the name — the interface offers a
    custom range beside the five, and a range the user typed must never be
    silently rounded to a named window.

    `anchor` is the day the window is measured back from. It defaults to today,
    but the pages pass the meeting on screen, so "this week" while looking at a
    meeting in April means that April week and not the current one.
    """
    if since or until:
        both = since and until
        return Window("custom", since, until,
                      f"{since} → {until}" if both else
                      f"since {since}" if since else f"until {until}")

    name = (period or "lifetime").lower()
    if name not in PERIODS:
        raise ValueError(
            f"unknown period {period!r}; expected one of {', '.join(PERIODS)}")

    end = _anchor(anchor)
    if name == "lifetime":
        # No lower bound at all, rather than an arbitrary early date: the
        # archive's first meeting is a fact about the data, and hard-coding one
        # here would silently exclude anything imported from before it.
        return Window(name, None, None, "LIFETIME")
    if name == "day":
        return Window(name, end.isoformat(), end.isoformat(), end.isoformat())
    if name == "week":
        # The seven days ending on the anchor, inclusive of both ends — not the
        # ISO week, which would make "this week" mean two days on a Tuesday.
        start = end - dt.timedelta(days=6)
        return Window(name, start.isoformat(), end.isoformat(),
                      f"{start.isoformat()} → {end.isoformat()}")
    if name == "month":
        start = end - dt.timedelta(days=29)
        return Window(name, start.isoformat(), end.isoformat(),
                      f"{start.isoformat()} → {end.isoformat()}")

    start_year = season_of(end)
    start = dt.date(start_year, SEASON_START_MONTH, 1)
    return Window(name, start.isoformat(), end.isoformat(),
                  f"SEASON {season_label(start_year)}")


def clause(window: Window | None, column: str = "b.race_date"
           ) -> tuple[str, list[Any]]:
    """The SQL fragment and its parameters, for a query to AND into its WHERE.

    Returns an always-true fragment for a lifetime window rather than an empty
    string, so callers can join unconditionally instead of each one inventing
    its own way to skip it.
    """
    if window is None or (window.since is None and window.until is None):
        return "1 = 1", []
    parts, params = [], []
    if window.since:
        parts.append(f"{column} >= ?")
        params.append(window.since)
    if window.until:
        parts.append(f"{column} <= ?")
        params.append(window.until)
    return " AND ".join(parts), params


def named_clause(window: Window | None, column: str = "b.race_date",
                 prefix: str = "win") -> tuple[str, dict[str, Any]]:
    """The same fragment for a query that binds by NAME.

    sqlite3 will not mix `?` and `:name` in one statement, and a few queries
    here are named throughout. Rather than let those grow a second way of
    applying a window — which is how one of them ended up windowed and another
    not, with nothing to tell them apart — this is the same rule in the other
    binding style.
    """
    if window is None or (window.since is None and window.until is None):
        return "1 = 1", {}
    parts, params = [], {}
    if window.since:
        parts.append(f"{column} >= :{prefix}_since")
        params[f"{prefix}_since"] = window.since
    if window.until:
        parts.append(f"{column} <= :{prefix}_until")
        params[f"{prefix}_until"] = window.until
    return " AND ".join(parts), params

"""Slicing the corpus, and the risk that carries — Lookup's analysis tabs.

Design brief 07 §8 names the risk in the interface itself:

    "This page's genuine risk is manufacturing false signals through repeated
     slicing. A pivot is the easiest way in this whole tool to manufacture a
     false finding."

So nothing here returns a bare rate. A breakdown row carries its baseline and
an interval on the difference; a pivot cell carries its n and whether it is
under the minimum; and both report how many cells would clear p<.05 by chance
alone at the size shown. That last figure is what turns "eight cells cleared
significance" into "eight cleared where seven were expected at random", which
is the same number read honestly.

Split out of `lookup.py`, which the filters and the search already fill. The
filter vocabulary and `_where` stay there and are imported here, so a filter
means the same thing on the grid and in every panel computed over it.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.probability import actual_over_expected
from hkrd.query.lookup import _where
from hkrd.store.connect import Connection, get_conn

__all__ = ["breakdown", "pivot", "outliers", "corpus", "DIMENSIONS",
           "METRICS", "MIN_SAMPLE", "OUTLIER_DELTA"]

# What can be sliced on. Each is a real column, so the grouping is SQL and the
# counts are the same counts the grid shows.
DIMENSIONS: dict[str, str] = {
    "venue": "a.venue",
    "course": "a.course",
    "surface": "a.surface",
    "going": "a.going",
    "class": "a.race_class",
    "distance": "a.distance",
    "draw": "r.draw",
    "style": "p.pace_style",
    # The race's own pace, banded. Not a stored column: the band is derived
    # from the field's early-pace deviation at read time, so there is one
    # definition of it rather than a second copy in a table that could drift
    # from derive/pace.py.
    "pace": ("CASE WHEN p.early_dev IS NULL THEN NULL"
             "      WHEN p.early_dev <= -1.0 THEN 'Very Slow'"
             "      WHEN p.early_dev <= -0.35 THEN 'Slow'"
             "      WHEN p.early_dev <   0.35 THEN 'Neutral'"
             "      WHEN p.early_dev <   1.0 THEN 'Fast'"
             "      ELSE 'Very Fast' END"),
    "jockey": "r.jockey",
    "trainer": "r.trainer",
    "field size": ("(SELECT count(*) FROM runners f WHERE f.race_date = r.race_date"
                   " AND f.race_no = r.race_no)"),
    "month": "substr(r.race_date, 1, 7)",
}

METRICS = ("strike_rate", "place_rate", "ae", "avg_figure")

# Under this a cell is dimmed rather than hidden. Brief 07: a slice that thin
# is not evidence, but removing it hides how much of the pivot is noise.
MIN_SAMPLE = 30

# A run that finished six or more places away from its market rank. Brief 07's
# own framing: "ONE RUN IS A STORY, NOT A SIGNAL".
OUTLIER_DELTA = 6

_PLACED = ("r.place <= (CASE WHEN (SELECT count(*) FROM runners f "
           " WHERE f.race_date = r.race_date AND f.race_no = r.race_no) >= 7"
           " THEN 3 ELSE 2 END)")

_BOOK = ("(SELECT sum(1.0 / f.win_odds) FROM runners f "
         " WHERE f.race_date = r.race_date AND f.race_no = r.race_no "
         "   AND f.win_odds > 0)")

_FROM = """
    FROM runners r
    JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
    LEFT JOIN runner_et e   USING (race_date, race_no, horse_no)
    LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
    LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
"""

# The market's own ranking of the field, by closing price. Ties share a rank,
# so two horses at 4.0 are both second favourite rather than second and third.
_MARKET_RANK = """
    (SELECT count(DISTINCT f.win_odds) FROM runners f
      WHERE f.race_date = r.race_date AND f.race_no = r.race_no
        AND f.win_odds > 0 AND f.win_odds < r.win_odds) + 1
"""


def _rate_interval(hits: int, n: int) -> tuple[float, float] | None:
    """Wilson score interval on a proportion.

    Wilson rather than the normal approximation because the cells here are
    small and often near zero: a 0-for-12 cell gets [0, 0.24] rather than the
    normal interval's [0, 0], which would claim a certainty twelve runs cannot
    support.
    """
    if n <= 0:
        return None
    z = 1.96
    phat = hits / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def _difference_clears(hits: int, n: int, baseline: float | None) -> bool:
    """True when the slice's interval excludes the baseline rate.

    Not a p-value: the same statement, read off the interval that is already on
    screen, so the page never shows a number the reader cannot check.
    """
    ci = _rate_interval(hits, n)
    if ci is None or baseline is None:
        return False
    return ci[0] > baseline or ci[1] < baseline


def corpus(*, conn: Connection | None = None) -> dict[str, Any]:
    """What the database currently holds, for the line every page carries.

    "every page carries this line — it states what is current and quantifies
    what is missing" — Lookup's own artboard. The pace-labelled fraction is on
    it because a page that slices by running style over a corpus that is 60%
    unlabelled is slicing something other than what it says.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute("""
            SELECT (SELECT count(*) FROM runners) runs,
                   (SELECT count(*) FROM races) races,
                   (SELECT max(race_date) FROM races) latest,
                   (SELECT min(race_date) FROM races) earliest,
                   (SELECT count(*) FROM runner_pace
                     WHERE pace_style IS NOT NULL) pace_labelled,
                   (SELECT count(*) FROM runner_et) figures,
                   (SELECT count(*) FROM trials) trials
        """).fetchone()
        runs = row["runs"] or 0
        return {
            "runs": runs, "races": row["races"] or 0, "trials": row["trials"] or 0,
            "earliest": row["earliest"], "latest": row["latest"],
            "pace_labelled": row["pace_labelled"] or 0,
            "pace_share": round((row["pace_labelled"] or 0) / runs, 3) if runs else None,
            "figures": row["figures"] or 0,
            "figure_share": round((row["figures"] or 0) / runs, 3) if runs else None,
            "min_sample": MIN_SAMPLE,
        }
    finally:
        if own:
            conn.close()


def breakdown(dimension: str, *, conn: Connection | None = None,
              min_sample: int = MIN_SAMPLE, **filters: Any) -> dict[str, Any]:
    """One dimension, each value against the baseline of the whole slice.

    The baseline is the FILTERED slice's own rate, not the database's. Sliced
    to Happy Valley sprints, the question is which draws beat Happy Valley
    sprints — comparing them to every race ever run would fold the venue's own
    effect into every row.
    """
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimension must be one of {', '.join(DIMENSIONS)}")
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = _where(filters)
        expr = DIMENSIONS[dimension]
        base = conn.execute(f"""
            SELECT count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN {_PLACED} THEN 1 ELSE 0 END) places
            {_FROM} WHERE {where} AND r.place IS NOT NULL""", params).fetchone()
        base_runs = base["runs"] or 0
        base_sr = (base["wins"] or 0) / base_runs if base_runs else None
        base_pr = (base["places"] or 0) / base_runs if base_runs else None

        rows = [dict(x) for x in conn.execute(f"""
            SELECT {expr} value, count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN {_PLACED} THEN 1 ELSE 0 END) places,
                   avg(e.figure) avg_figure, count(e.figure) figures,
                   sum(CASE WHEN r.win_odds > 0 AND {_BOOK} > 0
                            THEN (1.0 / r.win_odds) / {_BOOK} END) expected_wins,
                   sum(CASE WHEN r.win_odds > 0 AND {_BOOK} > 0 THEN 1 ELSE 0 END) priced
            {_FROM}
            WHERE {where} AND r.place IS NOT NULL AND {expr} IS NOT NULL
            GROUP BY {expr} ORDER BY count(*) DESC""", params)]

        cleared = 0
        for row in rows:
            n = row["runs"]
            row["strike_rate"] = round(row["wins"] / n, 4) if n else None
            row["place_rate"] = round(row["places"] / n, 4) if n else None
            row["win_ci"] = list(_rate_interval(row["wins"], n) or ())
            row["place_ci"] = list(_rate_interval(row["places"], n) or ())
            row["win_delta"] = (round(row["strike_rate"] - base_sr, 4)
                                if base_sr is not None else None)
            row["place_delta"] = (round(row["place_rate"] - base_pr, 4)
                                  if base_pr is not None else None)
            row["clears"] = _difference_clears(row["wins"], n, base_sr)
            row["place_clears"] = _difference_clears(row["places"], n, base_pr)
            row["thin"] = n < min_sample
            row["avg_figure"] = (round(row["avg_figure"], 1)
                                 if row["avg_figure"] is not None else None)
            row.update(actual_over_expected(row.pop("expected_wins"),
                                            row["wins"], row.pop("priced") or 0))
            if row["clears"] and not row["thin"]:
                cleared += 1

        shown = [r for r in rows if not r["thin"]]
        return {
            "dimension": dimension,
            "rows": rows,
            "baseline": {"runs": base_runs,
                         "strike_rate": round(base_sr, 4) if base_sr is not None else None,
                         "place_rate": round(base_pr, 4) if base_pr is not None else None},
            "cells": len(shown),
            "cleared": cleared,
            # The honest denominator. Eight cells clearing p<.05 out of 153 is
            # not eight findings when 7.0 were expected at random.
            "expected_by_chance": round(len(shown) * 0.05, 1),
            "thin_hidden": sum(1 for r in rows if r["thin"]),
            "min_sample": min_sample,
        }
    finally:
        if own:
            conn.close()


def pivot(rows: str, cols: str, *, metric: str = "strike_rate",
          conn: Connection | None = None, min_sample: int = MIN_SAMPLE,
          **filters: Any) -> dict[str, Any]:
    """Two dimensions crossed. Every cell carries its n.

    A pivot is the easiest way in this tool to manufacture a false finding, so
    `expected_notable` is returned beside the cell count — twenty cells means
    one will look notable at p<.05 with nothing behind it.
    """
    for name in (rows, cols):
        if name not in DIMENSIONS:
            raise ValueError(f"dimension must be one of {', '.join(DIMENSIONS)}")
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {', '.join(METRICS)}")
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = _where(filters)
        rexpr, cexpr = DIMENSIONS[rows], DIMENSIONS[cols]
        # The A/E columns cost a correlated subquery over the field for every
        # row and are needed for exactly one of the four metrics. Asking for
        # them regardless took a style-by-venue pivot from 40ms to 460ms.
        priced_cols = ("," if metric == "ae" else "-- ") + f"""
                   sum(CASE WHEN r.win_odds > 0 AND {_BOOK} > 0
                            THEN (1.0 / r.win_odds) / {_BOOK} END) expected_wins,
                   sum(CASE WHEN r.win_odds > 0 AND {_BOOK} > 0 THEN 1 ELSE 0 END) priced
        """ if metric == "ae" else ""
        raw = [dict(x) for x in conn.execute(f"""
            SELECT {rexpr} row_value, {cexpr} col_value, count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN {_PLACED} THEN 1 ELSE 0 END) places,
                   avg(e.figure) avg_figure
                   {priced_cols}
            {_FROM}
            WHERE {where} AND r.place IS NOT NULL
              AND {rexpr} IS NOT NULL AND {cexpr} IS NOT NULL
            GROUP BY {rexpr}, {cexpr}""", params)]

        def value(cell: dict[str, Any]) -> float | None:
            n = cell["runs"]
            if metric == "strike_rate":
                return round(cell["wins"] / n, 4) if n else None
            if metric == "place_rate":
                return round(cell["places"] / n, 4) if n else None
            if metric == "avg_figure":
                return (round(cell["avg_figure"], 1)
                        if cell["avg_figure"] is not None else None)
            ae = actual_over_expected(cell.get("expected_wins"), cell["wins"],
                                      cell.get("priced") or 0)
            return ae["ae"]

        grid: dict[Any, dict[Any, dict]] = {}
        row_totals: dict[Any, dict] = {}
        for cell in raw:
            cell["value"] = value(cell)
            cell["thin"] = cell["runs"] < min_sample
            grid.setdefault(cell["row_value"], {})[cell["col_value"]] = cell
            t = row_totals.setdefault(cell["row_value"],
                                      {"runs": 0, "wins": 0, "places": 0})
            t["runs"] += cell["runs"]
            t["wins"] += cell["wins"]
            t["places"] += cell["places"]

        col_values = sorted({c["col_value"] for c in raw},
                            key=lambda v: (v is None, v))
        row_values = sorted(grid, key=lambda v: -row_totals[v]["runs"])
        for t in row_totals.values():
            t["strike_rate"] = round(t["wins"] / t["runs"], 4) if t["runs"] else None
            t["place_rate"] = round(t["places"] / t["runs"], 4) if t["runs"] else None

        shown = [c for c in raw if not c["thin"]]
        return {
            "rows": rows, "cols": cols, "metric": metric,
            "row_values": row_values, "col_values": col_values,
            "grid": {str(rv): {str(cv): grid[rv][cv] for cv in grid[rv]}
                     for rv in grid},
            "row_totals": {str(k): v for k, v in row_totals.items()},
            "cells": len(raw),
            "thin_cells": len(raw) - len(shown),
            "min_sample": min_sample,
            "expected_notable": round(len(shown) * 0.05, 1),
        }
    finally:
        if own:
            conn.close()


def outliers(*, delta: int = OUTLIER_DELTA, limit: int = 200,
             conn: Connection | None = None,
             **filters: Any) -> dict[str, Any]:
    """Runs whose finish most disagrees with the market's ranking.

    Brief 07 puts the reading on the artboard: "ONE RUN IS A STORY, NOT A
    SIGNAL. These are runs where the finishing position most disagrees with the
    market's expectation. That makes them worth watching and worth a blackbook
    note — it does not make them a pattern. A horse appearing here twice is the
    only thing on this tab that starts to mean something."

    So `repeat_horses` is returned beside the list, with the base rate a horse
    would appear twice at by chance given how often it ran in this slice.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = _where(filters)
        rows = [dict(x) for x in conn.execute(f"""
            SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
                   r.win_odds, r.draw, r.jockey, a.venue, a.course, a.surface,
                   a.going, a.distance, a.race_class,
                   p.pace_style, e.figure,
                   {_MARKET_RANK} market_rank,
                   (SELECT count(*) FROM runners f
                     WHERE f.race_date = r.race_date
                       AND f.race_no = r.race_no) field_size
            {_FROM}
            WHERE {where} AND r.place IS NOT NULL AND r.win_odds > 0
              AND abs({_MARKET_RANK} - r.place) >= ?
            ORDER BY abs({_MARKET_RANK} - r.place) DESC,
                     r.race_date DESC LIMIT ?""", [*params, delta, limit])]
        for row in rows:
            # Positive means the horse beat its market rank.
            row["fin_delta"] = row["market_rank"] - row["place"]

        # How many runs each horse had in the slice at all, so "appeared twice"
        # can be read against how often it ran rather than in isolation.
        appearances: dict[str, int] = {}
        for row in rows:
            appearances[row["horse_name"]] = appearances.get(row["horse_name"], 0) + 1
        repeats = {h: n for h, n in appearances.items() if n > 1}
        for row in rows:
            row["repeat"] = appearances[row["horse_name"]] > 1
            row["appearances"] = appearances[row["horse_name"]]

        # Counted over the whole slice, not the page of it returned. "Showing
        # first N of M matching runs · every panel and tab is computed on all
        # M" -- the artboard's own line, which a `matched` capped at the limit
        # would quietly break.
        total = conn.execute(f"""
            SELECT count(*) n, count(DISTINCT r.horse_name) horses,
                   sum(CASE WHEN abs({_MARKET_RANK} - r.place) >= ?
                            THEN 1 ELSE 0 END) flagged
            {_FROM}
            WHERE {where} AND r.place IS NOT NULL AND r.win_odds > 0""",
            # delta first: its placeholder is in the SELECT list, and SQLite
            # binds by position in the SQL text, not by clause.
            [delta, *params]).fetchone()
        n_runs = total["n"] or 0
        flagged = total["flagged"] or 0
        return {
            "runs": rows,
            "delta": delta,
            "matched": flagged,
            "shown": len(rows),
            "of_runs": n_runs,
            "share": round(flagged / n_runs, 4) if n_runs else None,
            # Repeats are counted over the rows returned, so a truncated list
            # undercounts them -- said rather than left to be assumed.
            "horses": len(appearances),
            "repeat_horses": len(repeats),
            "slice_horses": total["horses"] or 0,
            "truncated": flagged > len(rows),
        }
    finally:
        if own:
            conn.close()

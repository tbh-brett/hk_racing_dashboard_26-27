"""Filtered exploration of the whole database — Lookup.

PROMPTS.md Phase 3 specifies `search_runs(**filters) -> list[RunnerLine]` and
one rule about how: "Every filter in search_runs is a WHERE clause. Nothing
filtered in pandas." A filter that runs in Python cannot be indexed and cannot
be counted without materialising the whole table, so both the speed and the
insight panel depend on that being literal.

Design note 11 §2 adds the source dimension. Trials had their own search
interface, which duplicated this one over half the data; the fix is for Lookup
to filter to trial runs alongside race runs, so there is one search rather than
two covering different halves.

Design brief 07 §8 names the risk this page carries and requires the interface
to show it rather than hide it: "This page's genuine risk is manufacturing
false signals through repeated slicing." Hence `expected_by_chance` on every
insight, and n beside every figure.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from hkrd.derive.probability import actual_over_expected
from hkrd.query.race import _LINE_SQL, _to_line, tags_bulk
from hkrd.query.types import RunnerLine
from hkrd.store.connect import Connection, get_conn

__all__ = ["search_runs", "insight", "filter_options", "FILTERS",
           "SOURCES", "STYLE_ORDER", "PACE_BANDS", "MULTI"]

# Front of the field to the back, matching the ordinal the pages sort on.
STYLE_ORDER = ("Leader", "On-Pace", "Midfield", "Closer")

# Slowest to fastest, so the chips read as a scale rather than a list.
PACE_BANDS = ("Very Slow", "Slow", "Neutral", "Fast", "Very Fast")

SOURCES = ("race", "trial", "both")

# Named so the API and the page can render the filter set without either of
# them holding a second copy of the list.
FILTERS = {
    "race context": ("date_from", "date_to", "venue", "course", "surface",
                     "going", "distance", "distance_min", "distance_max",
                     "race_class",
                     "field_size_min", "field_size_max"),
    "runner": ("horse", "jockey", "trainer", "draw_min", "draw_max",
               "weight_min", "weight_max"),
    "derived": ("pace_style", "race_pace", "et_min", "et_max", "tag",
               "sarr_rank_max"),
    "outcome": ("place", "placed", "won", "odds_min", "odds_max"),
}

# Every entry is (sql fragment, how to bind). Keeping them in one table is what
# makes "nothing filtered in pandas" checkable rather than a good intention.
# The race's own pace, banded from the field's early deviation at read time.
# Imported by slices.py too: one definition, so a race cannot be "Fast" in
# the grid and "Neutral" in the breakdown computed over the same rows.
_PACE_BAND = ("CASE WHEN p.early_dev IS NULL THEN NULL"
              "      WHEN p.early_dev <= -1.0 THEN 'Very Slow'"
              "      WHEN p.early_dev <= -0.35 THEN 'Slow'"
              "      WHEN p.early_dev <   0.35 THEN 'Neutral'"
              "      WHEN p.early_dev <   1.0 THEN 'Fast'"
              "      ELSE 'Very Fast' END")

_CLAUSES: dict[str, str] = {
    "date_from": "r.race_date >= ?",
    "date_to": "r.race_date <= ?",
    "venue": "a.venue = ?",
    "course": "a.course = ?",
    "surface": "a.surface = ?",
    "going": "a.going = ?",
    "distance_min": "a.distance >= ?",
    "distance_max": "a.distance <= ?",
    "race_class": "a.race_class = ?",
    "distance": "a.distance = ?",
    "horse": "r.horse_name = ?",
    "jockey": "r.jockey = ?",
    "trainer": "r.trainer = ?",
    "draw_min": "r.draw >= ?",
    "draw_max": "r.draw <= ?",
    "weight_min": "r.actual_weight >= ?",
    "weight_max": "r.actual_weight <= ?",
    "pace_style": "p.pace_style = ?",
    "race_pace": _PACE_BAND + " = ?",
    "et_min": "e.figure >= ?",
    "et_max": "e.figure <= ?",
    "sarr_rank_max": "s.sarr_rank <= ?",
    "place": "r.place = ?",
    "odds_min": "r.win_odds >= ?",
    "odds_max": "r.win_odds <= ?",
}

# Filters that are a fact about the field rather than the runner, so they sit
# in HAVING-shaped subqueries rather than the WHERE above.
_FIELD_CLAUSES: dict[str, str] = {
    "field_size_min": (
        "(SELECT count(*) FROM runners f WHERE f.race_date = r.race_date "
        " AND f.race_no = r.race_no) >= ?"),
    "field_size_max": (
        "(SELECT count(*) FROM runners f WHERE f.race_date = r.race_date "
        " AND f.race_no = r.race_no) <= ?"),
}

_UPPER = {"horse", "venue", "course"}


# Filters the interface offers as a multi-select. The design's filter panel is
# a grid of chip groups, not a column of single-value inputs: a punter asks for
# "Sha Tin AND Happy Valley, class 3 or 4" in one pass, and forcing that into
# one value per key turns one question into four searches.
MULTI = frozenset({"venue", "course", "surface", "going", "race_class",
                   "jockey", "trainer", "pace_style", "distance",
                   "race_pace"})


def _expand(key: str, value: Any) -> tuple[str, list[Any]]:
    """One clause for a filter, whether it carries one value or several.

    A list becomes `col IN (?, ?, ?)` rather than several ANDed equalities,
    which would match nothing the moment a second value was chosen — the bug
    this shape exists to make impossible.
    """
    sql = _CLAUSES[key]
    values = value if isinstance(value, (list, tuple, set)) else [value]
    values = [v for v in values if v not in (None, "")]
    if not values:
        return "", []
    prepared = [str(v).upper() if key in _UPPER else v for v in values]
    if len(prepared) == 1:
        return sql, prepared
    if key not in MULTI or " = ?" not in sql:
        # A range bound has no plural reading; take the first and say so by
        # ignoring the rest rather than silently ANDing contradictions.
        return sql, prepared[:1]
    column = sql.split(" = ?")[0]
    marks = ", ".join("?" * len(prepared))
    return f"{column} IN ({marks})", prepared


def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses, params = ["1 = 1"], []
    for key, value in filters.items():
        if value is None or value == "" or value == []:
            continue
        if key in _CLAUSES:
            sql, bound = _expand(key, value)
            if not sql:
                continue
            clauses.append(sql)
            params.extend(bound)
        elif key in _FIELD_CLAUSES:
            clauses.append(_FIELD_CLAUSES[key])
            params.append(value)
        elif key == "placed" and value:
            # HK pays three places in fields of seven or more, two below.
            clauses.append(
                "r.place <= (CASE WHEN (SELECT count(*) FROM runners f "
                " WHERE f.race_date = r.race_date AND f.race_no = r.race_no) >= 7"
                " THEN 3 ELSE 2 END)")
        elif key == "won" and value:
            clauses.append("r.place = 1")
        elif key == "tag":
            clauses.append(
                "EXISTS (SELECT 1 FROM runner_tags t "
                " WHERE t.race_date = r.race_date AND t.race_no = r.race_no "
                "   AND t.horse_no = r.horse_no AND t.tag = ?)")
            params.append(value)
    return " AND ".join(clauses), params


def search_runs(*, source: str = "race", limit: int = 500,
                order: str = "recent", conn: Connection | None = None,
                **filters: Any) -> list[RunnerLine]:
    """Runs matching a filter set, as the same RunnerLine every page uses.

    "A horse's past run in the form guide and the same run in race lookup must
    be literally the same object" — PROMPTS.md Phase 3. It is: this returns the
    type `get_race` and `get_horse_form` return, built by the same function.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    own = conn is None
    conn = conn or get_conn()
    try:
        if source == "trial":
            return _search_trials(conn, filters, limit)

        where, params = _where(filters)
        sort = {"recent": "r.race_date DESC, r.race_no DESC, r.horse_no",
                "figure": "e.figure IS NULL, e.figure DESC",
                "odds": "r.win_odds IS NULL, r.win_odds",
                "place": "r.place IS NULL, r.place"}.get(order, "r.race_date DESC")
        rows = conn.execute(
            f"{_LINE_SQL} WHERE {where} ORDER BY {sort} LIMIT ?",
            [*params, limit]).fetchall()
        lines = [_to_line(r) for r in rows]
        # The trip tags the grid can filter on but, until now, could not show.
        # One query for the whole page rather than one per row — see
        # `tags_bulk` for why the difference decides whether the column exists.
        found = tags_bulk(conn, [(x.race_date, x.race_no, x.horse_no)
                                 for x in lines])
        lines = [replace(x, tags=found.get((x.race_date, x.race_no, x.horse_no),
                                           ((), ()))[0],
                         lane_notes=found.get((x.race_date, x.race_no, x.horse_no),
                                              ((), ()))[1])
                 for x in lines]

        if source == "both":
            lines += _search_trials(conn, filters, limit)
            lines.sort(key=lambda x: (x.race_date, x.race_no), reverse=True)
            lines = lines[:limit]
        return lines
    finally:
        if own:
            conn.close()


def _search_trials(conn: Connection, filters: dict[str, Any],
                   limit: int) -> list[RunnerLine]:
    """Trials as RunnerLine, so one grid renders both.

    A trial has no odds, no draw and no class, and those stay null rather than
    being filled with a plausible-looking zero. `race_no` carries the trial
    number: the shape is the same, which is what lets one search cover both.
    """
    clauses, params = ["1 = 1"], []
    simple = {"date_from": "t.trial_date >= ?", "date_to": "t.trial_date <= ?",
              "venue": "t.venue = ?", "surface": "t.surface = ?",
              "horse": "t.horse_name = ?", "place": "t.place = ?"}
    for key, sql in simple.items():
        value = filters.get(key)
        if value not in (None, ""):
            clauses.append(sql)
            params.append(str(value).upper() if key in _UPPER else value)
    if filters.get("won"):
        clauses.append("t.place = 1")
    # A filter this source cannot honour must EXCLUDE it rather than be
    # ignored — silently returning trials that ignore a distance filter would
    # be worse than returning none.
    if any(filters.get(k) not in (None, "") for k in
           ("distance_min", "distance_max", "race_class", "jockey", "trainer",
            "draw_min", "draw_max", "weight_min", "weight_max", "pace_style",
            "et_min", "et_max", "sarr_rank_max", "odds_min", "odds_max", "tag",
            "course", "going", "field_size_min", "field_size_max")):
        return []

    rows = conn.execute(f"""
        SELECT t.trial_date, t.trial_no, t.horse_name, t.place, t.finish_time,
               t.section_times, t.running_positions, t.venue, t.surface,
               t.gear, t.comment_text,
               (SELECT count(*) FROM trials f
                 WHERE f.trial_date = t.trial_date
                   AND f.trial_no = t.trial_no) AS field_size
        FROM trials t
        WHERE {' AND '.join(clauses)}
        ORDER BY t.trial_date DESC, t.trial_no DESC LIMIT ?
    """, [*params, limit]).fetchall()

    from hkrd.store.coerce import parse_finish_time, parse_running_positions, \
        parse_section_times
    out = []
    for r in rows:
        out.append(RunnerLine(
            race_date=r["trial_date"], race_no=r["trial_no"], horse_no=0,
            horse_name=r["horse_name"], place=r["place"],
            finish_time=r["finish_time"], venue=r["venue"],
            surface=r["surface"], gear=r["gear"],
            field_size=r["field_size"] or 0,
            section_times=parse_section_times(r["section_times"]),
            running_positions=parse_running_positions(r["running_positions"]),
            running_comment=r["comment_text"],
            tags=("trial",),
        ))
    return out


def insight(*, source: str = "race", conn: Connection | None = None,
            **filters: Any) -> dict[str, Any]:
    """What the filtered slice actually shows, with its own weakness on screen.

    Brief 07 §8 makes two requirements non-negotiable: n beside every figure,
    and the expected-by-chance count. "Across 153 condition cells, 8 cleared
    significance where 7.0 were expected at random — which is to say,
    essentially nothing was found, but any individual cell looked convincing in
    isolation."
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = _where(filters)
        placed = ("r.place <= (CASE WHEN (SELECT count(*) FROM runners f "
                  " WHERE f.race_date = r.race_date AND f.race_no = r.race_no) >= 7"
                  " THEN 3 ELSE 2 END)")
        book = ("(SELECT sum(1.0 / f.win_odds) FROM runners f "
                " WHERE f.race_date = r.race_date AND f.race_no = r.race_no "
                "   AND f.win_odds > 0)")

        row = conn.execute(f"""
            SELECT count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN {placed} THEN 1 ELSE 0 END) places,
                   avg(e.figure) avg_figure,
                   count(e.figure) figures,
                   sum(CASE WHEN r.win_odds > 0 AND {book} > 0
                            THEN (1.0 / r.win_odds) / {book} END) expected_wins,
                   sum(CASE WHEN r.win_odds > 0 AND {book} > 0 THEN 1 ELSE 0 END) priced,
                   count(DISTINCT r.race_date || r.race_no) races
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            LEFT JOIN runner_et e   USING (race_date, race_no, horse_no)
            LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
            LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
            WHERE {where} AND r.place IS NOT NULL
        """, params).fetchone()

        runs = row["runs"] or 0
        by_style = [dict(r) for r in conn.execute(f"""
            SELECT p.pace_style style, count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN {placed} THEN 1 ELSE 0 END) places
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            LEFT JOIN runner_et e   USING (race_date, race_no, horse_no)
            LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
            LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
            WHERE {where} AND r.place IS NOT NULL AND p.pace_style IS NOT NULL
            GROUP BY p.pace_style ORDER BY count(*) DESC
        """, params)]
        for b in by_style:
            b["strike_rate"] = round(b["wins"] / b["runs"], 3) if b["runs"] else None
            b["place_rate"] = round(b["places"] / b["runs"], 3) if b["runs"] else None

        ae = actual_over_expected(row["expected_wins"], row["wins"] or 0,
                                  row["priced"] or 0)
        active = [k for k, v in filters.items() if v not in (None, "", False)]
        return {
            "runs": runs, "races": row["races"] or 0,
            "wins": row["wins"] or 0, "places": row["places"] or 0,
            "strike_rate": round((row["wins"] or 0) / runs, 3) if runs else None,
            "place_rate": round((row["places"] or 0) / runs, 3) if runs else None,
            "avg_figure": round(row["avg_figure"], 1) if row["avg_figure"] else None,
            "figures": row["figures"] or 0,
            **ae,
            "by_style": by_style,
            "filters": active,
            # Under this many runs the slice is not evidence, whatever it says.
            # Stated by the query rather than left for the page to decide, so
            # every surface agrees on what thin means.
            "thin": runs < 30,
            # The honest denominator for "is this notable": one slice at 5% is
            # one chance in twenty of looking notable by luck alone.
            "expected_by_chance": 0.05,
            "clears": bool(ae["ae"] is not None
                           and (ae["ae_lo"] > 1.0 or ae["ae_hi"] < 1.0)),
        }
    finally:
        if own:
            conn.close()


def filter_options(*, top: int = 14,
                   conn: Connection | None = None) -> dict[str, list[Any]]:
    """The values each chip group offers, read from the archive itself.

    The design's filter panel is a grid of chips carrying real names — Moreira,
    Purton, Teetan — not a free-text box. Those have to come from the data or
    they go stale the first time a jockey leaves, and a chip for a value the
    archive does not contain is a filter that always returns nothing.

    Jockeys and trainers are capped at the busiest `top`, because the full list
    is hundreds long and a chip grid that needs scrolling is a dropdown wearing
    a costume. The free-text box beside them reaches the rest.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        def distinct(sql: str, params: tuple = ()) -> list[Any]:
            return [r[0] for r in conn.execute(sql, params) if r[0] not in (None, "")]

        return {
            "venue": distinct("SELECT DISTINCT venue FROM races ORDER BY venue"),
            "course": distinct("SELECT DISTINCT course FROM races ORDER BY course"),
            "surface": distinct("SELECT DISTINCT surface FROM races ORDER BY surface"),
            "going": distinct("SELECT DISTINCT going FROM races ORDER BY going"),
            # Class is the categorisation this page filters on. Rating bands are
            # deliberately not offered: `rating` stopped populating in April
            # 2026 alongside horse_id, so a rating filter would quietly exclude
            # every recent run rather than narrowing anything.
            "race_class": distinct(
                "SELECT DISTINCT race_class FROM races "
                "WHERE race_class IS NOT NULL ORDER BY race_class"),
            "distance": distinct(
                "SELECT DISTINCT distance FROM races "
                "WHERE distance IS NOT NULL ORDER BY distance"),
            "jockey": distinct(
                "SELECT jockey FROM runners WHERE jockey IS NOT NULL "
                "GROUP BY jockey ORDER BY count(*) DESC LIMIT ?", (top,)),
            "trainer": distinct(
                "SELECT trainer FROM runners WHERE trainer IS NOT NULL "
                "GROUP BY trainer ORDER BY count(*) DESC LIMIT ?", (top,)),
            "pace_style": list(STYLE_ORDER),
            "race_pace": list(PACE_BANDS),
        }
    finally:
        if own:
            conn.close()

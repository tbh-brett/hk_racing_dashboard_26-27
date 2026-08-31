"""The form guide. Two query calls, not a pipeline.

The version this replaces read from eight distinct sources -- the SQLite results
table, an 8 MB Excel file, a racecard .xlsx, pace_index.json and several
reports/*.json -- and was spawned as a subprocess from the dashboard three
separate times, each paying a 15.33s read_excel for data already sitting in the
database next to it.

Here it is get_race, then get_horse_form per runner. Both return the same
RunnerLine, so a horse's past run in the form guide, the same run in lookup and
the same run on the results page are literally one object. They cannot disagree
about the ET figure or the pace style, because there is only one of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from hkrd.query.race import get_horse_form, get_race
from hkrd.query.types import FormGuide, RunnerLine
from hkrd.query import pace as pace_q
from hkrd.store.connect import Connection, get_conn

__all__ = ["build_form_guide", "race_quality", "gear_timeline",
           "condition_fit", "head_to_head", "notes_for_horses",
           "ConditionCell"]


def build_form_guide(date: str, race_no: int, *, history: int = 6,
                     conn: Connection | None = None) -> FormGuide:
    """One race's card plus each runner's recent form."""
    own = conn is None
    conn = conn or get_conn()
    try:
        race = get_race(date, race_no, conn=conn)
        return FormGuide(
            race=race,
            history={
                r.horse_name: tuple(
                    get_horse_form(r.horse_name, limit=history, before=date, conn=conn))
                for r in race.runners
            },
        )
    finally:
        if own:
            conn.close()


# ── projected race pace ──────────────────────────────────────────────────────

# Design note 03 §7 fixes both the vocabulary and the meaning: pace is "one
# value per race", describing how the WHOLE RACE was run relative to what is
# typical for the distance and grade, on a five-step scale. An earlier version
# invented CRAWL/SLOW/EVEN/STRONG/HOT, which is a different scale wearing the
# same shape.
def race_quality(date: str, race_no: int, *, top: int = 5,
                 conn: Connection | None = None) -> list[dict[str, Any]]:
    """The top finishers of a past race and what each did in its NEXT start.

    Design brief 02 calls this the most distinctive thing in the current guide.
    It is what turns "won a race" into "won a race whose form held up" -- if the
    top five all ran poorly next time, that was a modest race, and the figure
    should be read accordingly.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        finishers = conn.execute("""
            SELECT horse_no, horse_name, place, dead_heat
            FROM runners
            WHERE race_date = ? AND race_no = ? AND place IS NOT NULL
            ORDER BY place LIMIT ?""", (date, race_no, top)).fetchall()

        out: list[dict[str, Any]] = []
        for f in finishers:
            nxt = conn.execute("""
                SELECT race_date, race_no, place, place_code
                FROM runners
                WHERE horse_name = ? AND race_date > ?
                ORDER BY race_date, race_no LIMIT 1""",
                (f["horse_name"], date)).fetchone()
            out.append({
                "place": f["place"],
                "dead_heat": bool(f["dead_heat"]),
                "horse_no": f["horse_no"],
                "horse_name": f["horse_name"],
                "next_date": nxt["race_date"] if nxt else None,
                "next_race_no": nxt["race_no"] if nxt else None,
                # None means "has not run since", which is different from
                # "ran and was unplaced" and must not render the same.
                "next_place": nxt["place"] if nxt else None,
                "next_place_code": nxt["place_code"] if nxt else None,
            })
        return out
    finally:
        if own:
            conn.close()


# ── condition fit ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConditionCell:
    """One slice of a horse's record against a condition.

    `n` is not decoration. Across 153 condition cells in prior analysis, 8
    cleared significance where 7.0 were expected by chance -- essentially
    nothing, though any single cell looked convincing alone. The UI is required
    to de-emphasise thin cells, so the count travels with the figure and
    `is_thin` states the judgement rather than leaving it to each page.
    """

    label: str
    starts: int
    wins: int
    places: int
    avg_figure: float | None

    @property
    def is_thin(self) -> bool:
        return self.starts < 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "starts": self.starts, "wins": self.wins,
            "places": self.places, "avg_figure": self.avg_figure,
            "is_thin": self.is_thin,
            # Never a bare rate: the count is part of the value.
            "win_display": f"{self.wins}/{self.starts}" if self.starts else "—",
            "place_display": f"{self.places}/{self.starts}" if self.starts else "—",
        }


def condition_fit(horse_name: str, *, distance: int | None = None,
                  course: str | None = None, going: str | None = None,
                  surface: str | None = None, before: str | None = None,
                  conn: Connection | None = None) -> list[ConditionCell]:
    """How this horse's record looks under today's conditions.

    Framed as context, not as an edge indicator. The market prices conditions
    efficiently; this panel answers "how does this horse fit today", never "this
    is under-bet".
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        cells: list[ConditionCell] = []
        slices: list[tuple[str, str, Any]] = []
        if distance:
            slices.append((f"{distance}m", "a.distance = ?", distance))
        if course:
            slices.append((f"Course {course}", "a.course = ?", course))
        if going:
            slices.append((f"Going {going}", "a.going = ?", going))
        if surface:
            slices.append((surface, "a.surface = ?", surface))

        for label, clause, value in slices:
            sql = f"""
                SELECT count(*) starts,
                       sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                       sum(CASE WHEN r.place <= 3 THEN 1 ELSE 0 END) places,
                       avg(e.figure) avg_figure
                FROM runners r
                JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
                LEFT JOIN runner_et e USING (race_date, race_no, horse_no)
                WHERE r.horse_name = ? AND r.place IS NOT NULL AND {clause}"""
            params: list[Any] = [horse_name.strip().upper(), value]
            if before:
                sql += " AND r.race_date < ?"
                params.append(before)
            row = conn.execute(sql, params).fetchone()
            cells.append(ConditionCell(
                label=label, starts=row["starts"] or 0, wins=row["wins"] or 0,
                places=row["places"] or 0,
                avg_figure=round(row["avg_figure"], 1) if row["avg_figure"] else None,
            ))
        return cells
    finally:
        if own:
            conn.close()


# ── head to head ─────────────────────────────────────────────────────────────

def head_to_head(horse_a: str, horse_b: str, *, before: str | None = None,
                 conn: Connection | None = None) -> dict[str, Any]:
    """Every previous meeting between two of today's runners.

    The weight swing is the gap BETWEEN them, not each horse's weight alone --
    a pair both going up 5lb has not changed relative to one another.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        a, b = horse_a.strip().upper(), horse_b.strip().upper()
        sql = """
            SELECT x.race_date, x.race_no, a.distance, a.going,
                   x.place pa, x.draw da, x.actual_weight wa,
                   y.place pb, y.draw db, y.actual_weight wb
            FROM runners x
            JOIN runners y ON y.race_date = x.race_date AND y.race_no = x.race_no
            JOIN races a ON a.race_date = x.race_date AND a.race_no = x.race_no
            WHERE x.horse_name = ? AND y.horse_name = ?
              AND x.place IS NOT NULL AND y.place IS NOT NULL"""
        params: list[Any] = [a, b]
        if before:
            sql += " AND x.race_date < ?"
            params.append(before)
        sql += " ORDER BY x.race_date DESC"
        meetings = [dict(r) for r in conn.execute(sql, params).fetchall()]

        a_ahead = sum(1 for m in meetings if m["pa"] < m["pb"])
        b_ahead = sum(1 for m in meetings if m["pb"] < m["pa"])
        last_gap = None
        if meetings and meetings[0]["wa"] is not None and meetings[0]["wb"] is not None:
            last_gap = meetings[0]["wa"] - meetings[0]["wb"]
        return {
            "horse_a": a, "horse_b": b, "meetings": meetings,
            "record": {"a": a_ahead, "b": b_ahead},
            "last_weight_gap": last_gap,
        }
    finally:
        if own:
            conn.close()


def weight_swing(last_gap: int | None, today_gap: int | None) -> int | None:
    """How much the weight gap between two horses has moved since they last met.

    Escalating badge thresholds are 4, 6 and 8 lb; most pairs will not clear
    them, which is correct rather than a bug.
    """
    if last_gap is None or today_gap is None:
        return None
    return abs(today_gap - last_gap)


# ── run notes ────────────────────────────────────────────────────────────────

def notes_for_horses(horse_names: list[str], *,
                     conn: Connection | None = None) -> dict[str, list[dict]]:
    """Every run note for a set of horses, keyed by horse.

    One query for the whole card rather than one per runner: a twelve-runner
    field expanded is twelve round trips otherwise, and the page opens rows
    faster than that.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        names = [n.strip().upper() for n in horse_names if n]
        if not names:
            return {}
        marks = ",".join("?" * len(names))
        out: dict[str, list[dict]] = {}
        for r in conn.execute(
                f"SELECT horse_name, race_date, race_no, note, written_at "
                f"FROM run_notes WHERE horse_name IN ({marks}) "
                f"ORDER BY race_date DESC, race_no DESC", names):
            out.setdefault(r["horse_name"], []).append(dict(r))
        return out
    finally:
        if own:
            conn.close()


def gear_timeline(horse_names: list[str], *, before: str | None = None,
                  conn: Connection | None = None) -> dict[str, dict[str, list[str]]]:
    """When each piece of gear FIRST appears in a horse's record.

    Design note 03 §3 wants first-time gear marked distinctly, since it is "one
    of the more reliable public signals bettors watch for". First-time cannot be
    read off the six runs the form guide shows — a blinker first worn eight runs
    back would render as new. It needs the whole record, so it is computed here
    where the whole record is, and returned as {horse: {run_key: [new tokens]}}.

    A horse's first appearance in the archive is not evidence that its gear is
    new, so the earliest recorded run never reports first-time gear.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        names = [n.strip().upper() for n in horse_names if n]
        if not names:
            return {}
        marks = ",".join("?" * len(names))
        sql = (f"SELECT horse_name, race_date, race_no, gear FROM runners "
               f"WHERE horse_name IN ({marks})")
        params: list[Any] = list(names)
        if before:
            sql += " AND race_date <= ?"
            params.append(before)
        sql += " ORDER BY horse_name, race_date, race_no"

        out: dict[str, dict[str, list[str]]] = {}
        seen: dict[str, set[str]] = {}
        first_run: set[str] = set()
        for r in conn.execute(sql, params):
            horse = r["horse_name"]
            tokens = {t.strip() for t in (r["gear"] or "").split("/") if t.strip()}
            known = seen.setdefault(horse, set())
            key = f"{r['race_date']}:{r['race_no']}"
            if horse not in first_run:
                first_run.add(horse)          # nothing is "new" on the first run
            else:
                new = sorted(tokens - known)
                if new:
                    out.setdefault(horse, {})[key] = new
            known |= tokens
        return out
    finally:
        if own:
            conn.close()



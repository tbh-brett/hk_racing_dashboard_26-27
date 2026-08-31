"""Race and horse queries. Read-only, returns RunnerLine.

Every filter is a WHERE clause. Nothing is filtered in pandas: a targeted query
against this data measures 0.000s where a full-table read measures 1.09s and the
spreadsheet path measured 15.33s.
"""
from __future__ import annotations

from collections.abc import Sequence

from hkrd.store.coerce import parse_running_positions, parse_section_times
from hkrd.store.connect import Connection, get_conn
from hkrd.query.types import RaceLine, RunnerLine

__all__ = ["get_race", "get_meeting", "get_horse_form", "list_meetings",
           "list_horses"]

# Derived tables are LEFT JOINed: a runner with no ET row still returns, with a
# null figure. A missing derived value must never make a runner disappear.
_LINE_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.draw, r.jockey,
       r.trainer, r.actual_weight, r.declared_weight, r.gear, r.place,
       r.place_code, r.dead_heat, r.finish_time, r.lengths_behind,
       r.running_positions, r.section_times, r.win_odds,
       a.venue, a.course, a.surface, a.going, a.distance, a.race_class,
       a.off_time,
       e.figure AS et_figure, e.len_vs_par AS et_len_vs_par,
       e.len_vs_race AS et_len_vs_race, e.sec_vs_par AS et_sec_vs_par,
       e.et_n_eff, e.confidence AS et_confidence,
       p.pace_style, p.early_dev, p.late_dev,
       s.sarr, s.sarr_rank,
       (SELECT count(*) FROM runners f
         WHERE f.race_date = r.race_date AND f.race_no = r.race_no) AS field_size,
       -- The tote's PLACE payout for THIS horse, per $10. `combination` is the
       -- horse number as published, so it is compared as text on both sides
       -- rather than cast: a cast would turn an unparseable combination into a
       -- silent zero, and a dividend that reads 0 is a losing ticket.
       (SELECT d.dividend_per_10 FROM dividends d
         WHERE d.race_date = r.race_date AND d.race_no = r.race_no
           AND d.pool = 'PLACE'
           AND trim(d.combination) = cast(r.horse_no AS TEXT)) AS place_dividend
FROM runners r
JOIN races a       ON a.race_date = r.race_date AND a.race_no = r.race_no
LEFT JOIN runner_et e   USING (race_date, race_no, horse_no)
LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
"""


def _tags(conn: Connection, date: str, race_no: int,
          horse_no: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (tags, lane_notes).

    Lane descriptors are stored namespaced 'lane:' so they share one table with
    trip tags while staying separable: they are an objective statement of where
    the horse travelled, not a judgement about its trip.
    """
    rows = conn.execute(
        "SELECT tag FROM runner_tags WHERE race_date=? AND race_no=? AND horse_no=? "
        "ORDER BY tag", (date, race_no, horse_no)).fetchall()
    tags, lanes = [], []
    for r in rows:
        tag = r["tag"]
        (lanes if tag.startswith("lane:") else tags).append(
            tag[5:] if tag.startswith("lane:") else tag)
    return tuple(tags), tuple(lanes)


def _comments(conn: Connection, date: str, race_no: int,
              horse_no: int) -> tuple[str | None, str | None]:
    """Returns (running_comment, incident_comment).

    Two accounts of the same race, kept apart. Corunning is HKJC's objective
    description of where the horse went; the incident report is the stewards'
    account of what went wrong. The form guide shows both.
    """
    rows = conn.execute(
        "SELECT source, comment_text FROM runner_comments "
        "WHERE race_date=? AND race_no=? AND horse_no=?",
        (date, race_no, horse_no)).fetchall()
    by_source = {r["source"]: r["comment_text"] for r in rows}
    return by_source.get("corunning"), by_source.get("incident")


def _to_line(row, tags: tuple[str, ...] = (), lane_notes: tuple[str, ...] = (),
             comments: tuple[str | None, str | None] = (None, None)) -> RunnerLine:
    return RunnerLine(
        race_date=row["race_date"], race_no=row["race_no"],
        horse_no=row["horse_no"], horse_name=row["horse_name"],
        draw=row["draw"], jockey=row["jockey"], trainer=row["trainer"],
        actual_weight=row["actual_weight"], declared_weight=row["declared_weight"],
        gear=row["gear"],
        venue=row["venue"], course=row["course"], surface=row["surface"],
        going=row["going"], distance=row["distance"], race_class=row["race_class"],
        field_size=row["field_size"] or 0,
        place=row["place"], place_code=row["place_code"],
        dead_heat=bool(row["dead_heat"]),
        finish_time=row["finish_time"], lengths_behind=row["lengths_behind"],
        running_positions=parse_running_positions(row["running_positions"]),
        section_times=parse_section_times(row["section_times"]),
        et_figure=row["et_figure"], et_len_vs_par=row["et_len_vs_par"],
        et_len_vs_race=row["et_len_vs_race"],
        et_sec_vs_par=row["et_sec_vs_par"], et_n_eff=row["et_n_eff"],
        et_confidence=row["et_confidence"],
        pace_style=row["pace_style"], early_dev=row["early_dev"],
        late_dev=row["late_dev"],
        sarr=row["sarr"], sarr_rank=row["sarr_rank"],
        tags=tags, lane_notes=lane_notes,
        running_comment=comments[0], incident_comment=comments[1],
        win_odds=row["win_odds"],
        place_dividend=row["place_dividend"],
    )


def get_race(date: str, race_no: int, *, conn: Connection | None = None) -> RaceLine:
    """One race, with its runners as RunnerLine."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            _LINE_SQL + " WHERE r.race_date = ? AND r.race_no = ? "
                        " ORDER BY r.horse_no", (date, race_no)).fetchall()
        if not rows:
            return RaceLine(race_date=date, race_no=race_no)
        runners = tuple(
            _to_line(r, *_tags(conn, date, race_no, r["horse_no"]),
                     comments=_comments(conn, date, race_no, r["horse_no"]))
            for r in rows)
        head = rows[0]
        return RaceLine(
            race_date=date, race_no=race_no, venue=head["venue"],
            course=head["course"], surface=head["surface"], going=head["going"],
            distance=head["distance"], race_class=head["race_class"],
            # RaceLine has carried an off_time field all along and nothing
            # filled it, so the Race Day header had no time to show and put the
            # venue in the slot instead — a value the global chrome already has.
            off_time=head["off_time"],
            field_size=len(runners), runners=runners,
        )
    finally:
        if own:
            conn.close()


def get_meeting(date: str, *, conn: Connection | None = None) -> list[RaceLine]:
    """Every race on one date."""
    own = conn is None
    conn = conn or get_conn()
    try:
        nos = [r[0] for r in conn.execute(
            "SELECT DISTINCT race_no FROM races WHERE race_date=? ORDER BY race_no",
            (date,))]
        return [get_race(date, n, conn=conn) for n in nos]
    finally:
        if own:
            conn.close()


def get_horse_form(horse_name: str, *, limit: int = 6, before: str | None = None,
                   conn: Connection | None = None) -> list[RunnerLine]:
    """A horse's recent runs, most recent first.

    Keyed on horse_name, never horse_id: horse_id is 0% populated from July 2026
    and degrading from April, so a join on it silently returns no history.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = _LINE_SQL + " WHERE r.horse_name = ?"
        params: list[object] = [horse_name.strip().upper()]
        if before:
            sql += " AND r.race_date < ?"
            params.append(before)
        sql += " ORDER BY r.race_date DESC, r.race_no DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [
            _to_line(r, *_tags(conn, r["race_date"], r["race_no"], r["horse_no"]),
                     comments=_comments(conn, r["race_date"], r["race_no"], r["horse_no"]))
            for r in rows]
    finally:
        if own:
            conn.close()


def list_meetings(*, limit: int = 50,
                  conn: Connection | None = None) -> list[dict[str, object]]:
    """Recent meeting dates, most recent first, for the global selector."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT race_date, venue, count(*) AS races FROM races "
            "GROUP BY race_date, venue ORDER BY race_date DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"race_date": r["race_date"], "venue": r["venue"],
                 "races": r["races"]} for r in rows]
    finally:
        if own:
            conn.close()


def list_horses(*, limit: int = 400, query: str | None = None,
                conn: Connection | None = None) -> list[dict]:
    """The horse index behind the command palette.

    retain_discard.md §3.3: six items in the nav, everything else reachable by
    typing. A horse is one of the things worth typing, so it needs to be
    addressable without a page of its own.

    Ordered by most recent run, not alphabetically -- the horse you want is
    almost always one that has run lately, and 1,967 names sorted A-Z puts the
    answer nowhere near the top.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = """
            SELECT horse_name, count(*) runs, max(race_date) last_run
            FROM runners
            WHERE horse_name IS NOT NULL AND horse_name != ''
        """
        params: list = []
        if query:
            sql += " AND horse_name LIKE ?"
            params.append(f"%{query.strip().upper()}%")
        sql += " GROUP BY horse_name ORDER BY last_run DESC, runs DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        if own:
            conn.close()

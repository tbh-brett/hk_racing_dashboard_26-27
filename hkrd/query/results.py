"""A finished race, assembled once — the Results page.

Everything the page shows is already computed somewhere: the runners are the
RunnerLine `get_race` returns, the pace band comes from `query/formguide`, the
race-quality retrospective from the same module, the bets from `query/bets` and
the booked horses from `query/blackbook`. This module joins them and adds the
three things that only make sense once a race has been run -- the dividends
paid, the stewards' report, and whether the money on the race came back.

The central rule the rebuild exists for applies hardest here, because this page
shows more numbers than any other: a number appears in exactly one place and is
computed exactly once. Nothing below recomputes a figure another module owns.

Design brief 04 puts the reading requirement on the quality verdict, and it is
the reason `provisional` is returned rather than assumed:

    "Whether the first five go on to win is what settles this — those runs have
     not happened yet. The verdict above is provisional and will be revised,
     not backfilled silently."
"""
from __future__ import annotations

from typing import Any

from hkrd.derive import sectionals as sx
from hkrd.query import bets as bets_q
from hkrd.query import formguide as fg_q
from hkrd.query.race import get_race
from hkrd.store.connect import Connection, get_conn

__all__ = ["meeting_results", "race_result", "dividends", "stewards",
           "race_money", "race_sectionals"]


def meeting_results(date: str, *, conn: Connection | None = None
                    ) -> dict[str, Any]:
    """Every race on the card and whether it has been run.

    A race with no finishing positions has not been run; the page says so and
    offers nothing else, rather than rendering an empty result grid that reads
    as a race in which nobody finished.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = [dict(r) for r in conn.execute("""
            SELECT a.race_no, a.venue, a.course, a.surface, a.going,
                   a.distance, a.race_class, a.off_time,
                   (SELECT count(*) FROM runners r
                     WHERE r.race_date = a.race_date
                       AND r.race_no = a.race_no) field_size,
                   (SELECT count(*) FROM runners r
                     WHERE r.race_date = a.race_date AND r.race_no = a.race_no
                       AND r.place IS NOT NULL) finished
            FROM races a WHERE a.race_date = ? ORDER BY a.race_no""", (date,))]
        for r in rows:
            r["run"] = r["finished"] > 0
        return {
            "race_date": date,
            "races": rows,
            "run": sum(1 for r in rows if r["run"]),
            "total": len(rows),
        }
    finally:
        if own:
            conn.close()


def dividends(date: str, race_no: int, *,
              conn: Connection | None = None) -> list[dict[str, Any]]:
    """What each pool paid, per $10 unit.

    HKJC publishes dividends per $10, and they are stored and shown that way
    rather than normalised to $1 -- the number on the page should be the number
    on the ticket.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        # Pools in the order the card pays them, not alphabetically: WIN before
        # PLACE before the exotics is how the result is read.
        order = ("WIN", "PLACE", "QIN", "QPL", "TRIO", "TCE", "FCT", "QTT", "F4")
        rows = [dict(r) for r in conn.execute(
            "SELECT pool, combination, dividend_per_10 FROM dividends "
            "WHERE race_date = ? AND race_no = ?", (date, race_no))]
        rank = {p: i for i, p in enumerate(order)}
        rows.sort(key=lambda r: (rank.get(r["pool"], len(order)),
                                 r["combination"]))
        return rows
    finally:
        if own:
            conn.close()


def stewards(date: str, race_no: int, *,
             conn: Connection | None = None) -> list[dict[str, Any]]:
    """The stewards' report, one entry per runner it names.

    Kept as prose against the horse it concerns rather than summarised. The
    trip tags derived from it live on the runner already (`runner_tags`), and
    duplicating the derivation here would give the page two versions of the
    same judgement.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT c.horse_no, c.comment_text, c.source, r.horse_name, r.place
            FROM runner_comments c
            LEFT JOIN runners r ON r.race_date = c.race_date
                               AND r.race_no = c.race_no
                               AND r.horse_no = c.horse_no
            WHERE c.race_date = ? AND c.race_no = ?
              AND c.comment_text IS NOT NULL AND c.comment_text != ''
            ORDER BY c.horse_no, c.source""", (date, race_no))]
    finally:
        if own:
            conn.close()


def race_money(date: str, race_no: int, *,
               conn: Connection | None = None) -> dict[str, Any]:
    """Did the bets on this race hit.

    An all-up passing through the race is included and marked: it is money at
    risk on this result, but its settlement belongs to the whole ticket rather
    than to this leg, so its P/L is shown against the ticket and named as
    spanning.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        placed = bets_q.bets_for_race(date, race_no, conn=conn)
        seen: dict[str, dict[str, Any]] = {}
        for row in placed:
            ticket = seen.setdefault(row["bet_id"], {
                "bet_id": row["bet_id"], "bet_type": row["bet_type"],
                "stake": row["stake"], "returned": row["returned"],
                "pnl": row["pnl"], "hit": row["hit"], "status": row["status"],
                # An all-up carries no race number of its own and spans more
                # than one leg race. Either alone would misread a Quartet
                # multi-banker, whose `legs` are positions in ONE race.
                "spans_races": row.get("race_no") is None
                or (row.get("legs") or 1) > 1,
                "selections": []})
            ticket["selections"].append({
                "horse_no": row.get("horse_no"),
                "horse_name": row.get("horse_name"),
                "place": row.get("place"),
                "is_banker": row.get("is_banker"),
            })
        tickets = list(seen.values())
        # Only the tickets whose settlement is this race's. An all-up's stake
        # rides on other races too, so summing it into a race P/L would say
        # money was won or lost here that was not.
        own_race = [t for t in tickets if not t["spans_races"]]
        staked = sum(t["stake"] or 0 for t in own_race)
        returned = sum(t["returned"] or 0 for t in own_race)
        return {
            "race_date": date, "race_no": race_no,
            "tickets": tickets,
            "bets": len(tickets),
            "spanning": sum(1 for t in tickets if t["spans_races"]),
            "staked": round(staked, 2),
            "returned": round(returned, 2),
            "pnl": round(returned - staked, 2),
            "roi": round((returned - staked) / staked, 3) if staked else None,
        }
    finally:
        if own:
            conn.close()


def race_result(date: str, race_no: int, *, conn: Connection | None = None
                ) -> dict[str, Any]:
    """One finished race, with everything the page renders about it."""
    own = conn is None
    conn = conn or get_conn()
    try:
        race = get_race(date, race_no, conn=conn)
        # A race with no runners at all does not exist. `get_race` returns an
        # empty RaceLine rather than raising, so without this the page would
        # render a blank card for R11 of a nine-race meeting -- silent success
        # and silent failure looking identical, which is the class of fault
        # this rebuild exists to remove.
        if not race.runners:
            raise LookupError(f"no race {race_no} on {date}")
        run = any(r.place is not None for r in race.runners)
        body: dict[str, Any] = {
            "race": race.to_dict() if hasattr(race, "to_dict") else {
                "race_date": race.race_date, "race_no": race.race_no,
                "venue": race.venue, "course": race.course,
                "surface": race.surface, "going": race.going,
                "distance": race.distance, "race_class": race.race_class,
                "field_size": race.field_size,
                "runners": [r.to_dict() for r in race.runners],
            },
            "run": run,
        }
        if not run:
            # A race that has not been run is not a race in which nobody
            # finished, and the page must not render one as the other.
            body.update({"dividends": [], "stewards": [], "money": None,
                         "pace": None, "quality": None, "booked": [],
                         "winning_time": None})
            return body

        body["dividends"] = dividends(date, race_no, conn=conn)
        body["stewards"] = stewards(date, race_no, conn=conn)
        body["money"] = race_money(date, race_no, conn=conn)
        body["pace"] = fg_q.race_pace(date, race_no, conn=conn)
        # The retrospective: what the first five did NEXT. Provisional by
        # construction -- those runs may not have happened yet.
        quality = fg_q.race_quality(date, race_no, conn=conn)
        body["quality"] = {
            "runners": quality,
            "provisional": any(q.get("next_start") is None for q in quality),
            "note": ("Whether the first five go on to win is what settles "
                     "this. Those runs have not all happened yet, so the "
                     "verdict is provisional and will be revised, not "
                     "backfilled silently."),
        }
        body["booked"] = _booked_that_ran(conn, date, race_no)
        body["sectionals"] = race_sectionals(date, race_no, conn=conn)
        winner = next((r for r in race.runners if r.place == 1), None)
        body["winning_time"] = winner.finish_time if winner else None
        body["winning_time_display"] = (
            winner.finish_time_display if winner else None)
        return body
    finally:
        if own:
            conn.close()


def _booked_that_ran(conn: Connection, date: str, race_no: int
                     ) -> list[dict[str, Any]]:
    """Blackbook horses in this race, and whether any money was on them.

    `backed` is a join, not something anyone had to remember to log — design
    brief 06: "the user shouldn't have to remember to record an absence."
    """
    return [dict(r) for r in conn.execute("""
        SELECT b.id entry_id, b.horse_name, b.status, b.added_date,
               b.reasoning, r.horse_no, r.place, r.win_odds,
               (SELECT group_concat(t.tag, ',') FROM blackbook_tags t
                 WHERE t.id = b.id) tags,
               EXISTS (SELECT 1 FROM bet_selections s
                        JOIN bets bt ON bt.bet_id = s.bet_id
                        WHERE bt.race_date = r.race_date
                          AND s.race_no = r.race_no
                          AND s.horse_no = r.horse_no) backed
        FROM blackbook b
        JOIN runners r ON r.horse_name = b.horse_name
        WHERE r.race_date = ? AND r.race_no = ? AND r.race_date >= b.added_date
        ORDER BY r.place IS NULL, r.place""", (date, race_no))]


def race_sectionals(date: str, race_no: int, *,
                    conn: Connection | None = None) -> dict[str, Any]:
    """Where the race was won, section by section, keyed by horse number.

    Keyed rather than listed because the page joins it onto the runner rows it
    already has — returning a second ordered list would give the page two
    orderings of the same field to keep in step.

    A race whose distance has no published section layout comes back empty
    with the reason, rather than raising: an unusual trip is not a reason for
    the whole result to fail to load.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        race = conn.execute(
            "SELECT distance FROM races WHERE race_date = ? AND race_no = ?",
            (date, race_no)).fetchone()
        if not race or race["distance"] is None:
            return {"by_horse": {}, "unavailable": "no distance recorded"}
        runners = [dict(r) for r in conn.execute(
            "SELECT race_date, race_no, horse_no, horse_name, section_times, "
            "running_positions FROM runners "
            "WHERE race_date = ? AND race_no = ?", (date, race_no))]
        try:
            rows = sx.race_sections(runners, race["distance"])
        except sx.SectionError as exc:
            return {"by_horse": {}, "unavailable": str(exc)}
        return {
            "by_horse": {str(r["horse_no"]): r for r in rows},
            "distance": race["distance"],
            "notable_seconds": sx.NOTABLE_SECONDS,
            "unavailable": None if rows else "no sectionals recorded",
        }
    finally:
        if own:
            conn.close()

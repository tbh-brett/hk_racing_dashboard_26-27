"""The blackbook as the REST of the dashboard sees it.

Two questions, both asked while looking at a race card rather than at the book:
what of mine is declared in this race, and what is declared anywhere on this
meeting. `query/meeting` and `query/raceday` read them; the Blackbook page
itself reads none of it.

Carved out of `query/blackbook.py` at 628 of a 600-line cap, and the seam was
already there — the same one `query/meeting` was split on. These two are the
only functions in the module that take a DATE rather than an entry, and the
only ones that have to answer "as at that race" rather than "as it stands".
That distinction is what both rules below encode, and keeping them next to the
two callers that need them is what stops a third page inventing a third answer.
"""
from __future__ import annotations

from typing import Any

from hkrd.query import triggers as trig_q
from hkrd.store.connect import Connection, get_conn

__all__ = ["for_race", "declared_on", "LIVE_AT_RACE_SQL"]

# is the bug this replaces: nothing here read `status` at all, so an entry
# retired by hand stayed live everywhere except the Blackbook page itself.
#
# An entry closed on a day nobody recorded reads as closed TODAY. It is
# certainly not live over a card being looked at now, which is the complaint;
# and over an archived card the honest default is the one that does not erase a
# thesis that was, as far as anything here knows, standing at the time. Every
# close from now on carries its date, so this fallback only ever covers entries
# retired before there was a column to record it in.
LIVE_AT_RACE_SQL = """
    (b.added_date <= r.race_date
     AND (b.status = 'active'
          OR coalesce(b.closed_date, date('now')) > r.race_date))"""

# Did this run ask the question the thesis was about — "first time at the trip
# since you booked it" rather than merely "runs today".
#
# The band counts the field inline because, unlike the record in
# `query/blackbook`, it has no grouped subquery to read a field size off. One
# subquery per BOOKED runner rather than one per runner on the card: the band
# is a handful of rows, not a field.
_BAND_TRIGGER_SQL = trig_q.met_sql(
    entry="b", runner="r", race="a",
    field_size=("(SELECT count(*) FROM runners z WHERE z.race_date = r.race_date"
                " AND z.race_no = r.race_no)"))




def _with_conditions(conn: Connection, rows: list[dict[str, Any]]
                     ) -> list[dict[str, Any]]:
    """The conditions in words, beside the flag saying whether they were met.

    The flag on its own is not enough for the band to earn the space: "meets
    its conditions" says nothing a reader can act on, where "1200-1400m · Turf"
    says what to look at. And `on_conditions` is 1 for an entry that has NO
    conditions, which is a different statement from "today is the day" — the
    text being empty is how the page tells those apart.
    """
    conditions = trig_q.for_entries([r["id"] for r in rows], conn=conn)
    for row in rows:
        row["conditions"] = conditions.get(row["id"], [])
        row["conditions_text"] = trig_q.describe(row["conditions"])
        row["on_conditions"] = bool(row["on_conditions"])
    return rows


def for_race(date: str, race_no: int, *, conn: Connection | None = None
             ) -> list[dict[str, Any]]:
    """Booked horses declared in one race — the Race Day blackbook band.

    Closed entries are returned too, flagged: a horse I once followed and gave
    up on is worth knowing about when it turns up, and dropping it is how the
    band silently under-reports. What it must NOT do is read as a live thesis —
    see `_LIVE_AT_RACE_SQL`, which is what every caller gates its highlight on.

    `status` is the entry's state NOW. On an archived race that is not the same
    question as "was I watching this horse that day", so `live_at_race` is
    returned alongside it — a booking made in July must not render as a live
    thesis over a race run in May.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return _with_conditions(conn, [dict(r) for r in conn.execute(f"""
            SELECT b.id, b.horse_name, b.status, b.confidence, b.reasoning,
                   b.added_date, b.closed_date, b.closed_reason, b.source_race,
                   r.horse_no, r.draw, r.jockey, r.win_odds,
                   b.added_date <= r.race_date AS booked_before_race,
                   {LIVE_AT_RACE_SQL} AS live_at_race,
                   {_BAND_TRIGGER_SQL} AS on_conditions,
                   (SELECT group_concat(t.tag) FROM blackbook_tags t
                     WHERE t.id = b.id) tag_csv
            FROM blackbook b
            JOIN runners r ON r.horse_name = b.horse_name
            LEFT JOIN races a ON a.race_date = r.race_date
                             AND a.race_no = r.race_no
            WHERE r.race_date = ? AND r.race_no = ?
            ORDER BY r.horse_no
        """, (date, race_no)).fetchall()])
    finally:
        if own:
            conn.close()


def declared_on(date: str, *, conn: Connection | None = None
                ) -> list[dict[str, Any]]:
    """Every booked horse declared across one meeting.

    "Highlight prominently: entries with a horse declared to run today. That's
    the moment the page earns its keep." — design brief 06.

    Carries the same `live_at_race` flag as `for_race`, and for the same
    reason: over an archived meeting a booking made months later is not a live
    thesis, and the band must be able to show that rather than imply it was.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return _with_conditions(conn, [dict(r) for r in conn.execute(f"""
            SELECT b.id, b.horse_name, b.status, b.confidence, b.added_date,
                   b.closed_date, b.closed_reason, b.reasoning,
                   r.race_no, r.horse_no, r.draw, r.win_odds,
                   b.added_date <= r.race_date AS booked_before_race,
                   {LIVE_AT_RACE_SQL} AS live_at_race,
                   {_BAND_TRIGGER_SQL} AS on_conditions,
                   (SELECT group_concat(t.tag) FROM blackbook_tags t
                     WHERE t.id = b.id) tag_csv
            FROM blackbook b
            JOIN runners r ON r.horse_name = b.horse_name
            LEFT JOIN races a ON a.race_date = r.race_date
                             AND a.race_no = r.race_no
            WHERE r.race_date = ?
            ORDER BY r.race_no, r.horse_no
        """, (date,)).fetchall()])
    finally:
        if own:
            conn.close()



"""What the automatic blackbook reads: which days are ready, and their runs.

`jobs/auto_book` decides; this only answers questions of the database. Kept
apart from `query/blackbook`, which is at its line cap and is about entries
rather than about the meetings they are written from.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.query import trials as trials_q
from hkrd.store.coerce import NO_COMMENT_PREFIX
from hkrd.store.connect import Connection

__all__ = ["BACKSTOP_DAYS", "TESTED_RUNS", "meetings", "ready_meetings",
           "meeting_runners",
           "ready_trial_days", "trial_read_at", "trial_day", "followed", "tested_entries"]

# HKJC writes a meeting's comments on running up DAYS after it: on 2026-09-21
# the meetings of 6, 9 and 13 September were published and 16 September, five
# days old, was not (`jobs/scrape_corunning.published`). The pass waits for
# them, because some traffic is only in them: two of the owner's traffic
# entries have nothing in the stewards' report at all. Waiting costs almost
# nothing — horses run a median 24 days apart and 4.6% run again within ten —
# but a meeting HKJC never writes up must not wait for ever, so after this
# many days it is read on the stewards' report and the sectionals alone, and
# every entry it writes says so.
BACKSTOP_DAYS = 8

# A system entry is tested over this many starts and then closed, unless the
# owner has adopted it (docs/auto-book.md). A fixed test is what
# makes the system's record a fair control for the owner's: every entry is
# asked the same question the same number of times.
TESTED_RUNS = 3

_PLACEHOLDER = NO_COMMENT_PREFIX + "%"


def _day(date: str, days: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


_MEETINGS_SQL = """
    WITH m AS (
        SELECT race_date,
               count(DISTINCT race_no) races,
               count(DISTINCT CASE WHEN place IS NOT NULL
                                   THEN race_no END) settled
        FROM runners WHERE race_date >= :since AND race_date <= :until
        GROUP BY race_date)
    SELECT m.race_date, m.races, m.settled,
           EXISTS (SELECT 1 FROM runner_comments c
                    WHERE c.race_date = m.race_date
                      AND c.source = 'corunning'
                      AND c.comment_text NOT LIKE :placeholder) commented,
           EXISTS (SELECT 1 FROM runner_comments c
                    WHERE c.race_date = m.race_date
                      AND c.source = 'incident') stewards,
           (SELECT p.ran_at FROM auto_book_pass p
             WHERE p.kind = 'results' AND p.source_date = m.race_date) read_at
    FROM m ORDER BY m.race_date
"""


def meetings(conn: Connection, *, since: str, until: str) -> list[dict[str, Any]]:
    """Every meeting in the window: how many races have a result, whether
    each of HKJC's two texts is in, and when the pass read it, if it has."""
    return [dict(r) for r in conn.execute(
        _MEETINGS_SQL, {"since": since, "until": until,
                        "placeholder": _PLACEHOLDER})]


def ready_meetings(conn: Connection, *, since: str, today: str
                   ) -> tuple[list[tuple[str, str]], list[str]]:
    """([(meeting, basis)], [meetings still waiting, with why]).

    A meeting is ready once every race on it has a result and either its
    comments on running are in ('full') or it is BACKSTOP_DAYS old and has a
    stewards' report ('stewards'). One already read is never offered again.
    Today's meeting is never ready: its results are still landing.
    """
    ready: list[tuple[str, str]] = []
    waiting: list[str] = []
    for r in meetings(conn, since=since, until=_day(today, -1)):
        date = r["race_date"]
        if r["read_at"]:
            continue
        if r["settled"] < r["races"]:
            waiting.append(f"{date}: {r['races'] - r['settled']} races "
                           "without a result")
        elif r["commented"]:
            ready.append((date, "full"))
        elif today >= _day(date, BACKSTOP_DAYS) and r["stewards"]:
            ready.append((date, "stewards"))
        elif today >= _day(date, BACKSTOP_DAYS):
            waiting.append(f"{date}: no stewards' report and no comments on "
                           "running, well past the backstop")
        else:
            waiting.append(f"{date}: comments on running not published "
                           f"(read without them from {_day(date, BACKSTOP_DAYS)})")
    return ready, waiting


def meeting_runners(conn: Connection, date: str) -> dict[int, list[dict[str, Any]]]:
    """Every runner that started, by race, with both HKJC texts beside it.

    Withdrawn horses are left out — they did not run, and counted into the
    field they would make every "6th of 12" read "6th of 13".
    """
    out: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute("""
        SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.place,
               r.lengths_behind, r.draw, r.win_odds, r.running_positions,
               p.late_dev,
               (SELECT c.comment_text FROM runner_comments c
                 WHERE c.race_date = r.race_date AND c.race_no = r.race_no
                   AND c.horse_no = r.horse_no AND c.source = 'incident') incident,
               (SELECT c.comment_text FROM runner_comments c
                 WHERE c.race_date = r.race_date AND c.race_no = r.race_no
                   AND c.horse_no = r.horse_no AND c.source = 'corunning'
                   AND c.comment_text NOT LIKE ?) corunning
        FROM runners r
        LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
        WHERE r.race_date = ?
          AND NOT (r.place IS NULL AND (r.place_code LIKE 'WV%'
                                        OR r.place_code LIKE 'WX%'))
        ORDER BY r.race_no, r.horse_no
    """, (_PLACEHOLDER, date)):
        out.setdefault(r["race_no"], []).append(dict(r))
    return out


def ready_trial_days(conn: Connection, *, since: str, today: str
                     ) -> tuple[list[str], list[str]]:
    """([trial days to read], [days still waiting]).

    HKJC puts a trial day's times up first and its positions and comments
    later (`jobs/scrape_trials`). A trial is only rated STANDOUT on a comment,
    so a day is read once every batch has one — or at the backstop.
    """
    days = [r[0] for r in conn.execute("""
        SELECT DISTINCT t.trial_date FROM trials t
        WHERE t.trial_date >= ? AND t.trial_date <= ?
          AND NOT EXISTS (SELECT 1 FROM auto_book_pass p
                           WHERE p.kind = 'trials'
                             AND p.source_date = t.trial_date)
        ORDER BY t.trial_date""", (since, today))]
    if not days:
        return [], []
    unfinished = {r[0] for r in conn.execute("""
        SELECT DISTINCT trial_date FROM (
            SELECT trial_date FROM trials WHERE trial_date >= ?
            GROUP BY trial_date, venue, trial_no
            HAVING max(length(coalesce(running_positions, ''))) = 0
                OR max(length(coalesce(comment_text, ''))) = 0)""",
        (min(days),))}
    ready, waiting = [], []
    for d in days:
        if d not in unfinished or today >= _day(d, BACKSTOP_DAYS):
            ready.append(d)
        else:
            waiting.append(f"trials {d}: HKJC has not finished publishing it")
    return ready, waiting


def trial_read_at(conn: Connection, date: str) -> str | None:
    """When the pass read this trial day, or None."""
    row = conn.execute("SELECT ran_at FROM auto_book_pass WHERE kind = 'trials' "
                       "AND source_date = ?", (date,)).fetchone()
    return row[0] if row else None


def trial_day(conn: Connection, date: str) -> list[dict[str, Any]]:
    """Every trial runner of one day, rated by the same engine as the Trials
    page and the Form Guide's trial band — one rating, not a second one that
    drifts from it."""
    rows = conn.execute(f"{trials_q._BATCH_SQL} WHERE t.trial_date = ? "
                        "ORDER BY t.venue, t.trial_no, t.place", (date,)).fetchall()
    return [trials_q._runner(r, r["field_size"], r["best_time"]) for r in rows]


def followed(conn: Connection, names: list[str]) -> set[str]:
    """The horses among these that already have a live entry, whoever wrote it."""
    if not names:
        return set()
    marks = ",".join("?" * len(names))
    return {r[0] for r in conn.execute(
        f"SELECT DISTINCT horse_name FROM blackbook "
        f"WHERE status = 'active' AND horse_name IN ({marks})", names)}


def tested_entries(conn: Connection) -> list[dict[str, Any]]:
    """Live system entries, not adopted, that have run TESTED_RUNS times since
    they were booked — with the day of the last of those runs and how they
    went. A run counts as `query/blackbook` counts it: a finished run on or
    after the booking day, never the run the entry was written from."""
    return [dict(r) for r in conn.execute("""
        WITH runs AS (
            SELECT b.id, r.race_date, r.place,
                   row_number() OVER (PARTITION BY b.id
                                      ORDER BY r.race_date, r.race_no) n
            FROM blackbook b
            JOIN runners r ON r.horse_name = b.horse_name
                          AND r.race_date >= b.added_date
                          AND NOT (b.source_date IS NOT NULL
                                   AND r.race_date = b.source_date
                                   AND r.race_no IS b.source_race_no)
            WHERE b.origin = 'system' AND b.adopted_date IS NULL
              AND b.status = 'active' AND r.place IS NOT NULL)
        SELECT id,
               max(CASE WHEN n = :k THEN race_date END) last_test,
               sum(CASE WHEN n <= :k AND place = 1 THEN 1 ELSE 0 END) wins,
               sum(CASE WHEN n <= :k AND place <= 3 THEN 1 ELSE 0 END) top3
        FROM runs GROUP BY id HAVING count(*) >= :k
        ORDER BY id""", {"k": TESTED_RUNS})]

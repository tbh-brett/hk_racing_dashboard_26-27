"""Per-source freshness — the strip that says what needs attention.

Design brief 07 §6 argues the interface for scraping should not be a page of
buttons, because that makes the mechanism the interface and leaves the user
remembering what to run and when. That is exactly how the pace column went
missing for weeks in the old system: nobody knew a step had not run.

    Card ✓2h   Odds ⚠47m   Results —   Trials ✓3d   Vet ✓2h

So the system says what is stale rather than the user remembering to check, and
"stale" is judged against what is NORMAL for that source. Odds go stale in
minutes; barrier trials are published weekly and a three-day-old trials scrape
is perfectly current. One shared threshold would call odds fine and trials
broken, or the reverse.

Two signals, deliberately combined:

  * **when the job last succeeded**, from `job_runs`. This is the honest
    answer to "is the pipeline running".
  * **what the data itself shows**, as a fallback for sources whose rows carry
    their own capture time — odds snapshots do.

A job that ran and wrote nothing is the failure this whole package exists to
make visible, so the row counts the run reported travel with the mark. Silent
success and silent failure must never look the same.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["strip", "SOURCES", "age_label"]

# `normal` is how old this source is allowed to get before it is worth saying
# something, in minutes. Taken from the design's own artboard values, which
# were chosen against how each source actually behaves.
SOURCES: tuple[dict[str, Any], ...] = (
    {"key": "card", "name": "Card", "job": "scrape_meeting:card", "normal": 720},
    {"key": "odds", "name": "Odds", "job": "scrape_odds", "normal": 15},
    {"key": "results", "name": "Results", "job": "scrape_meeting:results",
     "normal": 240},
    {"key": "trials", "name": "Trials", "job": "scrape_trials", "normal": 10080},
    {"key": "vet", "name": "Vet", "job": "scrape_meeting:vet", "normal": 720},
)


def age_label(minutes: float | None) -> str:
    """Minutes as the coarsest unit that still reads honestly."""
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{int(minutes)}m"
    if minutes < 1440:
        return f"{round(minutes / 60)}h"
    return f"{round(minutes / 1440)}d"


def _minutes_since(stamp: str | None, *, now: dt.datetime) -> float | None:
    if not stamp:
        return None
    try:
        when = dt.datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    # Job rows are written in UTC and may or may not carry the offset; compare
    # on one clock rather than across two kinds, which raises TypeError.
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (now - when).total_seconds() / 60.0)


def _last_success(conn: Connection) -> dict[str, tuple[str, str]]:
    """The most recent successful run per job, with what it reported."""
    rows = conn.execute("""
        SELECT job, max(finished_at) AS at, detail
          FROM job_runs
         WHERE ok = 1 AND finished_at IS NOT NULL
         GROUP BY job
    """).fetchall()
    return {r["job"]: (r["at"], r["detail"] or "") for r in rows}


def _odds_capture(conn: Connection) -> str | None:
    """Odds rows carry their own capture time, so they can answer directly.

    This is the one source whose freshness is a fact about the DATA rather than
    about the job, and it is the source where staleness matters most — a price
    from this morning misclassifies the concentration band in 60% of races.
    """
    row = conn.execute(
        "SELECT max(captured_at) v FROM odds_snapshots").fetchone()
    return row["v"] if row else None


def strip(*, conn: Connection | None = None,
          now: dt.datetime | None = None) -> dict[str, Any]:
    """One entry per source: how old, whether that is normal, and what it wrote.

    `mark` is the glyph the strip renders — ✓ current, ⚠ overdue, — never run.
    An overdue source is not an error: a results scrape has nothing to fetch
    before the first race of the day, and saying "⚠" there would train the eye
    to ignore the strip. `stale` is therefore reported separately from `ok`.
    """
    own = conn is None
    conn = conn or get_conn()
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        last = _last_success(conn)
        out = []
        for src in SOURCES:
            at, detail = last.get(src["job"], (None, ""))
            if src["key"] == "odds":
                # Prefer the data's own capture time; fall back to the job.
                at = _odds_capture(conn) or at
            minutes = _minutes_since(at, now=now)
            stale = minutes is not None and minutes > src["normal"]
            out.append({
                "key": src["key"], "name": src["name"],
                "last_success": at,
                "minutes": None if minutes is None else round(minutes),
                "age": age_label(minutes),
                "normal_minutes": src["normal"],
                "normal": age_label(src["normal"]),
                "stale": stale,
                "mark": "—" if minutes is None else ("⚠" if stale else "✓"),
                # Never a bare mark: the counts the run reported travel with it,
                # because a job that ran and wrote nothing is the failure this
                # strip exists to make visible.
                "detail": detail,
                "job": src["job"],
            })
        return {"sources": out,
                "stale": [s["key"] for s in out if s["stale"]],
                "never": [s["key"] for s in out if s["minutes"] is None]}
    finally:
        if own:
            conn.close()

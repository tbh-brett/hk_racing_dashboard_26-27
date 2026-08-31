"""What the scheduled jobs did, recorded where the dashboard can read it.

On a laptop a failed scrape is obvious: you ran it and watched it fail. On a
box that scrapes at 23:15 twice a week while nobody is looking, the failure
mode is different and worse — the dashboard keeps serving last week's card and
looks exactly as it did when it was right. A stale page and a current page are
indistinguishable, which is the fault this package exists to remove.

So every scheduled run opens a row before it starts and closes it when it ends.
An unclosed row is a run that was killed mid-flight, and that is a distinct
answer from "failed" and from "never ran" — three states the health endpoint
reports separately because the fix for each is different.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from hkrd.store.connect import get_conn, init_db

__all__ = ["record", "record_source", "running", "last_run", "recent"]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def record(conn: sqlite3.Connection, job: str, *, started_at: str,
           ok: bool | None, detail: str = "") -> None:
    """Write or close a run. Keyed on (job, started_at), so closing a row is
    the same call that opened it with the same timestamp."""
    conn.execute(
        "INSERT INTO job_runs (job, started_at, finished_at, ok, detail) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (job, started_at) DO UPDATE SET "
        "  finished_at = excluded.finished_at, "
        "  ok          = excluded.ok, "
        "  detail      = excluded.detail",
        (job, started_at, _now() if ok is not None else None,
         None if ok is None else int(ok), detail[:2000]))


@contextmanager
def running(job: str, db: Path | None = None) -> Iterator[dict]:
    """Open a row, hand back a dict the caller fills in, close it on the way out.

    The row is written BEFORE the work starts, not after. A run that opens a
    row and never closes it is visible as an interrupted run; a run that only
    writes on success is indistinguishable from one that never started.
    """
    conn = get_conn(db)
    init_db(conn)
    started = _now()
    outcome: dict = {"ok": False, "detail": ""}
    try:
        record(conn, job, started_at=started, ok=None)
        try:
            yield outcome
        except Exception as exc:                    # noqa: BLE001 — re-raised
            outcome["detail"] = f"{type(exc).__name__}: {exc}"
            outcome["ok"] = False
            raise
        finally:
            record(conn, job, started_at=started, ok=bool(outcome["ok"]),
                   detail=str(outcome["detail"]))
    finally:
        conn.close()


def last_run(conn: sqlite3.Connection, job: str) -> dict | None:
    """The most recent run of one job, whatever its outcome."""
    row = conn.execute(
        "SELECT job, started_at, finished_at, ok, detail FROM job_runs "
        "WHERE job = ? ORDER BY started_at DESC LIMIT 1", (job,)).fetchone()
    return _shape(row)


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """The last few runs of every job, newest first."""
    rows = conn.execute(
        "SELECT job, started_at, finished_at, ok, detail FROM job_runs "
        "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [r for r in (_shape(x) for x in rows) if r]


def _shape(row) -> dict | None:
    if row is None:
        return None
    return {"job": row["job"], "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            # Three states, not two. NULL is "started and never finished",
            # which needs a different response from "ran and failed".
            "ok": None if row["ok"] is None else bool(row["ok"]),
            "detail": row["detail"] or ""}


def record_source(conn: sqlite3.Connection, job: str, *, ok: bool,
                  detail: str, at: str | None = None) -> None:
    """Record one SOURCE's outcome inside a job that fetches several.

    `scrape_meeting` fetches the card, the results, the dividends and the vet
    records in one pass, and the freshness strip reports them separately —
    because they go stale at very different rates and because a vet scrape that
    failed while the card succeeded is a fact the strip has to be able to show.

    A closed row, written at the end, rather than the open-then-close pair
    `running` uses: these are steps inside a run that is already being tracked,
    not runs of their own.
    """
    record(conn, job, started_at=at or _now(), ok=ok, detail=detail)

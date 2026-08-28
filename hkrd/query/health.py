"""Is the box alive, and is the data current? Two different questions.

LIVENESS is for the platform. Fly restarts a machine whose health check fails,
so the only thing that may fail it is a fault a restart could fix — the
database file gone, the volume unmounted, the schema unreadable. Stale data
must not fail it: killing a machine that is serving correctly because Sunday's
meeting has not run yet turns a quiet week into an outage.

FRESHNESS is for the owner. On a laptop a failed scrape is obvious, because you
ran it and watched it fail. On a box that scrapes at 23:15 while nobody is
looking, a stale dashboard and a current one are the same page with a different
date on it, and nothing on screen says which one you are reading. So the answer
goes on the page.
"""
from __future__ import annotations

import datetime as dt

from hkrd.store import job_log
from hkrd.store.connect import Connection, db_path, get_conn

__all__ = ["liveness", "status", "STALE_AFTER_DAYS"]

# Hong Kong races roughly twice a week, so four days without a new meeting in
# the database is within a normal gap — the season also breaks for summer. Six
# is not, on a season week. This is a prompt to look, not a verdict.
STALE_AFTER_DAYS = 6


def liveness(conn: Connection | None = None) -> dict[str, object]:
    """Can we read the database? Nothing else, and nothing about its contents.

    This endpoint is served without a session because the platform calls it
    before one can exist, so it must not describe the data to whoever finds
    the URL. It answers one bit.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("SELECT count(*) FROM runners").fetchone()
        return {"ok": True}
    finally:
        if own:
            conn.close()


def status(conn: Connection | None = None, *,
           today: dt.date | None = None) -> dict[str, object]:
    """The full picture, for the signed-in owner: what is here and how old."""
    own = conn is None
    conn = conn or get_conn()
    today = today or dt.date.today()
    try:
        counts = {
            "runners": _count(conn, "runners"),
            "races": _count(conn, "races"),
            "trials": _count(conn, "trials"),
            "bets": _count(conn, "bets"),
            "blackbook": _count(conn, "blackbook"),
        }
        latest = conn.execute(
            "SELECT race_date, venue FROM races "
            "ORDER BY race_date DESC LIMIT 1").fetchone()
        latest_date = latest["race_date"] if latest else None

        age = None
        if latest_date:
            age = (today - dt.date.fromisoformat(latest_date)).days

        last = job_log.last_run(conn, "nightly")
        return {
            "database": str(db_path()),
            "counts": counts,
            "latest_meeting": latest_date,
            "latest_venue": latest["venue"] if latest else None,
            "days_since_meeting": age,
            # Stale is a prompt to look, never a reason to fail the health
            # check — see the module docstring.
            "stale": age is not None and age > STALE_AFTER_DAYS,
            "last_scrape": last,
            "scrape_state": _scrape_state(last),
        }
    finally:
        if own:
            conn.close()


def _scrape_state(last: dict | None) -> str:
    """Four answers, because the fix for each is different.

    never    — nothing has ever run. The schedule is not wired up.
    running  — a row was opened and never closed. Either it is running now, or
               the process was killed mid-scrape and the row is a headstone.
    failed   — it ran and said so. The detail names what broke.
    ok       — it ran and finished.
    """
    if last is None:
        return "never"
    if last["ok"] is None:
        return "running"
    return "ok" if last["ok"] else "failed"


def _count(conn: Connection, table: str) -> int:
    # Interpolated because a table name cannot be a bound parameter. Every
    # caller is a literal in this file; nothing from a request reaches here.
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

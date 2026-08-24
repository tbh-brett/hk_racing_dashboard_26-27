"""Market queries — concentration, movement, and odds coverage.

Two findings govern this module.

The market is the best predictor available. Its win odds rank horses better
(AUC 0.785) than every model built here, the best of which reaches 0.727. So
odds are an input, not an output, and nothing here tries to beat them.

Accuracy improves as money arrives: AUC 0.748 on the earliest snapshot, 0.778
on the latest, 0.794 at the final price. Every odds-dependent figure therefore
reads from the LATEST snapshot. Market concentration in particular moves from a
mean of 0.539 in the morning to 0.637 at post time, and 60% of races land in a
different band -- always making a race look weaker than it is, which causes
systematic under-covering of exactly the races a top-3 box performs best in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from hkrd.derive.probability import devig
from hkrd.store.connect import Connection, get_conn

__all__ = ["concentration", "band", "price_movement", "odds_coverage",
           "latest_prices", "snapshot_age_hours", "STALE_AFTER_HOURS"]

# A price captured well before the off is not the price the rule was measured
# on. Concentration moves from a mean of 0.539 in the morning to 0.637 at post
# time, so a figure computed from a stale snapshot understates it and
# under-covers exactly the races a top-3 box performs best in.
STALE_AFTER_HOURS = 3.0


def snapshot_age_hours(race_date: str, captured_at: str | None) -> float | None:
    """Hours between a capture and the race day's first race, roughly.

    Deliberately coarse -- off_time is not always stored, and the question being
    answered is "is this price from today, near the off" rather than an exact
    interval.
    """
    if not captured_at:
        return None
    try:
        captured = datetime.fromisoformat(captured_at)
        raceday = datetime.fromisoformat(f"{race_date}T13:00:00")
    except ValueError:
        return None
    return round((raceday - captured).total_seconds() / 3600.0, 1)


# Bands drive how many combinations get covered. Strike rate on a top-3 box was
# 35.9% in concentrated markets against 10.3% in weak ones.
_BANDS = ((0.70, "strong"), (0.55, "moderate"), (0.0, "weak"))


def band(value: float | None) -> str | None:
    if value is None:
        return None
    return next(name for threshold, name in _BANDS if value >= threshold)


def latest_prices(date: str, race_no: int, *, at: str = "latest",
                  conn: Connection | None = None) -> list[dict[str, Any]]:
    """Win and place prices from one capture.

    `at` is 'latest' or an ISO timestamp. It is not 'earliest': computing a
    figure from a morning snapshot misclassifies the concentration band in 60%
    of races, always downward.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if at == "latest":
            captured = conn.execute(
                "SELECT max(captured_at) FROM odds_snapshots "
                "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()[0]
        else:
            captured = at
        if not captured:
            return []
        rows = conn.execute(
            "SELECT horse_no, win_odds, place_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ? "
            "ORDER BY horse_no", (date, race_no, captured)).fetchall()
        return [{"horse_no": r["horse_no"], "win_odds": r["win_odds"],
                 "place_odds": r["place_odds"], "captured_at": captured}
                for r in rows]
    finally:
        if own:
            conn.close()


def concentration(date: str, race_no: int, *, at: str = "latest",
                  conn: Connection | None = None) -> dict[str, Any]:
    """Sum of the top-3 de-vigged win probabilities.

    This is the input to the coverage sizing rule, which is the strongest
    finding in the whole project precisely because it uses the market as an
    input rather than trying to beat it.
    """
    prices = latest_prices(date, race_no, at=at, conn=conn)
    live = [p for p in prices if p["win_odds"]]
    if len(live) < 3:
        return {"race_date": date, "race_no": race_no, "value": None,
                "band": None, "runners": len(live), "captured_at": None,
                "age_hours": None, "stale": None,
                "note": "fewer than three priced runners"}
    probs = sorted(devig([p["win_odds"] for p in live]), reverse=True)
    value = float(sum(probs[:3]))
    captured = prices[0]["captured_at"]
    age = snapshot_age_hours(date, captured)
    stale = age is not None and age > STALE_AFTER_HOURS
    out = {"race_date": date, "race_no": race_no, "value": round(value, 4),
           "band": band(value), "runners": len(live), "captured_at": captured,
           "age_hours": age, "stale": stale}
    if stale:
        # Say so rather than returning a number that looks post-time.
        out["note"] = (f"price is {age:.0f}h before racing; concentration read "
                       f"this early understates the band in ~60% of races")
    return out


def price_movement(date: str, race_no: int, *,
                   conn: Connection | None = None) -> list[dict[str, Any]]:
    """First captured price against the last, per runner.

    Descriptive only. Settlement is tote, so the final dividend is what is
    paid regardless of when the bet was struck -- drift is an operational
    signal about when to look, never a selection rule.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        bounds = conn.execute(
            "SELECT min(captured_at) f, max(captured_at) l FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()
        if not bounds or not bounds["f"] or bounds["f"] == bounds["l"]:
            return []
        rows = conn.execute(
            "SELECT a.horse_no, a.win_odds early, b.win_odds late "
            "FROM odds_snapshots a JOIN odds_snapshots b "
            "  ON a.race_date = b.race_date AND a.race_no = b.race_no "
            " AND a.horse_no = b.horse_no "
            "WHERE a.race_date = ? AND a.race_no = ? "
            "  AND a.captured_at = ? AND b.captured_at = ? "
            "  AND a.win_odds IS NOT NULL AND b.win_odds IS NOT NULL",
            (date, race_no, bounds["f"], bounds["l"])).fetchall()
        out = []
        for r in rows:
            change = (r["late"] - r["early"]) / r["early"]
            out.append({"horse_no": r["horse_no"], "early": r["early"],
                        "late": r["late"], "change_pct": round(100 * change, 1),
                        "direction": "shortened" if change < -0.02
                        else "drifted" if change > 0.02 else "flat"})
        return sorted(out, key=lambda x: x["change_pct"])
    finally:
        if own:
            conn.close()


def odds_coverage(*, conn: Connection | None = None) -> dict[str, Any]:
    """Which meetings have odds and which do not.

    This exists because the odds history is thin, and the reason is not what it
    was assumed to be. Snapshot rotation was blamed, but no race ever reached
    the rotation threshold -- capture simply did not run. Eight real meetings
    have complete results and no odds at all.

    A miss that nobody sees is the failure mode. Reporting coverage is what
    turns it into something you notice on the day.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        meetings = conn.execute("""
            SELECT a.race_date,
                   count(DISTINCT a.race_no) races,
                   count(DISTINCT o.race_no) races_with_odds,
                   count(o.captured_at) snapshots
            FROM races a
            LEFT JOIN odds_snapshots o ON o.race_date = a.race_date
            GROUP BY a.race_date ORDER BY a.race_date DESC""").fetchall()
        rows = [{"race_date": m["race_date"], "races": m["races"],
                 "races_with_odds": m["races_with_odds"],
                 "snapshots": m["snapshots"],
                 "complete": m["races_with_odds"] == m["races"]}
                for m in meetings]
        covered = [r for r in rows if r["races_with_odds"]]
        return {
            "meetings": len(rows),
            "meetings_with_any_odds": len(covered),
            "meetings_complete": sum(1 for r in covered if r["complete"]),
            "missing": [r["race_date"] for r in rows if not r["races_with_odds"]],
            "detail": rows,
        }
    finally:
        if own:
            conn.close()

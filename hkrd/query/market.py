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

from datetime import datetime, timedelta, timezone
from typing import Any

from hkrd.derive.probability import devig, pair_probability, place_probability
from hkrd.store.connect import Connection, get_conn

__all__ = ["concentration", "band", "price_movement", "odds_coverage",
           "latest_prices", "snapshot_age_hours", "STALE_AFTER_HOURS",
           "MIN_WINDOW_MINUTES", "warm", "place_probabilities",
           "ranked_pairs", "PLACE_PAYING_FIELD"]

# A price captured well before the off is not the price the rule was measured
# on. Concentration moves from a mean of 0.539 in the morning to 0.637 at post
# time, so a figure computed from a stale snapshot understates it and
# under-covers exactly the races a top-3 box performs best in.
STALE_AFTER_HOURS = 3.0

# Race dates are plain dates and their times are Hong Kong local. Named so a
# tz-aware capture lands on the same clock rather than eight hours out.
_HKT = timezone(timedelta(hours=8))

# Two captures closer together than this observed nothing. Reporting 0%
# movement from them claims the market held steady, which is a different
# and unsupported statement.
MIN_WINDOW_MINUTES = 20.0


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
    # `ingest/odds.py` stores the scraper's `scraped_at` verbatim and validates
    # only that it parses, so a snapshot can arrive tz-aware ("...+08:00") or
    # naive. `race_date` is a plain date whose 13:00 is Hong Kong local, so an
    # aware capture is converted into that clock rather than subtracted across
    # kinds -- which raised TypeError and took the concentration figure, and
    # every pre-bet panel reading it, down with it.
    if captured.tzinfo is not None:
        captured = captured.astimezone(_HKT).replace(tzinfo=None)
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


def _window_minutes(first: str | None, last: str | None) -> float | None:
    """Minutes between two captures, or None if either cannot be read."""
    if not first or not last:
        return None
    try:
        a = datetime.fromisoformat(first)
        b = datetime.fromisoformat(last)
    except ValueError:
        return None
    return round((b - a).total_seconds() / 60.0, 1)


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
        # How much time the two captures actually span. Without this a pair of
        # snapshots taken 77 seconds apart reports 0% movement on every runner,
        # which reads as "the market did not move" when it means "nothing was
        # observed". The real archive is full of exactly that case.
        window = _window_minutes(bounds["f"], bounds["l"])
        # An unreadable timestamp means the window is unknown, not that it was
        # wide. Treating unknown as observed would let a bad capture masquerade
        # as evidence of a steady market.
        observed = window is not None and window >= MIN_WINDOW_MINUTES

        out = []
        for r in rows:
            change = (r["late"] - r["early"]) / r["early"]
            out.append({"horse_no": r["horse_no"], "early": r["early"],
                        "late": r["late"], "change_pct": round(100 * change, 1),
                        "window_minutes": window, "observed": observed,
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


def warm() -> None:
    """Pay the numeric import cost up front.

    Measured: the first concentration figure took 1,008ms cold and 5.6ms warm,
    all of the difference being numpy's import. On race day the first request is
    the one that matters most, so the API calls this at startup.

    It lives here rather than in the router because api/ reaches data through
    query/ and must not import derive/ itself.
    """
    devig([2.0, 3.0, 4.0])


# HKJC pays three places in fields of seven or more, two below that. The
# transform depends on it, so it is named once here rather than inlined at each
# call site with a different guess.
PLACE_PAYING_FIELD = 7


def place_probabilities(date: str, race_no: int, *, at: str = "latest",
                        conn: Connection | None = None) -> dict[str, Any]:
    """P(top three) per runner, and what the 3× rule of thumb would have said.

    The correct transform is Harville with the Henery discount. The linear one
    -- `p / sum(p) * 3` -- is not a transform at all, and on a real card it
    overstates a short-priced banker by around 34 points: it will tell you a
    horse places 94.5% of the time when the honest figure is 60.3%.

    Both are returned deliberately. Design brief 06 Part 2 puts them side by
    side on the pre-bet panel, and a wrong number the user can SEE being wrong
    is worth more than one that was quietly corrected, because the rule of thumb
    is the thing they would otherwise reach for.
    """
    prices = latest_prices(date, race_no, at=at, conn=conn)
    live = [p for p in prices if p["win_odds"]]
    if len(live) < 2:
        return {"race_date": date, "race_no": race_no, "runners": [],
                "captured_at": None, "places": None,
                "note": "fewer than two priced runners"}

    odds = [p["win_odds"] for p in live]
    places = 3 if len(live) >= PLACE_PAYING_FIELD else 2
    win = devig(odds)
    harville = place_probability(odds, places=places)
    # The rule of thumb, reproduced exactly as it is usually applied so the
    # comparison is honest: win probability scaled by the number of places.
    linear = [min(1.0, float(w) * places) for w in win]

    runners = []
    for p, w, hv, ln in zip(live, win, harville, linear):
        runners.append({
            "horse_no": p["horse_no"],
            "win_odds": p["win_odds"], "place_odds": p["place_odds"],
            "win_pct": round(100 * float(w), 1),
            "place_pct": round(100 * float(hv), 1),
            "linear_pct": round(100 * ln, 1),
            "gap_points": round(100 * (ln - float(hv)), 1),
        })
    return {"race_date": date, "race_no": race_no,
            "captured_at": live[0]["captured_at"], "places": places,
            "field_priced": len(live), "runners": runners}


def ranked_pairs(date: str, race_no: int, *, top: int = 5, at: str = "latest",
                 conn: Connection | None = None) -> list[dict[str, Any]]:
    """The most likely quinella pairs, best first.

    Ranking pairs is worth about +25 ROI points over taking them at random
    within the pool. It does not clear the ~17.5% takeout -- nothing here does
    -- but it is the right way to choose which combinations to take, and the
    design shows it next to the ticket so the chosen set can be compared
    against the ranking rather than assumed to match it.
    """
    prices = latest_prices(date, race_no, at=at, conn=conn)
    live = [p for p in prices if p["win_odds"]]
    if len(live) < 2:
        return []
    pairs = pair_probability([p["win_odds"] for p in live])
    ordered = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [{"rank": i + 1,
             "horse_nos": [live[a]["horse_no"], live[b]["horse_no"]],
             "prob": round(100 * prob, 1)}
            for i, ((a, b), prob) in enumerate(ordered)]

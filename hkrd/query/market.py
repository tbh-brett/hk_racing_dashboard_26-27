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

from hkrd.derive.probability import devig
from hkrd.store.connect import Connection, get_conn

__all__ = ["concentration", "band", "odds_coverage",
           "latest_prices", "live_prices", "snapshot_age_hours",
           "STALE_AFTER_HOURS", "MIN_WINDOW_MINUTES", "warm",
           "late_move", "LATE_MINUTES", "NO_PRICE", "DAY_START",
           "day_start", "opening_capture",
           "changes_since", "MOVE_THRESHOLD", "CADENCE_MINUTES",
           "PAIR_CADENCE_MINUTES", "SETTLED_AFTER_MINUTES",
           "SETTLED_INTERVAL_MINUTES", "interval_for",
           "minutes_to_off", "poll_seconds", "MIN_POLL_SECONDS", "stamp",
           "poll_state"]

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

# HKJC quotes 999.0 on an open pool nobody has bet into yet. `ingest/odds`
# stores it as None now, but nothing ever deletes from `odds_snapshots`, so the
# rows written before that fix are still there and are still the EARLIEST
# capture of their race — which is the one every movement figure is measured
# from. Filtered on read as well as on write; see `ingest.odds.NO_PRICE`.
NO_PRICE = 999.0

# WHERE THE MARKET THIS DASHBOARD MEASURES BEGINS: midnight on the race day.
#
# HKJC opens a pool around midday the day BEFORE racing, and for the first
# hours of it there is no money in it — the 2026-09-09 capture at 12:01 on the
# 8th was 999.0 on every runner across eight races. Even once real prices
# appear, the day-before market is a handful of bets: measured from it,
# MACANESE MASTER read +309% into 9.0 on a card where its actual race-day move
# was 7.0 to 9.0. The big number was almost entirely the first stranger to bet
# on the race.
#
# So every figure that measures a CHANGE measures it from the first capture at
# or after this. The rows before it are kept — nothing deletes from
# `odds_snapshots` — and are still the opening price of the pool; they are just
# not the baseline for "how has this moved today", which is the question the
# card asks.
DAY_START = "T00:00:00"

# The window the money actually arrives in. The owner's own reading of it:
# "all the late money almost will not show until the final 5-0 minutes before
# the race actually starts, odds movement across the board is not as
# substantial beforehand." A single first-to-last percentage averages that
# window together with twenty hours of nothing, so the two are reported apart.
LATE_MINUTES = 10.0

# ── how fast this market moves, by distance from the off ─────────────────────
#
# One definition, two readers. `jobs/scrape_odds` uses it to decide when a race
# is worth re-pricing; `api/` uses it to tell the browser when it is worth
# asking again. They must not drift: a page polling every thirty seconds
# against an hourly capture is 120 pointless requests, and a page polling
# hourly against a minute-by-minute capture shows a price from before the money
# arrived.
#
# The shape is not uniform because the market is not. Money arrives in the last
# five to ten minutes; the overnight market barely moves. A flat schedule spends
# most of its rows recording that nothing happened and then samples the only
# interesting window three times.

# A race whose off time is this far past is settled. Its price cannot move
# again, and `odds_snapshots` is the one table nothing prunes, so re-capturing
# it would grow the table forever without adding a fact. Kept this long rather
# than cut at the off because a delayed start is real, and the price that
# settles the bet is the one at the ACTUAL off.
SETTLED_AFTER_MINUTES = 30

# Once a race is settled nothing can change again, so the last rung is not a
# rung at all — it is where the ladder stops. The capture already drops a
# settled race, but a PAGE left open on a finished meeting kept asking every
# thirty seconds forever, and the answer was always 304. An hour is the same
# distance from the action as the day before is.
SETTLED_INTERVAL_MINUTES = 60

# Read as: with more than 180 minutes to go, once an hour.
CADENCE_MINUTES: tuple[tuple[float, float], ...] = (
    (180, 60),      # the day before, and race morning
    (30, 15),
    (10, 5),
    (-SETTLED_AFTER_MINUTES, 1),
    (float("-inf"), SETTLED_INTERVAL_MINUTES),
)

# Pair odds are sampled on their own, coarser ladder, and the reason is size
# rather than taste. A 14-runner field has 14 win prices and 182 pair prices,
# so pairs are 88% of the rows; measured on disk at 106 bytes a row, pairs on
# the ladder above would be 0.93 GB a season, and nothing here ever deletes.
# The two differ where it costs least — overnight, where the market barely
# moves — and are identical where the money is.
PAIR_CADENCE_MINUTES: tuple[tuple[float, float], ...] = (
    (180, 180),     # the day before: eight captures, not twenty-one
    (30, 15),
    (-SETTLED_AFTER_MINUTES, 5),
    (float("-inf"), SETTLED_INTERVAL_MINUTES),
)


def minutes_to_off(date: str, off_time: str | None,
                   now: datetime | None = None) -> float | None:
    """Minutes until this race is due off. Negative once it is past."""
    try:
        hh, mm = str(off_time or "").split(":")[:2]
        off = datetime.fromisoformat(date).replace(hour=int(hh), minute=int(mm))
    except (ValueError, IndexError):
        return None
    return ((off - (now or datetime.now())).total_seconds() / 60.0)


def interval_for(to_off: float | None,
                 ladder: tuple[tuple[float, float], ...] = CADENCE_MINUTES
                 ) -> float:
    """The capture interval, in minutes, for a race this far from its off.

    An unreadable or missing off time falls back to the coarsest band rather
    than the finest: a card without times must not be priced every minute all
    day, and the freshness strip already says when odds last landed.
    """
    if to_off is None:
        return ladder[0][1]
    for threshold, every in ladder:
        if to_off > threshold:
            return every
    return ladder[-1][1]


# Half the capture interval, so a new price is on screen within half a cycle of
# landing, and never below this many seconds however close the off is.
MIN_POLL_SECONDS = 15


def poll_seconds(date: str, off_time: str | None,
                 now: datetime | None = None) -> int:
    """How long a page should wait before asking for this race again.

    Sampling twice as often as the capture is the classic answer and it is the
    right one here: it bounds how stale the screen can be at half a capture
    interval, without inventing a second schedule that can drift from the one
    the data is actually written on.
    """
    every = interval_for(minutes_to_off(date, off_time, now))
    return max(MIN_POLL_SECONDS, int(every * 60 / 2))


def poll_state(date: str, race_no: int | None = None, *,
               conn: Connection | None = None) -> tuple[str, str | None, int]:
    """`(stamp, captured_at, poll_after_seconds)` for one race, in one open
    connection.

    Three indexed lookups, answered without assembling anything. This is the
    whole cost of a poll that finds nothing new.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        identity = stamp(date, race_no, conn=conn)
        captured = identity.split("|", 1)[0]
        row = conn.execute(
            "SELECT off_time FROM races WHERE race_date = ?"
            + ("" if race_no is None else " AND race_no = ?"),
            (date,) if race_no is None else (date, race_no)).fetchone()
        after = poll_seconds(date, row["off_time"] if row else None)
        return identity, (captured if captured != "-" else None), after
    finally:
        if own:
            conn.close()


def stamp(date: str, race_no: int | None = None, *,
          conn: Connection | None = None) -> str:
    """A short identity for the newest thing an odds-driven page would read.

    This is what lets a page ask "anything new?" without the server building
    an answer. The Race Day card is 36 KB and ~220ms to assemble — form,
    veterinary history, head-to-head, blackbook, one query per runner — and on
    race day a page has to ask every thirty seconds to keep up with a market
    captured every minute. Assembling it each time to discover it had not
    changed is the whole cost, and it is avoidable: the two `max()` lookups
    below are indexed and answer in under a millisecond.

    Two clocks, because two different things change what the card says:

      the latest odds capture — the price, and everything derived from it
      the latest job run     — a re-scraped card, a scratching, a re-derive

    Blackbook edits are deliberately NOT in here. They are made by the person
    looking at the page, which has already re-rendered locally by the time any
    poll returns, and the next capture carries them anyway.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if race_no is None:
            odds = conn.execute(
                "SELECT max(captured_at) FROM odds_snapshots "
                "WHERE race_date = ?", (date,)).fetchone()[0]
        else:
            odds = conn.execute(
                "SELECT max(captured_at) FROM odds_snapshots "
                "WHERE race_date = ? AND race_no = ?",
                (date, race_no)).fetchone()[0]
        job = conn.execute(
            "SELECT max(finished_at) FROM job_runs").fetchone()[0]
        return f"{odds or '-'}|{job or '-'}"
    finally:
        if own:
            conn.close()


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
        return [{"horse_no": r["horse_no"], "win_odds": _priced(r["win_odds"]),
                 "place_odds": _priced(r["place_odds"]), "captured_at": captured}
                for r in rows]
    finally:
        if own:
            conn.close()


def day_start(date: str) -> str:
    """Midnight on the race day, as a comparable timestamp.

    `captured_at` is stored as an ISO string and compared as one, so this is a
    string too: a date and a time in the same format the captures carry.
    """
    return f"{date}{DAY_START}"


def opening_capture(conn: Connection, date: str, race_no: int, *,
                    column: str = "win_odds") -> str | None:
    """The capture every change on this race is measured from.

    The first REAL price at or after midnight on the race day. Two fallbacks,
    in this order, and each of them is a different fact rather than a guess:

      * no capture at or after midnight — an archived meeting whose only
        captures are from the day before, which is most of what the legacy
        import rescued. The earliest real price is then the only baseline
        there is, and it is still an honest one for that race.
      * no real price at all — nothing to measure, and the caller says so
        rather than reporting a change from a price that never existed.
    """
    since = day_start(date)
    for lower in (since, None):
        sql = ("SELECT min(captured_at) FROM odds_snapshots "
               "WHERE race_date = ? AND race_no = ? "
               f"  AND {column} IS NOT NULL AND {column} < ?")
        params: list[Any] = [date, race_no, NO_PRICE]
        if lower is not None:
            sql += " AND captured_at >= ?"
            params.append(lower)
        found = conn.execute(sql, params).fetchone()[0]
        if found:
            return found
    return None


def _priced(value: float | None) -> float | None:
    """A stored price, or None where the row is HKJC's placeholder.

    Applied on read because the rows are already written and this table is the
    one nothing is ever permitted to delete from.
    """
    return None if value is None or value >= NO_PRICE else value


def live_prices(date: str, race_no: int, *,
                conn: Connection | None = None) -> dict[int, dict[str, Any]]:
    """The latest capture, keyed by horse number.

    `runners.win_odds` is written by the RESULTS scrape and by nothing else,
    so it is the starting price and it does not exist until the race has been
    run — NULL for all 120 runners of a card declared two days out. Anything
    that has to price an upcoming race reads this instead, and every caller
    fills gaps with it rather than overwriting: where a starting price exists
    it is the final price, later than any snapshot, and it is what the models
    were fitted and backtested against.
    """
    return {p["horse_no"]: p
            for p in latest_prices(date, race_no, conn=conn)}


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


# A price is not "on the move" because it twitched. Two percent is the same
# threshold `price_movement` uses to call a direction, and using a second one
# here would let a runner be drifting in the strip and flat in its own row.
MOVE_THRESHOLD = 0.02


def changes_since(date: str, since: str | None, *,
                  conn: Connection | None = None) -> dict[str, Any]:
    """What moved across the whole meeting since a given moment.

    Design brief 01 asks the Race Day page to answer "has the market moved
    since I last looked, and on which horse" — and calls the favourite changing
    "the single most informative thing on the screen", which the old dashboard
    did not show at all. It happens in 44% of races.

    `since` is the viewer's own last visit, so this is per-person state and the
    page supplies it. With no `since` there is nothing to diff and the strip
    says so rather than inventing a baseline — a "changes since" computed from
    an arbitrary starting point is a number that looks informative and is not.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if not since:
            return {"race_date": date, "since": None, "drifts": 0,
                    "firmers": 0, "fav_swaps": [], "scratched": [],
                    "observed": False,
                    "note": "no earlier visit to compare against"}

        # `now` is the runner's price in the race's LATEST capture, not in the
        # runner's own last row. Those differ for exactly the case that matters:
        # a scratched runner still HAS rows, from before it came out, and
        # reading its own last row reports it as priced and moving normally.
        rows = conn.execute("""
            SELECT a.race_no, a.horse_no,
                   (SELECT win_odds FROM odds_snapshots b
                     WHERE b.race_date = a.race_date AND b.race_no = a.race_no
                       AND b.horse_no = a.horse_no AND b.captured_at <= ?
                     ORDER BY b.captured_at DESC LIMIT 1) AS before,
                   (SELECT win_odds FROM odds_snapshots c
                     WHERE c.race_date = a.race_date AND c.race_no = a.race_no
                       AND c.horse_no = a.horse_no
                       AND c.captured_at = latest.at) AS now
              FROM (SELECT DISTINCT race_date, race_no, horse_no
                      FROM odds_snapshots WHERE race_date = ?) a
              JOIN (SELECT race_no, max(captured_at) AS at
                      FROM odds_snapshots WHERE race_date = ?
                     GROUP BY race_no) latest ON latest.race_no = a.race_no
        """, (since, date, date)).fetchall()

        drifts = firmers = 0
        scratched: list[dict[str, Any]] = []
        by_race: dict[int, list[tuple[int, float | None, float | None]]] = {}
        for r in rows:
            before, now = r["before"], r["now"]
            by_race.setdefault(r["race_no"], []).append(
                (r["horse_no"], before, now))
            if before is None:
                continue
            if now is None:
                # Priced earlier and unpriced now: the runner came out.
                scratched.append({"race_no": r["race_no"],
                                  "horse_no": r["horse_no"]})
                continue
            change = (now - before) / before
            if change > MOVE_THRESHOLD:
                drifts += 1
            elif change < -MOVE_THRESHOLD:
                firmers += 1

        # The favourite changing is the headline, so it names the race rather
        # than being folded into a count.
        fav_swaps = []
        for race_no, entries in sorted(by_race.items()):
            was = [(h, b) for h, b, _ in entries if b]
            now = [(h, n) for h, _, n in entries if n]
            if not was or not now:
                continue
            before_fav = min(was, key=lambda x: x[1])[0]
            now_fav = min(now, key=lambda x: x[1])[0]
            if before_fav != now_fav:
                fav_swaps.append({"race_no": race_no, "from": before_fav,
                                  "to": now_fav})

        return {"race_date": date, "since": since, "drifts": drifts,
                "firmers": firmers, "fav_swaps": fav_swaps,
                "scratched": scratched, "observed": True,
                "runners_compared": sum(1 for r in rows if r["before"])}
    finally:
        if own:
            conn.close()

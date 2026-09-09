"""Odds movement, split into the part that carries information and the part
that does not.

WHAT MOVEMENT IS NOT. Settlement is tote. You are paid the final dividend
whatever price you took, so a horse that shortened from 9.0 to 3.4 pays the
same 3.4 to the person who backed it overnight and to the person who backed it
at the gate. There is no timing edge here and nothing in this module should
ever be used to build one — AGENTS.md states it as a rule, and a meeting's
worth of Fisher exact tests on 6 Sep failed to contradict it (best p = 0.113).

WHAT IT IS. Three things, and each of them wants a different number:

  **Confirmation.** The market ranks horses better than every model here — AUC
  0.785 against the best model's 0.727. When a model pick is being backed, the
  two agree; when it is drifting, the better forecaster disagrees with the
  worse one. That is a SIZING input, applied before the bet, and it needs the
  whole-window move.

  **Late money.** The owner's reading, and the reason the capture ladder
  tightens to once a minute: "all the late money almost will not show until the
  final 5-0 minutes before the race actually starts, odds movement across the
  board is not as substantial beforehand." A single first-to-last percentage
  averages that window together with twenty hours of nothing — a horse that sat
  at 9.0 all day and was smashed into 5.5 in the last four minutes reads
  identically to one that drifted in from 5.5 overnight and never moved again.
  They are not the same event, and only the second one is stable money.

  **Operations.** A price that moves hard and late is a reason to LOOK — a
  scratching elsewhere in the race, a jockey change, money that knows
  something. It says when to check, never what to back.

WHERE IT BELONGS. Race Day, which is the page that exists for the window this
is measured in. Not the Form Guide: a form line's market history would need the
odds archive to cover the runs on it, and 39 of 56 meetings have no snapshot at
all. It becomes a Form Guide column when the capture has a season behind it,
and putting it there now would render a column of dashes that quietly teaches
you the data is missing rather than saying so.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from hkrd.query.market import (
    LATE_MINUTES, MIN_WINDOW_MINUTES, MOVE_THRESHOLD, NO_PRICE,
    _window_minutes, day_start, minutes_to_off, opening_capture,
)
from hkrd.store.connect import Connection, get_conn

__all__ = ["late_move", "split_move", "price_movement",
           "LATE_MINUTES"]

# NAMING. `early` and `late` are the two ENDPOINT prices — the first real one
# and the last one captured — and they keep those names because that is the
# vocabulary `market.price_movement` already established and the card already
# reads. The ten-minute window is "the rush", which is what late money is
# called, so `rush_pct` cannot be misread as "the change up to the late price".


def _pct(early: float | None, late: float | None) -> float | None:
    if not early or not late:
        return None
    return round(100 * (late - early) / early, 1)


def _direction(change: float | None) -> str | None:
    """One vocabulary for both halves, so a runner cannot be firming in one
    column and shortening in another."""
    if change is None:
        return None
    if change < -100 * MOVE_THRESHOLD:
        return "shortened"
    return "drifted" if change > 100 * MOVE_THRESHOLD else "flat"


def split_move(date: str, race_no: int, *, off_time: str | None = None,
               late_minutes: float = LATE_MINUTES,
               conn: Connection | None = None) -> list[dict[str, Any]]:
    """Per runner: the whole move, and the last few minutes of it on its own.

    Three prices, not two. The FIRST real price of the race, the price at
    `late_minutes` before the off, and the last one captured. The overnight
    half is the first pair; the late half is the second, and it is the only
    half the owner's own reading of this market says is worth watching.

    A race with no capture inside the late window comes back with `late` null
    rather than falling back to the whole-window figure — a number computed
    over a different window and labelled as this one is worse than a gap.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if off_time is None:
            row = conn.execute(
                "SELECT off_time FROM races WHERE race_date = ? AND race_no = ?",
                (date, race_no)).fetchone()
            off_time = row["off_time"] if row else None

        # FROM MIDNIGHT ON THE RACE DAY. The pool opens around midday the day
        # before and takes almost nothing for hours; measured from there,
        # MACANESE MASTER read +309% into 9.0 when its race-day move was 7.0 to
        # 9.0. The earlier rows are kept and are still the opening price of the
        # pool — they are not the baseline for "how has this moved today".
        since = opening_capture(conn, date, race_no) or day_start(date)
        rows = conn.execute(
            "SELECT horse_no, captured_at, win_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at >= ? "
            "  AND win_odds IS NOT NULL AND win_odds < ? "
            "ORDER BY horse_no, captured_at",
            (date, race_no, since, NO_PRICE)).fetchall()
        if not rows:
            return []

        # The cut is a moment in the meeting's own clock, not a row count: a
        # race captured every minute and one captured hourly must both mean
        # "the last ten minutes" by the same definition.
        by_horse: dict[int, list[tuple[str, float]]] = {}
        for r in rows:
            by_horse.setdefault(r["horse_no"], []).append(
                (r["captured_at"], r["win_odds"]))

        out = []
        for horse_no, series in sorted(by_horse.items()):
            early_at, early = series[0]
            late_at, late = series[-1]
            cut = _at_or_before(series, date, off_time, late_minutes)
            window = _window_minutes(early_at, late_at)
            out.append({
                "horse_no": horse_no,
                "early": early, "early_at": early_at,
                "late": late, "late_at": late_at,
                "captures": len(series),
                # The whole window, which is what a sizing decision reads.
                # Same three field names `market.price_movement` returns, so
                # this is a drop-in for it rather than a second vocabulary.
                "change_pct": _pct(early, late),
                "direction": _direction(_pct(early, late)),
                "window_minutes": window,
                "observed": window is not None and window >= MIN_WINDOW_MINUTES,
                # And the window the money actually arrives in, alone.
                "rush_from": cut[1] if cut else None,
                "rush_from_at": cut[0] if cut else None,
                "rush_pct": _pct(cut[1], late) if cut else None,
                "rush_direction": _direction(_pct(cut[1], late)) if cut else None,
                "rush_minutes": late_minutes,
            })
        return out
    finally:
        if own:
            conn.close()


def _at_or_before(series: list[tuple[str, float]], date: str,
                  off_time: str | None, late_minutes: float
                  ) -> tuple[str, float] | None:
    """The last capture taken more than `late_minutes` before the off.

    That is the baseline the late move is measured from — the price as it stood
    going into the window, so the figure is what happened INSIDE the window and
    not what happened up to it. None where the race has no off time, or where
    every capture is already inside the window: with nothing before it there is
    no late move to report, only a first-to-last one that already exists.
    """
    if not off_time:
        return None
    found = None
    for at, price in series:
        try:
            when = datetime.fromisoformat(at)
        except ValueError:
            continue
        to_off = minutes_to_off(date, off_time, when)
        if to_off is None:
            return None
        if to_off > late_minutes:
            found = (at, price)
        else:
            break
    return found


def late_move(date: str, race_no: int, *, late_minutes: float = LATE_MINUTES,
              conn: Connection | None = None) -> dict[str, Any]:
    """The race's late money, summarised — who was backed, who was let go.

    Reported as a race-level answer because that is the question: not "did this
    horse move" but "where did the money go in the last ten minutes". A race
    where nothing moved is an answer too, and it is said rather than left as an
    empty list.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        runners = split_move(date, race_no, late_minutes=late_minutes, conn=conn)
        measured = [r for r in runners if r["rush_pct"] is not None]
        if not measured:
            return {"race_date": date, "race_no": race_no, "observed": False,
                    "late_minutes": late_minutes, "runners": runners,
                    "backed": [], "let_go": [],
                    "note": ("no capture before the last "
                             f"{late_minutes:.0f} minutes to measure from")}
        backed = sorted((r for r in measured if r["rush_direction"] == "shortened"),
                        key=lambda r: r["rush_pct"])
        let_go = sorted((r for r in measured if r["rush_direction"] == "drifted"),
                        key=lambda r: -r["rush_pct"])
        return {
            "race_date": date, "race_no": race_no, "observed": True,
            "late_minutes": late_minutes, "runners": runners,
            "measured": len(measured),
            "backed": backed[:5], "let_go": let_go[:5],
            # Said plainly, because the temptation this figure creates is to
            # read it as a tip and the settlement rule says it cannot be one.
            "note": ("settlement is tote — the late price is what everyone is "
                     "paid, so this is a sizing and an attention signal, never "
                     "a timing edge"),
        }
    finally:
        if own:
            conn.close()


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
        # From MIDNIGHT on the race day, not from the first capture ever taken.
        # The day-before pool is a handful of bets and it dominated every
        # figure measured from it — see DAY_START.
        first = opening_capture(conn, date, race_no)
        last = conn.execute(
            "SELECT max(captured_at) FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? "
            "  AND win_odds IS NOT NULL AND win_odds < ?",
            (date, race_no, NO_PRICE)).fetchone()[0]
        if not first or not last or first == last:
            return []
        bounds = {"f": first, "l": last}
        rows = conn.execute(
            "SELECT a.horse_no, a.win_odds early, b.win_odds late "
            "FROM odds_snapshots a JOIN odds_snapshots b "
            "  ON a.race_date = b.race_date AND a.race_no = b.race_no "
            " AND a.horse_no = b.horse_no "
            "WHERE a.race_date = ? AND a.race_no = ? "
            "  AND a.captured_at = ? AND b.captured_at = ? "
            "  AND a.win_odds IS NOT NULL AND b.win_odds IS NOT NULL "
            "  AND a.win_odds < ? AND b.win_odds < ?",
            (date, race_no, bounds["f"], bounds["l"],
             NO_PRICE, NO_PRICE)).fetchall()
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
                        # The two moments, so a caller can show what the figure
                        # is measured from. `split_move` carries them and these
                        # two answers must not differ in shape.
                        "early_at": bounds["f"], "late_at": bounds["l"],
                        "window_minutes": window, "observed": observed,
                        "direction": "shortened" if change < -0.02
                        else "drifted" if change > 0.02 else "flat"})
        return sorted(out, key=lambda x: x["change_pct"])
    finally:
        if own:
            conn.close()

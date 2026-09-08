"""Odds movement, split into the window that carries money and the one that
does not — and the placeholder price that made every runner look backed.

Two separate faults, both visible on one card:

**999.0.** HKJC's tote board has four digits and no way to say "nothing", so an
open pool nobody has bet into quotes 999.0 on every runner. Captured as a
price it becomes the FIRST price of the race, which is the one every movement
figure is measured from, and the whole field then reads as firming 98%. The
2026-09-09 capture at 12:01 the day before racing was 86 rows of it across
eight races; the 13:01 one had real prices on all of them, and not one of the
8,716 rows captured on a raced day is 999.0.

**One number for two windows.** The cadence ladder tightens to once a minute
through the last ten minutes because that is where the money arrives. Averaging
that window together with twenty hours of nothing throws away the only part
worth watching: on 2026-09-06 R9, runner 4 moved +1.6% over the whole day and
+21.6% inside the final ten minutes, so a single figure said FLAT about a horse
that was being let go.

None of this is a timing edge and nothing here should ever become one.
Settlement is tote — the late price is what everyone is paid.
"""
from __future__ import annotations

import pytest

from hkrd.query import market, movement
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-09"
OFF = "13:00"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """One race, captured from the day before through to the off.

    Runner 1 sits all day and is let go hard in the run-in — the case a single
    first-to-last figure calls flat. Runner 2 is backed steadily. Runner 3 has
    the placeholder as its earliest row, which is what the real capture wrote.
    """
    path = tmp_path / "move.db"
    conn = get_conn(path)
    init_db(conn)
    rows = []

    def cap(at, prices):
        for horse_no, win in prices.items():
            rows.append({"race_date": DATE, "race_no": 1, "horse_no": horse_no,
                         "captured_at": at, "win_odds": win,
                         "place_odds": None if win is None else round(win / 3, 1)})

    # The day before: the pool is open and unbet.
    cap(f"2026-09-08T12:01:00", {1: 999.0, 2: 999.0, 3: 999.0})
    cap(f"2026-09-08T13:01:00", {1: 9.0, 2: 12.0, 3: 4.0})
    cap(f"{DATE}T09:00:00", {1: 9.0, 2: 9.5, 3: 4.1})
    # Going into the last ten minutes.
    cap(f"{DATE}T12:45:00", {1: 9.2, 2: 7.0, 3: 4.0})
    # Inside them.
    cap(f"{DATE}T12:56:00", {1: 11.0, 2: 6.4, 3: 4.0})
    cap(f"{DATE}T12:59:00", {1: 13.0, 2: 6.0, 3: 4.0})

    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1200,
             "off_time": OFF}])
        upsert.upsert_odds_snapshots(conn, rows)
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── the placeholder ──────────────────────────────────────────────────────────

def test_the_placeholder_is_not_a_price(db):
    """Filtered on READ as well as on write, because nothing is ever permitted
    to delete from `odds_snapshots` and the rows are already there."""
    prices = market.latest_prices(DATE, 1)
    assert all(p["win_odds"] and p["win_odds"] < 999 for p in prices)

    early = market.latest_prices(DATE, 1, at="2026-09-08T12:01:00")
    assert [p["win_odds"] for p in early] == [None, None, None]


def test_the_opening_price_is_the_first_real_one(db):
    """Otherwise every runner opens at 999 and the card reads −98% across."""
    moves = {m["horse_no"]: m for m in market.price_movement(DATE, 1)}
    assert moves[1]["early"] == 9.0
    assert moves[1]["change_pct"] == pytest.approx(44.4, abs=0.1)
    assert all(abs(m["change_pct"]) < 100 for m in moves.values())


# ─── the two windows ──────────────────────────────────────────────────────────

def test_a_horse_that_sat_all_day_and_was_let_go_late_is_not_flat(db):
    """The case the single figure gets wrong. 9.0 to 13.0 over the whole day
    is a drift either way here, but the SHAPE is entirely in the run-in."""
    by_no = {m["horse_no"]: m for m in movement.split_move(DATE, 1)}
    one = by_no[1]
    assert one["early"] == 9.0 and one["late"] == 13.0
    assert one["rush_from"] == 9.2
    assert one["rush_pct"] == pytest.approx(41.3, abs=0.1)
    assert one["rush_direction"] == "drifted"
    # And the whole-window figure is still there, because that is what a sizing
    # decision reads.
    assert one["direction"] == "drifted"


def test_the_baseline_is_the_price_going_into_the_window(db):
    """Not the first capture inside it. The figure has to be what happened
    INSIDE the last ten minutes, not what happened up to them."""
    by_no = {m["horse_no"]: m for m in movement.split_move(DATE, 1)}
    assert by_no[2]["rush_from_at"] == f"{DATE}T12:45:00"
    assert by_no[2]["rush_from"] == 7.0
    assert by_no[2]["rush_pct"] == pytest.approx(-14.3, abs=0.1)
    assert by_no[2]["rush_direction"] == "shortened"


def test_a_price_that_did_not_move_late_is_flat_not_missing(db):
    by_no = {m["horse_no"]: m for m in movement.split_move(DATE, 1)}
    assert by_no[3]["rush_pct"] == 0.0
    assert by_no[3]["rush_direction"] == "flat"


def test_with_no_capture_before_the_window_there_is_no_late_figure(db):
    """A number computed over a different window and labelled as this one is
    worse than a gap."""
    # A window wider than the whole capture: every row is already inside it,
    # so there is no price from BEFORE it to measure against.
    got = movement.split_move(DATE, 1, late_minutes=2000)
    assert all(m["rush_pct"] is None for m in got)
    assert all(m["change_pct"] is not None for m in got), (
        "the whole-window figure still stands — only the late one is unknown")


def test_a_race_with_no_off_time_has_no_late_window(db):
    got = movement.split_move(DATE, 1, off_time="")
    assert all(m["rush_from"] is None for m in got)


def test_the_race_summary_says_where_the_money_went(db):
    got = movement.late_move(DATE, 1)
    assert got["observed"] is True and got["measured"] == 3
    assert [r["horse_no"] for r in got["backed"]] == [2]
    assert [r["horse_no"] for r in got["let_go"]] == [1]
    # And says what it is not, because the temptation this figure creates is to
    # read it as a tip and the settlement rule says it cannot be one.
    assert "never a timing edge" in got["note"]


def test_it_is_a_drop_in_for_the_figure_the_card_already_read(db):
    """The card reads `early`, `direction`, `change_pct`, `observed` and
    `window_minutes`. A second vocabulary would let one page call a runner
    firming while another calls it drifting."""
    fields = set(movement.split_move(DATE, 1)[0])
    assert {"early", "late", "change_pct", "direction", "observed",
            "window_minutes"} <= fields

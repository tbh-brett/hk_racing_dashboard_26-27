"""The head-to-head card, and the draw that was not there.

The card printed `GATE 11 12`. Those are not this pair's draws today — they are
each horse's draw at the LAST meeting, with today's never shown at all. So the
line answered a question nobody asked while looking exactly like it answered
the one they did, and the draw read as "gone" across the page.

Everything the design's card needs was already in `head_to_head`; the pair
builder dropped it on the way out.
"""
from __future__ import annotations

import pytest

from hkrd.query import raceday as rd
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

PAST, TODAY = "2026-02-11", "2026-07-15"


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "h.db")
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650,
             "race_class": "4"} for d in (PAST, TODAY)])
        # They met in February: SETANTA drawn 11 and 2nd on 120lb, TO INFINITY
        # drawn 12 and 4th on 129lb.
        upsert.upsert_runners(conn, [
            {"race_date": PAST, "race_no": 1, "horse_no": 5,
             "horse_name": "SETANTA", "draw": 11, "actual_weight": 120,
             "place": "2", "win_odds": 6.0},
            {"race_date": PAST, "race_no": 1, "horse_no": 6,
             "horse_name": "TO INFINITY", "draw": 12, "actual_weight": 129,
             "place": "4", "win_odds": 9.0},
        ])
        # Today: SETANTA out to 12, TO INFINITY in to 6.
        upsert.upsert_runners(conn, [
            {"race_date": TODAY, "race_no": 1, "horse_no": 5,
             "horse_name": "SETANTA", "draw": 12, "actual_weight": 133,
             "win_odds": 18.0},
            {"race_date": TODAY, "race_no": 1, "horse_no": 6,
             "horse_name": "TO INFINITY", "draw": 6, "actual_weight": 133,
             "win_odds": 27.0},
        ])
    yield conn
    conn.close()


def _pair(conn):
    card = rd.build_card(TODAY, 1, conn=conn)
    assert card["head_to_head"], "the pair met before and must be found"
    return card["head_to_head"][0]


def test_the_card_carries_today_s_draw_as_well_as_the_old_one(db) -> None:
    """The whole bug. Only the old one travelled, so the card could not show a
    move even though today's draw was on the runner beside it."""
    p = _pair(db)
    assert (p["a_gate_then"], p["a_gate_now"]) == (11, 12)
    assert (p["b_gate_then"], p["b_gate_now"]) == (12, 6)


def test_the_card_carries_how_each_finished_and_what_it_carried(db) -> None:
    """The weight swing is a change FROM something. Without the weights each
    horse carried when they last met, the line above it has no baseline."""
    p = _pair(db)
    assert (p["a_place"], p["a_weight_then"]) == (2, 120)
    assert (p["b_place"], p["b_weight_then"]) == (4, 129)


def test_the_swing_is_the_gap_between_them_not_each_horse_s_change(db) -> None:
    """Both went up — SETANTA 120→133, TO INFINITY 129→133 — but the gap
    between them moved 9lb, and the gap is what the pair is about."""
    p = _pair(db)
    assert p["gap_then"] == -9 and p["gap_now"] == 0
    assert p["swing"] == 9
    # Shown as context and nothing more: the swing earns no mark (query/h2h).
    assert "swing_tier" not in p


def test_the_beaten_horse_is_named_with_what_has_moved_since(db) -> None:
    """TO INFINITY was 4th to SETANTA's 2nd. Then it was drawn one gate
    outside SETANTA (12 to 11); today six inside it (6 to 12): seven gates
    better relatively, which is a measured swing. The 9lb it gained on the
    weights since is not."""
    p = _pair(db)
    assert (p["beaten_no"], p["beaten_name"]) == (6, "TO INFINITY")
    assert p["draw_swing"] == 7
    assert any("drawn 7 gates" in t for t in p["turn"])
    assert not any("lb" in t for t in p["turn"])


def test_a_missing_draw_does_not_invent_one(db) -> None:
    with transaction(db):
        db.execute("UPDATE runners SET draw = NULL "
                   "WHERE race_date = ? AND horse_no = 5", (TODAY,))
    p = _pair(db)
    assert p["a_gate_then"] == 11
    assert p["a_gate_now"] is None

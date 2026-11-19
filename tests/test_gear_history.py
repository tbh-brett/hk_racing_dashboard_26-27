"""A horse's own record in its gear (query/gear_history.py).

The owner's case, 19 Nov 2026: PRIVATE WEALTH, blinkers first time on 25 Oct
and keen; blinkers on 11 Nov and keen throughout, a warning on its manners;
blinkers off on 18 Nov and second at 19.0. The card said only "B OFF".

What the chips claim follows what was confirmed: blinkers make horses keener
(a caution about how it will run); nothing here is shown to beat the price,
so no chip is coloured as if it did.
"""
from __future__ import annotations

import pytest

from hkrd.derive.gear import SWITCH_ON_CODES, raced_keen
from hkrd.query import gear as gear_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

TODAY = "2026-11-18"
DATES = ("2026-10-01", "2026-10-25", "2026-11-11")
KEEN = "Raced keenly throughout, gave ground quickly inside 150M."
SETTLED = "Settled midfield on the rail, ran on fairly in the straight."
NOTHING = "No Comments on Running information for this horse."


# ─── reading a keen run ───────────────────────────────────────────────────────

@pytest.mark.parametrize("texts, keen", [
    ([KEEN], True),
    (["Travelled keenly in the middle stages."], True),
    (["M Chadwick stated that leaving the 1000 Metres, when racing keenly, his saddle shifted."], True),
    (["Over-raced early."], True),
    ([SETTLED], False),
    ([NOTHING, SETTLED], False),
    ([NOTHING], None),
    ([None, ""], None),
    ([], None),
    # a horse's NAME is not a description of the run (15 reports, 19 Nov 2026)
    (["Near the 800 Metres shifted in and away from KEEN MOLLY."], False),
    (["KEEN UNITY was slow to begin."], False),
])
def test_a_keen_run_is_read_and_an_unwritten_one_is_not_a_settled_one(texts, keen):
    assert raced_keen(texts) is keen


def test_the_switch_on_family_is_the_gear_that_narrows_sight():
    """A pricker (PC) steers and a hood settles; neither sharpens a horse."""
    assert {"B", "V", "CP"} <= SWITCH_ON_CODES
    assert not {"PC", "H", "XB", "TT", "SR"} & SWITCH_ON_CODES


# ─── the record ───────────────────────────────────────────────────────────────

# (horse_no, name, [(gear, race call, stewards) for DATES], today's gear)
HORSES = [
    (1, "OFF AFTER KEEN", [("XB", SETTLED, None), ("XB/B1", KEEN, "In the middle stages raced keenly."),
                           ("B/XB", KEEN, None)], "B-/XB"),
    (2, "KEPT ON", [("XB", SETTLED, None), ("XB/B1", KEEN, None), ("B/XB", KEEN, None)], "B/XB"),
    (3, "SETTLED IN THEM", [("XB", SETTLED, None), ("B1", KEEN, None), ("B", SETTLED, None)], "B"),
    (4, "UNREAD", [("XB", SETTLED, None), ("B1", KEEN, None), ("B", None, None)], "B-"),
    (5, "HOOD", [("H", SETTLED, None), ("H", SETTLED, None), ("H", SETTLED, None)], "H-"),
    (6, "FIRST TIME", [("TT", KEEN, None), ("TT", SETTLED, None), ("TT", SETTLED, None)], "B1/TT"),
    (7, "NO GEAR RECORD", [("XB/B1", KEEN, None), ("B", KEEN, None), (None, KEEN, None)], "B-"),
    (8, "SECOND TRY", [("XB", SETTLED, None), ("XB", SETTLED, None), ("XB/B1", SETTLED, None)], "B2/XB"),
    (9, "CHEEKS OFF", [("CP", SETTLED, None), ("CP", SETTLED, None), ("CP", SETTLED, None)], "CP-"),
]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "gear_history.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}
            for d in (*DATES, TODAY)])
        for no, name, history, today in HORSES:
            for date, (gear, call, stewards) in zip(DATES, history):
                upsert.upsert_runners(conn, [
                    {"race_date": date, "race_no": 1, "horse_no": no, "horse_name": name,
                     "gear": gear, "place": "5", "win_odds": 12.0, "running_positions": "7 6 5"}])
                for source, text in (("corunning", call), ("incident", stewards)):
                    if text:
                        conn.execute(
                            "INSERT INTO runner_comments (race_date, race_no, horse_no, "
                            "comment_text, source) VALUES (?, 1, ?, ?, ?)", (date, no, text, source))
            upsert.upsert_runners(conn, [
                {"race_date": TODAY, "race_no": 1, "horse_no": no,
                 "horse_name": name, "gear": today}])
        # A withdrawn run is not a run: it would otherwise count as unread.
        upsert.upsert_races(conn, [
            {"race_date": "2026-10-12", "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-10-12", "race_no": 1, "horse_no": 1,
             "horse_name": "OFF AFTER KEEN", "gear": "B", "place_code": "WV"}])
    conn.close()
    return path


@pytest.fixture()
def card(db):
    conn = get_conn(db)
    try:
        return gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()


def piece(card, no, code):
    return next(p for p in card[no]["pieces"] if p["code"] == code)


def test_blinkers_off_after_keen_runs_in_them_is_named_without_claiming_an_edge(card):
    """Private Wealth's path: the chip must say more than "B OFF", and no more
    than was shown: the result effect did not hold in 2024-25."""
    b = piece(card, 1, "B")
    assert b["signal"] == "off_after_keen" and b["notable"] is True
    assert (b["history"]["keen"], b["history"]["read"]) == (2, 2)
    assert (b["history"]["without_keen"], b["history"]["without_read"]) == (0, 1)
    assert "Raced keenly in 2 of its 2 runs in blinkers" in b["detail"]
    assert "not settled" in b["detail"]


def test_the_timeline_shows_each_run_and_which_source_called_it_keen(card):
    """The reading comes with its evidence: newest first, token, sources, early, finish."""
    tl = piece(card, 1, "B")["timeline"]
    assert [r["race_date"] for r in tl] == ["2026-11-11", "2026-10-25", "2026-10-01"]
    assert [r["token"] for r in tl] == ["B", "B1", None]
    assert tl[1]["keen_by"] == ["race call", "stewards"]
    assert tl[0]["keen_by"] == ["race call"] and tl[2]["keen"] is False
    assert (tl[0]["first_call"], tl[0]["finish"], tl[0]["odds"]) == (7, 5, 12.0)


def test_a_withdrawal_is_not_a_run_in_the_gear(card):
    assert piece(card, 1, "B")["history"]["runs"] == 2


def test_kept_on_after_a_keen_run_is_a_caution_about_how_it_will_run(card):
    """Keen again 14.6% of the time against 8% of all runs: confirmed."""
    b = piece(card, 2, "B")
    assert b["state"] == "on" and b["signal"] == "keen_in"
    assert b["notable"] is True, "a plain B is not news; a plain B on a horse keen in it is"
    assert "not shown to cost it against the price" in b["detail"]


def test_keen_once_but_settled_last_time_in_them_says_nothing(card):
    b = piece(card, 3, "B")
    assert b["signal"] is None and b["notable"] is False
    assert (b["history"]["keen"], b["history"]["read"]) == (1, 2)


def test_a_last_run_nobody_described_is_not_evidence_either_way(card):
    b = piece(card, 4, "B")
    assert b["history"]["last_keen"] is None
    assert b["signal"] is None and b["state"] == "off"


def test_a_hood_off_is_a_lead_with_its_record_not_a_claim(card):
    h = piece(card, 5, "H")
    assert h["signal"] == "lead_off" and h["notable"] is True
    assert "Hood off: 247 runs" in h["detail"] and "not a confirmed edge" in h["detail"]


def test_cheek_pieces_off_is_the_other_lead(card):
    cp = piece(card, 9, "CP")
    assert cp["signal"] == "lead_off" and "Cheek pieces off: 530 runs" in cp["detail"]


def test_first_time_blinkers_carry_the_confirmed_keen_rate_and_the_horses_own(card):
    b = piece(card, 6, "B")
    assert b["state"] == "first" and b["signal"] is None
    assert "11.4% of runs against 7.7% for comparable horses" in b["detail"]
    assert "raced keenly in 1 of its 3 runs on record" in b["detail"]


def test_the_second_run_in_blinkers_is_still_inside_the_keen_window(card):
    b = piece(card, 8, "B")
    assert b["state"] == "second" and b["signal"] is None
    assert "first three runs in blinkers" in b["detail"]


def test_a_trial_in_gear_it_has_raced_in_since_is_not_a_withheld_schooling(db):
    """PRIVATE WEALTH read TRIALLED B ("schooled, not yet committed to") on the
    card where its blinkers came off after two races in them."""
    conn = get_conn(db)
    try:
        conn.execute("INSERT INTO trials (trial_date, trial_no, horse_name, venue, gear, place) "
                     "VALUES ('2026-10-13', 4, 'OFF AFTER KEEN', 'ST', 'B/XB', 3), "
                     "('2026-11-13', 2, 'HOOD', 'ST', 'B', 1)")
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    assert [t["signal"] for t in found[1]["trial_gear"] if t["code"] == "B"] == ["raced_since"]
    # A horse that has never raced in them is still the withheld case.
    assert [t["signal"] for t in found[5]["trial_gear"]] == ["withheld"]


def test_a_run_with_no_gear_on_record_is_not_a_run_without_gear(card):
    """April-July 2026 has no gear column: a NULL is not "no blinkers"."""
    b = piece(card, 7, "B")
    assert b["history"]["runs"] == 2 and b["history"]["without_read"] == 0
    assert b["signal"] == "off_after_keen"

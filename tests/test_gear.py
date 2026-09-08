"""Gear changes, and the barrier trial that comes before one.

Design note 03 §3 calls first-time gear "one of the more reliable public
signals bettors watch for". The dashboard was reconstructing it by diffing one
run's gear string against the run before, and HKJC had been publishing it
outright the whole time: `B1` is blinkers first time, `B-` is blinkers off,
`B2` is second time. 737 `TT1`, 584 `B1`, 651 `B-` in the archive, every one of
them rendered as literal text in a column with nothing saying what it meant.

Three things are pinned here, in order of how badly getting them wrong would
mislead:

  * a NULL gear column is not "no gear". The results write erased gear for
    April to July 2026 — July has 0 of 641 runs on record — and comparing
    against a blank reports every piece on every horse as newly applied.
  * `--` is the card's own placeholder, not a piece of gear called "--".
  * a horse trialled in blinkers and NOT wearing them today is a fact worth
    stating and is not a tip. It says the stable has been preparing something
    that has not happened yet.
"""
from __future__ import annotations

import pytest

from hkrd.derive.gear import FOCUS_CODES, describe, parse_pieces
from hkrd.query import gear as gear_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

TODAY = "2026-09-06"
LAST = "2026-08-16"


# ─── the suffix HKJC already publishes ────────────────────────────────────────

@pytest.mark.parametrize("raw, code, state", [
    ("B", "B", "on"),
    ("B1", "B", "first"),
    ("B2", "B", "second"),
    ("B-", "B", "off"),
    ("TT1", "TT", "first"),
    ("CP-", "CP", "off"),
])
def test_the_state_is_read_not_inferred(raw, code, state):
    piece = parse_pieces(raw)[0]
    assert (piece.code, piece.state) == (code, state)
    assert piece.raw == raw


def test_a_removed_piece_is_not_worn():
    off = parse_pieces("B-")[0]
    assert off.worn is False and off.notable is True
    on = parse_pieces("B")[0]
    assert on.worn is True and on.notable is False, (
        "gear worn every start is not a signal; a chip that fires on everyone "
        "stops being read")


def test_the_cards_placeholder_is_not_a_piece_of_gear():
    """2,552 of 19,868 archived runs carry `--`, which means no gear at all."""
    assert parse_pieces("--") == ()
    assert parse_pieces("") == () and parse_pieces(None) == ()


def test_an_unknown_code_keeps_its_own_token():
    """A chip that says "blinkers" about something else is worse than one that
    says the code."""
    piece = parse_pieces("ZZ")[0]
    assert (piece.code, piece.name) == ("ZZ", "ZZ")


def test_the_headgear_family_is_named_and_the_breathing_aids_are_not_in_it():
    """A tongue tie in a trial is a breathing aid, not a schooling step, and it
    is on nearly every runner — reporting it beside a blinker trial would bury
    the one signal under the commonest piece of gear in racing."""
    assert "B" in FOCUS_CODES and "V" in FOCUS_CODES and "H" in FOCUS_CODES
    assert "TT" not in FOCUS_CODES and "SR" not in FOCUS_CODES


def test_the_tooltip_says_what_the_chip_cannot():
    assert describe(parse_pieces("B1")[0]) == "blinkers for the first time on record"
    assert describe(parse_pieces("B-")[0]) == "blinkers taken off for this run"


# ─── what the record adds ─────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """One horse with a gear history, one with a hole in it, and two trials."""
    path = tmp_path / "gear.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}
            for d in ("2026-06-01", LAST, TODAY)])
        # REINSTATED: blinkers on in June, off in August, back on today.
        # HKJC writes a plain `B` for the third one.
        for date, gear in (("2026-06-01", "B/TT"), (LAST, "B-/TT"),
                           (TODAY, "B/TT")):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": 1,
                 "horse_name": "COMEBACK", "gear": gear, "place": "3"}])
        # A HOLE: last start has no gear column at all.
        for date, gear in (("2026-06-01", "TT"), (LAST, None), (TODAY, "TT")):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": 2,
                 "horse_name": "NO RECORD", "gear": gear, "place": "4"}])
        # SCHOOLED: trialled in blinkers, wearing them today for the first time.
        upsert.upsert_runners(conn, [
            {"race_date": LAST, "race_no": 1, "horse_no": 3,
             "horse_name": "SCHOOLED", "gear": "TT", "place": "5"},
            {"race_date": TODAY, "race_no": 1, "horse_no": 3,
             "horse_name": "SCHOOLED", "gear": "B1/TT", "place": "1"}])
        # WITHHELD: trialled in blinkers and is not wearing them.
        upsert.upsert_runners(conn, [
            {"race_date": LAST, "race_no": 1, "horse_no": 4,
             "horse_name": "WITHHELD", "gear": "TT", "place": "6"},
            {"race_date": TODAY, "race_no": 1, "horse_no": 4,
             "horse_name": "WITHHELD", "gear": "TT", "place": "2"}])
        conn.executemany(
            "INSERT INTO trials (trial_date, trial_no, horse_name, venue, gear, "
            "place) VALUES (?, ?, ?, 'ST', ?, ?)",
            [("2026-08-30", 1, "SCHOOLED", "B", 1),
             ("2026-08-30", 1, "WITHHELD", "B", 2),
             # A tongue tie in a trial is not a schooling step.
             ("2026-08-30", 1, "COMEBACK", "TT", 3)])
    conn.close()
    return path


def test_gear_back_on_after_a_break_is_named(db):
    """HKJC's `1` means first time EVER, so a re-instatement is a plain `B` —
    indistinguishable in the string from gear worn every start for two seasons.
    Only the record can tell them apart."""
    conn = get_conn(db)
    try:
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    piece = next(p for p in found[1]["pieces"] if p["code"] == "B")
    assert piece["reinstated"] is True and piece["notable"] is True
    assert "back on" in piece["detail"]
    # And the piece it has worn throughout is not news.
    assert next(p for p in found[1]["pieces"]
                if p["code"] == "TT")["notable"] is False


def test_a_missing_gear_column_is_not_a_gear_change(db):
    """July 2026 has 0 of 641 runs with gear on record. Compared against a
    blank, every piece on every horse reads as newly applied — which is what
    the first version of this did on a real card."""
    conn = get_conn(db)
    try:
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    assert found[2]["comparable"] is False
    assert not any(p["reinstated"] for p in found[2]["pieces"])
    assert not any(p["state"] == "off" for p in found[2]["pieces"])


def test_a_horse_schooled_in_blinkers_and_wearing_them_is_the_strong_case(db):
    conn = get_conn(db)
    try:
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    trial = found[3]["trial_gear"]
    assert [t["signal"] for t in trial] == ["applied"]
    assert trial[0]["code"] == "B" and trial[0]["new_today"] is True
    assert trial[0]["trial_date"] == "2026-08-30"


def test_a_horse_schooled_in_blinkers_and_not_wearing_them_is_stated(db):
    """The owner's observation, and the reason this exists: the trainer has
    prepared something and not committed to it. A fact, not a tip."""
    conn = get_conn(db)
    try:
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    trial = found[4]["trial_gear"]
    assert [t["signal"] for t in trial] == ["withheld"]
    assert trial[0]["declared_today"] is False
    assert "NOT declared today" in trial[0]["detail"]


def test_a_tongue_tie_in_a_trial_is_not_a_schooling_step(db):
    conn = get_conn(db)
    try:
        found = gear_q.for_race(TODAY, 1, conn=conn)
    finally:
        conn.close()
    assert found[1]["trial_gear"] == []


def test_a_trial_from_a_previous_preparation_does_not_count(db):
    """Six weeks. Reaching further back reports a horse as freshly schooled in
    gear it has since raced in twice."""
    conn = get_conn(db)
    try:
        far = gear_q.trial_school(conn, ["SCHOOLED"], before=TODAY,
                                  since="2026-09-01")
    finally:
        conn.close()
    assert far == {}

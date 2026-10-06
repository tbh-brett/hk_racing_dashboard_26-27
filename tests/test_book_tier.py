"""The book's tiers: which of the book's horses in a race to back.

The owner's decisions of 2026-10-05 under test: a trial note counts whether or
not the horse is booked; the note must be the owner's, on a trial since the
last run, written before the off; an entry with three starts since it was
booked (or renewed) is stale and muted, never closed; renewing restarts the
count; and the trial note carries two optional taps.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import write_notes
from hkrd.query import book_tier as bt
from hkrd.store.connect import get_conn, init_db, transaction

CARD = "2026-10-01"


def _db(tmp_path):
    path = tmp_path / "hkrd.db"
    conn = get_conn(path)
    init_db(conn)
    return path, conn


def _race(conn, date, race_no, runners, off="16:10"):
    conn.execute("INSERT INTO races (race_date, race_no, venue, distance, off_time) "
                 "VALUES (?, ?, 'ST', 1200, ?)", (date, race_no, off))
    for no, name, place, odds in runners:
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name, "
                     "place, win_odds) VALUES (?, ?, ?, ?, ?, ?)",
                     (date, race_no, no, name, place, odds))


def _entry(conn, eid, name, added, *, tags=(), origin="owner", status="active",
           source_date=None, source_race_no=None):
    conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status, origin, "
                 "source_date, source_race_no) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (eid, name, added, status, origin, source_date, source_race_no))
    for t in tags:
        conn.execute("INSERT INTO blackbook_tags (id, tag) VALUES (?, ?)", (eid, t))


def _note(conn, name, trial_date, written_at, note="asked, reacted very well",
          trial_no=1):
    conn.execute("INSERT INTO trial_notes (horse_name, trial_date, trial_no, note, "
                 "written_at) VALUES (?, ?, ?, ?, ?)",
                 (name, trial_date, trial_no, note, written_at))


def _card(conn):
    _race(conn, CARD, 7, [(1, "NOTED", None, None), (2, "BOOKED", None, None),
                          (3, "PLAIN", None, None), (4, "LATE NOTE", None, None),
                          (5, "OLD NOTE", None, None), (6, "SYS NOTE", None, None)])


# ── the rule itself ──────────────────────────────────────────────────────────

def test_one_tier_per_runner_in_the_owners_order():
    entry = {"id": "bb_1", "tags": ["traffic"]}
    note = [{"note": "x", "trial_date": "2026-09-19"}]
    # A fresh trial note beats a stale entry: the note is the evidence.
    assert bt.classify(entry=entry, starts=5, notes=note, standout=None,
                       run_note=None)["tier"] == "NOTE"
    assert bt.classify(entry=entry, starts=0, notes=[], standout={"trial_date": "x"},
                       run_note=None)["tier"] == "STANDOUT"
    assert bt.classify(entry=entry, starts=3, notes=[], standout=None,
                       run_note=None)["tier"] == "STALE"
    assert bt.classify(entry=entry, starts=2, notes=[], standout=None,
                       run_note=None)["tier"] == "EXCUSE"
    quiet = {"id": "bb_2", "tags": ["improvement"]}
    assert bt.classify(entry=quiet, starts=0, notes=[], standout=None,
                       run_note=None)["tier"] == "QUIET"
    assert bt.classify(entry=None, starts=0, notes=[], standout=None,
                       run_note={"note": "y"})["tier"] == "RUN_NOTE"
    assert bt.classify(entry=None, starts=0, notes=[], standout=None,
                       run_note=None) is None


def test_a_standout_trial_counts_only_for_a_horse_in_the_book():
    # The system books every STANDOUT trial; one the owner dismissed stays out.
    assert bt.classify(entry=None, starts=0, notes=[], standout={"trial_date": "x"},
                       run_note=None) is None


# ── the trial note, read as at the off ───────────────────────────────────────

def test_a_trial_note_counts_whether_or_not_the_horse_is_booked(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        _card(conn)
        _race(conn, "2026-09-06", 1, [(1, "NOTED", 5, 10.0), (2, "OLD NOTE", 4, 8.0)])
        _note(conn, "NOTED", "2026-09-19", "2026-09-28T09:51:00+00:00")
    tiers = bt.for_meeting(CARD, conn=conn, with_record=False)
    assert tiers[(7, 1)]["tier"] == "NOTE"
    assert tiers[(7, 1)]["in_book"] is False
    assert tiers[(7, 1)]["note"]["note"] == "asked, reacted very well"


def test_only_the_owners_note_since_the_last_run_written_before_the_off(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        _card(conn)
        _race(conn, "2026-09-20", 2, [(1, "OLD NOTE", 3, 6.0)])
        # Written after the off (16:10 HKT = 08:10 UTC): hindsight.
        _note(conn, "LATE NOTE", "2026-09-19", "2026-10-01T09:00:00+00:00")
        # On a trial before the horse's last run: already used.
        _note(conn, "OLD NOTE", "2026-09-10", "2026-09-12T09:00:00+00:00")
        # The system's words are not the owner's eye.
        _note(conn, "SYS NOTE", "2026-09-19", "2026-09-20T09:00:00+00:00",
              note="System: Won trial 4. Rated STANDOUT.")
    tiers = bt.for_meeting(CARD, conn=conn, with_record=False)
    assert (7, 4) not in tiers
    assert (7, 5) not in tiers
    assert (7, 6) not in tiers


# ── stale, and renewing ──────────────────────────────────────────────────────

def _three_runs(conn, name):
    for i, d in enumerate(("2026-09-06", "2026-09-13", "2026-09-23"), start=1):
        _race(conn, d, 9, [(1, name, 6, 12.0)])


def test_three_starts_since_booking_is_stale_and_renewing_restarts_it(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        _card(conn)
        _three_runs(conn, "BOOKED")
        _entry(conn, "bb_0001", "BOOKED", "2026-09-01", tags=["traffic"])
    tiers = bt.for_meeting(CARD, conn=conn, with_record=False)
    assert tiers[(7, 2)]["tier"] == "STALE"
    assert tiers[(7, 2)]["starts"] == 3
    assert bt.stale_entries(today=CARD, conn=conn) == {"bb_0001": 3}
    conn.close()

    write_notes.renew("bb_0001", today="2026-09-24", db=path)
    conn = get_conn(path)
    tiers = bt.for_meeting(CARD, conn=conn, with_record=False)
    assert tiers[(7, 2)]["tier"] == "EXCUSE"
    assert tiers[(7, 2)]["starts"] == 0
    assert bt.stale_entries(today=CARD, conn=conn) == {}


def test_a_win_since_booking_restarts_the_count(tmp_path):
    # LUCK IS BACK, 6 Oct: booked in May, won at 32.0 on 13 Sep, and was still
    # marked stale for its starts since May. A win is the thesis paying.
    path, conn = _db(tmp_path)
    with transaction(conn):
        _card(conn)
        _race(conn, "2026-09-06", 9, [(1, "BOOKED", 6, 12.0)])
        _race(conn, "2026-09-13", 9, [(1, "BOOKED", 1, 32.0)])
        _race(conn, "2026-09-23", 9, [(1, "BOOKED", 5, 5.1)])
        _entry(conn, "bb_0001", "BOOKED", "2026-05-04", tags=["traffic"])
    tiers = bt.for_meeting(CARD, conn=conn, with_record=False)
    assert tiers[(7, 2)]["starts"] == 1
    assert tiers[(7, 2)]["tier"] == "EXCUSE"
    assert bt.stale_entries(today=CARD, conn=conn) == {}


def test_the_run_an_entry_was_booked_from_is_not_one_of_its_starts(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        _card(conn)
        _three_runs(conn, "BOOKED")
        _entry(conn, "bb_0001", "BOOKED", "2026-09-06", tags=["traffic"],
               source_date="2026-09-06", source_race_no=9)
    assert bt.for_meeting(CARD, conn=conn, with_record=False)[(7, 2)]["starts"] == 2


def test_renew_refuses_a_closed_entry_and_an_unadopted_system_one(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        _entry(conn, "bb_0001", "SHUT", "2026-09-01", status="retired")
        _entry(conn, "bb_0002", "SYSTEMS", "2026-09-01", origin="system")
    conn.close()
    with pytest.raises(ValueError):
        write_notes.renew("bb_0001", db=path)
    with pytest.raises(ValueError):
        write_notes.renew("bb_0002", db=path)
    with pytest.raises(KeyError):
        write_notes.renew("bb_9999", db=path)


# ── the race in one line, and the record ─────────────────────────────────────

def test_the_race_line_names_what_to_back_and_says_when_there_is_nothing():
    note = {"tier": "NOTE", "in_book": False}
    quiet = {"tier": "QUIET", "in_book": True}
    line = bt.race_line([{"horse_no": 7, "horse_name": "A", "book_tier": note},
                         {"horse_no": 10, "horse_name": "B", "book_tier": dict(note, in_book=True)},
                         {"horse_no": 2, "horse_name": "C", "book_tier": quiet},
                         {"horse_no": 3, "horse_name": "D", "book_tier": None}])
    assert line["back"] == [7, 10]
    assert line["tone"] == "back"
    assert "back #7, #10 to win" in line["text"]
    assert line["text"].startswith("Book 2 here")
    none = bt.race_line([{"horse_no": 2, "horse_name": "C", "book_tier": quiet}])
    assert "no book bet in this race" in none["text"]
    assert bt.race_line([{"horse_no": 3, "horse_name": "D", "book_tier": None}]) is None


def test_the_record_scores_each_tier_against_the_tote(tmp_path):
    path, conn = _db(tmp_path)
    with transaction(conn):
        # A noted horse that won at 4.0 in a two-horse book of 2.0 + 4.0.
        _race(conn, "2026-09-20", 1, [(1, "NOTED", 1, 4.0), (2, "OTHER", 2, 2.0)])
        _note(conn, "NOTED", "2026-09-10", "2026-09-12T09:00:00+00:00")
    rec = bt.record(conn=conn)
    assert rec["NOTE"]["runs"] == 1 and rec["NOTE"]["won"] == 1
    # De-vigged: (1/4) / (1/4 + 1/2) = 1/3.
    assert rec["NOTE"]["expected"] == pytest.approx(0.3, abs=0.05)
    assert rec["QUIET"]["runs"] == 0


# ── the trial note's taps ────────────────────────────────────────────────────

def test_the_trial_note_keeps_its_two_taps_and_refuses_anything_else(tmp_path):
    path, conn = _db(tmp_path)
    conn.close()
    saved = write_notes.save_trial_note("cavamazing", "2026-09-19", 7,
                                        "reacted very well when shown the whip",
                                        asked=1, response="strong", db=path)
    assert saved["asked"] == 1 and saved["response"] == "strong"
    conn = get_conn(path)
    row = conn.execute("SELECT asked, response FROM trial_notes").fetchone()
    assert (row["asked"], row["response"]) == (1, "strong")
    conn.close()
    # Saving again without them clears them: a save carries both every time.
    write_notes.save_trial_note("CAVAMAZING", "2026-09-19", 7, "edited", db=path)
    conn = get_conn(path)
    row = conn.execute("SELECT asked, response FROM trial_notes").fetchone()
    assert (row["asked"], row["response"]) == (None, None)
    with pytest.raises(ValueError):
        write_notes.save_trial_note("X", "2026-09-19", 1, "n", response="great", db=path)
    with pytest.raises(ValueError):
        write_notes.save_trial_note("X", "2026-09-19", 1, "n", asked=2, db=path)


def test_a_tap_added_later_does_not_move_when_the_note_was_written(tmp_path):
    # When it was written is what says the note came before the race; a tap
    # added after the race must not turn it into hindsight. New words do.
    path, conn = _db(tmp_path)
    with transaction(conn):
        _note(conn, "CAVAMAZING", "2026-09-19", "2026-09-28T09:51:00+00:00",
              note="reacted very well", trial_no=7)
    conn.close()
    saved = write_notes.save_trial_note("CAVAMAZING", "2026-09-19", 7,
                                        "reacted very well", asked=1,
                                        response="strong", db=path)
    assert saved["written_at"] == "2026-09-28T09:51:00+00:00"
    saved = write_notes.save_trial_note("CAVAMAZING", "2026-09-19", 7,
                                        "reacted very well, rewritten", db=path)
    assert saved["written_at"] > "2026-10-01"

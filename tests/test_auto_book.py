"""The automatic blackbook — what it books, when, and what it must never touch.

The rules under test are the owner's decisions of 2026-09-28: read a meeting
once, when its comments on running are in (or at the backstop, saying so);
book at most six; never a horse the book already follows; close a system entry
after three starts unless it was adopted; and keep every system entry out of
the owner's own record.
"""
from __future__ import annotations

import pytest

from hkrd.derive import book_candidates as bc
from hkrd.jobs import auto_book, write_notes
from hkrd.query import auto_book as auto_q, bets as bets_q, blackbook as bb
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

MEETING = "2026-09-27"
# A day-by-day clock for the pass: the meeting, a day it is still waiting
# for its comments, and the backstop.
WAITING = "2026-09-29"
BACKSTOP = "2026-10-05"


def _run(no, name, place, *, lbw=None, odds=10.0, draw=None, pos=None,
         late=0.0, incident=None, corunning=None, date=MEETING, race_no=1):
    return {"race_date": date, "race_no": race_no, "horse_no": no,
            "horse_name": name, "place": place, "lengths_behind": lbw,
            "win_odds": odds, "draw": draw if draw is not None else no,
            "running_positions": pos, "late_dev": late,
            "incident": incident, "corunning": corunning}


# ── the rules, on one race ───────────────────────────────────────────────────

def _race():
    return [
        _run(1, "WINNER", 1, late=-0.9, pos="1 1 1"),
        _run(2, "HELD UP LATE", 4, lbw=1.5, odds=12.0, pos="6 6 4", late=0.1,
             incident="Jumped fairly.  Over the concluding stages was held up "
                      "for clear running behind WINNER."),
        _run(3, "FLEW HOME", 5, lbw=2.0, odds=30.0, draw=11, pos="12 11 5",
             late=-0.6),
        _run(4, "WIDE NO COVER", 3, lbw=1.0, odds=8.0, pos="5 5 3", late=0.2,
             corunning="Three wide without cover throughout, stuck on well."),
        _run(5, "BLED", 6, lbw=2.5, pos="9 9 6", late=-0.5,
             incident="Bled from both nostrils."),
        _run(6, "TAKEN BACK", 7, lbw=3.0, pos="10 10 7", late=0.3,
             incident="Was taken back soon after the start."),
        _run(7, "OUTRAN IT", 2, lbw=0.5, odds=45.0, pos="3 3 2", late=0.4),
        _run(8, "NOTHING", 8, lbw=4.0, pos="4 4 8", late=0.5),
        _run(9, "TAILED OFF", 9, lbw=12.0, pos="8 8 9", late=0.6,
             incident="Held up in the straight."),
    ]


def _by_name(cands):
    return {c.horse_name: c for c in cands}


def test_each_reason_is_read_off_the_run_it_names():
    got = _by_name(bc.race_candidates(_race()))
    assert got["HELD UP LATE"].kinds == ["traffic"]
    assert got["HELD UP LATE"].evidence[0].late
    assert "concluding stages" in got["HELD UP LATE"].reasoning()
    assert "closing" in got["FLEW HOME"].kinds
    assert got["WIDE NO COVER"].kinds == ["wide"]
    assert "outran_price" in got["OUTRAN IT"].kinds


def test_reasons_are_booked_in_the_owners_vocabulary():
    got = _by_name(bc.race_candidates(_race()))
    assert got["HELD UP LATE"].tags == ["traffic"]
    assert got["WIDE NO COVER"].tags == ["bad_run"]
    assert "exceptional_performer" in got["OUTRAN IT"].tags
    # Drawn 11 and flew home: the wide gate is part of the reason.
    assert {"final_sectional", "bad_draw"} <= set(got["FLEW HOME"].tags)


def test_a_winner_is_never_booked():
    assert "WINNER" not in _by_name(bc.race_candidates(_race()))


def test_a_run_with_a_veterinary_finding_is_not_booked():
    """Its next start is a question about the horse's health, not its trip,
    even with the second-fastest finish in the race."""
    assert "BLED" not in _by_name(bc.race_candidates(_race()))


def test_a_rider_settling_the_horse_is_not_trouble():
    assert "TAKEN BACK" not in _by_name(bc.race_candidates(_race()))


def _pair(incident, *, lbw=2.0, late=0.3):
    """One run worth reading in a field of six, its closing section middling
    so that only the text can make it a candidate."""
    return [_run(1, "WINNER", 1, late=-0.9, pos="1 1 1"),
            _run(2, "OTHER", 2, lbw=1.0, pos="2 2 2", late=-0.5),
            _run(3, "THIRD", 3, lbw=1.5, pos="3 3 3", late=-0.2),
            _run(4, "SUBJECT", 4, lbw=lbw, pos="4 4 4", late=late,
                 incident=incident),
            _run(5, "FIFTH", 5, lbw=3.0, pos="5 5 5", late=0.4),
            _run(6, "LAST", 6, lbw=4.0, pos="6 6 6", late=0.5)]


def test_trouble_a_keen_horse_brought_on_itself_is_not_hard_luck():
    """2026-09-27 R8 and R10: two of the first cut's six picks read exactly
    like this, and neither was a run that hid its form."""
    got = _by_name(bc.race_candidates(_pair(
        "Near the 900 Metres commenced to race keenly and shifted out when "
        "being steadied to avoid STORMY GROVE.")))
    assert "SUBJECT" not in got


def test_keen_early_does_not_excuse_trouble_later():
    """Read by the sentence: keen at the 1000m and held up in the straight is
    still a horse that was held up."""
    got = _by_name(bc.race_candidates(_pair(
        "Raced keenly in the early stages.  Over the final 100 Metres was "
        "held up for clear running.")))
    assert got["SUBJECT"].kinds == ["traffic"]
    assert "final 100 Metres" in got["SUBJECT"].reasoning()


@pytest.mark.parametrize("text", [
    "When questioned C Y Ho stated that after making contact at the start his "
    "saddle shifted back, placing him at a disadvantage throughout.",
    "Noted to have lost its right front plate after the race.",
    "Lost a plate during the race.",
])
def test_gear_that_failed_in_the_run_is_a_reason(text):
    """THE BOOM BOX, 2026-09-27 R11: the saddle slipped, and the stewards'
    report is the only place that says so."""
    got = _by_name(bc.race_candidates(_pair(text)))
    assert got["SUBJECT"].kinds == ["equipment"]
    assert got["SUBJECT"].tags == ["bad_run"]
    assert "Stewards: “" in got["SUBJECT"].reasoning()


def test_a_saddle_mentioned_without_trouble_is_not_a_reason():
    got = _by_name(bc.race_candidates(_pair(
        "C Y Ho was fined for presenting with the incorrect saddle cloth.")))
    assert "SUBJECT" not in got


def test_a_run_beaten_a_long_way_is_not_hidden_form():
    assert "TAILED OFF" not in _by_name(bc.race_candidates(_race()))


def test_the_stewards_are_quoted_not_paraphrased():
    got = _by_name(bc.race_candidates(_race()))
    text = got["HELD UP LATE"].reasoning()
    assert "Stewards: “Over the concluding stages was held up" in text
    assert text.startswith("4th of 9, beaten 1½L at 12")


def test_a_reason_written_without_the_comments_says_so():
    c = _by_name(bc.race_candidates(_race()))["HELD UP LATE"]
    assert "not yet published" in c.reasoning(basis="stewards")
    assert "not yet published" not in c.reasoning(basis="full")


def test_the_cap_holds_and_books_each_horse_once():
    one = bc.race_candidates(_race())
    two = [bc.Candidate(**{**c.__dict__, "source_no": 2}) for c in one]
    got = bc.pick(one + two, 3)
    assert len(got) == 3
    assert len({c.horse_name for c in got}) == 3


def test_more_reasons_rank_first_then_the_straight_then_the_price():
    ev = lambda kind, late=False: bc.Evidence(kind, kind, (kind,), late)  # noqa: E731
    mk = lambda name, evs, odds: bc.Candidate(  # noqa: E731
        MEETING, 1, name, evs, "f", horse_no=len(name), odds=odds)
    two = mk("TWO", [ev("closing"), ev("traffic")], 5.0)
    late = mk("LATE", [ev("traffic", late=True)], 5.0)
    long_ = mk("LONG", [ev("closing")], 60.0)
    short = mk("SHORT", [ev("closing")], 4.0)
    assert [c.horse_name for c in bc.pick([short, long_, late, two], 4)] == \
        ["TWO", "LATE", "LONG", "SHORT"]


@pytest.mark.parametrize("lbw,text", [
    (0.05, "a nose"), (0.2, "a head"), (0.3, "a neck"), (0.5, "½L"),
    (1.75, "1¾L"), (3.0, "3L"), (None, "no margin recorded")])
def test_margins_read_as_they_are_called(lbw, text):
    assert bc.margin_text(lbw) == text


# ── the pass, against a database ─────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "auto.db"
    conn = get_conn(path)
    init_db(conn)
    runs = _race()
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": MEETING, "race_no": 1,
                                    "venue": "ST", "surface": "Turf",
                                    "distance": 1200}])
        upsert.upsert_runners(conn, [{k: (str(v) if k == "place" else v)
                                      for k, v in r.items()
                                      if k not in ("late_dev", "incident",
                                                   "corunning")} for r in runs])
        upsert.upsert_comments(conn, [
            {"race_date": MEETING, "race_no": 1, "horse_no": r["horse_no"],
             "comment_text": r["incident"], "source": "incident"}
            for r in runs if r["incident"]])
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, late_dev, "
            "derive_version) VALUES (?, 1, ?, ?, 'test')",
            [(MEETING, r["horse_no"], r["late_dev"]) for r in runs])
    conn.close()
    return path


def _publish_comments(path, text="Three wide without cover throughout."):
    conn = get_conn(path)
    with transaction(conn):
        upsert.upsert_comments(conn, [
            {"race_date": MEETING, "race_no": 1, "horse_no": no,
             "comment_text": text if no == 4 else "Raced midfield.",
             "source": "corunning"} for no in range(1, 10)])
    conn.close()


def _system(path):
    conn = get_conn(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM blackbook WHERE origin = 'system' ORDER BY id")]
    finally:
        conn.close()


def test_a_meeting_waits_for_its_comments_on_running(db):
    report = auto_book.run(db, since=MEETING, today=WAITING)
    assert _system(db) == []
    assert any("comments on running not published" in w for w in report.waiting)


def test_the_comments_landing_is_what_releases_it(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    got = {e["horse_name"]: e for e in _system(db)}
    assert "WIDE NO COVER" in got
    assert "Running: “Three wide without cover" in got["WIDE NO COVER"]["reasoning"]
    assert "not yet published" not in got["WIDE NO COVER"]["reasoning"]


def test_at_the_backstop_it_is_read_without_them_and_says_so(db):
    auto_book.run(db, since=MEETING, today=BACKSTOP)
    entries = _system(db)
    assert entries
    assert all("not yet published" in e["reasoning"] for e in entries)


def test_an_entry_is_dated_the_day_after_the_meeting_and_names_its_run(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=BACKSTOP)
    e = next(e for e in _system(db) if e["horse_name"] == "HELD UP LATE")
    assert e["added_date"] == "2026-09-28"
    assert e["source_race"] == f"{MEETING} R1"
    assert e["status"] == "active" and e["adopted_date"] is None


def test_a_meeting_is_read_once_so_a_dismissed_horse_stays_dismissed(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    first = _system(db)
    write_notes.set_status(first[0]["id"], "retired", reason="not for me",
                           db=db)
    auto_book.run(db, since=MEETING, today=BACKSTOP)
    assert len(_system(db)) == len(first)


def test_a_horse_the_book_already_follows_is_not_booked_again(db):
    write_notes.promote_to_blackbook("HELD UP LATE", reasoning="mine",
                                     source_date=MEETING, source_race_no=1,
                                     db=db)
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    assert "HELD UP LATE" not in {e["horse_name"] for e in _system(db)}
    conn = get_conn(db)
    row = conn.execute("SELECT in_book FROM auto_book_pass "
                       "WHERE kind = 'results'").fetchone()
    conn.close()
    assert row["in_book"] == "HELD UP LATE"


def _run_note(path, horse):
    conn = get_conn(path)
    try:
        row = conn.execute("SELECT note FROM run_notes WHERE horse_name = ? "
                           "AND race_date = ? AND race_no = 1",
                           (horse, MEETING)).fetchone()
        return row["note"] if row else None
    finally:
        conn.close()


def test_a_horse_already_followed_gets_the_reason_on_its_run_instead(db):
    """LET'S HAVE FUN, 2026-09-27: picked, already in the book, so no second
    entry — the reason it was picked goes on the run as its note."""
    write_notes.promote_to_blackbook("HELD UP LATE", reasoning="mine", db=db)
    _publish_comments(db)
    report = auto_book.run(db, since=MEETING, today=WAITING)
    note = _run_note(db, "HELD UP LATE")
    assert note.startswith(write_notes.SYSTEM_NOTE)
    assert "concluding stages" in note
    assert "noted on 1 of their runs" in report.passes[0]


def test_the_owners_note_on_a_run_is_never_replaced(db):
    write_notes.promote_to_blackbook("HELD UP LATE", reasoning="mine", db=db)
    write_notes.save_note("HELD UP LATE", MEETING, 1, "my own words", db=db)
    _publish_comments(db)
    report = auto_book.run(db, since=MEETING, today=WAITING)
    assert _run_note(db, "HELD UP LATE") == "my own words"
    assert "already had a note" in report.passes[0]


def test_a_horse_the_system_booked_gets_an_entry_not_a_note(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    assert _run_note(db, "HELD UP LATE") is None


def test_the_result_a_system_entry_came_from_can_find_it(db):
    """Dated the day after its meeting, a system entry is not "in the book"
    over the race it was written from — so the Results page asks for the
    entries booked FROM a race separately, and credits none of them."""
    from hkrd.query import results as rq

    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    conn = get_conn(db)
    try:
        here = {b["horse_name"]: b for b in rq._booked_from_here(conn, MEETING, 1)}
        ran = rq._booked_that_ran(conn, MEETING, 1)
    finally:
        conn.close()
    assert {e["horse_name"] for e in _system(db)} == set(here)
    assert here["HELD UP LATE"]["origin"] == "system"
    assert "concluding stages" in here["HELD UP LATE"]["reasoning"]
    assert ran == []


def test_a_dry_run_writes_nothing(db):
    _publish_comments(db)
    report = auto_book.run(db, dates=[MEETING], dry_run=True, today=WAITING)
    assert report.picks and _system(db) == []
    conn = get_conn(db)
    assert conn.execute("SELECT count(*) FROM auto_book_pass").fetchone()[0] == 0
    conn.close()


# ── a system entry's test, and who may end it ────────────────────────────────

def _later_runs(path, horse, places, start="2026-10-04"):
    conn = get_conn(path)
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    with transaction(conn):
        for i, place in enumerate(places):
            date = (d0 + dt.timedelta(days=7 * i)).isoformat()
            upsert.upsert_races(conn, [{"race_date": date, "race_no": 1,
                                        "venue": "ST", "distance": 1200}])
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": 1,
                 "horse_name": horse, "place": str(place), "win_odds": 5.0},
                *[{"race_date": date, "race_no": 1, "horse_no": n,
                   "horse_name": f"FIELD {n}", "place": str(n if n > place else n - 1),
                   "win_odds": 10.0} for n in range(2, 9)]])
    conn.close()


def test_a_system_entry_closes_after_three_starts_with_its_record(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    _later_runs(db, "HELD UP LATE", [4, 1, 6])
    report = auto_book.run(db, since=MEETING, today="2026-10-20")
    e = next(e for e in _system(db) if e["horse_name"] == "HELD UP LATE")
    assert e["status"] == "retired"
    # The day after the third start, so the entry was live at all three.
    assert e["closed_date"] == "2026-10-19"
    assert "1 win, 1 in the first three" in e["closed_reason"]
    assert report.closed


def test_two_starts_is_not_yet_a_test(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    _later_runs(db, "HELD UP LATE", [4, 1])
    auto_book.run(db, since=MEETING, today="2026-10-20")
    e = next(e for e in _system(db) if e["horse_name"] == "HELD UP LATE")
    assert e["status"] == "active"


def test_an_adopted_entry_is_the_owners_and_is_never_closed_for_them(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    e = next(e for e in _system(db) if e["horse_name"] == "HELD UP LATE")
    out = write_notes.adopt(e["id"], db=db)
    assert out["origin"] == "system" and out["adopted_date"]
    _later_runs(db, "HELD UP LATE", [4, 1, 6])
    auto_book.run(db, since=MEETING, today="2026-10-20")
    e = next(x for x in _system(db) if x["id"] == e["id"])
    assert e["status"] == "active"


def test_nothing_but_the_owner_closes_an_owners_entry(db):
    mine = write_notes.promote_to_blackbook("NOTHING", reasoning="mine", db=db)
    conn = get_conn(db)
    try:
        with pytest.raises(ValueError):
            with transaction(conn):
                write_notes.close_tested(conn, mine["id"],
                                         closed_date="2026-10-01", reason="x")
    finally:
        conn.close()


def test_only_a_system_entry_can_be_adopted(db):
    mine = write_notes.promote_to_blackbook("NOTHING", reasoning="mine", db=db)
    with pytest.raises(ValueError):
        write_notes.adopt(mine["id"], db=db)
    with pytest.raises(KeyError):
        write_notes.adopt("bb_nope", db=db)


# ── the owner's record stays the owner's ─────────────────────────────────────

def test_system_entries_stay_out_of_the_owners_record(db):
    write_notes.promote_to_blackbook("OUTRAN IT", reasoning="mine",
                                     tags=["traffic"], db=db)
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    _later_runs(db, "HELD UP LATE", [1])
    conn = get_conn(db)
    try:
        owner = bb.book_summary(conn=conn)
        system = bb.book_summary(conn=conn, book="system")
        assert owner["total"] == 1 and owner["wins_since"] == 0
        assert system["total"] == len(_system(db)) and system["wins_since"] == 1
        assert owner["system_live"] == system["active"]
        owner_tags = {t["tag"]: t for t in bb.tag_performance(conn=conn)}
        assert owner_tags["traffic"]["entries_booked"] == 1
        sys_tags = {t["tag"]: t for t in bb.tag_performance(conn=conn,
                                                            book="system")}
        assert sys_tags["traffic"]["wins"] == 1
    finally:
        conn.close()


def test_an_adopted_entry_counts_in_both_records(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    e = next(e for e in _system(db) if e["horse_name"] == "HELD UP LATE")
    write_notes.adopt(e["id"], db=db)
    conn = get_conn(db)
    try:
        assert bb.book_summary(conn=conn)["total"] == 1
        assert bb.book_summary(conn=conn, book="system")["total"] == len(_system(db))
    finally:
        conn.close()


def test_a_system_horse_never_backed_is_not_the_owners_miss(db):
    _publish_comments(db)
    auto_book.run(db, since=MEETING, today=WAITING)
    _later_runs(db, "HELD UP LATE", [1])
    conn = get_conn(db)
    try:
        assert bets_q.backed_and_missed(conn=conn)["runs"] == 0
    finally:
        conn.close()


def test_an_unknown_book_is_refused_not_read_as_everything(db):
    conn = get_conn(db)
    try:
        with pytest.raises(ValueError):
            bb.tag_performance(conn=conn, book="theirs")
    finally:
        conn.close()


# ── trials ───────────────────────────────────────────────────────────────────

TRIAL_DAY = "2026-09-30"


def _trials(path, *, comments=True):
    conn = get_conn(path)
    with transaction(conn):
        upsert.upsert_trials(conn, [
            {"trial_date": TRIAL_DAY, "trial_no": 3, "horse_name": name,
             "place": str(place), "venue": "ST", "finish_time": 58.0 + place,
             "running_positions": f"{place} {place}" if comments else None,
             "comment_text": text if comments else None}
            for name, place, text in (
                ("TRIAL STAR", 1, "Led all the way to score; impressive."),
                ("TRIAL PLAIN", 2, "Raced wide."),
                ("TRIAL POOR", 3, "Gave ground."))])
    conn.close()


def test_a_standout_trial_is_booked_with_its_comment(db):
    _trials(db)
    report = auto_book.run(db, since=TRIAL_DAY, today=TRIAL_DAY)
    got = {e["horse_name"]: e for e in _system(db)}
    assert set(got) == {"TRIAL STAR"}
    star = got["TRIAL STAR"]
    assert star["source_race"] == f"{TRIAL_DAY} T3"
    assert star["source_race_no"] is None
    assert star["added_date"] == TRIAL_DAY           # never a day in the future
    assert "Rated STANDOUT" in star["reasoning"]
    assert any(p.startswith("trials") for p in report.passes)


def test_a_standout_already_followed_gets_a_trial_note_not_a_run_note(db):
    """A trial is a T, not an R: its note goes on the trial, where a note on
    race 3 of that date would be a different run altogether."""
    write_notes.promote_to_blackbook("TRIAL STAR", reasoning="mine", db=db)
    _trials(db)
    auto_book.run(db, since=TRIAL_DAY, today=TRIAL_DAY)
    conn = get_conn(db)
    try:
        trial = conn.execute("SELECT note FROM trial_notes WHERE trial_no = 3"
                             ).fetchone()
        races = conn.execute("SELECT count(*) FROM run_notes").fetchone()[0]
    finally:
        conn.close()
    assert trial["note"].startswith(write_notes.SYSTEM_NOTE)
    assert races == 0


def test_days_read_together_are_read_in_the_order_they_happened(db):
    """A backfill reads a season at once. A horse that won a trial before the
    meeting it was picked from is booked off the trial — the meeting is its
    first test and gets the note — as it would have been, read live."""
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_trials(conn, [
            {"trial_date": "2026-09-20", "trial_no": 1, "horse_name": name,
             "place": str(place), "venue": "ST", "finish_time": 58.0 + place,
             "running_positions": f"{place} {place}", "comment_text": text}
            for name, place, text in (
                ("HELD UP LATE", 1, "Led all the way to score; impressive."),
                ("SOMEONE", 2, "Raced wide."))])
    conn.close()
    _publish_comments(db)
    report = auto_book.run(db, since="2026-09-20", today=WAITING)
    got = {e["horse_name"]: e for e in _system(db)}
    assert got["HELD UP LATE"]["source_race"] == "2026-09-20 T1"
    assert _run_note(db, "HELD UP LATE").startswith(write_notes.SYSTEM_NOTE)
    assert report.passes[0].startswith("trials  2026-09-20")


def test_a_trial_day_hkjc_has_not_finished_waits(db):
    _trials(db, comments=False)
    auto_book.run(db, since=TRIAL_DAY, today=TRIAL_DAY)
    assert _system(db) == []
    conn = get_conn(db)
    ready, waiting = auto_q.ready_trial_days(conn, since=TRIAL_DAY,
                                             today=TRIAL_DAY)
    conn.close()
    assert ready == [] and waiting


# ── migration ────────────────────────────────────────────────────────────────

def test_every_entry_already_in_the_book_is_the_owners(tmp_path):
    """An existing book gains the column and nothing about it changes."""
    path = tmp_path / "old.db"
    conn = get_conn(path)
    conn.execute("CREATE TABLE blackbook (id TEXT PRIMARY KEY, horse_name TEXT "
                 "NOT NULL, added_date TEXT NOT NULL, status TEXT NOT NULL, "
                 "reasoning TEXT, confidence TEXT, source_race TEXT, "
                 "source_date TEXT, source_race_no INTEGER, "
                 "source_date_from TEXT, closed_date TEXT, closed_reason TEXT)")
    conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                 "VALUES ('bb_0001', 'OLD ONE', '2026-01-01', 'active')")
    conn.commit()
    init_db(conn)
    row = conn.execute("SELECT origin, adopted_date FROM blackbook").fetchone()
    conn.close()
    assert row["origin"] == "owner" and row["adopted_date"] is None

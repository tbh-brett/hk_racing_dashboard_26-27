"""The blackbook — import, and the record derived against it.

The point under test is the optimisation the rebuild exists for: what happened
after a horse was booked is DERIVED from the runners table, never read from
whatever anyone remembered to log. The legacy export recorded a subsequent run
for 25 of its 196 entries; the derivation finds 429 runs across the same 196.
"""
from __future__ import annotations

import datetime as dt

import json

import pytest

from hkrd.jobs import import_blackbook
from hkrd.query import blackbook as bb
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _export(entries, definitions=None):
    return {"entries": entries, "next_id": "bb_9999",
            "tag_definitions": definitions or {
                "traffic": "Horse suffered traffic / blocked run last start",
                "improvement": "On an upward trajectory over recent starts"}}


@pytest.fixture()
def db(tmp_path):
    """Two horses, five races. FAST ONE is booked mid-way through its record so
    the runs on either side of the booking are unambiguous."""
    path = tmp_path / "bb.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650}
            for d in ("2026-03-01", "2026-04-01", "2026-05-01",
                      "2026-06-01", "2026-07-01")])
        # A seven-runner field, so three places pay.
        for date, fast_place in (("2026-03-01", 4), ("2026-04-01", 2),
                                 ("2026-05-01", 1), ("2026-06-01", 3),
                                 ("2026-07-01", 7)):
            rows = [{"race_date": date, "race_no": 1, "horse_no": 1,
                     "horse_name": "FAST ONE", "place": str(fast_place),
                     "win_odds": 5.0, "draw": 1}]
            rows += [{"race_date": date, "race_no": 1, "horse_no": i,
                      "horse_name": f"FILLER {i}", "place": str(i),
                      "win_odds": 10.0, "draw": i} for i in range(2, 8)]
            upsert.upsert_runners(conn, rows)
    conn.close()
    return path


def _write(tmp_path, doc):
    src = tmp_path / "blackbook.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    return src


# ── import ───────────────────────────────────────────────────────────────────

def test_a_source_race_without_a_date_does_not_stop_the_import(tmp_path, db):
    """'R7 1400m' and 'Trial 2026-05-15 B3' are both real legacy values.

    An earlier version parsed source_race as one fixed format and lost eight
    entries to it. The field is a hand-typed memo, so what can be read is read
    and the rest is kept verbatim.
    """
    src = _write(tmp_path, _export([
        {"id": "bb_1", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "source_race": "R7 1400m", "status": "active", "tags": ["traffic"]},
        {"id": "bb_2", "horse_name": "FILLER 2", "added_date": "2026-04-10",
         "source_race": "Trial 2026-05-15 B3", "status": "active"},
        {"id": "bb_3", "horse_name": "FILLER 3", "added_date": "2026-04-10",
         "source_race": "", "status": "active"},
        {"id": "bb_4", "horse_name": "FILLER 4", "added_date": "2026-04-10",
         "source_race": "2026-04-01 R6", "status": "active"},
    ]))
    report = import_blackbook.run(src, db=db)
    assert report.errors == []
    assert report.entries == 4

    conn = get_conn(db)
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, source_date, source_race_no, source_race FROM blackbook")}
    conn.close()
    assert rows["bb_1"]["source_race_no"] == 7
    assert rows["bb_2"]["source_date"] == "2026-05-15"   # date without a race
    assert rows["bb_3"]["source_date"] is None           # nothing to read
    assert rows["bb_4"] == {"id": "bb_4", "source_date": "2026-04-01",
                            "source_race_no": 6, "source_race": "2026-04-01 R6"}


def test_a_missing_source_date_is_recovered_only_on_a_confirmed_match(tmp_path, db):
    """'R1 1650m' names a race but not a day. The horse's own runs supply it —
    but only when race number, distance and timing all agree."""
    src = _write(tmp_path, _export([
        # matches 2026-04-01 R1: right race number, right distance, 9 days out
        {"id": "bb_hit", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "source_race": "R1 1650m", "status": "active"},
        # right race number, WRONG distance — must not be filled in
        {"id": "bb_miss", "horse_name": "FILLER 2", "added_date": "2026-04-10",
         "source_race": "R1 1200m", "status": "active"},
    ]))
    report = import_blackbook.run(src, db=db)
    assert report.dates_recovered == 1

    conn = get_conn(db)
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, source_date, source_date_from FROM blackbook")}
    conn.close()
    assert rows["bb_hit"] == {"id": "bb_hit", "source_date": "2026-04-01",
                              "source_date_from": "matched"}
    assert rows["bb_miss"]["source_date"] is None


def test_the_same_tag_typed_twice_is_merged(tmp_path, db):
    src = _write(tmp_path, _export([
        {"id": "bb_1", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "status": "active", "tags": ["improving", "barrier_trial", "traffic"]},
    ]))
    report = import_blackbook.run(src, db=db)
    assert report.merged_tags == 2

    conn = get_conn(db)
    tags = sorted(r["tag"] for r in conn.execute(
        "SELECT tag FROM blackbook_tags WHERE id = 'bb_1'"))
    conn.close()
    assert tags == ["improvement", "traffic", "trial"]


def test_import_is_idempotent(tmp_path, db):
    src = _write(tmp_path, _export([
        {"id": "bb_1", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "status": "active", "tags": ["traffic"],
         "performances": [{"date": "2026-05-01", "race_number": 1,
                           "finish": "1", "bb_verdict": "VALIDATED"}]},
    ]))
    first = import_blackbook.run(src, db=db)
    second = import_blackbook.run(src, db=db)
    assert (first.entries, first.tags, first.notes) == (1, 1, 1)
    assert (second.entries, second.tags, second.notes) == (1, 1, 1)

    conn = get_conn(db)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM blackbook), "
        "(SELECT count(*) FROM blackbook_tags), "
        "(SELECT count(*) FROM blackbook_notes)").fetchone()
    conn.close()
    assert tuple(counts) == (1, 1, 1)


def test_a_tag_in_use_with_no_definition_is_reported_not_invented(tmp_path, db):
    """Nineteen definitions cover eighteen tags in use, and one of the eighteen
    is not among them. Guessing which defined tag it means would rewrite the
    user's own label, so it is surfaced instead."""
    src = _write(tmp_path, _export([
        {"id": "bb_1", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "status": "active", "tags": ["traffic", "strong_finish"]},
    ]))
    report = import_blackbook.run(src, db=db)
    assert report.undefined_tags == ["strong_finish"]
    assert report.errors == []


def _bet(conn, bet_id, date, race_no, *horses, stake=100.0, returned=0.0,
         bet_type="QIN", legs=None):
    """One ticket in the ledger. `legs` names the races an all-up spanned —
    (race_no, horse_no) pairs — so a multi-race ticket can be written the way
    the statement importer writes one."""
    conn.execute(
        "INSERT INTO bets (bet_id, race_date, race_no, bet_type, stake, "
        "returned, pnl, status, hit, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (bet_id, date, None if legs else race_no, bet_type, stake, returned,
         returned - stake, "settled", 1 if returned > 0 else 0, "statement"))
    picks = ([(race_no, h) for h in horses] if not legs else legs)
    for i, (rn, hn) in enumerate(picks, start=1):
        conn.execute(
            "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
            "is_banker) VALUES (?,?,?,?,0)",
            (bet_id, rn, hn, i if legs else 0))


# ── the derived record ───────────────────────────────────────────────────────

@pytest.fixture()
def booked(tmp_path, db):
    """FAST ONE booked 2026-04-10, closed 2026-06-15 — two runs before the
    booking, three after, and the close falling between two of the archived
    races so `live_at_race` has something to separate.

    The export still carries `expiry_date`, because that is what the legacy
    file holds, and the importer is expected to DROP it: a clock running out
    while a file sat on disk is not a decision anybody took. So the entry
    arrives active, and this fixture then closes it the only way an entry can
    now be closed — by saying so.

    That close is in the PAST, which is what the historical tests need and what
    the "is it live now" tests must not silently inherit: any test about an
    OPEN entry reopens it and says so.
    """
    src = _write(tmp_path, _export([
        {"id": "bb_1", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "expiry_date": "2026-06-15", "status": "active", "tags": ["traffic"],
         "confidence": "high", "reasoning": "blocked at the 300",
         "source_race": "2026-04-01 R1",
         # One hand-written record where the derivation finds three runs.
         "performances": [{"date": "2026-05-01", "race_number": 1,
                           "finish": "1", "bb_verdict": "VALIDATED"}]},
    ]))
    import_blackbook.run(src, db=db)
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE blackbook SET status = 'retired', "
                     "closed_date = '2026-06-15', closed_reason = 'gave up on it' "
                     "WHERE id = 'bb_1'")
    conn.close()
    return db


def test_runs_since_booking_are_derived_not_read_from_the_export(booked):
    """The export logged one run. The horse actually had three."""
    conn = get_conn(booked)
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()

    assert entry["notes"] == 1                      # what was written down
    assert entry["runs_since"] == 3                 # what actually happened
    assert [r["race_date"] for r in entry["runs"]] == [
        "2026-07-01", "2026-06-01", "2026-05-01"]
    assert len(entry["notes_written"]) == 1


def test_a_run_before_the_booking_is_not_counted(booked):
    """Otherwise the book takes credit for form it was written from."""
    conn = get_conn(booked)
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert all(r["race_date"] > "2026-04-10" for r in entry["runs"])


def test_the_record_since_uses_the_hong_kong_place_rule(booked):
    """Three places in fields of seven or more, two below that.

    FAST ONE ran 1st, 3rd and 7th after booking in seven-runner fields: one
    win, two places. A flat top-3 would be right here; the rule is asserted so
    a smaller field cannot silently pay a third place that never paid.
    """
    conn = get_conn(booked)
    entry = bb.entry_detail("bb_1", conn=conn)
    assert (entry["wins_since"], entry["places_since"]) == (1, 2)

    # Same finishes, six runners: third place no longer pays.
    with transaction(conn):
        conn.execute("DELETE FROM runners WHERE horse_no = 7")
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert (entry["wins_since"], entry["places_since"]) == (1, 1)


def test_an_entry_with_no_runs_since_reports_zero_not_absent(booked):
    """A booking that has not been tested yet must still appear, with a zero.
    Dropping it would make the book look better tested than it is."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status)"
                     " VALUES ('bb_new', 'FAST ONE', '2026-08-01', 'active')")
    rows = {e["id"]: e for e in bb.list_entries(conn=conn)}
    conn.close()
    assert rows["bb_new"]["runs_since"] == 0
    assert rows["bb_new"]["review_due"] is False


def _live(conn, **cols):
    """The fixture entry, reopened and adjusted. Review is only ever prompted
    on a LIVE entry: the fixture arrives closed, and a closed thesis is not
    awaiting a verdict."""
    sets = "".join(f", {k} = :{k}" for k in cols)
    with transaction(conn):
        conn.execute("UPDATE blackbook SET status = 'active', "
                     f"closed_date = NULL{sets} WHERE id = 'bb_1'", cols)
    return bb.entry_detail("bb_1", conn=conn)


def test_a_thesis_is_not_owed_a_verdict_until_it_has_had_its_chances(booked):
    """Four runs used to prompt a review. Measured on the owner's own book,
    a quarter of the entries with nothing after three or four runs went on to
    place — so the old threshold asked for a verdict while the answer was still
    coming."""
    conn = get_conn(booked)
    entry = _live(conn, added_date="2026-01-01")
    conn.close()
    assert entry["runs_since"] == 4
    assert entry["review_due"] is False, "four runs is not yet an answer"


def test_five_runs_with_nothing_in_the_top_five_is_owed_a_verdict(booked):
    conn = get_conn(booked)
    # A fifth run, well beaten, for a horse whose other runs were nowhere near
    # the top five either.
    with transaction(conn):
        conn.execute("UPDATE runners SET place = 9 WHERE horse_name = 'FAST ONE'")
        conn.execute("INSERT INTO races (race_date, race_no, venue, distance, "
                     "surface, race_class) VALUES ('2026-06-20', 1, 'ST', 1200, "
                     "'Turf', 4)")
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, "
                     "horse_name, place, finish_time) VALUES ('2026-06-20', 1, 3, "
                     "'FAST ONE', 11, 70.0)")
    entry = _live(conn, added_date="2026-01-01")
    conn.close()
    assert entry["runs_since"] == 5 and entry["top_on_conditions"] == 0
    assert entry["review_due"] is True
    assert entry["review_reason"] == "5 runs, none in the top 5"


def test_a_run_in_the_top_five_keeps_the_thesis_open(booked):
    """Wider than the payout on purpose: a fifth of fourteen beaten a length is
    the run that says the reason is real and the day was not."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE runners SET place = 9 WHERE horse_name = 'FAST ONE'")
        conn.execute("UPDATE runners SET place = 5 WHERE horse_name = 'FAST ONE' "
                     "AND race_date = (SELECT max(race_date) FROM runners "
                     "WHERE horse_name = 'FAST ONE')")
        conn.execute("INSERT INTO races (race_date, race_no, venue, distance, "
                     "surface, race_class) VALUES ('2026-06-20', 1, 'ST', 1200, "
                     "'Turf', 4)")
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, "
                     "horse_name, place, finish_time) VALUES ('2026-06-20', 1, 3, "
                     "'FAST ONE', 11, 70.0)")
    entry = _live(conn, added_date="2026-01-01")
    conn.close()
    assert entry["runs_since"] == 5 and entry["top_on_conditions"] == 1
    assert entry["review_due"] is False


def test_runs_that_did_not_meet_the_conditions_do_not_count_against_it(booked):
    """The reason the count is over qualifying runs. A horse booked for 1200m
    and beaten five times at 1650m has not failed its thesis — nobody has asked
    it the question yet, and retiring it would be closing the wrong entry."""
    import datetime as dt

    conn = get_conn(booked)
    today = dt.date.today()
    # Five recent runs, every one of them at the wrong trip. Recent, so the
    # other half of the rule — the thesis that never gets its race — has not
    # run out and cannot be what answers this.
    with transaction(conn):
        conn.execute("INSERT INTO blackbook_trigger (id, kind, op, value) "
                     "VALUES ('bb_1', 'distance', 'is', '1200')")
        for n, back in enumerate((70, 50, 30, 20, 10), start=2):
            day = (today - dt.timedelta(days=back)).isoformat()
            conn.execute("INSERT INTO races (race_date, race_no, venue, course, "
                         "surface, going, distance) VALUES (?, ?, 'HV', 'C', "
                         "'Turf', 'G', 1650)", (day, n))
            conn.execute("INSERT INTO runners (race_date, race_no, horse_no, "
                         "horse_name, place, win_odds, draw) VALUES "
                         "(?, ?, 1, 'FAST ONE', '9', 5.0, 1)", (day, n))
    entry = _live(conn, added_date=(today - dt.timedelta(days=100)).isoformat())
    conn.close()
    assert entry["runs_since"] == 6, "six runs, and not one of them at 1200m"
    assert entry["runs_on_conditions"] == 0
    assert entry["review_due"] is False, "a question nobody asked is not a no"


def test_a_thesis_that_never_gets_its_race_is_eventually_owed_one_too(booked):
    """Every entry in the archive that eventually placed did so within 150 days
    of being booked. At 180 with no qualifying run, the silence is the answer —
    and the question it raises is whether the condition was ever realistic."""
    import datetime as dt

    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE races SET distance = 1650")
        conn.execute("INSERT INTO blackbook_trigger (id, kind, op, value) "
                     "VALUES ('bb_1', 'distance', 'is', '1200')")
    long_ago = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    entry = _live(conn, added_date=long_ago)
    conn.close()
    assert entry["runs_on_conditions"] == 0 and entry["review_due"] is True
    assert entry["review_reason"].endswith("has not run its conditions yet")


def test_the_page_is_told_the_threshold_rather_than_repeating_it(booked):
    """The literal-in-two-places trap, which this repo has already paid for:
    `sarr.MIN_PRIOR` was a hardcoded 2 in three files and the speed map went on
    printing a threshold the model no longer used. The footer names the review
    rule, so the rule travels to it."""
    from pathlib import Path

    conn = get_conn(booked)
    rule = bb.book_summary(conn=conn)["review_rule"]
    conn.close()
    assert rule == {"runs": bb.REVIEW_RUNS, "top": bb.REVIEW_TOP,
                    "untested_days": bb.REVIEW_UNTESTED_DAYS}

    js = (Path(__file__).resolve().parents[1]
          / "web/assets/blackbook.js").read_text(encoding="utf-8")
    assert "review_rule" in js, "the footer must read the rule it prints"
    assert "4+ RUNS" not in js, "the superseded threshold is still on the page"


def test_the_headline_count_and_the_rows_cannot_disagree(booked):
    """The summary sits directly above the list. A headline saying two entries
    are owed a verdict over a list showing one is the page arguing with
    itself, which is what a second query shaped like the rule always risks."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE runners SET place = 9 WHERE horse_name = 'FAST ONE'")
    _live(conn, added_date="2026-01-01")
    flagged = [e for e in bb.list_entries(conn=conn) if e["review_due"]]
    summary = bb.book_summary(conn=conn)
    conn.close()
    assert summary["review_due"] == len(flagged)


def test_the_run_the_thesis_came_from_is_not_a_test_of_it(booked):
    """The source run is shown in full, and kept out of the record.

    It is not a pedantic distinction. In 71 of the 193 legacy entries with a
    source date the source run falls AFTER the booking date — the entry was
    written off a trial or the card and names the engagement it was booked for
    — so counting it credited the book with the very run that inspired it.
    Over the real 196 entries the correction moves the flat-stake return from
    -5.1% to -16.6%.
    """
    conn = get_conn(booked)
    with transaction(conn):
        # Book it before the source run, the shape those 71 entries have.
        conn.execute("UPDATE blackbook SET added_date = '2026-03-01', "
                     "source_date = '2026-04-01', source_race_no = 1")
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()

    dates = [r["race_date"] for r in entry["runs"]]
    assert "2026-04-01" not in dates                 # the source run
    # 2026-03-01 is here because the booking is dated to it and a run on the
    # day of the booking now counts — a horse is routinely booked off a trial
    # for an engagement it runs that day. What this test is about is unchanged:
    # the SOURCE run is excluded by name, not by falling on a date.
    assert dates == ["2026-07-01", "2026-06-01", "2026-05-01", "2026-03-01"]
    assert entry["runs_since"] == 4
    # Shown, not discarded — the reasoning has to be visible without leaving.
    assert entry["source_run"]["race_date"] == "2026-04-01"
    assert entry["source_run"]["place"] == 2


def test_a_source_run_with_no_matching_race_is_none_not_an_error(booked):
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE blackbook SET source_date = '2019-01-01', "
                     "source_race_no = 9")
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert entry["source_run"] is None
    assert entry["runs_since"] == 3


def test_a_booking_made_after_a_race_is_flagged_over_it(booked):
    """status is the entry's state NOW. On an archived card that is a different
    question from "was I watching this horse that day"."""
    conn = get_conn(booked)
    before = bb.for_race("2026-03-01", 1, conn=conn)[0]   # booked 2026-04-10
    during = bb.for_race("2026-05-01", 1, conn=conn)[0]
    after = bb.for_race("2026-07-01", 1, conn=conn)[0]    # expired 2026-06-15
    conn.close()
    assert (before["booked_before_race"], before["live_at_race"]) == (0, 0)
    assert (during["booked_before_race"], during["live_at_race"]) == (1, 1)
    assert (after["booked_before_race"], after["live_at_race"]) == (1, 0)


def test_tag_performance_marks_thin_evidence_as_thin(booked):
    """"Weak evidence must look weak." Three runs is not a result."""
    conn = get_conn(booked)
    rows = {t["tag"]: t for t in bb.tag_performance(conn=conn)}
    conn.close()
    traffic = rows["traffic"]
    assert traffic["runs"] == 3
    assert traffic["thin"] is True
    assert traffic["strike_rate"] == pytest.approx(1 / 3, abs=1e-3)
    # One winner at 5.0 over three $1 stakes: 5.0 back, 3.0 staked.
    assert traffic["roi_win"] == pytest.approx((5.0 - 3.0) / 3.0, abs=1e-3)


def test_a_tag_booked_but_never_run_still_appears(booked):
    """Otherwise a tag's entry count silently means "entries that have run"."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status)"
                     " VALUES ('bb_new', 'FAST ONE', '2026-08-01', 'active')")
        conn.execute("INSERT INTO blackbook_tags (id, tag) "
                     "VALUES ('bb_new', 'gear_change')")
    rows = {t["tag"]: t for t in bb.tag_performance(conn=conn)}
    conn.close()
    assert rows["gear_change"]["entries_booked"] == 1
    assert rows["gear_change"]["runs"] == 0
    assert rows["gear_change"]["strike_rate"] is None
    assert rows["gear_change"]["thin"] is True


def test_filters_are_applied_not_ignored(booked):
    conn = get_conn(booked)
    assert len(bb.list_entries(tag="traffic", conn=conn)) == 1
    assert bb.list_entries(tag="no_such_tag", conn=conn) == []
    # The fixture's entry arrives closed, so "active" must not return it and
    # "retired" must. There is no "expired" to ask for any more: the two words
    # named one outcome and only one of them was ever a decision.
    assert bb.list_entries(status="active", conn=conn) == []
    assert len(bb.list_entries(status="retired", conn=conn)) == 1
    assert len(bb.list_entries(status="closed", conn=conn)) == 1
    with transaction(conn):
        conn.execute("UPDATE blackbook SET status = 'active', "
                     "closed_date = NULL WHERE id = 'bb_1'")
    assert len(bb.list_entries(status="active", conn=conn)) == 1
    assert bb.list_entries(status="retired", conn=conn) == []
    conn.close()


def test_a_missing_entry_is_none_not_an_empty_shell(booked):
    conn = get_conn(booked)
    assert bb.entry_detail("bb_nope", conn=conn) is None
    conn.close()


# ── against the price ────────────────────────────────────────────────────────

def test_ae_measures_wins_against_what_the_market_implied(booked):
    """Strike rate says a tag wins sometimes; A/E says whether it beats the
    PRICE. A tag can look strong purely by booking short-priced horses."""
    conn = get_conn(booked)
    rows = {t["tag"]: t for t in bb.tag_performance(conn=conn)}
    conn.close()
    t = rows["traffic"]
    # FAST ONE at 5.0 against six fillers at 10.0: the raw book is
    # 0.2 + 6x0.1 = 0.8, so the DE-VIGGED implied chance is 0.2/0.8 = 0.25 --
    # not the 0.2 the raw price suggests. Dividing the overround out is the
    # whole point; skipping it would understate every expectation by 20%.
    assert t["expected_wins"] == pytest.approx(0.25 * t["ae_runs"], abs=0.01)
    assert t["ae"] == pytest.approx(t["wins"] / t["expected_wins"], abs=0.02)
    assert t["ae_lo"] < t["ae"] < t["ae_hi"]


def test_a_partly_priced_race_is_left_out_of_ae(booked):
    """The implied probability has to be de-vigged against the whole field. A
    book summed over part of one is not a book, and dividing by it inflates
    every A/E it touches."""
    conn = get_conn(booked)
    before = {t["tag"]: t for t in bb.tag_performance(conn=conn)}["traffic"]
    with transaction(conn):
        conn.execute("UPDATE runners SET win_odds = NULL "
                     "WHERE race_date = '2026-05-01' AND horse_no = 4")
    after = {t["tag"]: t for t in bb.tag_performance(conn=conn)}["traffic"]
    conn.close()
    assert after["ae_runs"] == before["ae_runs"] - 1
    assert after["runs"] == before["runs"]      # the run still counts as a run


def test_a_tag_with_no_wins_keeps_an_upper_bound(booked):
    """A tag that has not won yet has not been shown to fail. The Poisson bound
    at zero events is 3.0, not zero."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE runners SET place = 4 WHERE horse_no = 1 "
                     "AND race_date > '2026-04-10'")
    rows = {t["tag"]: t for t in bb.tag_performance(conn=conn)}
    conn.close()
    t = rows["traffic"]
    assert t["wins"] == 0 and t["ae"] == 0.0
    assert t["ae_hi"] > 0


def test_the_book_summary_counts_resolution_not_size(booked):
    """"A blackbook that only ever grows becomes unusable within a season", so
    the health metric is how many entries were settled."""
    conn = get_conn(booked)
    with transaction(conn):
        # The fixture's own entry arrives closed, so this test supplies the
        # open one rather than assuming it.
        conn.execute("UPDATE blackbook SET status = 'active', "
                     "closed_date = NULL WHERE id = 'bb_1'")
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                     "VALUES ('bb_2', 'FAST ONE', '2026-01-01', 'won_out')")
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                     "VALUES ('bb_3', 'FAST ONE', '2026-01-01', 'retired')")
    s = bb.book_summary(conn=conn)
    conn.close()
    assert s["total"] == 3 and s["active"] == 1 and s["resolved"] == 2
    assert s["status"]["won_out"] == 1


def test_the_summary_reports_whether_there_is_a_bets_ledger_at_all(booked):
    """Brief 06 calls missed bets the most important feature on the page. With
    no bets loaded a zero would read as "nothing was missed", so the flag says
    which it is rather than leaving the page to guess."""
    conn = get_conn(booked)
    assert bb.book_summary(conn=conn)["bets_ledger"] is False
    with transaction(conn):
        _bet(conn, "b1", "2026-05-01", 1, 1, stake=100.0, returned=0.0)
    s = bb.book_summary(conn=conn)
    conn.close()
    assert s["bets_ledger"] is True


def test_declared_today_is_counted_only_when_a_date_is_given(booked):
    conn = get_conn(booked)
    assert bb.book_summary(conn=conn)["declared_today"] == 0
    s = bb.book_summary(today="2026-05-01", conn=conn)
    conn.close()
    assert s["declared_today"] == 1 and s["today"] == "2026-05-01"


# ── the money actually staked ────────────────────────────────────────────────
#
# Brief 06 asks for what was really bet on a booked horse, not a notional flat
# stake on every run since. A single run here attracted eight tickets at four
# different sizes; a fixed-stake ROI would describe a bet nobody placed.

@pytest.fixture()
def backed(booked):
    """FAST ONE is horse 1 in every race. Booked 2026-04-10 off 2026-04-01 R1.
    Runs after the booking: 05-01 (won), 06-01 (3rd), 07-01 (7th).

    Money: two tickets on 05-01, one of which paid; nothing at all on 06-01;
    one leg of a two-race all-up on 07-01."""
    conn = get_conn(booked)
    with transaction(conn):
        # A second race on the last day, so the all-up genuinely spans two.
        upsert.upsert_races(conn, [
            {"race_date": "2026-07-01", "race_no": 2, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-07-01", "race_no": 2, "horse_no": 4,
             "horse_name": "OTHER ONE", "place": "1", "win_odds": 8.0,
             "draw": 4}])
        _bet(conn, "won_a", "2026-05-01", 1, 1, 2, stake=100.0, returned=450.0)
        _bet(conn, "lost_a", "2026-05-01", 1, 1, 3, stake=60.0, returned=0.0)
        _bet(conn, "allup", "2026-07-01", 1, stake=240.0, returned=0.0,
             bet_type="ALLUP_QQP", legs=[(1, 1), (2, 4)])
    conn.close()
    return booked


def test_the_panel_shows_what_was_staked_not_a_notional_flat_bet(backed):
    conn = get_conn(backed)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    t = d["totals"]
    assert t["staked"] == 400.0        # 100 + 60 + 240, the real money
    assert t["returned"] == 450.0
    assert t["pnl"] == 50.0
    assert t["roi"] == round(50.0 / 400.0, 3)
    assert t["bets"] == 3 and t["backed_runs"] == 2 and t["winning_runs"] == 1


def test_a_run_with_no_ticket_stays_in_the_timeline_marked(backed):
    """That is the honest way to show a missed chance: the run happened, and
    there is no money against it. Nothing is invented about what a bet would
    have returned, because no bet was made."""
    conn = get_conn(backed)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    june = next(r for r in d["runs_since"] if r["race_date"] == "2026-06-01")
    assert june["backed"] is False
    assert june["bets"] == [] and june["staked"] == 0 and june["pnl"] == 0
    assert d["totals"]["missed_runs"] == 1
    # It is in the sequence, not dropped from it.
    assert [r["race_date"] for r in d["runs_since"]] == [
        "2026-05-01", "2026-06-01", "2026-07-01"]


def test_the_balance_is_carried_forward_run_by_run(backed):
    """Accumulation, not four unrelated results."""
    conn = get_conn(backed)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    assert [r["balance"] for r in d["runs_since"]] == [290.0, 290.0, 50.0]
    # A run with no bet moves nothing.
    assert d["runs_since"][1]["balance"] == d["runs_since"][0]["balance"]


def test_an_all_up_leg_is_matched_to_the_race_it_was_on(backed):
    """An all-up carries no race number of its own — 29 real ones have
    `bets.race_no` NULL — so keying off the ticket would strand every leg away
    from the run it was actually on."""
    conn = get_conn(backed)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    july = next(r for r in d["runs_since"] if r["race_date"] == "2026-07-01")
    assert july["backed"] is True
    assert [b["bet_id"] for b in july["bets"]] == ["allup"]
    assert july["staked"] == 240.0
    assert d["unmatched_bets"] == 0


def test_a_multi_leg_ticket_is_counted_in_full_and_said_to_be_one(backed):
    """It cannot be divided between its legs, so the whole stake sits against
    this horse — but the money was riding on other horses too, and the count
    is what the page prints to say so."""
    conn = get_conn(backed)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    assert d["totals"]["multi_leg_bets"] == 1
    july = next(r for r in d["runs_since"] if r["race_date"] == "2026-07-01")
    assert july["bets"][0]["legs"] == 2


def test_the_source_run_keeps_its_money_even_though_it_tests_nothing(tmp_path, db):
    """`entry_record` drops the run an entry was written from, because it is
    not a test of the thesis. Dropping it from the MONEY stranded 133 real
    tickets, eight of them on one entry whose panel then read "no bets"
    against a horse that had been backed eight times.

    Seventy-two real entries name a source race that ran AFTER the day they
    were booked, which is the case this covers."""
    src = _write(tmp_path, _export([
        {"id": "bb_9", "horse_name": "FAST ONE", "added_date": "2026-04-10",
         "status": "active", "tags": ["traffic"],
         "source_race": "2026-05-01 R1"}]))
    import_blackbook.run(src, db=db)
    conn = get_conn(db)
    with transaction(conn):
        _bet(conn, "on_source", "2026-05-01", 1, 1, stake=80.0, returned=0.0)
    record = bb.entry_detail("bb_9", conn=conn)
    money = bb.entry_bets("bb_9", conn=conn)
    conn.close()
    assert "2026-05-01" not in [r["race_date"] for r in record["runs"]]
    flagged = [r for r in money["runs_since"] if r["is_source"]]
    assert len(flagged) == 1 and flagged[0]["race_date"] == "2026-05-01"
    assert flagged[0]["staked"] == 80.0
    assert money["totals"]["staked"] == 80.0 and money["unmatched_bets"] == 0


def test_a_bet_placed_before_the_booking_is_not_the_entry_s_money(booked):
    """The book takes credit for what it caused, not for what came before it."""
    conn = get_conn(booked)
    with transaction(conn):
        _bet(conn, "early", "2026-03-01", 1, 1, stake=500.0, returned=0.0)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    assert d["totals"]["bets"] == 0 and d["totals"]["staked"] == 0
    assert d["totals"]["roi"] is None


def test_an_entry_with_no_bets_reports_zero_rather_than_an_roi(booked):
    conn = get_conn(booked)
    d = bb.entry_bets("bb_1", conn=conn)
    conn.close()
    assert d["totals"]["runs"] == 3 and d["totals"]["backed_runs"] == 0
    assert d["totals"]["missed_runs"] == 3
    assert d["totals"]["roi"] is None      # not 0.0, which would read as break-even


def test_a_missing_entry_gives_nothing_not_an_empty_ledger(booked):
    conn = get_conn(booked)
    assert bb.entry_bets("bb_nope", conn=conn) == {}
    conn.close()


# ── retiring is the only way a thesis ends ──────────────────────────────────


def test_a_passed_expiry_retires_nothing_on_the_way_past(tmp_path):
    """The clock is removed, and it does not get to make a last decision.

    A ninety-day expiry closed entries nobody had decided anything about, then
    printed EXPIRED beside RETIRED as though they were different endings. The
    first version of this migration read those dead dates one final time and
    retired everything behind them — 147 of the owner's 179 entries, 16 of them
    declared to run that evening. A migration is not entitled to a hundred
    judgements on its way past, so it takes none: the column goes, every status
    a person chose is left alone, and `expired` — which only the clock ever
    wrote — goes back to active.
    """
    import datetime as dt
    import sqlite3
    from pathlib import Path

    from hkrd.store.connect import get_conn, init_db

    old_schema = (Path(__file__).resolve().parents[1]
                  / "hkrd/store/schema.sql").read_text(encoding="utf-8")
    # The shape as it stood before the column was replaced.
    old_schema = old_schema.replace(
        "  closed_date  TEXT,                 -- NULL while the thesis is still running\n"
        "  closed_reason TEXT,                -- why it was closed, in the owner's words\n",
        "  expiry_date  TEXT,\n")

    target = tmp_path / "old.db"
    raw = sqlite3.connect(target)
    raw.executescript(old_schema)
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    raw.executemany(
        "INSERT INTO blackbook (id, horse_name, added_date, expiry_date, "
        "status, reasoning, confidence) VALUES (?,?,?,?,?,?,?)",
        [("x1", "LAPSED", "2026-01-01", past, "active", "blocked", "medium"),
         ("x2", "LIVE", "2026-01-01", future, "active", "sharp trial", "medium"),
         ("x3", "FLAGGED", "2026-01-01", None, "expired", "went wrong", "low"),
         ("x4", "PAID", "2026-01-01", past, "won_out", "duly won", "high")])
    raw.commit()
    raw.close()

    conn = get_conn(target)
    init_db(conn)
    rows = {e["horse_name"]: e for e in bb.list_entries(conn=conn)}

    # The clock ran out and that is all it did. The thesis is still the owner's
    # to close, and the row keeps its RETIRE button rather than its verdict.
    assert rows["LAPSED"]["status"] == "active"
    assert (rows["LAPSED"]["closed_date"], rows["LAPSED"]["closed_reason"]) == (None, None)
    # Still inside its window, and equally untouched.
    assert rows["LIVE"]["status"] == "active"
    assert rows["LIVE"]["closed_date"] is None
    # `expired` was written by the clock and by nothing else, so it cannot
    # survive the clock. It reads as open, which is what it always was.
    assert rows["FLAGGED"]["status"] == "active"
    # A status somebody chose is kept exactly as they left it, with a NULL date
    # that reads as "closed, day unknown" and never as "closed on day zero".
    assert rows["PAID"]["status"] == "won_out"
    assert rows["PAID"]["closed_date"] is None

    # Nothing decided means nothing to record: a status log full of retirements
    # nobody ordered would be the same fault written down.
    assert conn.execute("SELECT count(*) FROM blackbook_status_log").fetchone()[0] == 0
    # Nothing left to find on a second pass.
    init_db(conn)
    assert "expiry_date" not in {
        r["name"] for r in conn.execute("PRAGMA table_info(blackbook)")}
    assert {r["horse_name"]: r["status"] for r in bb.list_entries(conn=conn)} == {
        "LAPSED": "active", "LIVE": "active", "FLAGGED": "active", "PAID": "won_out"}
    conn.close()


def test_promoting_a_run_no_longer_stamps_an_end_date(tmp_path, db):
    """An entry runs until somebody closes it. Nothing closes it for them."""
    from hkrd.jobs import write_notes

    out = write_notes.promote_to_blackbook(
        "FAST ONE", reasoning="blocked at the 300", db=db)
    assert out["status"] == "active" and out["closed_date"] is None

    conn = get_conn(db)
    entry = bb.entry_detail(out["id"], conn=conn)
    conn.close()
    assert entry["closed_date"] is None and entry["closed"] is False


def test_a_retired_entry_is_not_live_over_today_but_still_was_over_june(booked):
    """The bug this replaces: nothing read `status`, so an entry retired by
    hand went on reading as a live thesis everywhere except the Blackbook page.

    Both halves matter. A horse retired in June must stop lighting up today's
    card; it must NOT retroactively stop having been followed in May.
    """
    conn = get_conn(booked)
    during = bb.for_race("2026-05-01", 1, conn=conn)[0]   # before the close
    after = bb.for_race("2026-07-01", 1, conn=conn)[0]    # after it
    conn.close()
    assert during["status"] == "retired"                  # closed NOW
    assert during["live_at_race"] == 1                    # and live THEN
    assert after["live_at_race"] == 0


def test_a_close_with_no_recorded_day_is_read_as_closed_today(db):
    """Entries retired before there was a column to record it in.

    Reading the unknown day as today is the only reading that satisfies both
    things at once: it is certainly not live over a card being looked at now,
    and over an archived card it does not erase a thesis that was, as far as
    anything here knows, standing at the time.
    """
    import datetime as dt

    conn = get_conn(db)
    today = dt.date.today().isoformat()
    with transaction(conn):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, "
                     "status, reasoning) VALUES ('bb_x', 'FAST ONE', "
                     "'2026-01-01', 'retired', 'gave up on it')")
        upsert.upsert_races(conn, [
            {"race_date": today, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650}])
        upsert.upsert_runners(conn, [
            {"race_date": today, "race_no": 1, "horse_no": 1,
             "horse_name": "FAST ONE", "draw": 1}])
    now = bb.for_race(today, 1, conn=conn)[0]
    old = bb.for_race("2026-05-01", 1, conn=conn)[0]
    conn.close()
    assert now["live_at_race"] == 0        # not a thesis I hold
    assert old["live_at_race"] == 1        # but one I held then


# ── reopening, on a new reason ──────────────────────────────────────────────


def test_retiring_stamps_the_day_and_the_reason(booked):
    """"Was I watching this horse THAT day" is a different question from "am I
    watching it now", and the closing date is the only thing that answers it."""
    import datetime as dt

    from hkrd.jobs import write_notes

    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE blackbook SET status = 'active', "
                     "closed_date = NULL WHERE id = 'bb_1'")
    conn.close()

    out = write_notes.set_status("bb_1", "retired",
                                 reason="beaten four times, thesis is dead",
                                 db=booked)
    assert out["status"] == "retired"
    assert out["closed_date"] == dt.date.today().isoformat()
    assert out["closed_reason"] == "beaten four times, thesis is dead"


def test_reopening_takes_a_new_thesis_and_keeps_the_old_one(booked):
    """Reopening on a fresh reason used to type over the reason the horse was
    booked for — and a thesis that failed is the most useful thing in the book.
    """
    from hkrd.jobs import write_notes

    conn = get_conn(booked)
    before = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert before["reasoning"] == "blocked at the 300"

    out = write_notes.set_status(
        "bb_1", "active", reason="new trainer, first-up off a trial",
        reasoning="handles the going and is 8lb below its last winning mark",
        db=booked)
    assert out["status"] == "active"
    # The close is cleared, or the entry reads as live now and closed then at
    # the same time.
    assert out["closed_date"] is None and out["closed_reason"] is None
    assert out["reasoning"] == ("handles the going and is 8lb below its last "
                                "winning mark")

    conn = get_conn(booked)
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert entry["reopened"] == 1
    # The thesis that was standing when it was reopened is on the history, not
    # lost to the field it was typed over.
    assert entry["history"][0]["to_status"] == "active"
    assert entry["history"][0]["reasoning"] == "blocked at the 300"
    assert entry["history"][0]["reason"] == "new trainer, first-up off a trial"


def test_reopening_without_a_new_thesis_keeps_the_one_it_had(booked):
    """The reason is optional. Reopening on the ORIGINAL thesis is a real
    thing to want — the horse was right and the timing was wrong — and it must
    not blank the field."""
    from hkrd.jobs import write_notes

    out = write_notes.set_status("bb_1", "active", db=booked)
    assert out["reasoning"] == "blocked at the 300"


def test_a_new_thesis_cannot_be_smuggled_into_a_closure(booked):
    """Closing an entry records what happened; it does not rewrite what was
    claimed. Otherwise the book can be made to look right after the fact."""
    from hkrd.jobs import write_notes

    with pytest.raises(ValueError, match="reopening"):
        write_notes.set_status("bb_1", "retired", reasoning="actually I meant",
                               db=booked)


def test_expired_is_no_longer_a_status_anything_can_be_set_to(booked):
    from hkrd.jobs import write_notes

    assert "expired" not in write_notes.STATUSES
    with pytest.raises(ValueError, match="status must be one of"):
        write_notes.set_status("bb_1", "expired", db=booked)


def test_a_run_on_the_day_it_was_booked_counts(db):
    """A horse is very often booked off a trial for an engagement it runs THAT
    DAY. I EXCELLE and POSITIVE SMILE were both booked on 2026-09-06 from 25
    August trials, both ran on 2026-09-06, both lost — and both showed "0 runs ·
    NO RUNS" afterwards. The tracker silently not tracking is the one thing it
    cannot do.

    The rest of this module already calls same-day "booked before the race"
    (`b.added_date <= r.race_date`), so `>` here disagreed with it.
    """
    conn = get_conn(db)
    conn.execute(
        "INSERT INTO blackbook (id, horse_name, status, confidence, "
        "added_date, source_date, source_race_no, reasoning) VALUES "
        "('bb-same', 'FAST ONE', 'active', 'high', '2026-05-01', "
        " '2026-03-01', 1, 'trial win')")
    conn.commit()
    entry = bb.entry_detail("bb-same", conn=conn)
    conn.close()

    # Booked on 2026-05-01 off a run in March: the 1 May run is a test of the
    # thesis, not its source, and it used to be dropped for sharing a date.
    assert "2026-05-01" in [r["race_date"] for r in entry["runs"]]
    assert entry["runs_since"] == 3


def test_the_run_the_entry_was_written_off_is_still_not_evidence(db):
    """`>` was doing two jobs. The one worth keeping — not counting the run
    that CREATED the thesis as a test of it — is done by naming that run, so
    letting same-day runs through does not bring it back.

    Every same-day case in the archive is exactly this: booked on 2026-04-08
    off race 1 on 2026-04-08.
    """
    conn = get_conn(db)
    conn.execute(
        "INSERT INTO blackbook (id, horse_name, status, confidence, "
        "added_date, source_date, source_race_no, reasoning) VALUES "
        "('bb-anchor', 'FAST ONE', 'active', 'high', '2026-05-01', "
        " '2026-05-01', 1, 'booked off this run')")
    conn.commit()
    entry = bb.entry_detail("bb-anchor", conn=conn)
    conn.close()

    assert "2026-05-01" not in [r["race_date"] for r in entry["runs"]]
    assert entry["runs_since"] == 2       # only June and July


# ── the conditions a thesis depends on ──────────────────────────────────────


def _condition(conn, entry_id, kind, op, value):
    with transaction(conn):
        conn.execute("INSERT INTO blackbook_trigger (id, kind, op, value) "
                     "VALUES (?, ?, ?, ?)", (entry_id, kind, op, value))


def test_a_run_that_missed_the_conditions_is_not_a_failed_thesis(booked):
    """The point of the whole feature.

    FAST ONE is booked and runs five times at 1650m. Book it for 1200m and the
    three runs since are no longer a test of anything — the record over EVERY
    run is unchanged, and the record over the runs that asked the question is
    empty. "Not tested" and "tested and lost" are different facts and the book
    used to print them as the same one.
    """
    conn = get_conn(booked)
    before = bb.entry_detail("bb_1", conn=conn)
    assert (before["runs_since"], before["runs_on_conditions"]) == (3, 3)

    _condition(conn, "bb_1", "distance", "is", "1200")
    after = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert after["runs_since"] == 3              # unchanged: it still ran
    assert after["runs_on_conditions"] == 0      # but never at the trip
    assert after["wins_on_conditions"] == 0
    assert [r["on_conditions"] for r in after["runs"]] == [0, 0, 0]


def test_conditions_that_are_met_leave_the_record_alone(booked):
    """The fixture runs at 1650m on Turf, so these two are satisfied by every
    run. A condition that matches must not quietly cost the entry its record."""
    conn = get_conn(booked)
    _condition(conn, "bb_1", "distance", "between", "1600-1700")
    _condition(conn, "bb_1", "surface", "is", "turf")   # stored as "Turf"
    e = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert e["runs_on_conditions"] == e["runs_since"] == 3
    assert e["wins_on_conditions"] == e["wins_since"] == 1


def test_conditions_are_anded_not_ored(booked):
    """"1200m on Turf" is one claim, not two chances to match."""
    conn = get_conn(booked)
    _condition(conn, "bb_1", "surface", "is", "Turf")     # every run
    _condition(conn, "bb_1", "distance", "is", "1200")    # no run
    e = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert e["runs_on_conditions"] == 0


def test_a_list_condition_matches_any_of_its_values(booked):
    conn = get_conn(booked)
    _condition(conn, "bb_1", "distance", "in", "1200,1650,1800")
    assert bb.entry_detail("bb_1", conn=conn)["runs_on_conditions"] == 3
    conn.close()


def test_a_list_condition_does_not_match_inside_another_value(booked):
    """A horse drawn 1 must not satisfy "drawn 11 or 12".

    Both sides are comma-wrapped for exactly this. Without it the test is a
    bare substring — '11,12' contains '1' — and every list condition silently
    widens to anything whose text appears inside it. The fixture draws FAST ONE
    in gate 1, which is the case.
    """
    conn = get_conn(booked)
    _condition(conn, "bb_1", "draw", "in", "11,12")
    assert bb.entry_detail("bb_1", conn=conn)["runs_on_conditions"] == 0
    # And the same list with the real draw in it does match, so the assertion
    # above is about the wrapping and not about the list being broken.
    with transaction(conn):
        conn.execute("UPDATE blackbook_trigger SET value = '1,11,12' "
                     "WHERE id = 'bb_1'")
    assert bb.entry_detail("bb_1", conn=conn)["runs_on_conditions"] == 3
    conn.close()


def test_a_text_condition_ignores_capitalisation(booked):
    """"Turf" typed into a form and "TURF" off the scrape are one condition. A
    trigger that failed on case would be the worst kind of bug here: silent,
    and the horse just stops appearing."""
    conn = get_conn(booked)
    _condition(conn, "bb_1", "surface", "is", "  tUrF ")
    assert bb.entry_detail("bb_1", conn=conn)["runs_on_conditions"] == 3
    conn.close()


def test_an_unknown_condition_value_fails_rather_than_passes(booked):
    """A race whose distance was never scraped cannot be shown to be the
    1200m the entry asked for. `query/gear` draws the same line: a NULL column
    is "this scrape did not carry it", never "there was none"."""
    conn = get_conn(booked)
    with transaction(conn):
        conn.execute("UPDATE races SET distance = NULL")
    _condition(conn, "bb_1", "distance", "between", "1600-1700")
    assert bb.entry_detail("bb_1", conn=conn)["runs_on_conditions"] == 0
    conn.close()


def test_an_entry_with_no_conditions_is_met_by_every_run(booked):
    """Which is what the whole book was before this existed, and is the right
    default: a thesis with no stated circumstances is a claim about the horse.
    """
    conn = get_conn(booked)
    e = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    assert e["conditions"] == [] and e["conditions_text"] == ""
    assert e["runs_on_conditions"] == e["runs_since"]


def test_the_band_says_whether_today_is_the_race_the_entry_wanted(db):
    """Not "AMAZING KIDS runs today" but "runs today, AT THE TRIP YOU BOOKED
    IT FOR". That is the difference between a reminder and a trigger."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, "
                     "status, reasoning) VALUES ('bb_t', 'FAST ONE', "
                     "'2026-01-01', 'active', 'wants a sprint')")
    _condition(conn, "bb_t", "distance", "is", "1200")

    row = bb.for_race("2026-05-01", 1, conn=conn)[0]      # run at 1650m
    assert row["on_conditions"] == 0
    with transaction(conn):
        conn.execute("UPDATE races SET distance = 1200 "
                     "WHERE race_date = '2026-05-01'")
    row = bb.for_race("2026-05-01", 1, conn=conn)[0]
    assert row["on_conditions"] == 1
    # And the whole meeting, which is what the sticky band reads.
    assert bb.declared_on("2026-05-01", conn=conn)[0]["on_conditions"] == 1
    conn.close()


def test_the_preferred_columns_became_conditions_and_then_went(tmp_path):
    """pref_distance, pref_surface and pref_jockey were written by the legacy
    import and read by NOTHING — the only two lines naming them were the two
    that wrote them. They move to the table something reads."""
    import sqlite3
    from pathlib import Path as _Path

    from hkrd.store.connect import get_conn as _get_conn, init_db as _init

    schema = (_Path(__file__).resolve().parents[1]
              / "hkrd/store/schema.sql").read_text(encoding="utf-8")
    schema = schema.replace("  source_date_from TEXT\n);",
                            "  source_date_from TEXT,\n  pref_distance TEXT,\n"
                            "  pref_surface  TEXT,\n  pref_jockey   TEXT\n);")
    target = tmp_path / "prefs.db"
    raw = sqlite3.connect(target)
    raw.executescript(schema)
    raw.execute("INSERT INTO blackbook (id, horse_name, added_date, status, "
                "pref_distance, pref_surface, pref_jockey) VALUES "
                "('p1', 'FAST ONE', '2026-01-01', 'active', '1200,1400', "
                "'Dirt', 'Z Purton')")
    raw.execute("INSERT INTO blackbook (id, horse_name, added_date, status, "
                "pref_distance) VALUES ('p2', 'OTHER', '2026-01-01', "
                "'active', '1650')")
    raw.commit()
    raw.close()

    conn = _get_conn(target)
    _init(conn)
    rows = {e["id"]: e for e in bb.list_entries(conn=conn)}
    p1 = {(c["kind"], c["op"], c["value"]) for c in rows["p1"]["conditions"]}
    assert p1 == {("distance", "in", "1200,1400"), ("surface", "is", "Dirt"),
                  ("jockey", "is", "Z Purton")}
    # A single-valued list is `is` said the long way, and prints better for it.
    assert rows["p2"]["conditions"][0]["op"] == "is"
    assert "1200/1400" in rows["p1"]["conditions_text"]

    assert "pref_distance" not in {
        r["name"] for r in conn.execute("PRAGMA table_info(blackbook)")}
    _init(conn)      # idempotent: no second copy of the conditions
    assert len(bb.list_entries(conn=conn)[0]["conditions"]) in (1, 3)
    conn.close()


def test_a_condition_nothing_can_evaluate_is_refused(booked):
    """A trigger that never matches is worse than no trigger: the horse
    silently stops appearing and the book looks empty rather than broken."""
    from hkrd.jobs import write_notes

    with pytest.raises(ValueError, match="kind must be one of"):
        write_notes.set_triggers("bb_1", [{"kind": "vibes", "op": "is",
                                           "value": "good"}], db=booked)
    with pytest.raises(ValueError, match="compares numbers"):
        write_notes.set_triggers("bb_1", [{"kind": "distance", "op": ">=",
                                           "value": "sprint"}], db=booked)
    with pytest.raises(ValueError, match="needs a numeric condition"):
        write_notes.set_triggers("bb_1", [{"kind": "going", "op": ">=",
                                           "value": "3"}], db=booked)
    with pytest.raises(ValueError, match="needs a value"):
        write_notes.set_triggers("bb_1", [{"kind": "going", "op": "is",
                                           "value": "  "}], db=booked)
    # Nothing was written by any of the four.
    conn = get_conn(booked)
    assert bb.entry_detail("bb_1", conn=conn)["conditions"] == []
    conn.close()


def test_setting_conditions_replaces_them_rather_than_appending(booked):
    """Editing 1200m to 1200-1400m has to leave ONE condition. Two would
    contradict each other and match nothing between them."""
    from hkrd.jobs import write_notes

    write_notes.set_triggers("bb_1", [{"kind": "distance", "op": "is",
                                       "value": "1200"}], db=booked)
    out = write_notes.set_triggers(
        "bb_1", [{"kind": "distance", "op": "between", "value": "1200-1400"}],
        db=booked)
    assert out["conditions"] == [{"kind": "distance", "op": "between",
                                  "value": "1200-1400"}]
    # And an empty list clears them, which says the claim is about the horse.
    assert write_notes.set_triggers("bb_1", [], db=booked)["conditions"] == []

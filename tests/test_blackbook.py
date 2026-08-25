"""The blackbook — import, and the record derived against it.

The point under test is the optimisation the rebuild exists for: what happened
after a horse was booked is DERIVED from the runners table, never read from
whatever anyone remembered to log. The legacy export recorded a subsequent run
for 25 of its 196 entries; the derivation finds 429 runs across the same 196.
"""
from __future__ import annotations

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


# ── the derived record ───────────────────────────────────────────────────────

@pytest.fixture()
def booked(tmp_path, db):
    """FAST ONE booked 2026-04-10 — two runs before, three after."""
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


def test_review_is_prompted_after_four_unresolved_runs(booked):
    conn = get_conn(booked)
    entry = bb.entry_detail("bb_1", conn=conn)
    assert entry["review_due"] is False              # three runs
    with transaction(conn):
        conn.execute("UPDATE blackbook SET added_date = '2026-01-01'")
    entry = bb.entry_detail("bb_1", conn=conn)
    conn.close()
    # Five races in the fixture, one of which is the source run and does not
    # count as a test of the thesis it produced.
    assert entry["runs_since"] == 4 and entry["review_due"] is True


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
    assert dates == ["2026-07-01", "2026-06-01", "2026-05-01"]
    assert entry["runs_since"] == 3
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
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                     "VALUES ('bb_2', 'FAST ONE', '2026-01-01', 'won_out')")
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                     "VALUES ('bb_3', 'FAST ONE', '2026-01-01', 'retired')")
    s = bb.book_summary(conn=conn)
    conn.close()
    assert s["total"] == 3 and s["active"] == 1 and s["resolved"] == 2
    assert s["status"]["won_out"] == 1


def test_the_summary_says_there_is_no_bets_ledger(booked):
    """Brief 06 calls missed bets the most important feature on the page. It
    needs a ledger that does not exist, and a zero would read as "nothing was
    missed" — so the flag is explicit."""
    conn = get_conn(booked)
    s = bb.book_summary(conn=conn)
    conn.close()
    assert s["bets_ledger"] is False


def test_declared_today_is_counted_only_when_a_date_is_given(booked):
    conn = get_conn(booked)
    assert bb.book_summary(conn=conn)["declared_today"] == 0
    s = bb.book_summary(today="2026-05-01", conn=conn)
    conn.close()
    assert s["declared_today"] == 1 and s["today"] == "2026-05-01"

"""Barrier trials — the rating, and the two surfaces that share it.

The Trials artboard names the requirement: "One engine, two surfaces: the same
finish + margin + comment rating used inline on a horse's own trial line,
aggregated here as a live feed — not a separately curated list."

The rating's vocabulary is measured rather than intuited, and these tests pin
the measurements that would have been got backwards by hand — "moved better
than before" reads as praise and predicts 1.7% next-start wins against an 8.2%
baseline; "settled midfield" reads as neutral and predicts 2.7%.
"""
from __future__ import annotations

import pytest

from hkrd.derive import trial_quality as tq
from hkrd.query import trials as trials_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


# ── the rating ───────────────────────────────────────────────────────────────

def test_winning_a_trial_and_being_praised_is_a_standout():
    r = tq.rate(place=1, field_size=9, comment="Led all the way to score.")
    assert r["band"] == "STANDOUT" and r["mark"] == "++"
    assert "won the trial" in r["reasons"]


def test_a_phrase_that_reads_as_praise_but_measured_negative_is_scored_negative():
    """"Moved better than before" is what the chart writer says about a horse
    that had been going badly: 1.7% next-start wins against a baseline of 8.2%.
    A hand-written vocabulary would have put it with the positives."""
    assert "moved better than before" in tq.NEGATIVE
    r = tq.rate(place=4, field_size=9, comment="Moved better than before.")
    assert r["band"] == "NEGATIVE"


def test_a_neutral_sounding_positional_phrase_measured_negative(  ):
    """In a trial, settling midfield is what a horse does when it cannot go
    with them — 2.7% next-start wins."""
    assert "settled midfield" in tq.NEGATIVE
    assert tq.rate(place=5, field_size=10,
                   comment="Settled midfield.")["band"] == "NEGATIVE"


def test_unimpressive_is_not_read_as_impressive():
    """Substring matching scores the same clause both ways."""
    r = tq.rate(place=4, field_size=9, comment="Unimpressive.")
    assert r["negatives"] == ["unimpressive"] and r["positives"] == []
    assert r["band"] == "NEGATIVE"


def test_a_horse_that_was_not_asked_is_untested_not_marked_down():
    """Every phrase in that family lands ON the baseline when measured, so a
    finish nobody was chasing is not a verdict on the horse."""
    r = tq.rate(place=8, field_size=9,
                comment="Racing wide under his own steam.")
    assert r["band"] == "UNTESTED" and r["mark"] == "/"
    assert r["reasons"] == ["comment: own steam"]


def test_a_measured_comment_overrides_the_untested_family():
    """"Held under a hold and weakened" is not an untested trial — something
    was learned, and it was bad."""
    r = tq.rate(place=8, field_size=9,
                comment="Under a hold early; weakened in the Straight.")
    assert r["band"] == "NEGATIVE"


def test_winning_is_never_untested_however_easily():
    r = tq.rate(place=1, field_size=9, comment="Won under a hold.")
    assert r["band"] != "UNTESTED"


def test_margin_is_carried_but_does_not_move_the_band():
    """Measured: holding the finish constant, mid-pack finishers go 7.2%
    next-start wins within two lengths and 5.7% at fourteen or more. A trial
    winner is often not extended, so the field's margin measures how hard the
    winner was ridden more than how well the rest went."""
    close = tq.rate(place=5, field_size=9, margin=0.2, comment="Ran on.")
    far = tq.rate(place=5, field_size=9, margin=18.0, comment="Ran on.")
    assert close["band"] == far["band"]
    assert close["score"] == far["score"]
    assert close["margin"] == 0.2 and far["margin"] == 18.0


def test_a_phrase_inside_the_interval_is_absent_from_the_vocabulary():
    """Fifty-one of the sixty-five clauses tested have an interval containing
    the baseline. They are ignored however they read — "stayed on well" among
    them, which is why only "stayed on comfortably" is listed."""
    assert "stayed on well" not in tq.POSITIVE
    assert "stayed on comfortably" in tq.POSITIVE
    assert tq.rate(place=4, field_size=9,
                   comment="Stayed on well.")["band"] == "NEUTRAL"


def test_a_missing_comment_is_rated_on_the_finish_alone():
    assert tq.rate(place=1, field_size=8, comment=None)["band"] == "POSITIVE"
    assert tq.rate(place=None, field_size=8, comment=None)["band"] == "NEUTRAL"


# ── the query layer ──────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """One batch of four, and a race afterwards that two of them ran in."""
    path = tmp_path / "t.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO trials (trial_date, trial_no, horse_name, place, "
            "finish_time, section_times, running_positions, venue, surface, "
            "comment_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [("2026-05-01", 1, "FAST ONE", 1, 60.00, "15.8; 22.2; 22.0",
              "1 1 1", "ST", "AWT", "Led all the way to score."),
             ("2026-05-01", 1, "SECOND ONE", 2, 60.16, "15.8; 22.2; 22.0",
              "2 2 2", "ST", "AWT", "Fought on."),
             ("2026-05-01", 1, "HELD ONE", 3, 61.60, "15.8; 22.2; 22.0",
              "4 4 3", "ST", "AWT", "Under a hold throughout."),
             ("2026-05-01", 1, "BAD ONE", 4, 63.20, "15.8; 22.2; 22.0",
              "3 3 4", "ST", "AWT", "Weakened in the Straight."),
             # An earlier trial for FAST ONE, so `before` has something to cut.
             ("2026-04-01", 2, "FAST ONE", 5, 62.00, "16.0; 22.5; 23.0",
              "5 5 5", "HV", "AWT", "Unimpressive.")])
        upsert.upsert_races(conn, [
            {"race_date": "2026-05-20", "race_no": 1, "venue": "ST",
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-05-20", "race_no": 1, "horse_no": 1,
             "horse_name": "FAST ONE", "place": "1", "draw": 1, "win_odds": 3.0},
            {"race_date": "2026-05-20", "race_no": 1, "horse_no": 2,
             "horse_name": "BAD ONE", "place": "8", "draw": 2, "win_odds": 20.0}]
            + [{"race_date": "2026-05-20", "race_no": 1, "horse_no": i,
                "horse_name": f"OTHER {i}", "place": str(i), "draw": i,
                "win_odds": 10.0} for i in range(3, 9)])
    conn.close()
    return path


def test_margin_is_derived_from_the_batch_winner_because_hkjc_publishes_none(db):
    conn = get_conn(db)
    b = trials_q.batch("2026-05-01", 1, conn=conn)
    conn.close()
    by_name = {r["horse_name"]: r for r in b["runners"]}
    assert by_name["FAST ONE"]["margin"] == 0.0
    # 0.16s a length: 60.16 is one length, 63.20 is twenty.
    assert by_name["SECOND ONE"]["margin"] == 1.0
    assert by_name["BAD ONE"]["margin"] == 20.0


def test_a_trial_carries_no_distance_rather_than_one_inferred_from_the_clock(db):
    """A horse's trial over an unknown trip is still a fact; a trip inferred
    from a time is not."""
    conn = get_conn(db)
    b = trials_q.batch("2026-05-01", 1, conn=conn)
    conn.close()
    assert b["distance"] is None


def test_the_batch_splits_are_the_batch_s_not_each_runner_s(db):
    """HKJC publishes one set of sectionals per trial and repeats it on every
    row. Showing them per runner would imply four measurements where there is
    one."""
    conn = get_conn(db)
    b = trials_q.batch("2026-05-01", 1, conn=conn)
    conn.close()
    assert b["section_times"] == [15.8, 22.2, 22.0]
    assert b["winning_time"] == 60.00


def test_next_start_is_a_race_never_another_trial(db):
    """"NEXT ACTUAL START SHOWS WHAT THE HORSE DID AT THE RACES AFTER THIS
    TRIAL, NOT ANOTHER TRIAL" — the artboard, in capitals."""
    conn = get_conn(db)
    b = trials_q.batch("2026-04-01", 2, conn=conn)
    conn.close()
    nxt = b["runners"][0]["next_start"]
    assert nxt["race_date"] == "2026-05-20"      # not the 2026-05-01 trial
    assert nxt["place"] == 1


def test_a_horse_with_no_start_since_reports_none_not_a_blank_row(db):
    conn = get_conn(db)
    b = trials_q.batch("2026-05-01", 1, conn=conn)
    conn.close()
    held = next(r for r in b["runners"] if r["horse_name"] == "HELD ONE")
    assert held["next_start"] is None


def test_the_form_guide_band_cannot_see_a_trial_run_after_the_race(db):
    """Showing it would let hindsight into a form guide."""
    conn = get_conn(db)
    now = trials_q.for_horses(["FAST ONE"], conn=conn)
    then = trials_q.for_horses(["FAST ONE"], before="2026-04-15", conn=conn)
    conn.close()
    assert [t["trial_date"] for t in now["FAST ONE"]] == ["2026-05-01", "2026-04-01"]
    assert [t["trial_date"] for t in then["FAST ONE"]] == ["2026-04-01"]


def test_both_surfaces_rate_the_same_trial_the_same_way(db):
    """One engine. A second rating in the page would drift within a season."""
    conn = get_conn(db)
    inline = trials_q.for_horses(["FAST ONE"], conn=conn)["FAST ONE"][0]
    in_batch = next(r for r in trials_q.batch("2026-05-01", 1, conn=conn)["runners"]
                    if r["horse_name"] == "FAST ONE")
    conn.close()
    assert inline["quality_band"] == in_batch["quality_band"]
    assert inline["quality_score"] == in_batch["quality_score"]
    assert inline["margin"] == in_batch["margin"]


def test_the_feed_is_the_rating_filtered_not_a_curated_list(db):
    """A list somebody maintained by hand would say more about who maintained
    it than about the trials."""
    conn = get_conn(db)
    feed = trials_q.standouts(days=90, conn=conn)
    conn.close()
    names = [r["horse_name"] for r in feed["runs"]]
    assert "FAST ONE" in names and "BAD ONE" not in names
    assert feed["considered"] >= feed["shown"]


def test_the_feed_shows_what_the_rest_of_the_batch_did_next(db):
    """A standout out of a batch whose other five all won next start says more
    about the batch than about the horse."""
    conn = get_conn(db)
    feed = trials_q.standouts(days=90, conn=conn)
    conn.close()
    pick = next(r for r in feed["runs"] if r["horse_name"] == "FAST ONE")
    others = {b["horse_name"] for b in pick["batch_next"]}
    assert "FAST ONE" not in others
    assert {"SECOND ONE", "HELD ONE", "BAD ONE"} == others


def test_the_calibration_table_is_recomputed_not_quoted(db):
    """The rating is only worth showing if the bands separate, so the page
    prints this rather than asking anyone to take the mark on trust."""
    conn = get_conn(db)
    cal = trials_q.calibration(conn=conn)
    conn.close()
    assert cal["order"] == list(tq.BANDS)
    assert sum(v["trials"] for v in cal["bands"].values()) == cal["overall"]["trials"] == 5
    # FAST ONE won its trial and then won next start.
    assert cal["bands"]["STANDOUT"]["wins"] == 1


def test_a_band_sitting_on_the_baseline_does_not_read_as_below_it(db):
    """NEUTRAL at 7.9% against a baseline of 8.2% is the same number. Painting
    it as a shortfall would invent a finding out of a rounding difference, so
    the page colours by whether the interval EXCLUDES the baseline."""
    conn = get_conn(db)
    cal = trials_q.calibration(conn=conn)
    conn.close()
    for band, v in cal["bands"].items():
        assert "clears_baseline" in v, band
        if v["with_next"]:
            assert len(v["next_win_ci"]) == 2, band
    # Five trials in the fixture: nothing can clear anything at that size.
    assert not any(v["clears_baseline"] for v in cal["bands"].values())


def test_the_interval_keeps_a_near_zero_band_from_claiming_certainty(db):
    """0 of 3 is not 'never'. Wilson gives an upper bound instead."""
    lo, hi = trials_q._wilson(0, 3)
    assert lo == 0.0 and hi > 0.5

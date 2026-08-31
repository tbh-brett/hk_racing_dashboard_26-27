"""Run notes, promotion to the blackbook, and the projected race pace.

Design brief 06 Part 0 draws one line and the tests here hold it: a note is a
record of what happened in a run; an entry is a judgement that the horse is
worth following. Most notes are records, so writing one must never create an
entry — but promoting one must not make the user retype anything either.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import write_notes
from hkrd.query import formguide as fg
from hkrd.query import pace as pace_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    """One race of 10, styles assigned so the pace projection is checkable."""
    path = tmp_path / "w.db"
    conn = get_conn(path)
    init_db(conn)
    styles = ["Leader", "Leader", "On-Pace", "On-Pace", "Midfield",
              "Midfield", "Closer", "Closer", "Closer", None]
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1650}
            for d in ("2026-05-01", "2026-06-01")])
        for date in ("2026-05-01", "2026-06-01"):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": i + 1,
                 "horse_name": f"HORSE {i}", "place": str(i + 1),
                 "draw": i + 1, "actual_weight": 120, "win_odds": 5.0 + i}
                for i in range(10)])
        # Styles are recorded against the EARLIER meeting only, so the later
        # race's projection has to read each horse's prior form.
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, pace_style, "
            "derive_version) VALUES ('2026-05-01', 1, ?, ?, 't')",
            [(i + 1, s) for i, s in enumerate(styles) if s])
    conn.close()
    return path


# ── projected pace ───────────────────────────────────────────────────────────

def test_pace_is_projected_from_the_field_not_measured(db):
    """A race that has not been run has no sectionals; its pace can only come
    from who is in it."""
    conn = get_conn(db)
    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    # 2 leaders + 2 on-pace over 9 classified = (2 + 1) / 9
    assert p["pressure"] == pytest.approx(3 / 9, abs=1e-3)
    assert p["band"] == "Fast"
    assert p["counts"]["Leader"] == 2
    assert sorted(p["leaders"]) == ["HORSE 0", "HORSE 1"]


def test_an_unclassified_runner_is_excluded_not_counted_as_slow(db):
    """Dividing by the whole field would make every thin field look slow —
    missing evidence read as evidence of no pace."""
    conn = get_conn(db)
    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    assert p["field_size"] == 10 and p["unknown"] == 1
    assert p["pressure"] == pytest.approx(3 / 9, abs=1e-3)   # 9, not 10


def test_a_mostly_unclassified_field_is_not_confident(db):
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_pace WHERE horse_no > 3")
    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    assert p["band"] is not None      # still projected
    assert p["confident"] is False    # but flagged as thin


def test_a_field_with_no_styles_at_all_reads_nothing(db):
    """Not zero pressure — no read. A crawl and an unknown are different."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runner_pace")
    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    assert p["band"] is None and p["pressure"] is None
    assert p["unknown"] == p["field_size"] == 10


# ── notes ────────────────────────────────────────────────────────────────────

def test_a_note_does_not_create_a_blackbook_entry(db):
    """The whole point of Part 0. Auto-promotion would fill the book with the
    ordinary observations that make up most notes."""
    write_notes.save_note("HORSE 0", "2026-05-01", 1, "checked at the 800", db=db)
    conn = get_conn(db)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM run_notes), "
        "(SELECT count(*) FROM blackbook)").fetchone()
    conn.close()
    assert tuple(counts) == (1, 0)


def test_saving_twice_replaces_rather_than_duplicates(db):
    write_notes.save_note("HORSE 0", "2026-05-01", 1, "first reading", db=db)
    second = write_notes.save_note("HORSE 0", "2026-05-01", 1, "on review", db=db)
    conn = get_conn(db)
    rows = conn.execute("SELECT note FROM run_notes").fetchall()
    conn.close()
    assert [r["note"] for r in rows] == ["on review"]
    assert second["note"] == "on review"


def test_an_empty_note_is_refused_rather_than_stored_blank(db):
    with pytest.raises(ValueError):
        write_notes.save_note("HORSE 0", "2026-05-01", 1, "   ", db=db)


def test_notes_come_back_for_a_whole_card_in_one_call(db):
    """A twelve-runner field expanded is twelve round trips otherwise."""
    write_notes.save_note("HORSE 0", "2026-05-01", 1, "a", db=db)
    write_notes.save_note("HORSE 3", "2026-05-01", 1, "b", db=db)
    conn = get_conn(db)
    out = fg.notes_for_horses(["HORSE 0", "HORSE 3", "HORSE 7"], conn=conn)
    conn.close()
    assert sorted(out) == ["HORSE 0", "HORSE 3"]     # HORSE 7 has none
    assert out["HORSE 0"][0]["note"] == "a"


def test_a_horse_name_is_normalised_on_the_way_in(db):
    """horse_name is the join key everywhere; a lower-case note would be
    invisible to the page that wrote it."""
    write_notes.save_note("horse 0", "2026-05-01", 1, "typed in lower case", db=db)
    conn = get_conn(db)
    out = fg.notes_for_horses(["HORSE 0"], conn=conn)
    conn.close()
    assert out["HORSE 0"][0]["note"] == "typed in lower case"


# ── promotion ────────────────────────────────────────────────────────────────

def test_promotion_records_the_run_it_came_from(db):
    entry = write_notes.promote_to_blackbook(
        "HORSE 0", reasoning="blocked at the 300", source_date="2026-05-01",
        source_race_no=1, tags=["traffic", "bad_draw"], db=db)
    assert entry["source_race"] == "2026-05-01 R1"
    assert entry["tags"] == ["bad_draw", "traffic"]
    assert entry["status"] == "active"

    conn = get_conn(db)
    row = dict(conn.execute("SELECT * FROM blackbook").fetchone())
    tags = [r["tag"] for r in conn.execute("SELECT tag FROM blackbook_tags")]
    conn.close()
    assert row["source_date"] == "2026-05-01" and row["source_race_no"] == 1
    assert row["expiry_date"] > row["added_date"]
    assert sorted(tags) == ["bad_draw", "traffic"]


def test_an_entry_without_a_reason_is_refused(db):
    """The reason is what makes it a thesis rather than a favourite."""
    with pytest.raises(ValueError):
        write_notes.promote_to_blackbook("HORSE 0", reasoning="", db=db)


def test_ids_continue_the_legacy_sequence(db):
    """bb_0197 after bb_0196, not a second sequence beside it."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status) "
                     "VALUES ('bb_0196', 'HORSE 9', '2026-01-01', 'expired')")
    assert write_notes.next_entry_id(conn) == "bb_0197"
    conn.close()

    first = write_notes.promote_to_blackbook("HORSE 0", reasoning="one", db=db)
    second = write_notes.promote_to_blackbook("HORSE 1", reasoning="two", db=db)
    assert (first["id"], second["id"]) == ("bb_0197", "bb_0198")


def test_the_first_entry_in_an_empty_book_starts_at_one(db):
    entry = write_notes.promote_to_blackbook("HORSE 0", reasoning="first", db=db)
    assert entry["id"] == "bb_0001"


def test_a_source_run_with_no_date_still_records_the_race(db):
    """The same shape the legacy export used: 'R7' with no day."""
    entry = write_notes.promote_to_blackbook(
        "HORSE 0", reasoning="from the trial", source_race_no=7, db=db)
    assert entry["source_race"] == "R7"
    conn = get_conn(db)
    row = dict(conn.execute("SELECT * FROM blackbook").fetchone())
    conn.close()
    assert row["source_date"] is None and row["source_race_no"] == 7
    assert row["source_date_from"] is None


# ── race pace, on the brief's scale ──────────────────────────────────────────

def test_the_pace_scale_is_the_one_the_brief_specifies():
    """Design note 03 §7 names the five steps. An earlier version invented
    CRAWL/SLOW/EVEN/STRONG/HOT — a different scale wearing the same shape."""
    assert pace_q.PACE_BANDS == ("Very Slow", "Slow", "Neutral", "Fast", "Very Fast")


def test_a_run_race_is_measured_not_projected(db):
    """Pace is a property of how the race WAS run wherever that is knowable;
    the style projection is the fallback for a race with no sectionals."""
    conn = get_conn(db)
    # 40 comparable races at the distance, each with an early sectional, so the
    # z-score has something to be a z-score against.
    with transaction(conn):
        for i in range(40):
            date = f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}"
            upsert.upsert_races(conn, [
                {"race_date": date, "race_no": 1, "venue": "HV", "course": "C",
                 "surface": "Turf", "going": "G", "distance": 1650}])
            conn.executemany(
                "INSERT INTO runner_pace (race_date, race_no, horse_no, "
                "early_pace, pace_style, derive_version) VALUES (?,?,?,?,?,'t')",
                [(date, 1, h, 24.0 + (i % 5) * 0.1, "Midfield") for h in range(1, 9)])
        # The race under test goes markedly faster early than any of them.
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, early_pace, "
            "pace_style, derive_version) VALUES ('2026-06-01', 1, ?, ?, ?, 't')",
            [(h, 22.9, "Leader") for h in range(1, 11)])

    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    assert p["measured"] is True
    assert p["z"] < -1.2 and p["band"] == "Very Fast"
    assert p["peers"] >= 30


def test_too_few_comparable_races_falls_back_rather_than_inventing_a_z(db):
    """A z-score against eleven races is not a tempo reading."""
    conn = get_conn(db)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO runner_pace (race_date, race_no, horse_no, early_pace, "
            "pace_style, derive_version) VALUES ('2026-06-01', 1, ?, 23.0, ?, 't')",
            [(h, "Leader") for h in range(1, 11)])
    p = pace_q.race_pace("2026-06-01", 1, conn=conn)
    conn.close()
    assert p["measured"] is False          # projected from styles instead
    assert p["z"] is None


# ── gear ─────────────────────────────────────────────────────────────────────

def test_first_time_gear_is_read_from_the_whole_record(db):
    """Design note 03 §3. Six runs on screen cannot support the claim — a
    blinker first worn eight runs back would render as new."""
    conn = get_conn(db)
    with transaction(conn):
        # An earlier run with no gear, so 2026-05-01 is not the baseline.
        upsert.upsert_races(conn, [
            {"race_date": "2026-04-01", "race_no": 1, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-04-01", "race_no": 1, "horse_no": 1,
             "horse_name": "HORSE 0", "place": "5", "draw": 1}])
        conn.execute("UPDATE runners SET gear = 'B' WHERE horse_no = 1 "
                     "AND race_date = '2026-05-01'")
        conn.execute("UPDATE runners SET gear = 'B/TT' WHERE horse_no = 1 "
                     "AND race_date = '2026-06-01'")
    out = fg.gear_timeline(["HORSE 0"], conn=conn)
    conn.close()
    # B is new where it first appears, TT where it first appears — and the
    # 2026-04-01 baseline reports nothing.
    assert out["HORSE 0"] == {"2026-05-01:1": ["B"], "2026-06-01:1": ["TT"]}


def test_the_earliest_run_never_reports_first_time_gear(db):
    """A horse's first appearance in the archive is not evidence its gear is
    new — it is the first time we could see any gear at all."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE runners SET gear = 'B' WHERE horse_no = 1")
    out = fg.gear_timeline(["HORSE 0"], conn=conn)
    conn.close()
    assert out == {}

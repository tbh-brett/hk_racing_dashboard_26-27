"""A note on a trial, and an entry the trial prompted.

A TRIAL IS NOT A RUN, and the storage has to say so. Both carry a date and a
small number — batch 2 in the morning, race 2 that afternoon — so filed in
`run_notes` together, the second note written would silently replace the first
and neither would look wrong. They are also different kinds of observation:
"cruised, never asked" is a statement about intent, which is what a trial is
for, and it must not read as a comment on a race the horse ran.

The note belongs to the trial and shows in two places: the Trials page where it
was written, and the Form Guide's trial band, which is where it earns its keep
— the reason you followed a trial is what you want in front of you when the
horse turns up in a race.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import write_notes
from hkrd.query import trials as tq
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

TRIAL, RACE = "2026-08-21", "2026-08-21"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "n.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": RACE, "race_no": 2, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}])
        upsert.upsert_runners(conn, [
            {"race_date": RACE, "race_no": 2, "horse_no": 1,
             "horse_name": "GOLDEN SIXTY", "draw": 3, "place": "1",
             "win_odds": 2.1}])
        for no in (1, 2):
            conn.execute(
                "INSERT INTO trials (trial_date, trial_no, horse_name, place, "
                "finish_time, venue, surface, going, jockey, trainer, draw, "
                "gear, comment_text, section_times, running_positions) VALUES "
                "(?,?,'GOLDEN SIXTY',1,70.2,'ST','Turf','G','Z PURTON',"
                "'J SIZE',3,'B','held up','24.1; 23.4','3 1')", (TRIAL, no))
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    yield path


def test_a_trial_note_is_stored_against_the_trial(db) -> None:
    saved = write_notes.save_trial_note(
        "golden sixty", TRIAL, 1, "cruised, never asked", db=db)
    assert saved["trial_no"] == 1
    assert saved["horse_name"] == "GOLDEN SIXTY"       # normalised, like runs


def test_a_trial_note_and_a_race_note_on_the_same_day_coexist(db) -> None:
    """THE BUG THE SEPARATE TABLE PREVENTS. Batch 2 and race 2 fall on one
    date; sharing a table, the second write would replace the first and the
    page would show one note where two were written."""
    write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 2, "trial note", db=db)
    write_notes.save_note("GOLDEN SIXTY", RACE, 2, "race note", db=db)

    conn = get_conn(db)
    try:
        trial = conn.execute("SELECT note FROM trial_notes").fetchone()["note"]
        race = conn.execute("SELECT note FROM run_notes").fetchone()["note"]
    finally:
        conn.close()
    assert trial == "trial note"
    assert race == "race note"


def test_writing_the_same_trial_twice_replaces_rather_than_duplicates(db) -> None:
    write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 1, "first", db=db)
    write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 1, "second", db=db)
    conn = get_conn(db)
    try:
        rows = conn.execute("SELECT note FROM trial_notes").fetchall()
    finally:
        conn.close()
    assert [r["note"] for r in rows] == ["second"]


def test_an_empty_note_is_refused_rather_than_stored(db) -> None:
    with pytest.raises(ValueError, match="needs text"):
        write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 1, "   ", db=db)


def test_the_note_reaches_the_trials_page(db) -> None:
    write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 1, "never asked", db=db)
    conn = get_conn(db)
    try:
        runner = tq.batch(TRIAL, 1, conn=conn)["runners"][0]
    finally:
        conn.close()
    assert runner["note"]["note"] == "never asked"


def test_the_note_reaches_the_form_guide_s_trial_band(db) -> None:
    """Where it earns its keep: the reason you followed a trial, in front of
    you when the horse turns up in a race."""
    write_notes.save_trial_note("GOLDEN SIXTY", TRIAL, 1, "never asked", db=db)
    conn = get_conn(db)
    try:
        band = tq.for_horses(["GOLDEN SIXTY"], conn=conn)["GOLDEN SIXTY"]
    finally:
        conn.close()
    noted = [t for t in band if t.get("note")]
    assert len(noted) == 1
    assert noted[0]["trial_no"] == 1
    assert noted[0]["note"]["note"] == "never asked"


def test_a_trial_sourced_entry_is_marked_as_a_trial_not_a_race(db) -> None:
    """`2026-08-21 R1` would point the book at a race that was never run, and
    every later reader of `source_race` would believe it."""
    entry = write_notes.promote_to_blackbook(
        "GOLDEN SIXTY", reasoning="trialled sharply",
        source_date=TRIAL, source_trial_no=1, db=db)
    assert entry["source_race"] == f"{TRIAL} T1"

    conn = get_conn(db)
    try:
        row = conn.execute(
            "SELECT source_race_no FROM blackbook WHERE id = ?",
            (entry["id"],)).fetchone()
    finally:
        conn.close()
    # The race column stays empty: a batch number left in it would make the
    # Blackbook link back to race 1 of a meeting that may not exist.
    assert row["source_race_no"] is None


def test_a_race_sourced_entry_is_unchanged(db) -> None:
    """The trial path must not have moved the race path."""
    entry = write_notes.promote_to_blackbook(
        "GOLDEN SIXTY", reasoning="ran on strongly",
        source_date=RACE, source_race_no=2, db=db)
    assert entry["source_race"] == f"{RACE} R2"


def test_the_trials_page_says_which_horses_are_already_followed(db) -> None:
    """So the page can say "in the book" rather than offering to add a horse
    that is in it twice over."""
    conn = get_conn(db)
    try:
        assert tq.batch(TRIAL, 1, conn=conn)["runners"][0]["blackbook"] is None
    finally:
        conn.close()

    write_notes.promote_to_blackbook(
        "GOLDEN SIXTY", reasoning="trialled sharply",
        source_date=TRIAL, source_trial_no=1, db=db)

    conn = get_conn(db)
    try:
        runner = tq.batch(TRIAL, 1, conn=conn)["runners"][0]
    finally:
        conn.close()
    assert runner["blackbook"]["status"] == "active"


# ── what the legacy import was dropping ──────────────────────────────────────

def test_the_legacy_trials_import_keeps_the_draw_rider_and_stable() -> None:
    """All six fields are on EVERY row of the export and none reached the
    database, so 7,750 archived trials came through without them — and the
    columns that show them read as empty data rather than as a lossy import.
    """
    from hkrd.jobs.import_legacy_reports import _trial_rows

    doc = {"date": "2025-08-21", "batches": [{
        "batch_number": 1, "course": "Sha Tin All Weather Track",
        "going": "Good", "distance_m": 1200,
        "sectional_times": ["24.10", "23.40"],
        "horses": [{"horse_name": "CHIU CHOW SPIRIT", "draw": 5,
                    "jockey": "C L Chau", "trainer": "K L Man", "gear": "B",
                    "lbw": "1-1/2", "running_positions": [5, 6, 1],
                    "time": "1.12.22", "comment": "ran on well"}],
    }]}
    row = _trial_rows(doc)[0]
    assert row["draw"] == 5
    assert row["jockey"] == "C L CHAU"       # normalised, as everywhere else
    assert row["trainer"] == "K L MAN"
    assert row["going"] == "Good"
    assert row["distance"] == 1200
    assert row["surface"] == "AWT"


def test_lengths_beaten_is_parsed_not_coerced() -> None:
    """AGENTS.md's rule: `pd.to_numeric` on this field drops 79.1% of the
    values, because it carries "1-1/2", "SH", "NK" and "-"."""
    from hkrd.jobs.import_legacy_reports import _lbw

    assert _lbw("1-1/2") == 1.5
    assert _lbw("3-3/4") == 3.75
    assert _lbw("2") == 2.0
    # A margin word is not a number. None, never a zero — a zero here reads as
    # a dead heat, which is a different fact about the race.
    for word in ("SH", "NK", "-", "", None):
        assert _lbw(word) is None

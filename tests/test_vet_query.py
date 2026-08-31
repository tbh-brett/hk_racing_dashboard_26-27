"""Which veterinary records still earn a badge, and which are just history.

The records were scraped from the first build and never read back, so this is
the layer that was missing rather than the scrape. Its whole job is a judgement
— brief 07 §2: "recent records only… a horse with a clean sheet from two years
ago needs no badge" — and the reason that judgement lives here rather than in
`ingest/` is written into the ingest module: the old scraper scored records and
dropped the low ones, so a record that existed on the page and did not survive
the filter simply was not there, and nothing said so.

So the rule this file pins is that filtering is a QUERY decision and a
reversible one: `for_horse` returns everything, `for_race` returns what today's
card should show, and the same row can be in one and not the other.
"""
from __future__ import annotations

import pytest

from hkrd.query import vet as vet_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-07-15"


def _record(conn, horse, record_date, detail, category, passed=None):
    conn.execute(
        "INSERT OR REPLACE INTO vet_records (race_date, race_no, horse_no, "
        "horse_name, record_date, detail, passed_date, category) "
        "VALUES (?, 1, 1, ?, ?, ?, ?, ?)",
        (DATE, horse, record_date, detail, passed, category))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "vet.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": 1, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1800}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": i,
             "horse_name": n, "draw": i, "win_odds": 5.0}
            for i, n in enumerate(
                ["RECENT BLEEDER", "OLD BLEEDER", "ROUTINE PASS",
                 "STALE ROUTINE", "CLEARED HORSE", "CLEAN HORSE"], start=1)])

        # a month ago — well inside the window
        _record(conn, "RECENT BLEEDER", "2026-06-12",
                "Bled from both nostrils after racing.", "RESPIRATORY")
        # two years ago — the brief's own example of what needs no badge
        _record(conn, "OLD BLEEDER", "2024-06-12",
                "Bled from both nostrils after racing.", "RESPIRATORY")
        _record(conn, "ROUTINE PASS", "2026-07-04",
                "Passed post-race veterinary examination.", "PROCEDURAL")
        # a passed examination from last season is not news
        _record(conn, "STALE ROUTINE", "2025-09-01",
                "Passed post-race veterinary examination.", "PROCEDURAL")
        _record(conn, "CLEARED HORSE", "2026-06-01",
                "Lame right fore after trackwork.", "PHYSICAL",
                passed="2026-07-01")
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


# ─── what the card shows ──────────────────────────────────────────────────────

def test_a_recent_significant_record_earns_a_badge(db):
    out = vet_q.for_race(DATE, 1)
    assert "RECENT BLEEDER" in out
    assert out["RECENT BLEEDER"][0]["grade"] == "significant"


def test_a_two_year_old_record_does_not(db):
    """Brief 07 §2: 'a horse with a clean sheet from two years ago needs no badge'."""
    assert "OLD BLEEDER" not in vet_q.for_race(DATE, 1)


def test_a_recent_routine_pass_is_marked_routine_not_significant(db):
    """A badge that fires on every runner stops being read."""
    out = vet_q.for_race(DATE, 1)
    assert out["ROUTINE PASS"][0]["grade"] == "routine"


def test_a_routine_pass_from_last_season_drops_off(db):
    assert "STALE ROUTINE" not in vet_q.for_race(DATE, 1)


def test_a_horse_with_no_record_is_absent_rather_than_empty(db):
    out = vet_q.for_race(DATE, 1)
    assert "CLEAN HORSE" not in out


def test_a_clearance_is_carried_so_was_lame_reads_differently_from_is_lame(db):
    out = vet_q.for_race(DATE, 1)
    rec = out["CLEARED HORSE"][0]
    assert rec["cleared"] is True
    assert rec["passed_date"] == "2026-07-01"


def test_age_travels_with_every_record(db):
    """Never a bare figure: the badge has to be able to say how old it is."""
    out = vet_q.for_race(DATE, 1)
    assert out["RECENT BLEEDER"][0]["age_days"] == 33


# ─── what the horse's own page shows ──────────────────────────────────────────

def test_the_horse_history_keeps_what_the_card_drops(db):
    """Filtering is a per-question decision, and it must be reversible.

    The old scraper's filter was not: a dropped record was gone everywhere.
    """
    assert "OLD BLEEDER" not in vet_q.for_race(DATE, 1)
    history = vet_q.for_horse("OLD BLEEDER", before=DATE)
    assert len(history) == 1
    assert history[0]["grade"] == "historic"


def test_history_is_newest_first(db):
    conn = get_conn(db)
    with transaction(conn):
        _record(conn, "RECENT BLEEDER", "2026-01-05",
                "Passed veterinary examination.", "PROCEDURAL")
    conn.close()
    dates = [r["record_date"] for r in vet_q.for_horse("RECENT BLEEDER", before=DATE)]
    assert dates == sorted(dates, reverse=True)


def test_an_unreadable_date_is_shown_rather_than_dropped(db):
    """A date that will not parse is not evidence that nothing happened."""
    conn = get_conn(db)
    with transaction(conn):
        _record(conn, "RECENT BLEEDER", "not-a-date",
                "Bled from both nostrils.", "RESPIRATORY")
    conn.close()
    details = [r["detail"] for r in vet_q.for_race(DATE, 1)["RECENT BLEEDER"]]
    assert "Bled from both nostrils." in details


# ─── the card carries it ──────────────────────────────────────────────────────

def test_the_race_card_carries_vet_records_per_runner(db):
    from hkrd.query import raceday as raceday_q

    card = raceday_q.build_card(DATE, 1)
    by_name = {r["horse_name"]: r for r in card["runners"]}
    assert by_name["RECENT BLEEDER"]["vet"]
    assert by_name["CLEAN HORSE"]["vet"] == []

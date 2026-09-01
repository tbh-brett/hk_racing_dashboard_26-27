"""The trial calendar — the axis the meeting header cannot address.

Trials are held on mornings that are mostly NOT race days: 159 trial days in
the archive against 1,712 race meetings, and the two calendars barely overlap.
Layer 1 knows meetings, so asking it for "21 Aug 2026" gets nothing — no race
was run that day — and the whole trials archive was unreachable from the page
whose subject it is.

Brief 08 §1's ban on per-page date pickers is about four pages disagreeing over
which MEETING is on screen. A second calendar is not that.
"""
from __future__ import annotations

import pytest

from hkrd.query import trials as tq
from hkrd.store.connect import get_conn, init_db, transaction

DAYS = ("2026-08-21", "2026-08-20", "2026-07-07")


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    with transaction(conn):
        for date, venue, batches in (("2026-08-21", "ST", 3),
                                     ("2026-08-20", "HV", 2),
                                     ("2026-07-07", "ST", 1)):
            for no in range(1, batches + 1):
                for i in range(1, 4):
                    conn.execute(
                        "INSERT INTO trials (trial_date, trial_no, horse_name, "
                        "place, finish_time, venue, surface, going, jockey, "
                        "trainer, draw, gear, comment_text, section_times, "
                        "running_positions) VALUES (?,?,?,?,?,?,'Turf','G',"
                        "'Z PURTON','J SIZE',?,'B','ran on','24.1; 23.4','3 1')",
                        (date, no, f"HORSE {date[-2:]}{no}{i}", i,
                         70.0 + i * 0.2, venue, i))
    yield conn
    conn.close()


def test_the_calendar_is_newest_first(db) -> None:
    got = [d["trial_date"] for d in tq.days(conn=db)]
    assert got == list(DAYS)


def test_each_morning_carries_what_is_on_it(db) -> None:
    """A date alone is not a choice. How many batches and where they were run
    is what makes one morning worth opening over another."""
    newest = tq.days(conn=db)[0]
    assert newest == {"trial_date": "2026-08-21", "batches": 3,
                      "runners": 9, "venues": "ST"}


def test_a_day_at_two_tracks_lists_both_in_a_stable_order(db) -> None:
    """group_concat has no ordering guarantee. ST,HV one day and HV,ST the
    next reads as a difference between the days, and it is not one."""
    with transaction(db):
        db.execute(
            "INSERT INTO trials (trial_date, trial_no, horse_name, place, "
            "venue, surface) VALUES ('2026-08-21', 9, 'VISITOR', 1, 'HV', 'Turf')")
    assert tq.days(conn=db)[0]["venues"] == "HV,ST"


def test_pinning_a_morning_shows_only_that_morning(db) -> None:
    """The rolling feed takes the newest N batches, which spans mornings. A
    chosen date must not quietly pull in the one before it."""
    got = tq.recent_batches(limit=60, date="2026-08-20", conn=db)
    assert {b["trial_date"] for b in got} == {"2026-08-20"}
    assert len(got) == 2


def test_the_rolling_feed_is_unchanged_when_no_day_is_chosen(db) -> None:
    """The page's default behaviour before there was any way to choose."""
    got = tq.recent_batches(limit=4, conn=db)
    assert len(got) == 4
    assert got[0]["trial_date"] == "2026-08-21"


def test_the_venue_filter_narrows_the_calendar_too(db) -> None:
    """Filtering the batches to HV while the day list still offers ST-only
    mornings would offer a choice that comes back empty."""
    assert [d["trial_date"] for d in tq.days(venue="HV", conn=db)] == ["2026-08-20"]


def test_a_day_with_no_trials_is_absent_rather_than_empty(db) -> None:
    assert not tq.recent_batches(date="2026-01-01", conn=db)
    assert "2026-01-01" not in [d["trial_date"] for d in tq.days(conn=db)]


def test_only_the_newest_morning_is_live_the_rest_are_archived(db) -> None:
    """HKJC's barrier-trial page shows one day; everything before it is behind
    the archive, whose URL carries the date. The video player takes that page
    as its return link, so which one it should be is a fact about the data."""
    assert tq.batch("2026-08-21", 1, conn=db)["archived"] is False
    assert tq.batch("2026-08-20", 1, conn=db)["archived"] is True
    assert tq.batch("2026-07-07", 1, conn=db)["archived"] is True


# ── what the screening page needs from a batch ───────────────────────────────

def test_a_batch_says_how_many_it_flagged(db) -> None:
    """The batch header says it, so a morning worth opening is visible before
    it is opened."""
    b = tq.batch("2026-08-21", 1, conn=db)
    assert b["flagged"] == sum(
        1 for r in b["runners"] if r["quality_band"] in ("STANDOUT", "POSITIVE"))


def test_every_runner_carries_the_rest_of_its_batch(db) -> None:
    """The check on the mark: a standout out of a batch whose other five all
    won next start says more about the batch than about the horse."""
    b = tq.batch("2026-08-21", 1, conn=db)
    for r in b["runners"]:
        names = {o["horse_name"] for o in r["retro"]}
        assert r["horse_name"] not in names, "a horse is not its own retro"
        assert len(r["retro"]) == len(b["runners"]) - 1


def test_the_retro_is_built_without_a_query_per_runner(db) -> None:
    """A nine-horse batch would otherwise cost nine round trips to say what
    one already knows. Asserted by counting the statements the call makes."""
    # sqlite3.Connection.execute is read-only, so the count is taken with a
    # thin wrapper standing in for the connection.
    class Counting:
        def __init__(self, inner):
            self.inner, self.n = inner, 0

        def execute(self, sql, *a):
            self.n += 1
            return self.inner.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    spy = Counting(db)
    runners = len(tq.batch("2026-08-21", 1, conn=spy)["runners"])
    assert runners >= 3, "the fixture needs a batch worth counting over"
    # A handful of reads for the batch and its next starts — never one per
    # runner FOR each runner, which is what building the retro naively costs.
    assert spy.n < runners * runners, (
        f"{spy.n} statements for {runners} runners looks quadratic")


def test_each_read_is_on_the_runner_for_the_panel(db) -> None:
    r = tq.batch("2026-08-21", 1, conn=db)["runners"][0]
    assert [x["key"] for x in r["quality_reads"]] == ["FINISH", "COMMENT", "MARGIN"]


def test_the_band_hold_sentence_is_computed_not_quoted(db) -> None:
    """The artboard hard-codes one of these per band. Hard-coded, it is a
    claim about the archive that stops being true the first time the archive
    grows — and a calibration figure nobody recomputes is exactly the kind of
    number this page exists to argue against."""
    c = tq.calibration(conn=db)
    for band in c["order"]:
        hold = c["bands"][band]["hold"]
        assert hold and band in hold
        row = c["bands"][band]
        if row["with_next"]:
            # The sentence quotes the same rate the row carries, so the prose
            # and the table cannot disagree.
            assert f"{row['next_win_rate']:.1%}" in hold


def test_a_band_with_nothing_to_show_says_so_rather_than_zero(db) -> None:
    c = tq.calibration(conn=db)
    for band in c["order"]:
        row = c["bands"][band]
        if not row["with_next"]:
            assert "no next start on record" in row["hold"]

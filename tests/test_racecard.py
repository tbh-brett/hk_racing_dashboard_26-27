"""HKJC race cards — the field as declared, before the race is run.

The old scraper had a 27-column hardcoded map and fell back to it whenever
header detection found fewer than ten fields. That is the shape of the bug
that made `parse_corunning` read a four-column table as three and produce
10,690 records of nothing across 87 meetings — so the tests that matter here
are the ones that prove a layout change is noticed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import racecard as rc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def html():
    return (FIXTURES / "racecard.html").read_text(encoding="utf-8")


@pytest.fixture()
def reordered():
    return (FIXTURES / "racecard_reordered.html").read_text(encoding="utf-8")


# ── the header ───────────────────────────────────────────────────────────────

def test_venue_and_course_are_not_the_same_field(html):
    """The legacy scraper put the RAIL in a key called `race_course` and the
    surface in `surface`. Migrating the archive with those swapped had already
    put the rail in `venue` for all 1,712 races."""
    head = rc.parse_race_header(html, 4)
    assert head["venue"] == "HV"        # the racecourse
    assert head["course"] == "C"        # the rail position
    assert head["surface"] == "Turf"


def test_going_is_matched_longest_first(html):
    """"Good to Firm" contains "Good". A shortest-first scan records every GF
    meeting as G — and ET pars are computed per going band, so the whole race
    lands in the wrong reference."""
    assert rc.parse_race_header(html, 4)["going"] == "GF"


def test_the_header_carries_distance_class_and_off_time(html):
    head = rc.parse_race_header(html, 4)
    assert head["distance"] == 1650
    assert head["race_class"] == "4"
    assert head["off_time"] == "20:15"
    assert head["prize"] == 1275000
    assert head["race_name"] == "SHEK KIP MEI HANDICAP"


def test_an_all_weather_race_sets_both_surface_and_course(html):
    awt = html.replace('Course "C", 1650M', "All Weather Track, 1200M")
    head = rc.parse_race_header(awt, 4)
    assert head["surface"] == "AWT" and head["course"] == "AWT"


def test_an_unreadable_header_raises_naming_what_was_missing(html):
    with pytest.raises(rc.RacecardError, match="race header unreadable"):
        rc.parse_race_header("<html><body>nothing</body></html>", 4,
                             source="2026-07-15 HV R4")


# ── the field ────────────────────────────────────────────────────────────────

def test_columns_are_found_by_header_not_by_position(html, reordered):
    """The whole point. A parser that indexes by position reads a jockey's
    name as a horse number when the columns move."""
    a = {r["horse_no"]: r for r in rc.parse_racecard(html, 4)}
    b = {r["horse_no"]: r for r in rc.parse_racecard(reordered, 4)}
    assert a[1]["horse_name"] == b[1]["horse_name"] == "SKY DEEP"
    assert a[1]["jockey"] == b[1]["jockey"] == "J Moreira"
    assert a[6]["draw"] == b[6]["draw"] == 12
    assert a[6]["rating"] == b[6]["rating"] == 68


def test_a_missing_required_column_raises_rather_than_guessing(html):
    """There is no positional fallback for the horse number. Guessing where it
    is, is how a card becomes nonsense that still validates."""
    broken = html.replace("<th>Horse No.</th>", "<th>Ref</th>")
    with pytest.raises(rc.RacecardError, match="no horse_no column"):
        rc.parse_racecard(broken, 4, source="2026-07-15 HV R4")


def test_the_brand_number_is_split_out_of_the_name(html):
    """HKJC writes it into the name cell as "NAME (V123)". Leaving it there
    makes every join on horse_name miss."""
    row = next(r for r in rc.parse_racecard(html, 4) if r["horse_no"] == 1)
    assert row["horse_name"] == "SKY DEEP"
    assert row["brand_no"] == "V123"


def test_a_scratched_runner_is_marked_not_dropped(html):
    """The card is the record of what was DECLARED. A horse that came out is a
    fact about the race, and dropping it silently renumbers the field."""
    rows = rc.parse_racecard(html, 4)
    assert len(rows) == 3
    scratched = [r for r in rows if r["scratched"]]
    assert [r["horse_no"] for r in scratched] == [9]


def test_a_misaligned_table_raises_rather_than_returning_plausible_rows(html):
    """A shift produces rows that are structurally fine and semantically
    nonsense, which is how 10,690 corunning records survived 87 meetings."""
    shifted = html.replace(
        "<th>Horse No.</th><th>Last 6 Runs</th><th>Horse</th><th>Wt.</th>",
        "<th>Horse No.</th><th>Horse</th><th>Wt.</th><th>Last 6 Runs</th>")
    with pytest.raises(rc.RacecardError, match="misaligned"):
        rc.parse_racecard(shifted, 4, source="2026-07-15 HV R4")


def test_numbers_come_back_as_numbers(html):
    row = next(r for r in rc.parse_racecard(html, 4) if r["horse_no"] == 1)
    assert row["draw"] == 3
    assert row["actual_weight"] == 135
    assert row["declared_weight"] == 1102
    assert row["age"] == 5
    assert row["days_since_last"] == 21
    assert row["gear"] == "B"


def test_a_page_with_no_table_raises(html):
    with pytest.raises(rc.RacecardError, match="no race card table"):
        rc.parse_racecard("<html><body><p>nothing</p></body></html>", 4)


def test_no_raw_scaffolding_leaks_into_the_result(html):
    """The `_raw` copies exist for validation and must not reach the store."""
    rows = rc.parse_racecard(html, 4)
    assert all(not k.endswith("_raw") for r in rows for k in r)

"""HKJC veterinary records — what the vets said, and when.

The old module computed a concern score and then FILTERED its own output by
it, so the scraper decided what the interface was allowed to see using
thresholds that lived nowhere else. A record that existed on the page and did
not survive that filter simply was not there, and nothing said so.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import vet

FIXTURE = Path(__file__).parent / "fixtures" / "vet_race.html"


@pytest.fixture()
def html():
    return FIXTURE.read_text(encoding="utf-8")


def test_every_record_on_the_page_comes_back(html):
    """Ingest reports. What is worth showing is not the scraper's question —
    a vet note is read differently on a race card and on a horse's history."""
    rows = vet.parse_vet_table(html, race_date="2026-07-15")
    assert len(rows) == 5


def test_a_blank_horse_cell_continues_the_horse_above_it(html):
    """One horse's cell is filled once and its later records leave it blank."""
    rows = vet.parse_vet_table(html, race_date="2026-07-15")
    sky = [r for r in rows if r["horse_name"] == "SKY DEEP"]
    assert len(sky) == 2
    assert [r["record_date"] for r in sky] == ["2026-04-03", "2026-06-12"]
    assert all(r["horse_no"] == 1 for r in sky)


def test_stand_by_starters_are_not_folded_into_the_field(html):
    """Their records are real, but they are not in the race, and adding them
    silently puts horses on the card that will not run."""
    rows = vet.parse_vet_table(html, race_date="2026-07-15")
    assert "RESERVE HORSE" not in {r["horse_name"] for r in rows}


def test_dates_are_read_as_day_first(html):
    """HKJC writes dd/mm/yyyy. 03/04/2026 read as March 4th rather than April
    3rd moves a vet note a month."""
    rows = vet.parse_vet_table(html, race_date="2026-07-15")
    first = rows[0]
    assert first["record_date"] == "2026-04-03"
    assert first["passed_date"] == "2026-04-18"


def test_days_before_the_race_is_returned_not_used_to_filter(html):
    """What counts as recent is the reader's question."""
    rows = vet.parse_vet_table(html, race_date="2026-07-15")
    by_name = {r["horse_name"]: r for r in rows if r["horse_name"] != "SKY DEEP"}
    assert by_name["SHAMZ"]["days_before_race"] == 56
    assert by_name["LIGHT YEARS GLORY"]["days_before_race"] == 14


def test_no_race_date_leaves_the_age_unknown_rather_than_zero(html):
    rows = vet.parse_vet_table(html)
    assert all(r["days_before_race"] is None for r in rows)
    assert all(r["record_date"] for r in rows)


# ── the injury vocabulary ────────────────────────────────────────────────────

@pytest.mark.parametrize("detail,expected", [
    ("Substantial blood in the trachea after racing.", "RESPIRATORY"),
    ("Post-race scoping showed blood.", "RESPIRATORY"),
    ("Atrial fibrillation detected.", "CARDIAC"),
    ("Veterinary inspection found lameness in the near fore.", "PHYSICAL"),
    ("Withdrawn from racing on veterinary advice.", "PHYSICAL"),
    ("Unacceptable performance; rider concerned.", "PERFORMANCE"),
    ("Routine dental treatment.", "PROCEDURAL"),
])
def test_the_injury_vocabulary_files_a_note_where_it_belongs(detail, expected):
    assert vet.classify(detail) == expected


def test_an_unrecognised_note_is_unknown_not_the_mildest_category():
    """Filing it under PROCEDURAL because that is mildest would understate it
    in exactly the cases nobody has seen before."""
    assert vet.classify("Observed to have an unusual gait, cause "
                        "undetermined.") == "UNKNOWN"


def test_the_more_specific_family_wins():
    """A bleeding note is respiratory, not physical, even though bleeding into
    a joint would be physical — order in the table is the decision."""
    assert vet.classify("Bleeding from the nostrils post-race.") == "RESPIRATORY"


# ── failure ──────────────────────────────────────────────────────────────────

def test_a_missing_detail_column_raises_rather_than_guessing(html):
    broken = html.replace("<th>Details</th>", "<th>Notes On File</th>")
    with pytest.raises(vet.VetError, match="no detail column"):
        vet.parse_vet_table(broken, source="2026-07-15 HV R4")


def test_a_page_with_no_vet_table_raises(html):
    with pytest.raises(vet.VetError, match="no veterinary record table"):
        vet.parse_vet_table("<html><body><p>none</p></body></html>",
                            source="2026-07-15 HV R4")


def test_the_error_names_the_race_it_could_not_read(html):
    with pytest.raises(vet.VetError, match=r"2026-07-15 HV R4"):
        vet.parse_vet_table("<html></html>", source="2026-07-15 HV R4")

"""HKJC barrier trials — the batches, as run.

The old `_parse_data_table` indexed cells[0] through cells[9] with no check
that they held what it assumed. That is the corunning bug, and it is worse
here than usual: a trial table has no numeric column in its first three cells,
so a shift produces a jockey where the horse belongs and nothing about the
output looks wrong.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import trials as tr

FIXTURE = Path(__file__).parent / "fixtures" / "trials_day.html"


@pytest.fixture()
def html():
    return FIXTURE.read_text(encoding="utf-8")


def test_every_batch_on_the_day_is_found(html):
    batches = tr.parse_trial_day(html, source="2026-08-21")
    assert [b["trial_no"] for b in batches] == [1, 2]
    assert [len(b["runners"]) for b in batches] == [3, 1]


def test_distance_is_published_and_carried(html):
    """It is in the batch header. The legacy import dropped it, which is why
    no trial in the 7,750-row archive has one — the source had it all along."""
    batches = tr.parse_trial_day(html)
    assert batches[0]["distance"] == 1200
    assert batches[1]["distance"] == 1000


def test_venue_and_surface_come_from_the_course_name(html):
    batches = tr.parse_trial_day(html)
    assert batches[0]["venue"] == "ST" and batches[0]["surface"] == "AWT"
    assert batches[1]["venue"] == "HV" and batches[1]["surface"] == "Turf"


def test_the_batch_conditions_are_read(html):
    b = tr.parse_trial_day(html)[0]
    assert b["going"] == "GOOD"
    assert b["finish_time"] == "1.11.38"
    assert b["section_times"] == ["24.50", "22.90", "23.90"]


def test_place_is_derived_from_the_last_running_position(html):
    """RESULT is empty at source on every row, which settled open question C2:
    it was never populated rather than merely unwired."""
    runners = tr.parse_trial_day(html)[0]["runners"]
    assert all(not r.get("result") for r in runners)
    assert [r["place"] for r in runners] == [1, 2, 9]


def test_the_brand_code_is_split_out_of_the_name(html):
    r = tr.parse_trial_day(html)[0]["runners"][0]
    assert r["horse_name"] == "CALA DEI MORI"
    assert r["horse_code"] == "V123"


def test_hkjc_vulgar_fractions_survive(html):
    """"1-1/2" is one and a half lengths and "3/4" is three quarters. float()
    raises on both, and pd.to_numeric — which the old package used — silently
    discards them."""
    runners = tr.parse_trial_day(html)[0]["runners"]
    assert [r["lbw"] for r in runners] == [0.0, 1.5, 0.75]


def test_columns_are_found_by_header_not_by_position(html):
    """A trial table has no numeric column in its first three cells, so a
    shift shows up as a jockey where the horse belongs — and nothing about the
    output looks wrong."""
    reordered = html.replace(
        "<th>Horse</th><th>Jockey</th><th>Trainer</th>",
        "<th>Jockey</th><th>Horse</th><th>Trainer</th>")
    runners = tr.parse_trial_day(reordered)[0]["runners"]
    # The header now says cell 0 is the jockey, so the parser reads it as one.
    assert runners[0]["jockey"] == "CALA DEI MORI (V123)"
    assert runners[0]["horse_name"] == "Z PURTON"


def test_a_missing_horse_column_raises_rather_than_guessing(html):
    broken = html.replace("<th>Horse</th>", "<th>Runner Ref</th>")
    with pytest.raises(tr.TrialsError, match="no horse_name column"):
        tr.parse_trial_day(broken, source="2026-08-21")


def test_a_numeric_horse_name_is_a_misalignment_not_a_horse(html):
    """The reverse of the results parser's check: here a shift shows up as a
    NUMBER where a name belongs.

    The realistic case is HKJC adding a leading cell the header does not
    declare — a rank, an icon column — which pushes every field one to the
    right while the header stays put.
    """
    shifted = html
    for draw, name in ((3, "CALA DEI MORI (V123)"),
                       (5, "SELF IMPROVEMENT (D456)"),
                       (1, "ETALON OR (K789)")):
        shifted = shifted.replace(f"<td>{name}</td>",
                                  f"<td>{draw}</td><td>{name}</td>")
    assert shifted != html
    with pytest.raises(tr.TrialsError, match="misaligned"):
        tr.parse_trial_day(shifted, source="2026-08-21")


def test_a_page_with_no_batch_raises(html):
    with pytest.raises(tr.TrialsError, match="no trial batch"):
        tr.parse_trial_day("<html><body><p>nothing</p></body></html>",
                           source="2026-08-21")

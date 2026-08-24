"""ingest/corunning — the parser that replaces photo OCR for lane position.

The module exists because the previous parser was silently wrong for its whole
life: it indexed a four-column table as three, so every field read one column
left and the comment itself was never read. It produced 10,690 structurally
valid records across 87 meetings and not one contained any comment prose.

These tests pin the property that prevents a repeat -- columns are located by
header text, so a layout change raises instead of shifting.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import corunning

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_the_real_four_column_layout():
    rows = corunning.parse_corunning(_load("corunning_4col.html"))
    assert len(rows) == 3
    first = rows[0]
    assert first["place"] == 1
    assert first["horse_no"] == 3
    assert first["horse_name"] == "FASHION LEGEND"
    assert first["horse_code"] == "J080"
    assert "kicked clear" in first["comment"]


def test_the_exact_bug_that_broke_the_old_parser():
    """Positional indexing put the horse number in horse_name and the horse
    name in comment. Every field must now land where it belongs."""
    rows = corunning.parse_corunning(_load("corunning_4col.html"))
    for r in rows:
        assert not r["horse_name"].isdigit(), "horse_name holding a number = the old shift"
        assert not r["comment"].endswith(")"), "comment holding 'NAME (CODE)' = the old shift"
        assert len(r["comment"]) > 20, "the real comment text must be present"


def test_column_order_does_not_matter():
    """Headers drive the mapping, so a reordered table still parses correctly."""
    rows = corunning.parse_corunning(_load("corunning_reordered.html"))
    assert rows[0]["horse_name"] == "FASHION LEGEND"
    assert rows[0]["place"] == 1
    assert rows[0]["horse_no"] == 3
    assert rows[0]["comment"] == "Led throughout on the rail."


def test_missing_table_raises_naming_what_it_wanted():
    with pytest.raises(corunning.CoRunningError, match="Horse.*Comment"):
        corunning.parse_corunning("<html><body><p>nothing here</p></body></html>")


def test_empty_document_raises_with_the_source():
    with pytest.raises(corunning.CoRunningError, match="race 4"):
        corunning.parse_corunning("", source="race 4")


def test_url_accepts_either_date_form():
    expected = ("https://racing.hkjc.com/en-us/local/information/"
                "corunning?date=20260715&raceno=1")
    assert corunning.url_for("2026-07-15", 1) == expected
    assert corunning.url_for("20260715", 1) == expected


# ── lane extraction: the point of the exercise ───────────────────────────────

@pytest.mark.parametrize("comment,expected", [
    ("Raced three wide without cover throughout.", {"three_wide", "wide", "without_cover"}),
    ("Settled one off the fence, improved from the 400m.", {"one_off_rail"}),
    ("Held up on the rail, denied a run near the 200 Metres.", {"rail"}),
    ("Raced four deep early.", {"four_wide"}),
    ("Travelled on the inside.", {"inside"}),
])
def test_lane_notes_are_read_from_the_prose(comment, expected):
    assert set(corunning.extract_lane_notes(comment)) == expected


def test_lane_notes_report_only_what_the_text_says():
    """No inferred lane number. Inventing precision is the fault the OCR
    approach had -- it produced a number nobody could check."""
    assert corunning.extract_lane_notes("Jumped well and led.") == ()
    assert corunning.extract_lane_notes("") == ()


def test_end_to_end_lane_read_for_a_race():
    rows = corunning.parse_corunning(_load("corunning_4col.html"))
    lanes = {r["horse_name"]: corunning.extract_lane_notes(r["comment"]) for r in rows}
    assert "rail" in lanes["FASHION LEGEND"]
    assert "three_wide" in lanes["TELECOM POWER"]
    assert "one_off_rail" in lanes["LUCKY BLESSING"]

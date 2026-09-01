"""The extractor that decides whether a page has been checked at all.

This exists because the conformance test's first version read only a `COLS`
array, found none in six of the eight artboards, and skipped them — so it
covered two pages while the suite reported green on all eight, and the Trials
page shipped for weeks without the draw or the jockey the design asks for.

A check that cannot see a page looks exactly like a page with nothing wrong.
So the extractor is itself tested: on the real artboards, so a canvas re-export
that changes their shape fails here rather than quietly halving the coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from design_headers import header_parts, headers

DESIGN = Path(__file__).resolve().parent.parent / "web" / "design-source"

# Artboards with a table, and one header each that must survive extraction. Not
# the whole set — that would restate the design — but enough that an extractor
# returning something plausible and wrong still fails.
WITNESS = {
    # The screening artboard, September 2026. SCREEN is the column a run
    # leaves the page through; Q is the band mark it leaves on.
    "Trials": {"DR", "Q", "JOCKEY", "SECTIONS", "MGN", "TRIAL COMMENT",
               "NEXT START", "SCREEN"},
    "Results": {"FIN", "WIN SP", "PLC SP", "ET FIGURE"},
    "Race Day": {"DR", "HORSE", "JOCKEY"},
    "Form Guide": {"DR", "JOCKEY", "TRAINER", "WT"},
    "Lookup": {"DATE", "CONDITIONS", "HORSE", "FIN"},
    "Bets": {"DATE", "TYPE", "STAKE", "RETURN"},
    "Blackbook": {"TAG", "ENTRIES", "RUNS", "ROI"},
}


@pytest.mark.parametrize("artboard,expected", sorted(WITNESS.items()))
def test_the_extractor_finds_the_columns_that_are_there(
        artboard: str, expected: set[str]) -> None:
    found = headers(DESIGN / f"{artboard}.dc.html")
    missing = expected - found
    assert not missing, (
        f"{artboard}: the extractor no longer finds {sorted(missing)}. Either "
        "the artboard was re-exported in a shape design_headers.py does not "
        "understand — fix it there — or these columns really were removed from "
        "the design. It must never come back an empty set and pass.")


@pytest.mark.parametrize("artboard", sorted(WITNESS))
def test_no_artboard_with_a_table_reads_as_empty(artboard: str) -> None:
    """The failure mode that made six pages unchecked, asserted directly."""
    assert headers(DESIGN / f"{artboard}.dc.html"), (
        f"{artboard} has a table and the extractor found nothing in it")


def test_a_data_row_is_never_mistaken_for_a_header() -> None:
    """Header cells are literal; data cells are `{{ }}` placeholders.

    Reading a data row would fill the expected set with template expressions
    and every page would then fail for columns that do not exist.
    """
    for f in sorted(DESIGN.glob("*.dc.html")):
        for h in headers(f):
            assert "{{" not in h and "}}" not in h, f"{f.name}: {h!r}"


def test_a_packed_header_splits_into_its_columns() -> None:
    """The design writes several columns under one heading, on the middot."""
    parts = dict(header_parts("POSITIONS · TRIP"))
    assert set(parts) == {"POSITIONS", "TRIP"}
    assert parts["POSITIONS"] == ["POSITIONS"]


def test_a_caption_is_not_mistaken_for_a_column_name() -> None:
    """`95% CI` and `HARVILLE-HENERY v OLD 3× RULE` are prose in a header cell.

    Requiring them produced failures for columns that ARE on screen, and a
    check that cries wolf is one people start exempting.
    """
    for caption in ("95% CI", "HARVILLE-HENERY v OLD 3× RULE", "FIN Δ ≥6"):
        assert dict(header_parts(caption))[caption] == [], caption


def test_a_real_column_name_is_still_required() -> None:
    """The other half of the trade: skipping captions must not skip columns.

    `DR` and `JOCKEY` are the two the Trials page was missing. If the rule that
    lets captions through also let these through, the check would be decoration.
    """
    for header in ("DR", "JOCKEY", "NEXT ACTUAL START", "PLACE DIV"):
        words = dict(header_parts(header))[header]
        assert words, f"{header!r} must be checkable, not skipped as prose"
        assert all(w.isupper() for w in words)

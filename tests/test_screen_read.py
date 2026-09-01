"""The screen read — why a trial got the mark it got.

The screening artboard ships its own scoring: finish ±2, MARGIN ±1, and a
comment cue from a twelve-word regex. The engine already running scores finish
and comment only, from fourteen clauses that cleared a 95% interval against
7,622 real comments, and it excludes margin deliberately — holding the finish
constant, mid-pack finishers go 7.2% next-start wins inside two lengths and
5.7% beyond fourteen, which is noise across a sevenfold range.

The owner chose the measured engine. So the panel renders the design's LAYOUT
with the engine's verdict, and MARGIN appears at zero points carrying the
reason it is there at all — a factor shown at zero with no explanation reads
as one the engine forgot rather than one it weighed and discarded.
"""
from __future__ import annotations

import pytest

from hkrd.derive.trial_quality import rate


def _reads(**kw):
    return {r["key"]: r for r in rate(**kw)["reads"]}


def test_every_read_carries_what_it_was_worth() -> None:
    out = rate(place=1, field_size=6, margin=0.0,
               comment="Ran on nicely once clear.")
    assert [r["key"] for r in out["reads"]] == ["FINISH", "COMMENT", "MARGIN"]
    assert all("points" in r and "text" in r for r in out["reads"])


def test_the_reads_add_up_to_the_score_that_set_the_band() -> None:
    """A panel whose factors do not sum to the total is a panel that has to be
    taken on trust, which is the opposite of why it exists."""
    for comment in ("Ran on nicely once clear.", "Weakened in the straight.",
                    "Workmanlike, in the market for the run.", None):
        for place in (1, 2, 8):
            out = rate(place=place, field_size=9, margin=2.0, comment=comment)
            assert round(sum(r["points"] for r in out["reads"]), 2) == out["score"]


def test_margin_is_shown_and_never_scored() -> None:
    """The artboard gives it ±1. Measured, it does not carry."""
    close = _reads(place=4, field_size=9, margin=0.2, comment=None)["MARGIN"]
    wide = _reads(place=4, field_size=9, margin=14.0, comment=None)["MARGIN"]
    assert close["points"] == 0.0 and wide["points"] == 0.0
    assert "0.2L" in close["text"] and "14.0L" in wide["text"]


def test_a_zero_factor_says_why_it_is_zero() -> None:
    """Otherwise it reads as a factor the engine forgot."""
    for key in ("MARGIN", "COMMENT"):
        read = _reads(place=4, field_size=9, margin=2.0,
                      comment="nothing scoreable here")[key]
        assert read["points"] == 0.0
        assert read["note"], f"{key} at zero must explain itself"


def test_the_margin_note_names_the_measurement_not_a_preference() -> None:
    note = _reads(place=1, field_size=6, margin=0.0, comment=None)["MARGIN"]["note"]
    assert "does not move the band" in note


def test_winning_is_worth_two_and_says_so() -> None:
    read = _reads(place=1, field_size=8, margin=0.0,
                  comment="Ran on nicely once clear.")["FINISH"]
    assert read["points"] == 2.0
    assert read["text"] == "won the trial"


def test_an_untested_trial_still_produces_a_full_read() -> None:
    """A horse that was not asked to win short-circuits the score. The panel
    must still show all three factors, or the reader cannot tell a zero from a
    missing row."""
    out = rate(place=5, field_size=8, margin=3.0,
               comment="Ridden conservatively, just to stretch out.")
    assert out["band"] == "UNTESTED"
    assert [r["key"] for r in out["reads"]] == ["FINISH", "COMMENT", "MARGIN"]
    assert all(r["points"] == 0.0 for r in out["reads"])


def test_a_missing_margin_does_not_read_as_a_dead_heat() -> None:
    read = _reads(place=3, field_size=8, margin=None, comment=None)["MARGIN"]
    assert read["text"] == "no margin recorded"


@pytest.mark.parametrize("band,mark", [
    ("STANDOUT", "++"), ("POSITIVE", "+"), ("NEUTRAL", "·"), ("NEGATIVE", "−"),
])
def test_the_page_and_the_form_guide_read_one_engine(band: str, mark: str) -> None:
    """`Q` on this page and the inline band on the Form Guide are the same
    call. A second scoring in the browser would drift within a season."""
    from hkrd.derive.trial_quality import MARKS
    assert MARKS[band] == mark

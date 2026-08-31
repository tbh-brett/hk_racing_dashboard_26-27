"""WHAT MADE IT AN OUTLIER — the sentence, and why it is built server-side.

The artboard's own column. Production had the numbers and not the reading, so
the person had to do the arithmetic the page was already doing.

It is assembled in `query/slices.py` for the same reason `figure_display` is:
a reading written in the browser is a second opinion, and two opinions about
one run drift apart within a season with nothing to say which is right.

The design writes "no excuse in the figure" for every under-performance. That
is wrong on the runs where the stewards recorded one, and those are precisely
the runs worth keeping — so this diverges from the design deliberately.
"""
from __future__ import annotations

from hkrd.query.slices import _why_outlier


def _run(**kw):
    base = {"market_rank": 2, "place": 9, "figure": 88.0, "fin_delta": -7,
            "tags": []}
    return {**base, **kw}


def test_beating_the_market_names_the_figure_that_backs_it() -> None:
    why = _why_outlier(_run(market_rank=11, place=1, fin_delta=10, figure=104.0))
    assert "started 11th in the market and finished 1st" in why
    assert "figure 104 backs it up" in why


def test_under_performing_with_trouble_names_the_trouble() -> None:
    """The design's flat verdict is wrong here, and this is the case that
    matters: a horse checked at the 800m HAS an excuse, and calling it
    excuseless is the reading that loses a horse worth following."""
    why = _why_outlier(_run(tags=["checked", "wide_no_cover"]))
    assert "checked, wide no cover in running" in why
    assert "no excuse" not in why


def test_under_performing_with_no_trouble_says_so_plainly() -> None:
    why = _why_outlier(_run())
    assert why == "market rated it 2nd and it ran 9th — no excuse in the figure"


def test_a_missing_figure_is_never_stated_as_a_verdict() -> None:
    """With no figure there is nothing to have no excuse in. Saying otherwise
    would assert a measurement that was never taken."""
    why = _why_outlier(_run(figure=None))
    assert "no figure and no trouble reported" in why
    assert "no excuse in the figure" not in why


def test_only_the_first_two_tags_reach_the_sentence() -> None:
    """A run carrying six tags produces a paragraph, and a column that has to
    be hovered to be read is a column nobody reads."""
    why = _why_outlier(_run(tags=["checked", "hampered", "eased", "shifted_out"]))
    assert "eased" not in why
    assert why.count(",") == 1


def test_an_unreadable_rank_does_not_crash_or_invent_one() -> None:
    assert "—" in _why_outlier(_run(market_rank=None, place=None, fin_delta=-3))


def test_eleventh_is_not_eleven_st() -> None:
    """The ordinal rule everyone gets wrong, on the number a 12-horse field
    hits most."""
    why = _why_outlier(_run(market_rank=11, place=13, fin_delta=-2))
    assert "11th" in why and "13th" in why

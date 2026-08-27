"""HKJC dividends — what each pool actually paid.

The dividend table carries state down its rows: a pool label opens a section
and unlabelled rows continue it. Two things about that are easy to get wrong
in ways that produce plausible numbers, and both are pinned here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import dividends as dv

FIXTURE = Path(__file__).parent / "fixtures" / "dividends.html"


@pytest.fixture()
def html():
    return FIXTURE.read_text(encoding="utf-8")


def test_quinella_place_is_not_filed_as_quinella(html):
    """"QUINELLA PLACE" starts with "QUINELLA". A shortest-first scan files
    every QPL dividend under QIN — silently, and with a number that looks
    entirely reasonable. The ordering IS the correctness."""
    rows = dv.parse_dividends(html)
    qpl = [r for r in rows if r["pool"] == "QPL"]
    qin = [r for r in rows if r["pool"] == "QIN"]
    assert len(qpl) == 3 and len(qin) == 1
    assert {r["combination"] for r in qpl} == {"1,6", "1,9", "6,9"}
    assert qin[0]["combination"] == "1,6"
    assert qin[0]["dividend_per_10"] == 1358.00


def test_an_unlabelled_row_continues_the_pool_above_it(html):
    """PLACE pays three, and only the first row names the pool."""
    rows = dv.parse_dividends(html)
    place = [r for r in rows if r["pool"] == "PLACE"]
    assert [r["combination"] for r in place] == ["1", "6", "9"]
    assert [r["dividend_per_10"] for r in place] == [24.50, 77.00, 33.50]


def test_a_footnote_is_not_read_as_a_dividend(html):
    """The old parser took the first two cells of ANY unlabelled row, so a
    note became a dividend under whichever pool preceded it."""
    rows = dv.parse_dividends(html)
    assert all("refund" not in r["combination"].lower() for r in rows)
    assert len(rows) == 11


def test_dividends_stay_per_ten_dollars(html):
    """The number stored should be the number on the ticket. Normalising here
    would put a derived value in the ingest layer, where two callers could
    disagree about the divisor."""
    rows = dv.parse_dividends(html)
    win = next(r for r in rows if r["pool"] == "WIN")
    assert win["dividend_per_10"] == 382.50


def test_a_thousands_separator_does_not_truncate_the_amount(html):
    """"34,829.00" read as 34.0 is a plausible dividend and a wrong one."""
    rows = dv.parse_dividends(html)
    tce = next(r for r in rows if r["pool"] == "TCE")
    assert tce["dividend_per_10"] == 34829.00


def test_a_page_with_no_dividend_table_raises(html):
    """An empty list and a misread table look identical downstream."""
    with pytest.raises(dv.DividendsError, match="no dividend table"):
        dv.parse_dividends("<html><body><p>nothing here</p></body></html>")


def test_a_table_with_no_win_row_raises(html):
    """Every race pays a WIN dividend. Its absence means the pool labels are
    being read wrongly, not that the race did not pay."""
    broken = html.replace("<td>WIN</td>", "<td>WYN</td>")
    with pytest.raises(dv.DividendsError, match="no WIN dividend"):
        dv.parse_dividends(broken, source="2026-07-15 HV R4")


def test_the_error_names_the_race_it_could_not_read(html):
    """A failure that does not say which meeting it was on cannot be acted
    on."""
    with pytest.raises(dv.DividendsError, match=r"2026-07-15 HV R4"):
        dv.parse_dividends("<html></html>", source="2026-07-15 HV R4")


def test_a_pool_label_the_table_does_not_use_is_not_invented(html):
    rows = dv.parse_dividends(html)
    assert {r["pool"] for r in rows} == {
        "WIN", "PLACE", "QIN", "QPL", "TRIO", "TCE", "F4"}

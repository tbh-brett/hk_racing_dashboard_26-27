"""ingest/results — the results page, and the guard against a silent shift."""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import results

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_header_reads_conditions():
    info = results.parse_race_header(_load("results_race.html"))
    assert info["distance"] == 1650
    assert info["race_class"] == "5"
    assert info["going"] == "G"
    assert info["course"] == "C"
    assert info["surface"] == "Turf"


def test_awt_is_its_own_surface():
    """Never pooled with Sha Tin turf -- the v4 builder did and it corrupted
    every par time that averaged the two."""
    html = _load("results_race.html").replace('Course : "C"',
                                              "Course : ALL WEATHER TRACK")
    info = results.parse_race_header(html)
    assert info["surface"] == "AWT" and info["course"] == "AWT"


def test_runners_parse_into_the_expected_fields():
    rows = results.parse_results_table(_load("results_race.html"))
    assert len(rows) == 3
    first = rows[0]
    assert first["place"] == "1"
    assert first["horse_no"] == "3"
    assert first["horse_name"] == "FASHION LEGEND"     # code stripped
    assert first["jockey"] == "J Moreira"
    assert first["finish_time"] == "1:40.05"
    assert first["lbw"] == "---"                        # the winner


def test_horse_code_is_stripped_from_the_name():
    rows = results.parse_results_table(_load("results_race.html"))
    assert all("(" not in r["horse_name"] for r in rows)


def test_a_column_shift_is_caught_rather_than_stored():
    """The bug that ran undetected for 87 meetings in parse_corunning: a shifted
    parse produces structurally valid rows of nonsense. Validation is what turns
    that into a visible failure."""
    with pytest.raises(results.ResultsError, match="misaligned|holds numbers"):
        results.parse_results_table(_load("results_shifted.html"),
                                    source="test race")


def test_a_missing_table_raises_naming_the_source():
    with pytest.raises(results.ResultsError, match="race 4"):
        results.parse_results_table("<html><body>nothing</body></html>",
                                    source="race 4")


def test_ingest_returns_only_scraped_fact():
    """The old scraper wrote pace labels and going adjustments into the same
    JSON, putting derived values in the ingest layer where they could drift from
    the ones derive/pace.py computes."""
    rows = results.parse_results_table(_load("results_race.html"))
    derived = {"pace_label", "actual_dev", "actual_going_adj_s", "et_figure",
               "pace_style", "sarr"}
    assert not derived & set(rows[0])


def test_one_column_wrong_in_every_row_is_a_layout_change_not_a_quirk():
    """The two-field rule alone let the single catastrophic case through.

    Swap the Trainer and Act. Wt. headers and exactly one shape breaks:
    `actual_weight` holds "D Eustace" in every row. Trainer has no shape to
    break, because a trainer is a name and so is a horse — which is why the
    validator cannot rely on catching two at once. One column wrong in EVERY
    row is not a quirk; it is the wrong column.
    """
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "results_race.html").read_text(
        encoding="utf-8")
    shifted = html.replace(
        "<th>Trainer</th>\n  <th>Act. Wt.</th>",
        "<th>Act. Wt.</th>\n  <th>Trainer</th>")
    assert shifted != html, "fixture header changed; update this test"
    with pytest.raises(results.ResultsError, match="in every row"):
        results.parse_results_table(shifted, source="2026-07-15 HV R1")

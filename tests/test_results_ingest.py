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


# ─── sectionals moved out of the results page ─────────────────────────────────

def _sectional_fixture() -> str:
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / "sectional_race.html"
            ).read_text(encoding="utf-8")


def test_sectionals_come_off_the_dedicated_page():
    """They used to be a table inside the results page and `parse_sectional_
    table` read it. HKJC stopped putting them there: 151 of 151 runners had
    section times on 2026-06-27 and 152 of 153 on 2026-07-12, then 0 of 107 on
    2026-07-15 and 0 of 120 on 2026-09-06. Nothing raised — the header the
    parser looks for was simply not in the page, so it returned {} and every
    meeting since mid-July has had no sectionals at all.
    """
    from hkrd.ingest import results

    got = results.parse_sectional_page(_sectional_fixture())
    assert len(got) == 14
    # Keyed by SADDLECLOTH number, not finishing order: the winner of
    # 2026-09-06 ST R1 was number 13.
    assert got["13"]["section_times"] == "24.13; 22.33; 22.13"


def test_a_section_time_is_the_time_not_the_margin():
    """The cell reads "7 3 22.33 11.15 11.18" — position, margin behind the
    leader, the section time, then the 100m splits inside it. Reading it
    positionally would take the margin, and margins are lengths (`2-3/4`, `N`,
    `SH`) that sometimes look like small numbers."""
    from hkrd.ingest import results

    got = results.parse_sectional_page(_sectional_fixture())
    for entry in got.values():
        for part in entry["section_times"].split(";"):
            # A section of a Hong Kong race is 20-25 seconds. A margin is not.
            assert 15.0 < float(part) < 40.0


def test_the_winners_sections_sum_to_the_winning_time():
    """The check that says the right column was read. 2026-09-06 ST R1 was won
    in 1:08.59 and its winner's three sections are 24.13, 22.33 and 22.13."""
    from hkrd.ingest import results

    got = results.parse_sectional_page(_sectional_fixture())
    total = sum(float(p) for p in got["13"]["section_times"].split(";"))
    assert total == pytest.approx(68.59, abs=0.01)


def test_a_page_with_no_sectional_table_is_empty_not_an_error():
    """HKJC serves the site chrome for a race past the end of the card. The
    caller walks races, so that has to be an empty answer."""
    from hkrd.ingest import results

    assert results.parse_sectional_page("<html><body>nothing</body></html>") == {}


def test_the_results_page_is_still_preferred_when_it_has_them(monkeypatch):
    """One extra request per race is worth paying only where it is needed. A
    meeting whose results page still carries the table must not fetch twice."""
    from hkrd.ingest import results

    monkeypatch.setattr(results, "parse_results_table",
                        lambda html, source=None: [{"horse_no": "1"}])
    monkeypatch.setattr(results, "parse_race_header", lambda html: {})
    monkeypatch.setattr(results, "fetch_html", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(results, "parse_sectional_table",
                        lambda html: {"1": {"section_times": "24.00; 22.00"}})

    def must_not_run(*a, **k):
        raise AssertionError("fetched the sectional page when it was not needed")

    monkeypatch.setattr(results, "fetch_sectionals", must_not_run)
    got = results.fetch_race("2026-06-27", "ST", 1)
    assert got["runners"][0]["section_times"] == "24.00; 22.00"


# ─── the stewards' incident report ────────────────────────────────────────────

def _results_fixture() -> str:
    """A real 2026-09-06 ST race 1 page, which carries the incident table.
     beside it predates that table and is kept as the
    older shape."""
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / "results_incident.html"
            ).read_text(encoding="utf-8")


def test_the_incident_report_is_read_off_the_results_page():
    """`runner_comments` has carried a 'incident' source since the beginning and
    `query/race` PREFERS it over the objective comments-on-running text — but
    the only thing that ever wrote one was the one-off legacy import.

    So a live meeting had only the corunning endpoint, which for 2026-09-06
    answered "No Comments on Running information for this horse." for all 119
    runners while the incident report on the same page read "Approaching the
    900 Metres, when racing keenly, was steadied when crowded...". Every tag in
    `runner_tags` derives from this text, so the card had none.
    """
    from hkrd.ingest import results

    got = results.parse_incident_report(_results_fixture())
    assert got, "the fixture carries an incident table"
    assert all(r["horse_no"].isdigit() and r["comment"] for r in got)


def test_a_page_with_no_incident_table_is_empty_not_an_error():
    """A race can genuinely have no report, and the caller walks races."""
    from hkrd.ingest import results

    assert results.parse_incident_report("<html><body>none</body></html>") == []

def test_the_draw_column_is_read_from_the_results_page():
    """HKJC's header is "Dr.", not "Draw". With only the long alias the column
    never mapped, and the draw came from the racecard alone -- which HKJC stops
    serving for older meetings, so a backfilled season arrived with no draw at
    all and the draw term quietly switched off for it."""
    html = """
    <table>
      <tr><th>Pla.</th><th>Horse No.</th><th>Horse</th><th>Jockey</th>
          <th>Trainer</th><th>Act. Wt.</th><th>Declar. Horse Wt.</th>
          <th>Dr.</th><th>LBW</th><th>Running Position</th>
          <th>Finish Time</th><th>Win Odds</th></tr>
      <tr><td>1</td><td>5</td><td>AMAZING FUN (H442)</td><td>Z Purton</td>
          <td>C H Yip</td><td>128</td><td>1138</td><td>9</td><td>---</td>
          <td>9 9 1</td><td>0:56.22</td><td>15</td></tr>
    </table>"""
    rows = results.parse_results_table(html, source="test")
    assert rows[0]["draw"] == "9"
    assert rows[0]["horse_name"] == "AMAZING FUN"
    # and the long spelling must keep working
    assert results.parse_results_table(
        html.replace("<th>Dr.</th>", "<th>Draw</th>"), source="test")[0]["draw"] == "9"


def test_a_void_race_is_not_reported_as_a_broken_scraper():
    """HKJC voids a race outright -- a false start, a failed barrier -- and
    serves the card with every Pla. reading VOID and weight, LBW and time all
    "---". 2025-11-15 ST race 8 is one, and the shape check called it
    "columns look misaligned", which is the alarm that means HKJC CHANGED ITS
    HTML. Those need different answers from a human, and a message that cannot
    tell them apart trains you to dismiss the one that matters."""
    html = """
    <table>
      <tr><th>Pla.</th><th>Horse No.</th><th>Horse</th><th>Jockey</th>
          <th>Trainer</th><th>Act. Wt.</th><th>Declar. Horse Wt.</th>
          <th>Dr.</th><th>LBW</th><th>Finish Time</th></tr>
      <tr><td>VOID</td><td>1</td><td>ENDUED (K033)</td><td>H Bowman</td>
          <td>J Size</td><td>---</td><td>---</td><td>12</td><td>---</td>
          <td>---</td></tr>
      <tr><td>VOID</td><td>2</td><td>EMBLAZON (K122)</td><td>C L Chau</td>
          <td>W K Mo</td><td>---</td><td>---</td><td>9</td><td>---</td>
          <td>---</td></tr>
    </table>"""
    with pytest.raises(results.VoidRace) as e:
        results.parse_results_table(html, source="test")
    assert "VOID" in str(e.value)
    # still a ResultsError, so a caller watching for a scraper break sees it
    assert isinstance(e.value, results.ResultsError)


def test_a_genuinely_misaligned_table_is_still_reported_as_one():
    """The void check must not swallow the fault it sits in front of."""
    html = """
    <table>
      <tr><th>Pla.</th><th>Horse No.</th><th>Horse</th><th>Jockey</th>
          <th>Trainer</th><th>Act. Wt.</th><th>Declar. Horse Wt.</th>
          <th>Dr.</th><th>LBW</th><th>Finish Time</th></tr>
      <tr><td>1</td><td>5</td><td>REAL HORSE (H442)</td><td>Z Purton</td>
          <td>C H Yip</td><td>128</td><td>1138</td><td>9</td><td>---</td>
          <td>---</td></tr>
      <tr><td>2</td><td>6</td><td>OTHER HORSE (J312)</td><td>M L Yeung</td>
          <td>W Y So</td><td>128</td><td>1170</td><td>5</td><td>3/4</td>
          <td>---</td></tr>
    </table>"""
    with pytest.raises(results.ResultsError) as e:
        results.parse_results_table(html, source="test")
    assert not isinstance(e.value, results.VoidRace)

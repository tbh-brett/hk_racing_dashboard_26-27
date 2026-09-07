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
from bs4 import BeautifulSoup

from hkrd.ingest import racecard as rc
from hkrd.ingest._client import NotFound

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


def _with_intl_rating_column(html: str) -> str:
    """HKJC's June 2026 layout: an "Int'l Rtg." column immediately left of
    "Rtg.", holding "-" for every domestic horse."""
    soup = BeautifulSoup(html, "html.parser")
    table = rc._find_table(soup)
    header = rc._header_row(table)
    cells = header.find_all(["th", "td"])
    idx = next(i for i, c in enumerate(cells)
               if rc._clean(c.get_text()) == "Rtg.")
    th = soup.new_tag("th")
    th.string = "Int'l Rtg."
    cells[idx].insert_before(th)
    for tr in table.find_all("tr"):
        if tr is header:
            continue
        tds = tr.find_all(["td", "th"])
        if len(tds) <= idx:
            continue
        td = soup.new_tag("td")
        td.string = "-"
        tds[idx].insert_before(td)
    return str(soup)


def test_an_international_rating_column_does_not_capture_the_rating(html):
    """HKJC added "Int'l Rtg." left of "Rtg." in June 2026. Aliases match as
    substrings and the first field to match a column claims it, so "int'l rtg."
    contains "rtg." and `rating` read the international column -- "-" for every
    domestic horse. Nine meetings ingested with rating NULL and nothing raised,
    because _SHAPES lets a rating be "-": a wrong column that validates."""
    rows = {r["horse_no"]: r
            for r in rc.parse_racecard(_with_intl_rating_column(html), 4)}
    assert rows[1]["rating"] == 72
    assert rows[6]["rating"] == 68


@pytest.mark.parametrize("label", ["Int'l Rtg.", "Int’l Rtg.", "Intl Rtg."])
def test_the_decoy_is_claimed_however_its_apostrophe_is_written(label):
    """The straight apostrophe is what HKJC serves today. A curly one is one
    CMS change away, and the failure it would cause is silent."""
    cols = rc._map_columns(
        ["Horse No.", "Horse", "Draw", "Trainer", label, "Rtg.", "Rtg.+/-"],
        "probe")
    assert cols["int_rating"] == 4
    assert cols["rating"] == 5
    assert cols["rating_change"] == 6


def test_a_card_with_no_international_column_still_finds_the_rating(html):
    """The fix must not depend on the decoy being present."""
    rows = {r["horse_no"]: r for r in rc.parse_racecard(html, 4)}
    assert rows[1]["rating"] == 72


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


# ── the end of the card, and the shapes it must not be confused with ─────────
#
# HKJC does not 404 a race card page that does not exist: it answers 200 with
# an error panel, and serves the same body for a race past the end of the
# card, a wrong venue and a date with no meeting. Read as a card that page has
# no distance and no venue, so it used to come back as "race header
# unreadable" — which made every meeting shorter than the walk limit report a
# failed race, and logged the card source with ok=0 on a scrape that had just
# stored a full field.

NO_INFORMATION = (FIXTURES / "racecard_no_information.html").read_text(
    encoding="utf-8")
CARD_HTML = (FIXTURES / "racecard.html").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, status, text="", url=""):
        self.status_code, self.text, self.url = status, text, url


class _Cards:
    """A card `races` long. Anything past it gets HKJC's error panel."""

    def __init__(self, races=10, broken: int | None = None):
        self.races, self.broken = races, broken
        self.asked: list[int] = []

    def get(self, url, params=None, timeout=None):
        no = int((params or {}).get("RaceNo") or 0)
        self.asked.append(no)
        if no > self.races:
            return _Resp(200, NO_INFORMATION, url=url)
        if no == self.broken:
            # A page that is neither a card nor the error panel.
            return _Resp(200, "<html><body><p>nothing</p></body></html>",
                         url=url)
        return _Resp(200, CARD_HTML, url=url)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr("hkrd.ingest._client.MIN_INTERVAL", 0.0)


def test_the_page_served_past_the_last_race_is_not_a_parse_failure():
    """"There is no race 11" and "race 11 would not parse" are different
    answers with different fixes. NotFound is the one every walk already
    reads as the end of the card."""
    with pytest.raises(NotFound):
        rc.fetch_race("2026-09-06", "ST", 11, session=_Cards(10))


def test_a_ten_race_card_ends_without_naming_a_failed_race():
    """The bug: a warning for R11 on a card that has ten races. Every 10-race
    meeting hit it; only one at the walk limit escaped."""
    card = rc.fetch_meeting("2026-09-06", "ST", max_races=11,
                            session=_Cards(10))
    assert len(card["races"]) == 10
    assert card["errors"] == []


def test_the_walk_stops_asking_once_the_card_has_ended():
    """It must not carry on to race 11 after race 11 said there is none."""
    session = _Cards(8)
    rc.fetch_meeting("2026-09-06", "ST", max_races=11, session=session)
    assert session.asked == list(range(1, 10))


def test_a_race_that_will_not_parse_is_still_named():
    """The half that must keep failing loudly. A header this parser cannot
    read is a layout change, not a short meeting."""
    card = rc.fetch_meeting("2026-09-06", "ST", max_races=11,
                            session=_Cards(10, broken=4))
    assert any("R4" in e and "unreadable" in e for e in card["errors"])
    assert len(card["races"]) == 9


def test_no_card_at_all_raises_rather_than_reading_as_a_short_one():
    """`{"races": []}` cannot say whether the card is unpublished or published
    and empty, and the caller has to log those differently."""
    with pytest.raises(NotFound):
        rc.fetch_meeting("2026-09-08", "ST", session=_Cards(0))


def test_a_real_card_page_is_not_mistaken_for_the_error_panel(html):
    """The match is on the error container AND its text. Too tolerant a check
    truncates a real meeting, which is the failure NotFound exists to stop."""
    assert rc._NO_INFORMATION.search(html) is None
    assert rc._NO_INFORMATION.search(NO_INFORMATION) is not None

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


# ── fetching: the 1..N walk over ?raceno= ────────────────────────────────────

class _FakeResponse:
    def __init__(self, status: int, text: str = "", url: str = ""):
        self.status_code, self.text, self.url = status, text, url


class _FakeSession:
    """Serves a card of `races` races, 404 beyond it — as HKJC does."""

    def __init__(self, races: int, body: str, broken: set[int] | None = None):
        self.races, self.body, self.broken = races, body, broken or set()
        self.calls: list[int] = []

    def get(self, url, params=None, timeout=None):
        no = int(params["raceno"])
        self.calls.append(no)
        if no > self.races:
            return _FakeResponse(404, url=f"{url}?raceno={no}")
        if no in self.broken:
            return _FakeResponse(200, "<html><body><p>redesigned</p></body></html>")
        return _FakeResponse(200, self.body)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr("hkrd.ingest._client.MIN_INTERVAL", 0.0)


def test_fetch_meeting_walks_every_race_and_stops_at_the_end_of_the_card():
    s = _FakeSession(races=9, body=_load("corunning_4col.html"))
    out = corunning.fetch_meeting("2026-07-15", session=s)
    assert sorted(out) == list(range(1, 10))
    assert s.calls == list(range(1, 11))      # one 404 to learn the card ended
    assert all(len(v) == 3 for v in out.values())


def test_a_layout_change_raises_rather_than_being_skipped():
    """A missing race and a broken parser are different facts. The old scraper
    collapsed them, which is how a silent failure survived 87 meetings."""
    s = _FakeSession(races=4, body=_load("corunning_4col.html"), broken={3})
    with pytest.raises(corunning.CoRunningError, match="raceno=3"):
        corunning.fetch_meeting("2026-07-15", session=s)


def test_fetch_single_race():
    s = _FakeSession(races=3, body=_load("corunning_4col.html"))
    rows = corunning.fetch("2026-07-15", 2, session=s)
    assert rows[0]["horse_name"] == "FASHION LEGEND"


# ── shaping for the store ────────────────────────────────────────────────────

def test_comment_rows_are_tagged_as_a_distinct_source():
    """Corunning and the stewards' incident report are two accounts of one
    race. Both are kept; the form guide shows them side by side."""
    rows = corunning.parse_corunning(_load("corunning_4col.html"))
    shaped = corunning.comment_rows("2026-07-15", 1, rows)
    assert len(shaped) == 3
    assert {r["source"] for r in shaped} == {"corunning"}
    assert all(r["comment_text"] for r in shaped)


def test_lane_tags_are_namespaced_and_certain():
    """Confidence is 1.0 because nothing is inferred -- the comment says the
    horse raced three wide, so the tag records exactly that."""
    rows = corunning.parse_corunning(_load("corunning_4col.html"))
    tags = corunning.lane_tag_rows("2026-07-15", 1, rows)
    names = {t["tag"] for t in tags}
    assert "lane:rail" in names and "lane:three_wide" in names
    assert all(t["tag"].startswith("lane:") for t in tags)
    assert all(t["confidence"] == 1.0 for t in tags)


def test_rows_without_a_horse_number_are_skipped_not_stored_broken():
    rows = [{"horse_no": None, "horse_name": "X", "comment": "Raced wide."}]
    assert corunning.comment_rows("2026-07-15", 1, rows) == []
    assert corunning.lane_tag_rows("2026-07-15", 1, rows) == []

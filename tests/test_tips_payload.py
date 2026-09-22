"""The tips payload validator — one test per way the contract rejects a payload.

Every case starts from `fixtures/tips_payload.json` (a copy of the handover's
`payload.example.json`) and breaks exactly one thing about it, so each test
names the single rule it is about. The rules are SPEC.md §3; the few extra
ones are said where they appear.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hkrd.ingest import tips_payload as tp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads((FIXTURES / "tips_payload.json").read_text(encoding="utf-8"))


@pytest.fixture()
def body(fixture) -> dict:
    return copy.deepcopy(fixture)


def rejected(body: dict) -> str:
    with pytest.raises(tp.PayloadError) as exc:
        tp.parse(body)
    return str(exc.value)


# ── the fixture itself ───────────────────────────────────────────────────────

def test_the_fixture_is_a_valid_payload(body):
    p = tp.parse(body)
    assert p.race_date == "2026-09-23"
    assert (len(p.quotes), len(p.selections), len(p.quarantine)) == (7, 5, 2)


def test_underscore_keys_are_provenance_and_ignored(body):
    """The fixture carries `_fixture_note`; the contract says a leading
    underscore is the fixture's business and not the validator's."""
    assert "_fixture_note" in body
    tp.parse(body)


def test_rows_inherit_the_meeting_and_the_generation_time(body):
    """fetched_at is the payload's own clock, never the server's — so the
    same payload pushed twice is the same rows."""
    q = tp.parse(body).quotes[0]
    assert q["race_date"] == "2026-09-23"
    assert q["fetched_at"] == body["generated_at"]


# ── SPEC §3: rejects the whole payload ───────────────────────────────────────

@pytest.mark.parametrize("version", [2, 0, "1", None, True])
def test_an_unknown_version_is_a_hard_reject(body, version):
    body["payload_version"] = version
    assert "payload_version must be 1" in rejected(body)


@pytest.mark.parametrize("date", ["23/09/2026", "2026-02-30", "2026-9-23",
                                  "", None, 20260923])
def test_race_date_must_parse(body, date):
    body["race_date"] = date
    assert "race_date must be YYYY-MM-DD" in rejected(body)


@pytest.mark.parametrize("key", ["source", "role", "quote", "url",
                                 "extracted_by"])
def test_every_quote_has_its_required_fields(body, key):
    del body["quotes"][2][key]
    assert f"quotes[2]: missing {key}" in rejected(body)


@pytest.mark.parametrize("key", ["source", "role", "quote", "url",
                                 "extracted_by"])
def test_a_blank_required_quote_field_is_missing_too(body, key):
    body["quotes"][2][key] = "   "
    assert f"quotes[2]: {key} must be a non-empty string" in rejected(body)


@pytest.mark.parametrize("key", ["source", "tipster", "race_no", "horse_no"])
def test_every_selection_has_its_required_fields(body, key):
    del body["selections"][1][key]
    assert f"selections[1]: missing {key}" in rejected(body)


@pytest.mark.parametrize("rank", [0, 9, -1, 1.5, "1"])
def test_pick_rank_is_one_to_eight(body, rank):
    body["selections"][0]["pick_rank"] = rank
    assert "selections[0]: pick_rank must be 1..8" in rejected(body)


def test_pick_rank_may_be_absent(body):
    body["selections"][0]["pick_rank"] = None
    tp.parse(body)


@pytest.mark.parametrize("conf", [1.2, -0.1, "0.9", True])
def test_confidence_is_zero_to_one(body, conf):
    body["quotes"][0]["confidence"] = conf
    assert "quotes[0]: confidence must be 0..1" in rejected(body)


@pytest.mark.parametrize("conf", [0, 1, 0.0, None])
def test_confidence_bounds_are_inclusive_and_it_may_be_absent(body, conf):
    body["quotes"][0]["confidence"] = conf
    tp.parse(body)


# ── rules the spec implies and does not list ─────────────────────────────────

def test_generated_at_is_required(body):
    """It becomes every row's fetched_at, which is NOT NULL — and reading the
    server's clock instead would make a re-push a different set of rows."""
    del body["generated_at"]
    assert "generated_at must be an ISO-8601 timestamp" in rejected(body)


def test_an_unknown_top_level_key_is_a_typo_until_proven_otherwise(body):
    body["selection"] = body.pop("selections")
    assert "unknown top-level key 'selection'" in rejected(body)


@pytest.mark.parametrize("key, value", [
    ("role", "owner"), ("caption_kind", "auto"), ("caption_kind", "ASR"),
    ("stance", "bullish")])
def test_the_vocabularies_the_screen_draws_are_closed(body, key, value):
    """A caption_kind of "ASR" would silently drop the marker that tells the
    eye the names in a quote may be wrong."""
    body["quotes"][1][key] = value
    assert f"quotes[1]: {key} {value!r} is not one of" in rejected(body)


def test_a_pick_heard_through_speech_to_text_is_accepted_and_says_so(body):
    """SPEC §6 said never; Brett, 2026-09-22, said record them. It travels
    with the row so the card can mark how the pick was obtained."""
    body["selections"][0]["caption_kind"] = "asr"
    assert tp.parse(body).selections[0]["caption_kind"] == "asr"


def test_a_selection_caption_kind_is_manual_or_asr(body):
    body["selections"][0]["caption_kind"] = "auto"
    assert "selections[0]: caption_kind 'auto' is not one of" in rejected(body)


# ── what a payload is the whole answer for ───────────────────────────────────

def test_sources_default_to_the_ones_its_rows_name(body):
    """So a source that failed to harvest, and sent nothing, is untouched."""
    assert tp.parse(body).sources == ["bryan", "factcheck", "oncc",
                                      "rtw_interview", "threads"]


def test_a_declared_source_may_have_no_rows(body):
    """How an extractor says "on.cc was read, and tipped nothing"."""
    body["sources"] = ["bryan", "factcheck", "oncc", "rtw_interview",
                       "threads", "stheadline"]
    assert "stheadline" in tp.parse(body).sources


def test_every_row_source_must_be_declared(body):
    body["sources"] = ["factcheck"]
    assert "rows name sources not in `sources`" in rejected(body)


@pytest.mark.parametrize("value", ["factcheck", [""], [1], {}])
def test_sources_is_a_list_of_names(body, value):
    body["sources"] = value
    assert "sources must be a list of source names" in rejected(body)


@pytest.mark.parametrize("value", ["4", 4.0, True])
def test_numbers_are_json_integers_not_strings(body, value):
    body["quotes"][0]["race_no"] = value
    assert "quotes[0]: race_no must be an integer or null" in rejected(body)


def test_a_row_from_another_meeting_is_refused(body):
    body["selections"][0]["race_date"] = "2026-09-20"
    assert "is not the payload's 2026-09-23" in rejected(body)


def test_a_quarantine_reason_must_be_one_the_ops_page_knows(body):
    body["quarantine"][0]["reason"] = "dunno"
    assert "quarantine[0]: reason 'dunno' is not one of" in rejected(body)


def test_every_fault_is_reported_at_once(body):
    """One round trip to fix a broken extractor, not one per fault."""
    del body["quotes"][0]["url"]
    body["selections"][3]["pick_rank"] = 11
    with pytest.raises(tp.PayloadError) as exc:
        tp.parse(body)
    assert len(exc.value.problems) == 2


def test_a_payload_that_is_not_an_object_is_refused():
    assert "must be a JSON object" in rejected([])  # type: ignore[arg-type]

"""derive/names — does a published name belong to the horse at that number?

The names are race 1 of 2026-09-23 at Happy Valley, read off the real card in
`fixtures/racecard_zh.html`, plus the one abbreviation 賽馬Fact Check's own
subtitles use for one of them (馬靈, in `fixtures/tips_payload.json`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.derive import names
from hkrd.ingest import racecard_zh

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def race1() -> dict[int, str]:
    html = (FIXTURES / "racecard_zh.html").read_text(encoding="utf-8")
    return {r["horse_no"]: r["name_zh"]
            for r in racecard_zh.parse_racecard_zh(html, 1)}


def others(race1: dict[int, str], horse_no: int) -> list[str]:
    return [n for h, n in race1.items() if h != horse_no]


# ── similarity ───────────────────────────────────────────────────────────────

def test_the_same_name_is_a_perfect_match():
    assert names.similarity("神駒馬靈", "神駒馬靈") == 1.0
    assert names.similarity("Sky Cap", "SKY CAP") == 1.0


def test_an_abbreviation_inside_the_name_matches():
    """How the subtitles themselves say SOARING BRONCO."""
    assert names.similarity("馬靈", "神駒馬靈") == 0.9


def test_one_wrong_character_in_three_or_four_still_matches():
    assert names.similarity("神駒馬零", "神駒馬靈") >= names.MATCH_AT
    assert names.similarity("堅多褔", "堅多福") >= names.MATCH_AT


def test_one_character_is_never_enough():
    """HK horse names share single characters — 星, 駒, 勝 — far too often."""
    assert names.similarity("馬", "神駒馬靈") < names.MATCH_AT
    assert names.similarity("天星", "天馬") < names.MATCH_AT


def test_a_short_english_fragment_is_not_a_containment_match():
    assert names.similarity("SKY", "SKY CAP") < 0.9


# ── verdict ──────────────────────────────────────────────────────────────────

def test_a_name_that_fits_the_numbered_horse_passes(race1):
    assert names.verdict("馬靈", race1[3], others(race1, 3),
                         complete=True) is None


def test_a_name_that_fits_another_horse_is_a_mismatch(race1):
    assert names.verdict("馬靈", race1[4], others(race1, 4),
                         complete=True) == "name_mismatch"


def test_a_name_that_fits_nobody_on_a_named_card_is_unknown(race1):
    """良駒好友: the fixture's illustrative name, and no horse in race 1."""
    assert names.verdict("良駒好友", race1[4], others(race1, 4),
                         complete=True) == "name_unknown"


def test_a_name_equally_close_to_two_horses_is_unknown():
    assert names.verdict("金駒", "金駒永騰", ["金駒勇士"],
                         complete=True) == "name_unknown"


def test_without_names_there_is_nothing_to_check():
    assert names.verdict("馬靈", None, [None, None], complete=False) is None


def test_a_known_horse_whose_name_does_not_fit_fails_on_a_part_named_card(
        race1):
    assert names.verdict("良駒好友", race1[4], [None, None],
                         complete=False) == "name_mismatch"


# ── verdict_heard: the number was spoken, the name is speech-to-text ─────────

def test_a_heard_name_need_only_point_at_its_horse_more_than_the_rest(race1):
    """紅轉占士 for 紅磚戰士: one character in four, and that one belongs
    to no other horse in the race."""
    assert names.similarity("紅轉占士", race1[4]) < names.MATCH_AT
    assert names.verdict_heard("紅轉占士", race1[4], others(race1, 4)) is None


def test_a_heard_name_pointing_at_another_horse_is_a_mismatch(race1):
    assert names.verdict_heard("馬靈", race1[4],
                               others(race1, 4)) == "name_mismatch"


def test_one_shared_character_with_another_horse_is_enough_to_refuse(race1):
    """良駒好友 shares 駒 with 神駒馬靈 (#3) and nothing with #4. Weak, but
    it points the wrong way, and a heard pick has nothing else to go on."""
    assert names.verdict_heard("良駒好友", race1[4],
                               others(race1, 4)) == "name_mismatch"


def test_a_heard_name_sharing_nothing_with_anyone_is_unknown(race1):
    assert names.verdict_heard("良好友", race1[4],
                               others(race1, 4)) == "name_unknown"


def test_with_no_names_known_the_spoken_number_decides():
    assert names.verdict_heard("紅外嚇", None, [None, None]) is None

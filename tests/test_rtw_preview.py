"""tools/extract_rtw_preview — the pundit's selections out of a race preview.

`rtw_preview_20260923_excerpt.json` is the real speech-to-text of the closing
95 seconds of races 3 and 6 of the 23 Sep Happy Valley preview, where Paul
Lally reads his picks out as numbers; `roster_20260923.json` is that night's
card as the dashboard served it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import extract_rtw_preview as pv                            # noqa: E402
from _card import Card                                      # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def card() -> Card:
    return Card(load("roster_20260923.json"))


# ── the numbers, as speech-to-text writes them ───────────────────────────────

@pytest.mark.parametrize("said, tokens", [
    ("I 113 four and six.", ["113", "four", "six"]),
    ("So 493 and one.", ["493", "one"]),
    ("Anyway, 93 8 and 12.", ["93", "8", "12"]),
    ("So we ended up with 82 11 and five.", ["82", "11", "five"]),
    ("the likely leader in the race with the 10 pound claim 9683.", ["9683"]),
    ("He's drawn 11 and should lead.", []),
])
def test_only_the_numbers_a_sentence_ends_with_are_selections(said, tokens):
    assert pv.numbers_said(said) == tokens


def test_run_together_numbers_split_against_the_field():
    """「113」 in a field of twelve can only be 11 then 3. 「14612」 in a
    field of fourteen reads three ways — which is why the horse named on top
    has to settle it (race 6 below)."""
    assert pv.splits(["113", "four", "six"], set(range(1, 13))) == \
        [[11, 3, 4, 6]]
    assert sorted(map(tuple, pv.splits(["14612"], set(range(1, 15))))) == \
        [(1, 4, 6, 12), (14, 6, 1, 2), (14, 6, 12)]


def test_a_number_can_appear_only_once():
    assert pv.splits(["1", "1", "2"], set(range(1, 15))) == []


# ── the picks, out of the real chapters ──────────────────────────────────────

@pytest.fixture(scope="module")
def picks(card) -> dict[int, list[dict]]:
    _quotes, picks, held = pv.preview(
        load("rtw_preview_20260923_excerpt.json"), card, "x")
    assert held == []
    out: dict[int, list[dict]] = {}
    for p in picks:
        out.setdefault(p["race_no"], []).append(p)
    return out


def test_race_6_is_the_numbers_read_out_in_order(picks):
    """「14612 … Jumbo blessing for Paul」: four picks, JUMBO BLESSING on
    top, which is how the ambiguity with 14-6-12 is settled."""
    assert [p["horse_no"] for p in picks[6]] == [1, 4, 6, 12]
    assert picks[6][0]["pick_rank"] == 1


def test_a_horse_name_ending_in_a_number_is_not_a_selection(picks):
    """「Uh Rainbow 7 2483.」 — the 7 belongs to RAINBOW SEVEN; the picks
    are 2-4-8-3, and 「He goes on top shooting to top」 confirms #2."""
    assert [p["horse_no"] for p in picks[3]] == [2, 4, 8, 3]


def test_the_selector_is_named(picks):
    assert {p["tipster"] for ps in picks.values() for p in ps} == \
        {"Paul Lally"}


def test_only_the_closing_words_are_kept(card):
    quotes, _picks, _held = pv.preview(
        load("rtw_preview_20260923_excerpt.json"), card, "x")
    assert quotes and all(q["race_no"] in (3, 6) for q in quotes)
    assert all(len(q["quote"]) < 400 for q in quotes)
    assert all(q["role"] == "presenter" for q in quotes)

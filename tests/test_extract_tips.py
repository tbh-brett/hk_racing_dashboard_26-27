"""tools/extract_tips — quotes and picks out of a real 賽馬Fact Check preview.

Both fixtures are real: `factcheck_20260923_excerpt.json` is the harvested
9.23 preview cut to two stretches (the subtitles from 1:05 to 2:11, and the
speech-to-text tail where 譚朗蔚 gives and repeats her race-2 picks), and
`roster_20260923.json` is the card the dashboard served for that meeting.

The extractor lives in tools/ and runs on the PC, but what it sends has to
pass the dashboard's contract, so that is tested here too.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import extract_tips as ex                                   # noqa: E402
from hkrd.ingest import tips_payload                        # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-23"
AT = "2026-09-22T00:00:00Z"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def rec() -> dict:
    return load("factcheck_20260923_excerpt.json")


@pytest.fixture(scope="module")
def roster() -> dict:
    return load("roster_20260923.json")


@pytest.fixture(scope="module")
def card(roster) -> ex.Card:
    return ex.Card(roster)


def runner(q: dict) -> tuple[int, int]:
    return q["race_no"], q["horse_no"]


# ── quotes, from the subtitles ───────────────────────────────────────────────

def test_each_horse_discussed_gets_one_quote_on_the_right_runner(rec, card):
    got = {q["horse_said"]: q for q in ex.quotes_from(rec, card, AT)}
    assert {k: runner(q) for k, q in got.items()} == {
        "天星": (7, 3),          # AKASHVANI
        "富心星": (8, 3),        # SKY CAP
        "神駒馬靈": (1, 3),      # SOARING BRONCO
    }


def test_a_list_of_names_opens_no_comment(rec, card):
    """「當中希斯嘅天星同方嘉柏嘅富心星」 introduces two horses; the comments
    start on the lines that open with each name."""
    starts = sorted(int(q["t_start"]) for q in ex.quotes_from(rec, card, AT))
    assert starts == [80, 99, 123]


def test_a_horse_mentioned_mid_line_is_part_of_another_comment(rec, card):
    """福進 is a runner (R6 #1) but 「效法廐侶福進」 is said of 富心星."""
    quotes = ex.quotes_from(rec, card, AT)
    assert "福進" not in {q["horse_said"] for q in quotes}
    sky_cap = next(q for q in quotes if q["horse_said"] == "富心星")
    assert "效法廐侶福進同舞林寶典" in sky_cap["quote"]


def test_a_pause_ends_a_comment(rec, card):
    """The subtitles stop at 2:11 and the tail starts at 6:26: nothing from
    the tail is folded into 神駒馬靈's comment."""
    q = next(q for q in ex.quotes_from(rec, card, AT)
             if q["horse_said"] == "神駒馬靈")
    assert q["quote"].endswith("演出非常準繩。")


def test_quotes_link_to_the_second_they_are_said(rec, card):
    q = next(q for q in ex.quotes_from(rec, card, AT)
             if q["horse_said"] == "天星")
    assert q["url"] == "https://www.youtube.com/watch?v=-yNB49esPsI&t=80s"
    assert q["caption_kind"] == "manual" and q["role"] == "analyst"


# ── picks, from the speech-to-text tail ──────────────────────────────────────

def test_the_tail_picks_land_on_race_2_with_the_favourite_ranked(rec, card):
    picks, held = ex.picks_from(rec, card, AT)
    assert held == []
    assert [(p["race_no"], p["horse_no"], p["pick_rank"]) for p in picks] == [
        (2, 7, 1),       # SILVER SPURS 銀刺勇士, 「最喜歡七號」
        (2, 3, None),    # LOOKING BRIGHT 路路勁
        (2, 1, None),    # FIND MY LOVE 紅愛舍
    ]
    assert {p["tipster"] for p in picks} == {"譚朗蔚"}
    assert {p["caption_kind"] for p in picks} == {"asr"}


def test_the_race_is_inferred_when_speech_to_text_drops_it(rec, card):
    """Two of four previews lost the race entirely (「場1000米」). The names
    at the heard numbers agree with one race only."""
    for s in rec["segments"]:
        s["text"] = s["text"].replace("第二場", "場")
    picks, held = ex.picks_from(rec, card, AT)
    assert held == [] and {p["race_no"] for p in picks} == {2}


def test_a_race_said_that_the_names_contradict_is_held(rec, card):
    for s in rec["segments"]:
        s["text"] = s["text"].replace("第二場", "第五場")
    picks, held = ex.picks_from(rec, card, AT)
    assert picks == []
    assert held[0]["reason"] == "unparsed"
    assert "race 5 was said, the names point to race 2" in held[0]["raw"]


def test_a_date_is_not_a_pick(card):
    heard = ex._heard("回歸7月1號黃賽田草，係9月23號禮拜")
    assert heard == {}


# ── the meeting ──────────────────────────────────────────────────────────────

def test_a_preview_published_last_season_is_not_used(rec, tmp_path):
    (tmp_path / "factcheck").mkdir()
    old = copy.deepcopy(rec)
    old["published"] = "2025-09-21T05:00:09-07:00"
    (tmp_path / "factcheck" / "old.json").write_text(
        json.dumps(old, ensure_ascii=False), encoding="utf-8")
    assert ex.transcripts_for(DATE, tmp_path) == []


def test_the_payload_it_builds_passes_the_dashboards_contract(rec, roster,
                                                            tmp_path):
    (tmp_path / "factcheck").mkdir()
    shutil.copy(FIXTURES / "factcheck_20260923_excerpt.json",
                tmp_path / "factcheck" / "-yNB49esPsI.json")
    payload = ex.extract(DATE, tmp_path, roster)
    parsed = tips_payload.parse(payload)
    assert parsed.sources == ["factcheck"]
    assert (len(parsed.quotes), len(parsed.selections)) == (3, 3)

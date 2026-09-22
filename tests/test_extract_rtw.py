"""tools/extract_rtw — Racing To Win interviews, from real harvested videos.

Fixtures, all real:
  rtw_interview_20260916_excerpt.json   Meeting 04: HKJC's index, and 2:00-4:50
                                        of the speech-to-text (David Eustace on
                                        DAZZLING FIT, whole)
  rtw_interview_20260906_excerpt.json   Meeting 01: the index, and the first 50
                                        seconds, where KA YING RISING is heard
                                        as "Ying Star"
  rtw_cards.json                        those races' runners, jockeys, trainers
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

import extract_rtw as rtw                                   # noqa: E402
import extract_tips as ex                                   # noqa: E402
from hkrd.ingest import tips_payload                        # noqa: E402
from hkrd.jobs import import_tips                           # noqa: E402
from hkrd.store import upsert                               # noqa: E402
from hkrd.store.connect import get_conn, init_db, transaction  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
AT = "2026-09-22T00:00:00Z"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def sep16() -> dict:
    return load("rtw_interview_20260916_excerpt.json")


@pytest.fixture()
def sep06() -> dict:
    return load("rtw_interview_20260906_excerpt.json")


@pytest.fixture(scope="module")
def cards() -> dict:
    return load("rtw_cards.json")


def about(quotes: list[dict], race_no: int, horse_no: int) -> list[dict]:
    return [q for q in quotes if (q["race_no"], q["horse_no"]) == (race_no,
                                                                   horse_no)]


# ── the index is the identity ────────────────────────────────────────────────

def test_the_description_index_gives_race_horse_and_speaker(sep16):
    assert rtw.index(sep16["description"]) == [
        {"race_no": 3, "speaker": "Karis Teetan", "horse_no": 6,
         "horse_name": "ROSEWOOD FLEETFOOT"},
        {"race_no": 6, "speaker": "David Eustace", "horse_no": 1,
         "horse_name": "DAZZLING FIT"},
        {"race_no": 7, "speaker": "Andrea Atzeni", "horse_no": 3,
         "horse_name": "HORSEPOWER"},
        {"race_no": 8, "speaker": "Andrea Atzeni", "horse_no": 11,
         "horse_name": "ARMOR GOLDEN EAGLE"},
    ]


def test_the_speaker_is_the_trainer_or_jockey_by_surname(sep16, cards):
    quotes, _ = rtw.interview_quotes(sep16, cards["2026-09-16"], AT)
    assert {q["role"] for q in about(quotes, 6, 1)} == {"trainer"}   # D Eustace
    assert {q["role"] for q in about(quotes, 7, 3)} == {"jockey"}    # A Atzeni


# ── only the answers are quotes ──────────────────────────────────────────────

def test_each_answer_is_a_quote_linked_to_where_it_starts(sep16, cards):
    quotes, _ = rtw.interview_quotes(sep16, cards["2026-09-16"], AT)
    eustace = about(quotes, 6, 1)
    assert [int(q["t_start"]) for q in eustace] == [141, 158, 191, 233]
    assert eustace[0]["quote"].startswith("Yeah, he's had a nice break.")
    assert eustace[0]["url"].endswith("watch?v=yNRqxbwx11Q&t=141s")
    assert {q["caption_kind"] for q in eustace} == {"asr"}


def test_the_presenters_questions_are_not_quotes(sep16, cards):
    quotes, _ = rtw.interview_quotes(sep16, cards["2026-09-16"], AT)
    assert not [q for q in quotes
                if q["quote"].startswith(("David", "Andrea", "Always nice",
                                          "He was at the mercy", "And "))]


def test_a_missed_speaker_mark_does_not_turn_a_question_into_a_quote(sep06):
    """Drop the `>>` before Zac's first answer, as speech-to-text sometimes
    does: question and answer run together, and the presenter's next line
    ("And if you rewind …") lands where an answer is due."""
    segs = copy.deepcopy(sep06["segments"])
    for s in segs:
        if s["text"].startswith(">> Yeah, it certainly does"):
            s["text"] = s["text"][3:]
    said = [" ".join(x["text"] for x in turn)
            for turn in rtw._answers(rtw._turns(segs))]
    assert len(said) == 1 and said[0].startswith(">> He's a bigger")


# ── finding each interview, or holding it ────────────────────────────────────

def test_the_first_interview_opens_the_video_when_its_name_is_misheard(
        sep06, cards):
    """KA YING RISING is "Ying Star" to the recogniser. It is first in the
    index, and the video opens with it."""
    quotes, _ = rtw.interview_quotes(sep06, cards["2026-09-06"], AT)
    ka_ying = about(quotes, 3, 1)
    assert [int(q["t_start"]) for q in ka_ying] == [9, 42]
    assert {q["confidence"] for q in ka_ying} == {rtw.OPENS_VIDEO}
    assert {q["speaker"] for q in ka_ying} == {"Zac Purton"}


def test_an_interview_not_found_is_held_not_guessed(sep06, cards):
    """The excerpt stops at 0:50; the other three interviews are not in it."""
    quotes, held = rtw.interview_quotes(sep06, cards["2026-09-06"], AT)
    assert {(q["race_no"], q["horse_no"]) for q in quotes} == {(3, 1)}
    assert [h["race_no"] for h in held] == [7, 9, 10]
    assert all("not named in any question" in h["raw"] for h in held)


# ── through the extractor and into the dashboard ─────────────────────────────

def test_the_payload_passes_the_contract_and_the_card_check(sep16, cards,
                                                            tmp_path):
    raw = tmp_path / "raw"
    (raw / "rtw").mkdir(parents=True)
    shutil.copy(FIXTURES / "rtw_interview_20260916_excerpt.json",
                raw / "rtw" / "yNRqxbwx11Q.json")
    field = cards["2026-09-16"]
    roster = {"races": [{"race_no": r, "runners": [x for x in field
                                                   if x["race_no"] == r]}
                        for r in sorted({x["race_no"] for x in field})],
              "runners": len(field), "named": 0}
    payload = ex.extract("2026-09-16", raw, roster)
    assert tips_payload.parse(payload).sources == ["rtw_interview"]

    db = tmp_path / "tips.db"
    conn = get_conn(db)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": "2026-09-16", "race_no": r,
                                    "venue": "HV", "distance": 1200}
                                   for r in sorted({x["race_no"]
                                                    for x in field})])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-09-16", "race_no": x["race_no"],
             "horse_no": x["horse_no"], "horse_name": x["horse_name"]}
            for x in field])
    conn.close()
    got = import_tips.run(payload, db=db)
    # The index's English names are the card's: the card check refuses
    # nothing. The one held row is the extractor's own — ARMOR GOLDEN EAGLE,
    # whose interview is after the excerpt ends.
    assert got.quotes == len(payload["quotes"])
    assert dict(got.reasons) == {"unparsed": 1}
    assert "ARMOR GOLDEN EAGLE" in payload["quarantine"][0]["raw"]

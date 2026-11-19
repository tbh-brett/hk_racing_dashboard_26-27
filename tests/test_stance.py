"""The stance of a Racing To Win interview answer, read by rule.

Every answer quoted here is a real one from the archive (lightly trimmed), so
the rule is pinned to how jockeys and trainers actually talk, ASR slips and all.
"""
from __future__ import annotations

import pytest

from hkrd.derive import stance
from hkrd.jobs import init_store, read_stance
from hkrd.store import tips, upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-10-11"


@pytest.mark.parametrize("words, cue", [
    ("Uh, I was hoping he was going to be a little bit better to be honest. Once again, "
     "once I put him under that pressure, he gives a little bit and then empties out. "
     "So, still a bit of a work in progress.", "raw"),
    ("Um I think he'll probably take a run or two like most.", "needs a run"),
    ("Yes, um um 1600 is a bit too short for him and I remember the pace was fairly slow.",
     "trip or track"),
    ("Yeah, all beaten Griffin company, but um, he's going to have to obviously improve "
     "when he takes on, you know, the stronger class of genuine class 4.", "up in class"),
    ("I think he still need to carry big weight but I think 1200 maybe a little bit better.",
     "weight"),
])
def test_caution_reads_negative(words, cue):
    r = stance.read(words)
    assert r.stance == "negative" and cue in r.cues_against


@pytest.mark.parametrize("words, cue", [
    ("I'm feeling very confident as he put up a very good fight last time.", "confident"),
    ("Yeah, been very pleased with him. He's had the three trials now.", "happy"),
    ("Yeah, I mean it doesn't really matter as long as we had a smooth run. "
     "I think he he should perform well.", "will run well"),
    ("Yeah, he trled well. He arrived in in good order.", "trialled well"),
    ("I think he'll be jumping really well, positioning himself in the first four.",
     "will run well"),
])
def test_confidence_reads_positive(words, cue):
    r = stance.read(words)
    assert r.stance == "positive" and cue in r.cues_for


def test_an_answer_with_nothing_to_read_is_neutral():
    assert stance.read("Yeah, I tr uh two times. Uh so he t good in the in the morning.").stance \
        == "neutral"
    assert stance.read(None).stance == "neutral"


def test_the_interviewer_s_question_is_not_the_speaker_s_words():
    """The locator sometimes keeps a question in an answer."""
    q = "He's up six points, 5 lbs. Are you worried he can't defy that rise?"
    assert stance.read(q).cues_against == ()


def test_the_draw_of_a_past_race_is_history_not_a_worry():
    past = "Oh yeah, last time we got the outside draw and we have to sitting back a bit."
    now = "This time he's up in class and got a bit tricky draw."
    assert "bad draw" not in stance.read(past).cues_against
    assert {"bad draw", "up in class"} <= set(stance.read(now).cues_against)


def test_a_whole_interview_leans_the_same_way_as_one_answer():
    assert stance.lean(3, 1) == "positive"
    assert stance.lean(1, 2) == "negative"
    assert stance.lean(2, 2) == stance.lean(0, 0) == "neutral"


# ── stored ───────────────────────────────────────────────────────────────────

def _quote(qid: str, words: str, source: str = "rtw_interview") -> dict:
    """Keyed '<video_id>:<second>' as the store keys it: 'v:1' is v at 1 s."""
    video, second = qid.split(":")
    return {"video_id": video, "t_start": float(second), "race_date": DATE, "race_no": 1, "horse_no": 1,
            "speaker": "Zac Purton", "role": "jockey", "quote": words,
            "source": source, "extracted_by": "rule:rtw-interview-v2",
            "url": "https://example.invalid/v", "fetched_at": "2026-10-10T16:00:00"}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "stance.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": DATE, "race_no": 1, "venue": "ST",
                                    "course": "A", "surface": "Turf", "distance": 1200}])
        upsert.upsert_runners(conn, [{"race_date": DATE, "race_no": 1, "horse_no": 1,
                                      "horse_name": "GOLDEN SIXTY"}])
        tips.upsert_quotes(conn, [
            _quote("v:1", "Still a bit of a work in progress."),
            _quote("v:2", "Yeah, been very pleased with him."),
            _quote("v:3", "Yeah, I tr uh two times."),
            _quote("v:4", "A work in progress, I'm afraid.", source="factcheck"),
        ])
    conn.close()
    return path


def _stances(db) -> dict[str, str | None]:
    conn = get_conn(db)
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT quote_id, stance FROM connections_quote")}
    finally:
        conn.close()


def test_every_interview_answer_is_read_and_nothing_else(db):
    out = read_stance.run(db)
    assert out["answers"] == 3 and out["changed"] == 3
    assert _stances(db) == {"v:1": "negative", "v:2": "positive", "v:3": "neutral",
                            "v:4": None}
    assert read_stance.run(db)["changed"] == 0        # a second read changes nothing


def test_the_server_reads_them_when_it_starts(db):
    """`init_store` runs at startup, so a deploy reaches every stored answer."""
    out = init_store.run(db)
    assert out["stances"]["changed"] == 3
    assert _stances(db)["v:1"] == "negative"

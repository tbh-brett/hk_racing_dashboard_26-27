"""query/tips_talk — what a pundit's video discussed, section by section.

The meeting is the real 1 Oct 2026 card (`roster_20261001.json`, with its
Chinese names), and the video the real 10.1 賽馬Fact Check preview's opening
survey and 伍鵬志's section (`factcheck_20261001_excerpt.json`), put through
the PC's extractor and the dashboard's import exactly as the tips run does.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import extract_tips as ex                                   # noqa: E402
from hkrd.jobs import import_tips                           # noqa: E402
from hkrd.query import tips_summary, tips_talk              # noqa: E402
from hkrd.store import tips, upsert                         # noqa: E402
from hkrd.store.connect import get_conn, init_db, transaction  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-10-01"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def db(tmp_path) -> Path:
    roster = load("roster_20261001.json")
    path = tmp_path / "talk.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": r["race_no"], "venue": "ST",
             "distance": 1200} for r in roster["races"]])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": r["race_no"], **{
                k: x[k] for k in ("horse_no", "horse_name", "jockey", "trainer")}}
            for r in roster["races"] for x in r["runners"]])
        tips.upsert_horse_names(conn, [
            {"horse_name": x["horse_name"], "name_zh": x["name_zh"],
             "source": "racecard_zh", "seen_at": DATE}
            for r in roster["races"] for x in r["runners"] if x["name_zh"]])
    conn.close()
    raw = tmp_path / "raw" / "factcheck"
    raw.mkdir(parents=True)
    shutil.copy(FIXTURES / "factcheck_20261001_excerpt.json", raw / "I0joWiohg54.json")
    payload = ex.extract(DATE, tmp_path / "raw", roster)
    # 譚朗蔚's R1 picks, as the full video's tail gives them (the excerpt
    # carries none): heard through speech-to-text.
    payload["selections"] = [
        {"source": "factcheck", "tipster": "譚朗蔚", "race_no": 1, "horse_no": no,
         "pick_rank": None, "name_seen": said, "caption_kind": "asr",
         "url": f"https://www.youtube.com/watch?v=I0joWiohg54&t={t}s"}
        for no, said, t in ((5, "同油運", 351), (1, "新力驕", 355))]
    got = import_tips.run(payload, db=path)
    assert (got.quarantined, got.unplaced_quotes) == (0, 0)
    return path


def read(db: Path, fn, *a, **k):
    conn = get_conn(db)
    try:
        return fn(*a, conn=conn, **k)
    finally:
        conn.close()


def test_every_section_arrives_with_the_runners_it_names_identified(db):
    [video] = read(db, tips_talk.talk, DATE)
    assert (video["source"], video["label"], video["video_id"]) == \
        ("factcheck", "賽馬Fact Check", "I0joWiohg54")
    assert [int(s["t"]) for s in video["sections"]] == [21, 214]
    stable = video["sections"][1]
    assert stable["head"] == "伍鵬志被喻為季初最大發現"
    assert "累積連勝六日保持開季未斷纜" in stable["text"]
    assert [(r["race_no"], r["horse_no"], r["horse_name"])
            for r in stable["runners"]] == [(1, 5, "GOOD FORTUNE"),
                                              (9, 11, "DROMBEG BANNER")]
    # The jockey and trainer ride along: they say who 伍鵬志 is.
    assert stable["runners"][0]["trainer"] and stable["runners"][0]["jockey"]
    assert stable["unlinked"] == []


def test_the_videos_picks_come_after_its_sections(db):
    [video] = read(db, tips_talk.talk, DATE)
    assert [(p["race_no"], p["horse_no"], p["heard"], p["t"])
            for p in video["picks"]] == [(1, 5, True, 351), (1, 1, True, 355)]


def test_a_section_is_counted_as_one_not_as_a_quote(db):
    status = {s["source"]: s for s in
              read(db, tips_summary.summary, DATE)["source_status"]}
    assert (status["factcheck"]["sections"], status["factcheck"]["quotes"]) == (2, 2)
    assert status["factcheck"]["featured"] == 2      # GOOD FORTUNE, DROMBEG BANNER


def test_a_section_and_a_comment_on_the_same_second_are_two_rows():
    row = {"video_id": "v", "t_start": 214.2}
    assert tips.quote_id(row) == "v:214"
    assert tips.quote_id(dict(row, topic="section")) == "v:214:s"


def test_a_meeting_with_no_sections_has_nothing_to_show(db):
    assert read(db, tips_talk.talk, "2026-10-04") == []

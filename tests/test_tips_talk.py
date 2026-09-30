"""query/tips_talk — each section of a pundit's video, under every runner it names.

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


def test_each_section_sits_under_every_runner_it_names(db):
    got = read(db, tips_talk.by_runner, DATE)
    # 伍鵬志's section names his two runners, and sits under both.
    for key in ((1, 5), (9, 11)):                # GOOD FORTUNE, DROMBEG BANNER
        [section] = got[key]
        assert section["head"] == "伍鵬志被喻為季初最大發現"
        assert "累積連勝六日保持開季未斷纜" in section["text"]
        assert (section["label"], int(section["t"])) == ("賽馬Fact Check", 214)
        assert section["url"].endswith("v=I0joWiohg54&t=214s")
    # RAGING BLIZZARD: named in the opening survey, discussed nowhere.
    assert [s["head"] for s in got[(3, 1)]] == ["10月1日星期四國慶日"]
    assert set(got) == {(1, 5), (9, 11), (3, 1), (3, 7)}


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
    assert read(db, tips_talk.by_runner, "2026-10-04") == {}

"""tools/extract_threads — Horse Detective's picks, and never its winners.

`threads_posts_20260916.jsonl` is the four posts the Threads feed held on
25 Sep 2026, as `harvest_threads.py` stored them: one pre-race analysis
(13 Sep, R10 #10 金勝名駒, trimmed to its opening sentence) and three
result claims from the 16th.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import extract_threads as threads                            # noqa: E402
from hkrd.ingest.tips_payload import parse                  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def posts(tmp_path) -> list[dict]:
    shutil.copy(FIXTURES / "threads_posts_20260916.jsonl",
                tmp_path / "posts.jsonl")
    return threads.read(tmp_path)


def test_a_post_about_a_winner_is_never_a_pick(posts):
    """The account posts the bets that landed and not the ones that lost."""
    claims = [p for p in posts if p["kind"] == "result_claim"]
    assert len(claims) == 3
    assert not any(threads.is_for(p, "2026-09-16") for p in claims)


@pytest.mark.parametrize("meeting, counted", [
    ("2026-09-13", True),     # published 11:49 on race day
    ("2026-09-15", True),     # two days on: still inside the window
    ("2026-09-16", False),    # three days on: the next meeting's, not this
    ("2026-09-12", False),    # the day before it went up
])
def test_a_post_belongs_to_the_meeting_it_went_up_for(posts, meeting, counted):
    analysis = next(p for p in posts if p["kind"] == "analysis")
    assert threads.is_for(analysis, meeting) is counted


def test_the_pick_is_the_number_and_the_name_is_the_checksum(posts):
    got = threads.selections(posts, "2026-09-13", "2026-09-25T04:00:00Z")
    assert len(got) == 1
    pick = got[0]
    assert (pick["race_no"], pick["horse_no"], pick["name_seen"]) == \
        (10, 10, "金勝名駒")
    assert pick["pick_rank"] is None
    assert pick["note"].startswith("[王子] R10 10 金勝名駒")
    assert "#" not in pick["note"]


def test_two_posts_on_one_horse_are_one_pick(posts):
    analysis = next(p for p in posts if p["kind"] == "analysis")
    later = dict(analysis, published="2026-09-13T05:00:00+00:00",
                 text="[王子] R10 10 金勝名駒 later words")
    got = threads.selections([analysis, later], "2026-09-13", "x")
    assert len(got) == 1 and got[0]["note"] == "[王子] R10 10 金勝名駒 later words"


def test_the_picks_pass_the_payload_contract(posts):
    body = {"payload_version": 1, "race_date": "2026-09-13",
            "generated_at": "2026-09-25T04:00:00Z", "extractor": "rule:tips-v1",
            "sources": [threads.SOURCE],
            "selections": threads.selections(posts, "2026-09-13",
                                             "2026-09-25T04:00:00Z")}
    assert parse(json.loads(json.dumps(body))).selections[0]["horse_no"] == 10

"""derive/tags — stewards' prose into tags.

The vocabulary was built from frequency analysis over 10,852 real incident texts
across 87 meetings, per the build spec's instruction to let HKJC's own wording
drive the taxonomy rather than assumed categories.
"""
from __future__ import annotations

import pytest

from hkrd.derive import tags


def names(text: str) -> set[str]:
    return {t.name for t in tags.tag_comment(text)}


# ── trip trouble: the tags that matter most ──────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("On jumping shifted out and made contact with CELESTIAL HARMONY.",
     {"shifted_out", "contact"}),
    ("Near the 1100 Metres was steadied to avoid CONRAD THE GREAT.", {"steadied"}),
    ("Raced wide without cover throughout.", {"wide", "without_cover"}),
    ("Was denied a run near the 200 Metres.", {"short_of_room"}),
    ("Bumped at the start and hampered near the 400 Metres.", {"bumped", "hampered"}),
    ("Held up in the field.", {"held_up"}),
    ("Raced keenly in the early stages.", {"raced_keenly"}),
])
def test_trip_trouble_is_recognised(text, expected):
    assert expected <= names(text)


# ── routine must never look like trouble ─────────────────────────────────────

def test_a_passed_veterinary_examination_is_routine():
    """Design brief 07 §2: a passed post-race examination is not the same as a
    horse barred pending a trial. If they render identically the badge becomes
    noise and gets ignored.

    HKJC's house phrasing is "did not show any significant findings" -- assuming
    "no abnormalities" left 794 of these untagged in the first pass.
    """
    text = ("A veterinary inspection immediately following the race did not "
            "show any significant findings.")
    tagged = tags.tag_comment(text)
    assert {t.name for t in tagged} == {"vet_routine"}
    assert all(t.kind == "routine" for t in tagged)


def test_a_real_veterinary_finding_is_trouble_not_routine():
    text = "A veterinary examination found the horse to have bled from both nostrils."
    tagged = {t.name: t for t in tags.tag_comment(text)}
    assert "vet_finding" in tagged
    assert tagged["vet_finding"].kind == "trouble"
    assert "vet_routine" not in tagged, "a finding must supersede the routine tag"


def test_sampling_is_routine():
    assert {t.kind for t in tags.tag_comment("Sent for sampling post-race.")} == {"routine"}


def test_no_report_is_explicit_rather_than_untagged():
    """1,679 texts say exactly this. Tagging it beats leaving it blank, because
    "no incident" and "not yet processed" are different facts."""
    assert names("No report.") == {"no_report"}


# ── coverage properties ──────────────────────────────────────────────────────

def test_empty_input_yields_no_tags_without_raising():
    assert tags.tag_comment(None) == ()
    assert tags.tag_comment("") == ()
    assert tags.tag_comment("   ") == ()


def test_polarity_marks_excuses_apart_from_negatives():
    """An excuse means the horse ran better than the result looks; a negative
    means the opposite. Collapsing them loses the whole point of the tag."""
    excuse = {t.polarity for t in tags.tag_comment("Was badly hampered near the 300 Metres.")}
    negative = {t.polarity for t in tags.tag_comment("Raced keenly and weakened.")}
    assert 1 in excuse
    assert -1 in negative


def test_tag_rows_shape_matches_the_runner_tags_table():
    rows = tags.tag_rows([{
        "race_date": "2026-07-15", "race_no": 3, "horse_no": 8,
        "comment_text": "Bumped at the start and raced wide.",
    }])
    assert rows
    for r in rows:
        assert set(r) == {"race_date", "race_no", "horse_no", "tag", "confidence"}
        assert 0 < r["confidence"] <= 1


def test_tag_rows_skips_empty_comments_without_failing():
    assert tags.tag_rows([{"race_date": "2026-07-15", "race_no": 1,
                           "horse_no": 1, "comment_text": None}]) == []


def test_every_rule_has_a_distinct_name():
    seen = [name for name, _, _, _ in tags.TAG_RULES]
    assert len(seen) == len(set(seen))


def test_rules_declare_only_known_kinds():
    assert {k for _, k, _, _ in tags.TAG_RULES} <= {"trouble", "routine", "style", "lane"}

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


@pytest.mark.parametrize("text", [
    # LUCKY BALERION, 2026-10-19 ST 1800: tagged `jumped_fairly` alone, so
    # the page drew a clean trip.
    "Jumped only fairly, settled midfield, had difficulty obtaining clear "
    "running near the 200M, ran on fairly (See Incident Report).",
    "Near the 200 Metres had some difficulty obtaining clear running when "
    "being shifted to the outside of BIG BIG WORLD.",
    "Had difficulty securing clear running in the Straight.",
    "Had some trouble finding clear running near the 300 Metres.",
    "Near the 150 Metres was checked when disappointed for clear running.",
    "Tracked leaders against rail, unable to secure clear run until 100m out.",
    "Was unable to obtain clear running until the final 100 Metres.",
    "Failed to obtain clear running in the Straight.",
    "Did not obtain clear running until after the 50 Metres.",
    "Saved ground to turn, no clear running in straight.",
    "Was denied clear running from 100M to the finish.",
])
def test_the_stewards_words_for_no_run_are_tagged(text):
    """HKJC almost never writes "short of room". 2,177 texts said it in these
    words instead, and 561 of their runs read as clean trips. They get their
    own tag and leave `short_of_room` as it was, because that is the tag the
    model counts and counting these measured worse (derive/tags)."""
    tagged = names(text)
    assert "no_clear_running" in tagged
    assert "short_of_room" not in tagged


@pytest.mark.parametrize("text", [
    "Near the 300 Metres was shifted out to obtain clear running.",
    "Shifted to the outside of RUNNER to continue into clear running.",
    "Had clear running on straightening and finished well.",
    "Was eventually able to obtain clear running near the 100 Metres.",
])
def test_finding_a_run_is_not_being_denied_one(text):
    """The cure is not the trouble: a horse that switched out and found clear
    running is tagged by what it did (`shifted_out`), not as denied a run."""
    assert not {"no_clear_running", "short_of_room"} & names(text)


def test_held_up_for_clear_running_stays_one_tag():
    """`held_up` already owns this phrase; it must not also read as no run."""
    assert names("Held up for clear running near the 200 Metres.") == {"held_up"}


def test_the_short_form_is_still_short_of_room():
    """"no clear run" has always been `short_of_room` and still is; only
    "no clear running", which the \\b never matched, is the new tag."""
    assert names("Raced on the rail, no clear run in the straight.") == {"short_of_room"}


def test_the_model_does_not_count_no_clear_running():
    """A measured decision, not an oversight. Walk-forward 16 Nov 2026,
    2021-22 to 2025-26, nine seeds averaged: counting these runs as trouble
    made the model worse by 0.0010 a race (t -2.4), which the gate refuses.
    Re-measure before adding it to any fitted model's list."""
    from hkrd.model.screen import HARD_TROUBLE
    from hkrd.store.gbm import TROUBLE_TAGS
    assert "no_clear_running" not in TROUBLE_TAGS
    assert "no_clear_running" not in HARD_TROUBLE


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


def test_a_real_veterinary_finding_is_never_routine():
    text = "A veterinary examination found the horse to have bled from both nostrils."
    tagged = {t.name: t for t in tags.tag_comment(text)}
    assert "bled" in tagged, "the finding is named, not left as a catch-all"
    assert tagged["bled"].kind == "vet"
    assert "vet_routine" not in tagged, "a finding must supersede the routine tag"


def test_a_named_finding_replaces_the_catch_all():
    """`vet_finding` beside `lame_fore` says the same thing twice and makes one
    finding look like two."""
    tagged = {t.name for t in tags.tag_comment(
        "Veterinary examination revealed that horse to be lame in its left front leg.")}
    assert tagged == {"lame_fore"}


def test_the_catch_all_still_speaks_when_nothing_names_it():
    """It is what tells you the vocabulary has a gap, rather than the horse
    being fine."""
    tagged = {t.name for t in tags.tag_comment(
        "Veterinary examination revealed a fractured cannon bone.")}
    assert tagged == {"vet_finding"}


def test_every_named_finding_has_a_rule():
    """`NAMED_VET` is what the page draws as a finding and what the catch-all
    defers to. A name in it with no rule behind it would silently never fire."""
    ruled = {n for n, kind, _, _ in tags.TAG_RULES if kind == "vet"}
    assert tags.NAMED_VET <= ruled
    assert ruled - tags.NAMED_VET == {"vet_finding"}


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
    assert {k for _, k, _, _ in tags.TAG_RULES} <= {"trouble", "vet", "routine",
                                                    "style", "lane"}

"""Turn stewards' prose into tags. Pure functions — text in, tags out.

The vocabulary below was derived from frequency analysis over 10,852 real
incident texts across 87 meetings, not assumed. The counts in each comment are
the share of texts containing that phrase, at the time of writing.

The most important distinction it encodes is between trouble and routine.
`veterinary` appears in 23.5% of texts and `sampling` in 20%, which makes them
the two most common phrases in the corpus — and both are ordinary post-race
admin. Design brief 07 §2 is explicit that a passed examination is not the same
as a horse barred pending a trial: if everything renders identically the badge
becomes noise and gets ignored. So routine tags carry kind="routine" and the UI
is expected to treat them quietly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["DERIVE_VERSION", "Tag", "TAG_RULES", "NAMED_VET", "VET_TAGS",
           "tag_comment", "tag_rows"]

DERIVE_VERSION = "tags-1.0"


@dataclass(frozen=True)
class Tag:
    name: str
    kind: str        # trouble | vet | routine | style | lane
    polarity: int    # +1 excuse (ran better than it looks), -1 negative, 0 neutral
    confidence: float


# (name, kind, polarity, pattern). Ordered roughly by corpus frequency.
TAG_RULES: tuple[tuple[str, str, int, str], ...] = (
    # ── trip trouble: the tags that matter, per design brief 01 ──────────────
    ("bumped",          "trouble", +1, r"\bbump(ed|ing)?\b"),                 # 18.8%
    ("wide",            "trouble", +1, r"\bwide\b|\bdeep\b"),                 # 15.3%
    ("shifted_out",     "trouble",  0, r"\bshifted out\b|\blaid out\b"),      # 14.3%
    ("shifted_in",      "trouble",  0, r"\bshifted in\b|\blaid in\b"),        # 11.7%
    ("steadied",        "trouble", +1, r"\bsteadied\b|\btaken back\b"),       # 10.8%
    ("contact",         "trouble", +1, r"\bmade contact\b|\bcontacted\b"),    # 10.6%
    ("crowded",         "trouble", +1, r"\bcrowded\b|\btightened\b"),         #  8.3%
    ("without_cover",   "trouble", +1, r"\bwithout cover\b|\bno cover\b"),    #  7.7%
    ("held_up",         "trouble",  0, r"\bheld up\b|\brestrained\b"),        #  6.6%
    ("raced_keenly",    "trouble", -1, r"\braced keenly\b|\bover[- ]raced\b"),#  4.7%
    ("checked",         "trouble", +1, r"\bcheck(ed|ing)\b"),                 #  2.9%
    ("eased",           "trouble", -1, r"\beased\b"),                         #  2.6%
    ("hampered",        "trouble", +1, r"\bhamper(ed|ing)\b"),                #  1.8%
    ("short_of_room",   "trouble", +1, r"\bshort of room\b|\bdenied a run\b|"
                                       r"\bno clear run\b"),
    # Start descriptors. HKJC phrases these several ways and they were the
    # largest genuine gap in the first pass: "Jumped awkwardly" (61),
    # "Slow to begin" (48) and "Jumped only fairly" (209) all went untagged.
    ("awkwardly_away",  "trouble", +1, r"\bawkwardly away\b|\bslowly away\b|"
                                       r"\bbegan awkwardly\b|\bjumped awkwardly\b|"
                                       r"\bslow to begin\b|\blost ground (at the )?start\b"),
    ("jumped_fairly",   "trouble",  0, r"\bjumped only fairly\b|\bbegan only fairly\b"),
    ("stumbled",        "trouble", +1, r"\bstumbl(ed|ing)\b|\bclipped heels\b"),
    ("greenly",         "trouble",  0, r"\braced greenly\b|\bgreen\b"),
    ("weakened",        "trouble", -1, r"\bweakened\b|\bone[- ]paced\b"),

    # ── routine: common, and deliberately not trouble ────────────────────────
    ("sampling",        "routine",  0, r"\bsent for sampling\b|\bsampling\b"), # 20.0%
    # The house phrasing is "did not show any significant findings", not the
    # "no abnormalities" the first pass assumed -- which left 794 routine vet
    # notes untagged and indistinguishable from a real finding.
    ("vet_routine",     "routine",  0, r"\bveterinar\w+ (examination|inspection)\b"
                                       r"(?=.*\b(did not show|no significant|"
                                       r"no abnormalit\w+|passed)\b)"),
    ("no_report",       "routine",  0, r"^\s*no report\.?\s*$"),
    # ── veterinary findings, named ───────────────────────────────────────────
    #
    # THERE IS NO VET SCRAPE IN THE ARCHIVE — `vet_records` holds zero rows —
    # so every piece of veterinary information the dashboard has comes from
    # the stewards' text, and until now it arrived as one undifferentiated
    # `vet_finding`. "Bled from both nostrils" and "lame in its left fore" are
    # not the same fact about a horse and must not render as the same badge.
    #
    # Frequencies below are over the 10,775 incident texts in the archive.
    # Each is checked against the routine rule above: a passed examination
    # never matches any of these.
    ("bled",            "vet",     -1, r"\bbled\b|\bepistaxis\b|"
                                       r"blood in (the |its |the horse.s )?trachea"),  # 1.7%
    ("roarer",          "vet",     -1, r'"?\broarer\b|\brespiratory noise\b|'
                                       r"\bmaking a noise\b"),                 # 1.0%
    ("lame_fore",       "vet",     -1, r"lame[^.;]{0,30}\b(fore|front)\b"),    # 0.9%
    ("lame_hind",       "vet",     -1, r"lame[^.;]{0,30}\bhind\b"),            # 0.2%
    ("arrhythmia",      "vet",     -1, r"irregular heart|\barrhythmi\w+|"
                                       r"\bheart (rhythm|rate)\b"),            # 0.3%
    ("mucus",           "vet",     -1, r"\bmucus\b"),                          # 0.1%
    ("barred",          "vet",     -1, r"\bbarred\b|not be permitted to race|"
                                       r"\bstood down\b"),                     # 0.1%
    # The catch-all stays, for a finding none of the above names. It is what
    # tells you the vocabulary has a gap rather than the horse being fine.
    ("vet_finding",     "vet",     -1, r"\bveterinar\w+\b(?!.*no abnormalit)"
                                       r".*\b(bled|bleeding|lame|injur\w+|"
                                       r"cardiac|fractur\w+|barred)\b"),
)

_COMPILED = tuple((n, k, p, re.compile(pat, re.I)) for n, k, p, pat in TAG_RULES)

# The findings the vocabulary can name. `vet_finding` is the fallback for one
# it cannot, which is what tells you the vocabulary has a gap rather than the
# horse being fine.
#: Every tag of kind "vet", named and catch-all. The one list a page or a
#: query asks "is this a veterinary finding?" — `NAMED_VET` is the subset that
#: says WHAT was found, and `vet_finding` is the one that only says a finding
#: was made. `vet_routine` is deliberately absent: a passed examination is on
#: nearly every runner and is not a finding.
VET_TAGS = frozenset({"bled", "roarer", "lame_fore", "lame_hind",
                      "arrhythmia", "mucus", "barred", "vet_finding"})

NAMED_VET = frozenset({"bled", "roarer", "lame_fore", "lame_hind",
                       "arrhythmia", "mucus", "barred"})


def tag_comment(text: str | None) -> tuple[Tag, ...]:
    """Tags present in one stewards' comment.

    Confidence is deliberately coarse — these are regex matches over human
    prose, not a model. A flat 0.9 for a direct phrase match says "the words are
    there", which is all that is being claimed.
    """
    if not text or not text.strip():
        return ()
    found: list[Tag] = []
    for name, kind, polarity, pattern in _COMPILED:
        if pattern.search(text):
            found.append(Tag(name=name, kind=kind, polarity=polarity, confidence=0.9))
    names = {t.name for t in found}
    # A significant vet finding supersedes the routine tag on the same text: a
    # horse that bled did not merely pass an examination.
    if any(t.kind == "vet" for t in found):
        found = [t for t in found if t.name != "vet_routine"]
    # And the catch-all only speaks when nothing more precise did. Emitting
    # `vet_finding` beside `lame_fore` says the same thing twice and makes the
    # row look like two findings.
    if names & NAMED_VET:
        found = [t for t in found if t.name != "vet_finding"]
    return tuple(found)


def tag_rows(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """runner_tags rows from runner_comments rows.

    Input rows need race_date, race_no, horse_no, comment_text.
    """
    out: list[dict[str, Any]] = []
    for c in comments:
        for tag in tag_comment(c.get("comment_text")):
            out.append({
                "race_date": c.get("race_date"), "race_no": c.get("race_no"),
                "horse_no": c.get("horse_no"), "tag": tag.name,
                "confidence": tag.confidence,
            })
    return out

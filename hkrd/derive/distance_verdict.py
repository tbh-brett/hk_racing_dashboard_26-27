"""How far HKJC's new-horse profile says an import should get: stayer,
miler or sprinter.

The profile (ingest/newhorse) closes with the contractor's prospect —
"should be able to handle sprint races and could reach up to mile in
future", "should be most suited over 2000M or upper in distance". That
sentence is read here by rule into the longest trip it allows:

    sprinter   up to 1400 m       "a precocious sprinter", "1400M or under"
    miler      1500 to 1799 m     "up to a mile", "1400M or upper"
    stayer     1800 m and over    "middle distance", "mile or further"

An open end ("or upper", "or farther", "even longer") is read as one step
past the trip it names, so "1400M or upper" is a miler and "mile or further"
a stayer. A clause about the dam or the sire is not about this horse and is
skipped: LUCKY GIBS's dam "collected 4 wins in middle distance events and her
off-spring prefers sprint races" is a sprinter. The last sentence that gives
a trip and a judgement ("should", "suited", "expected") is the verdict.
Nothing is guessed where the profile says nothing about trip.

WHY IT IS READ AT ALL (16 Nov 2026, Claude outputs/pedigree-research):
imports the profile called stayers won at 1600 m and over about one and a
half times as often as their closing price said, whatever their sire. That
was found looking at every run to 16 Nov, so `query/background` scores it
apart from 17 Nov on: the runs after it are the test.
"""
from __future__ import annotations

import functools
import re
from typing import Any

__all__ = ["read", "VERDICTS", "SPRINT_MAX", "STAY_MIN"]

VERDICTS = ("stayer", "miler", "sprinter")
SPRINT_MAX = 1400   # a ceiling at or under this is a sprinter
STAY_MIN = 1800     # a ceiling at or over this is a stayer
_STEP = 200         # how far past its named trip an open end reaches

_JUDGED = re.compile(r"\b(?:should|could|would|will|may|might|expect|anticipat|project|"
                     r"likely|suit|handle|ideal|best|effective|prefer|capable|reach|"
                     r"extend|stretch|develop into|looks?)", re.I)
# Trips the prose names in words, and what each is worth in metres.
_WORDS = ((re.compile(r"\bmiddle[- ]distances?\b", re.I), 2000),
          (re.compile(r"\bstay(?:ers?|ing)\b", re.I), 2000),
          (re.compile(r"\bmilers?\b|\bmile\b", re.I), 1600),
          (re.compile(r"\bsprint(?:ers?|s)?\b", re.I), 1200))
_METRES = re.compile(r"\b(\d{4})\s?m\b", re.I)
_OPEN_AFTER = re.compile(r"^\W*(?:or|and)\s+(?:even\s+)?(?:upper|above|further|farther|"
                         r"beyond|longer|more|upwards?)\b|^\s*\+", re.I)
_OPEN_BEFORE = re.compile(r"\b(?:beyond|more than|further than|longer than)\s+(?:an?\s+|the\s+)?$",
                          re.I)
_UNDER_BEFORE = re.compile(r"\b(?:under|below|shorter than|less than)\s+(?:an?\s+|the\s+)?$",
                           re.I)
# A clause about the family, or about a race already run, says nothing about
# how far THIS horse should get.
_PEDIGREE = re.compile(r"\b(?:dam|sire|grandsire|progeny|family|siblings?|brother|sister|"
                       r"half-|foals?|filly|off-?spring|stablemate)", re.I)
_RAN = re.compile(r"\b(?<!lightly )(?:won|finished|scored|ran|raced|placed|beaten|collected|"
                  r"victory)\b", re.I)
# "according to dam pedigree, ... expected to handle 1800M" credits the family
# for a verdict that is still about the horse.
_SOURCE = re.compile(r"^\W*(?:however\s+)?(?:according to|judging by|given|based on)\b", re.I)
# "The son of DEEP FIELD should be..." is the horse itself, not its sire.
_HORSE = re.compile(r"\b(?:NAME|gelding|colt|horse|import|entire|he|him|runner|"
                    r"(?:grand)?(?:son|daughter) of)\b|"
                    r"\bthe (?:bay|brown|chestnut|grey|gray|roan|black)\b|"
                    r"\bhis\b(?!\s+(?:dam|sire|half|full|sibling|brother|sister|family|grand))",
                    re.I)
# "looks like his dam's sire LONHRO that should be..." is still the horse.
_LIKENESS = re.compile(r"\blooks? like\b|\bresembl|\btak(?:es|ing) after\b", re.I)
# Names on these pages are in capitals: LOVELY MILES is a horse, not a trip,
# and DUBAWI is a sire. Two letters (HK, NZ, G1) are places and grades.
_NAME = re.compile(r"\b[A-Z][A-Z'’.-]{2,}(?:\s+[A-Z][A-Z'’.-]+)*\b")


def _clauses(sentence: str) -> list[str]:
    return [c for c in re.split(r",\s*|;\s*|\s+(?:and|while|that|but|whereas)\s+",
                                sentence) if c.strip()]


def _ceiling(sentence: str) -> int | None:
    """The longest trip the sentence allows this horse, or None.

    Each clause is about the horse or about its family, and a clause that
    names neither ("that could handle up to 1500M") is about whichever the
    clause before it was about."""
    best = None
    about_horse, likeness, counted = True, False, False
    for clause in _clauses(_NAME.sub("NAME", sentence)):
        fam, own = _PEDIGREE.search(clause), _HORSE.search(clause)
        if fam and (not own or fam.start() < own.start()):
            about_horse = about_horse and (likeness or bool(_SOURCE.search(clause)))
        elif own:
            about_horse = True
            likeness = likeness or bool(_LIKENESS.search(clause))
        judged = bool(_JUDGED.search(_LIKENESS.sub("", clause)))
        if judged:
            likeness = False
        # "most suited in sprint and mile events": the second half carries
        # on the judgement of the first.
        carried = counted and not fam and not own
        counted = about_horse and (judged or carried) and not _RAN.search(clause)
        if not counted:
            continue
        found: list[tuple[int, int, int]] = []      # (metres, start, end)
        for m in _METRES.finditer(clause):
            found.append((int(m.group(1)), m.start(), m.end()))
        for pat, metres in _WORDS:
            for m in pat.finditer(clause):
                found.append((metres, m.start(), m.end()))
        for metres, a, b in found:
            opened = (_OPEN_AFTER.search(clause[b:b + 40])
                      or _OPEN_BEFORE.search(clause[max(0, a - 20):a]))
            under = _UNDER_BEFORE.search(clause[max(0, a - 20):a])
            reach = metres + (_STEP if opened else -_STEP if under else 0)
            best = reach if best is None else max(best, reach)
    return best


def read(about: str | None) -> dict[str, Any] | None:
    """The profile's distance verdict, or None where it gives none.

    {"verdict": "stayer" | "miler" | "sprinter", "ceiling_m": 2000,
     "sentence": the contractor's words it was read from}"""
    if not isinstance(about, str) or not about.strip():
        return None
    found = _read(about)
    return dict(found) if found else None


@functools.lru_cache(maxsize=4096)
def _read(about: str) -> dict[str, Any] | None:
    """A profile is never revised, so each text is read once per process."""
    # "...from 1630M to 1800M.The chestnut should..." has no space to split on.
    for s in reversed(re.split(r"(?<=[.!?])\s+|(?<=\w[.!?])(?=[A-Z][a-z])", about.strip())):
        if not _JUDGED.search(s):
            continue
        top = _ceiling(s)
        if top is None:
            continue
        verdict = ("stayer" if top >= STAY_MIN else
                   "sprinter" if top <= SPRINT_MAX else "miler")
        return {"verdict": verdict, "ceiling_m": top, "sentence": s.strip()}
    return None

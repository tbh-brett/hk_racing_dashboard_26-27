"""Does a published horse name belong to the horse at that number?

Pure functions — no database, no I/O. `jobs/import_tips` asks this of every
tip that carries the name its source printed or said, and `tools/` can import
the same functions to find names in a transcript, so the two sides of the
network cannot disagree about what counts as a match.

EXACT MATCHING IS TOO STRICT FOR WHERE THESE NAMES COME FROM. People shorten
神駒馬靈 to 馬靈; English speech-to-text writes "Dragon Sunris"; a tipster table
drops a character. So a published name matches a horse's name when it is:

  * the same name                                    1.00
  * a run of two or more characters inside it, or it
    inside the name — 馬靈 in 神駒馬靈                0.90
  * close enough by characters in order              SequenceMatcher ratio

and it has to be close to ONE horse. A name that sits as close to another
runner as to the one numbered is not evidence for either, and a name close to
nobody on a fully named card is not about this meeting.

That is for comparing a NAME with a name. Finding names inside running text
is a different job, and two-character fragments are no good for it: run over
the real 9.23 preview they matched 3 times and were wrong 3 times (贏馬之後,
"after winning", read as 揀馬之皇). `tools/extract_tips.py` scans text with
whole names and fragments of three or more.

What this cannot do is hear. Cantonese speech-to-text substitutes a different
character with the same SOUND (永騰 -> 永藤), and characters compared in order
see a mismatch there. That needs a romanisation table; it is noted here
rather than faked.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

__all__ = ["normalise", "is_chinese", "similarity", "verdict", "verdict_heard",
           "MATCH_AT"]

# One wrong character in a three-character name scores 0.667; in a two-
# character name it scores 0.5 and is NOT a match — two characters with one
# wrong is one character, and HK horse names share single characters (星, 駒,
# 勝, 利) far too often for one to identify anything.
MATCH_AT = 0.65

_CJK = re.compile(r"[㐀-鿿]")
_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


def is_chinese(name: str) -> bool:
    return bool(_CJK.search(name or ""))


def normalise(name: str) -> str:
    """Letters and characters only, Latin upper-cased: "Sky Cap" == "SKY CAP"."""
    return _NOISE.sub("", name or "").upper()


def similarity(said: str, name: str) -> float:
    """How closely a published name matches one horse's name, 0..1."""
    a, b = normalise(said), normalise(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    # Two characters, or four letters: below that a fragment names nothing.
    if len(short) >= (2 if is_chinese(short) else 4) and short in long_:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def verdict(said: str, claimed: str | None, others: Iterable[str | None], *,
            complete: bool) -> str | None:
    """None if `said` is consistent with the runner it was filed under.

    `claimed` is that runner's name in the language `said` is in (None if it
    is not known), `others` every other runner's name at the meeting, and
    `complete` whether every runner's name is known — without which "matches
    nobody" cannot be told apart from "matches a horse we have no name for".

    Returns "name_mismatch" when the name fits another runner better, or
    fits nobody while the numbered runner's own name is known; and
    "name_unknown" when it fits two runners equally, or nobody at all on a
    card where every name is known.
    """
    own = similarity(said, claimed) if claimed else 0.0
    best = max((similarity(said, o) for o in others if o), default=0.0)
    if own >= MATCH_AT:
        return None if own > best else "name_unknown"
    if best >= MATCH_AT:
        return "name_mismatch"
    # Close to nobody. If every name is known, it is no horse at this
    # meeting. If only the numbered horse's is, it is at least not that one.
    # Only when neither is known is there nothing to check against.
    if complete:
        return "name_unknown"
    return "name_mismatch" if claimed else None


def verdict_heard(said: str, claimed: str | None,
                  race_others: Iterable[str | None]) -> str | None:
    """The checksum for a runner whose NUMBER was heard, not read off a name.

    Measured on 賽馬Fact Check's 9.23 tail: the analyst says 「7號嘅銀刺勇士」
    and speech-to-text writes 銀次勇士; 「一號嘅紅愛舍」 comes out 紅外嚇,
    which shares one character in three with the real name. The spoken number
    is the identity there, and no absolute threshold can hold a name that
    mangled. So the name is only asked to point at the numbered horse MORE than
    at any other runner in the same race — a number misheard as another
    horse's is still caught, because its name then points at that horse.
    """
    own = similarity(said, claimed) if claimed else 0.0
    others = [o for o in race_others if o]
    best = max((similarity(said, o) for o in others), default=0.0)
    if claimed is None and not others:
        return None                     # no names known: the number decides
    if own > best:
        return None
    return "name_mismatch" if best > own else "name_unknown"

"""Finding a horse's name inside running text.

Two different jobs, and they need different rules.

CHINESE (賽馬Fact Check, 全方位Bryan) is written without spaces, so a name is
found by looking for it: the whole name, or a run of three or more characters
that belongs to one runner at this meeting and no other. Two-character runs
were tried and were wrong three times out of three on the 9.23 preview
(「贏馬之後」, "after winning", read as 揀馬之皇).

ENGLISH (Racing To Win) is speech-to-text, so the name arrives bent — "Super
Sakario" for SUPER SICARIO, "George and Sigma" for GEORGIAN SIGMA, "All Our
Mine" for ALL ARE MINE. Exact matching finds none of those. Instead each
runner's name is compared against every short run of WORDS in the line, which
only works because the candidates are one race's ten to fourteen runners
rather than the 1,900 horses in Hong Kong. Comparing words rather than a
window of letters also means what gets recorded as `horse_said` is the words
the show actually said, which is the evidence the dashboard re-checks.

Nothing here decides anything. It reports where a name was seen and how well
it matched; the caller attaches that to a runner, and the dashboard checks it
again against its own card.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

__all__ = ["flatten", "best_match", "english_mentions", "threshold_for",
           "Mention", "MATCH_AT", "SHORT_AT", "VERY_SHORT_AT"]

_KEEP = re.compile(r"[^A-Z0-9]")

# Measured on the 23 Sep Happy Valley preview, all nine races, against the
# real card: at these thresholds the show is heard naming 86 of its 108
# runners, and no two runners in one race have names closer to each other
# than 0.74 (FORTUNATE SON / FLYING FORTUNE), so the bar sits above the
# range where one could be read as another. Short names are the risk — five
# letters of speech match five letters of anything — so they are held
# to more: ANODE matched "and he" at 0.80.
MATCH_AT = 0.78           # names of 8 letters or more
SHORT_AT = 0.85           # 6 or 7
VERY_SHORT_AT = 0.92      # 5 or fewer


class Mention:
    """One runner, named once, somewhere in the text."""

    __slots__ = ("seg", "word", "said", "runner", "score")

    def __init__(self, seg: int, word: int, said: str, runner: dict,
                 score: float) -> None:
        self.seg, self.word, self.said = seg, word, said
        self.runner, self.score = runner, score

    def __repr__(self) -> str:
        return (f"<{self.runner.get('horse_name')} as {self.said!r} "
                f"{self.score:.2f} @seg {self.seg}>")


def flatten(text: str) -> str:
    """Letters and digits only, upper-cased: "Super Sakario" -> SUPERSAKARIO."""
    return _KEEP.sub("", (text or "").upper())


def threshold_for(name: str) -> float:
    n = len(flatten(name))
    return MATCH_AT if n >= 8 else SHORT_AT if n >= 6 else VERY_SHORT_AT


def best_match(name: str, text: str) -> tuple[float, str, int]:
    """(score, the words that matched, which word they start at).

    The run of words tried is the name's own word count, one fewer and two
    more: the recogniser splits names ("George and Sigma") and runs them into
    the next word as often as it drops one.
    """
    words = text.split()
    target = flatten(name)
    if not target or not words:
        return 0.0, "", -1
    span = max(1, len(name.split()))
    matcher = SequenceMatcher(None, target, "")
    best = (0.0, "", -1)
    for i in range(len(words)):
        for k in range(max(1, span - 1), span + 3):
            phrase = " ".join(words[i:i + k])
            if not phrase:
                continue
            matcher.set_seq2(flatten(phrase))
            score = matcher.ratio()
            if score > best[0]:
                best = (score, phrase, i)
    return best


def english_mentions(segments: list[dict], runners: list[dict],
                     *, at_least: float | None = None) -> list[Mention]:
    """Every runner named in these lines, in the order they are named.

    At most one mention per runner per line: a presenter saying a name twice
    in a sentence has still named it once.
    """
    out: list[Mention] = []
    for i, seg in enumerate(segments):
        flat = flatten(seg["text"])
        for runner in runners:
            name = runner.get("horse_name") or ""
            if not name:
                continue
            # A cheap gate before the compare: some run of the name's first
            # letters, allowing one wrong, has to be in the line at all.
            head = flatten(name)[:4]
            if not _near(head, flat):
                continue
            score, said, word = best_match(name, seg["text"])
            if score >= (at_least or threshold_for(name)) and _is_a_name(
                    name, said, seg["text"], word):
                out.append(Mention(i, word, said, runner, round(score, 3)))
    out.sort(key=lambda m: (m.seg, m.word))
    return out


# A short name can be an ordinary racing word: there is a horse called CLASS
# at Happy Valley on 23 Sep, and "class 4" is said in every race. A short
# name only counts when it is written as a name — capitalised, and not the
# label on a number.
_NUMBER_NEXT = re.compile(
    r"^(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)


def _is_a_name(name: str, said: str, text: str, word: int) -> bool:
    if len(flatten(name)) > 6 or not said:
        return True
    if not said.lstrip()[:1].isupper():
        return False
    after = text.split()[word + len(said.split()):]
    return not (after and _NUMBER_NEXT.match(after[0]))


def _near(head: str, flat: str) -> bool:
    if head in flat:
        return True
    return any(SequenceMatcher(None, head, flat[i:i + len(head)]).ratio() >= 0.75
               for i in range(0, max(1, len(flat) - len(head) + 1)))

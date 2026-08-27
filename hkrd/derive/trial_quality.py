"""How a horse went in a barrier trial, from finish and comment.

The Trials artboard states the requirement twice, and the second time as the
thing that makes the page worth having at all:

    "TRIAL QUALITY, FROM FINISH + MARGIN + COMMENT — SAME ENGINE AS THE FORM
     GUIDE'S TRIAL BAND"

    "One engine, two surfaces: the same finish + margin + comment rating used
     inline on a horse's own trial line, aggregated here as a live feed — not a
     separately curated list."

So this is one pure function, and both surfaces call it. A second rating living
in the page would drift from this one within a season.

MARGIN IS NOT IN THE SCORE, and the artboard names it. It was measured and does
not carry: holding the finishing position constant, mid-pack finishers in the
archive go 7.2% next-start wins within two lengths of the winner and 5.7% at
fourteen lengths or more, across a 7x range in margin -- a spread of one and a
half points on samples of a few hundred, which is noise. A trial winner is
often not extended, so the field's margin measures how hard the winner was
ridden more than how well the rest went. It is still SHOWN, because it is
useful context on a row; it just does not move the band.

The comment vocabulary is measured, not intuited. Every clause appearing 40+
times in the 7,622 trial comments was scored against the next actual start of
the 6,936 trials that have one, at a baseline of 8.2% next-start wins. Sixty-five
clauses were tested; fifty-one of them have a 95% interval containing the
baseline and are ignored here however they read. These fourteen are the ones
that clear it:

    improving                   n=44    27.3%  [16.3%, 41.8%]
    drew away to score          n=79    24.1%  [16.0%, 34.5%]
    led all the way to score    n=105   19.0%  [12.7%, 27.6%]
    jumped well                 n=56    16.1%  [ 8.7%, 27.8%]
    impressive                  n=100   14.0%  [ 8.5%, 22.1%]
    stayed on comfortably       n=139   15.1%  [10.1%, 22.0%]
    ---------------------------------------------- baseline 8.2%
    settled midfield            n=111    2.7%  [ 0.9%,  7.6%]
    gave ground                 n=232    2.6%  [ 1.2%,  5.5%]
    ran on ordinary             n=150    2.0%  [ 0.7%,  5.7%]
    limited response when asked n=152    2.0%  [ 0.7%,  5.6%]
    moved better than before    n=117    1.7%  [ 0.5%,  6.0%]
    failed to respond           n=141    1.4%  [ 0.4%,  5.0%]
    unimpressive                n=75     1.3%  [ 0.2%,  7.2%]
    weakened in the straight    n=79     1.3%  [ 0.2%,  6.8%]

Two of those are worth stating plainly, because a hand-written list would have
got them backwards. "Moved better than before" reads as praise and predicts
1.7% next-start wins against a baseline of 8.2% -- it is what the chart writer
says about a horse that had been going badly. And "settled midfield" reads as
neutral positional description and predicts 2.7%; in a trial, settling midfield
is what a horse does when it cannot go with them.

Separately, a trial the horse was not asked to win says nothing either way, and
that is a fact about the trial rather than a verdict on the horse. All ten
phrases in that family land ON the baseline when measured -- "not tested" 6.4%,
"under a hold" 8.0%, "own steam" 10.5%, "ridden conservatively" 7.3% -- so they
short-circuit to UNTESTED rather than letting a finish nobody was trying for be
scored down.

The bands separate, which is the only reason to show them. Measured over the
7,750 trials in the archive: STANDOUT 15.6% next-start wins, POSITIVE 13.1%,
NEUTRAL 7.8%, NEGATIVE 4.2%, UNTESTED 7.7%, against a baseline of 8.2%.
`query/trials.calibration()` recomputes that table live so the page can print
it rather than asking anyone to take the mark on trust.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["rate", "BANDS", "MARKS", "POSITIVE", "NEGATIVE", "UNTESTED",
           "positives", "negatives", "neutrals"]

# Ordinal, best first. The mark is what a row shows when there is no room for a
# word -- the artboard's own vocabulary.
BANDS = ("STANDOUT", "POSITIVE", "NEUTRAL", "NEGATIVE", "UNTESTED")
MARKS = {"STANDOUT": "++", "POSITIVE": "+", "NEUTRAL": "·",
         "NEGATIVE": "−", "UNTESTED": "/"}

# Only phrases whose 95% interval clears the 8.2% baseline. Anything that reads
# encouraging but tested inside the interval is absent on purpose.
POSITIVE = ("drew away to score", "led all the way to score",
            "stayed on comfortably", "jumped well", "improving", "impressive")

NEGATIVE = ("limited response when asked", "weakened in the straight",
            "moved better than before", "failed to respond", "ran on ordinary",
            "settled midfield", "gave ground", "unimpressive")

# A horse not asked to win. Every one of these lands on the baseline.
UNTESTED = ("not fully tested", "not tested", "just to stretch out",
            "to stretch", "under a hold", "under restraint", "own steam",
            "ridden conservatively", "needs time", "stretching out")

# Matched on word boundaries rather than as substrings, so "unimpressive" is
# not read as "impressive" and "hard ridden" is not read inside "limited
# response when hard ridden".
def _compile(vocabulary: tuple[str, ...]) -> tuple[tuple[str, re.Pattern], ...]:
    return tuple((phrase, re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])"))
                 for phrase in vocabulary)


_POS_RE = _compile(POSITIVE)
_NEG_RE = _compile(NEGATIVE)
_NEU_RE = _compile(UNTESTED)


def _hits(comment: str | None,
          patterns: tuple[tuple[str, re.Pattern], ...]) -> list[str]:
    low = (comment or "").lower()
    return [phrase for phrase, pattern in patterns if pattern.search(low)]


def positives(comment: str | None) -> list[str]:
    return _hits(comment, _POS_RE)


def negatives(comment: str | None) -> list[str]:
    return _hits(comment, _NEG_RE)


def neutrals(comment: str | None) -> list[str]:
    return _hits(comment, _NEU_RE)


def rate(*, place: int | None, field_size: int, margin: float | None = None,
         comment: str | None = None) -> dict[str, Any]:
    """One trial's quality, with the reasons it got there.

    `margin` is accepted and returned but does not enter the score -- see the
    module docstring. It is the row's context, not its verdict.

    The reasons come back with the band because a rating nobody can check is a
    rating nobody should act on. The Trials page prints them under the mark.
    """
    pos, neg, neu = positives(comment), negatives(comment), neutrals(comment)

    # A trial the horse was not asked to win says nothing about it. That
    # short-circuits before the finish is scored, so a horse held together at
    # the back is not marked down for a placing nobody was chasing.
    if neu and not pos and not neg and (place is None or place > 1):
        return {"band": "UNTESTED", "mark": MARKS["UNTESTED"], "score": 0.0,
                "reasons": [f"comment: {neu[0]}"], "margin": margin,
                "positives": pos, "negatives": neg, "neutrals": neu}

    score = 0.0
    reasons: list[str] = []
    if place == 1:
        score += 2.0
        reasons.append("won the trial")
    elif place is not None and place <= 3:
        score += 1.0
        reasons.append(f"finished {place}")
    elif place is not None and field_size and place >= field_size - 1:
        score -= 1.0
        reasons.append(f"finished {place} of {field_size}")

    # The negative vocabulary is the stronger of the two. A "weakened in the
    # straight" trial predicts 1.3% next-start wins against 24.1% for one that
    # drew away, so a bad comment can pull a placing down where a good one
    # cannot lift a tail-off.
    if pos:
        score += 1.0
        reasons.append(f"comment: {pos[0]}")
    if neg:
        score -= 1.5
        reasons.append(f"comment: {neg[0]}")

    band = ("STANDOUT" if score >= 3.0 else "POSITIVE" if score >= 1.5
            else "NEGATIVE" if score <= -1.0 else "NEUTRAL")
    return {"band": band, "mark": MARKS[band], "score": round(score, 2),
            "reasons": reasons, "margin": margin,
            "positives": pos, "negatives": neg, "neutrals": neu}

"""What a jockey or trainer's words lean towards: positive, negative or neutral.

Brett, 19 Nov 2026, after the first measurement of Racing To Win interviews:
who spoke was too thin to read (80 runners over 26 speakers), and whether they
were confident or cautious had never been recorded -- `connections_quote.stance`
was NULL on every row. This fills it, by rule and not by a language model
(the tips layer stays deterministic: no LLM spend).

WHAT A RULE CAN READ. Connections are optimistic by trade; nearly every answer
is warm. The information is in the caution -- "he'll probably need a run",
"up in class", "the thousand seems a little bit sharp for him", "a work in
progress" -- and in an outright claim -- "I'm feeling very confident", "he
should run well". So each answer is read for CUES of both kinds, by phrase,
and the stance is whichever kind it holds more of; a tie or nothing is neutral.

  * The interviewer's questions are not the speaker's words. The locator
    sometimes keeps one in an answer, so a sentence ending "?" is not read.
  * A cue is counted once per answer, however often it recurs.
  * The draw, the weight and the class are read only in sentences about THIS
    race: "last time we got the outside draw" is history, not a worry.
  * The phrases were written from the 283 interview answers stored by
    17 Nov 2026 and checked by reading them; `RULE` names this list, and a
    change to it is a new rule.

What it cannot read: sarcasm, a past race described as if it were this one,
an answer about another horse that the locator filed under this one. A stance
is the words' lean, not a judgement of the horse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["RULE", "CUES", "Reading", "read", "lean"]

RULE = "rule:stance-1"

# (label, kind, pattern). Kind "for" is a positive cue, "against" a cautious one.
_W = r"(?:really |very |pretty |quite |so |more than )?"
CUES: tuple[tuple[str, str, str], ...] = (
    # ── for ──────────────────────────────────────────────────────────────
    ("happy", "for", rf"\b{_W}(?:happy|pleased|thrilled|delighted) with\b"
                     r"|\b(?:very|really|so|extremely) (?:happy|pleased)\b|\bdelighted\b"),
    ("confident", "for", r"\bconfident\b|\bbelieve in (?:him|her)\b|\bgave (?:us|me) (?:a lot of )?confidence\b"),
    ("chance", "for", r"\b(?:good|big|great|strong|realistic|live|winning) chance\b|\bshould have a chance\b"),
    ("will run well", "for",
     rf"\b(?:should|will|'ll|would|can|could) (?:be )?(?:run|go|perform|race|finish|jump|jumping|travel|travelling|begin) "
     rf"{_W}(?:well|big|nicely|strongly|strong)\b"
     r"|\b(?:run|put up|give us|produce) a (?:big|good|nice|promising|bold|great|solid) (?:race|run|effort|show)\b"),
    ("trialled well", "for",
     r"\btri(?:a)?l+ed (?:really |very |quite )?(?:well|nicely)\b|\btrled well\b"
     r"|\b(?:good|nice|great|pleasing|strong|really good) trials?\b"
     r"|\btrials? (?:was|were|have been|has been) (?:good|great|nice|pleasing|really good|pretty good)\b"),
    ("in form", "for",
     r"\bspot[- ]on\b|\bin (?:great|good|top|peak|really good|fine|career[- ]best) (?:form|order|shape|condition|nick)\b"
     r"|\bready to (?:go|win|run|race)\b|\bfit and well\b|\bhorse is in form\b|\bin good shape\b"
     r"|\bgoing (?:very |really |pretty )?well\b|\b(?:he'?s|she'?s|is) fit\b|\blook(?:s|ing) (?:great|good|well)\b"),
    ("can win", "for",
     r"\b(?:can|could|should|will) win\b|\bcapable of winning\b|\bhard to beat\b|\bone to beat\b"
     r"|\bknocking on the door\b|\bdue a win\b|\bget it done\b|\bdo the job\b"),
    ("competitive", "for",
     rf"\b(?:be|'ll be|will be|should be) {_W}competitive\b|\bright in the finish\b|\bin the (?:mix|finish|frame)\b"
     r"|\bthereabouts\b|\bup to the task\b"),
    ("good feel", "for", r"\b(?:good|great|very good|really good|lovely|nice) feel(?:ing)?\b"
                         r"|\bfe(?:lt|els?|eling) (?:really |very )?(?:good|great)\b"),
    ("suits", "for", r"\b(?:will|should|'ll|would|might|does|to) suit\b|\bsuits (?:him|her)\b|\bshould be a positive\b"),
    ("good set-up", "for",
     r"\b(?:perfect|ideal|good|great|nice|better|lovely) (?:draw|gate|barrier|spot|position|set[- ]up)\b"
     r"|\b(?:light|lighter|lightweight|no) weight\b|\bweight (?:relief|advantage)\b"
     r"|\bdropped (?:a lot )?in (?:the )?(?:rating|ratings|class)\b|\bdown in class\b"),
    ("more to come", "for", r"\bmore to come\b|\bkeeps? improving\b|\bstill improving\b|\bon the improve\b"),

    # ── against ──────────────────────────────────────────────────────────
    ("needs a run", "against",
     r"\bneeds? (?:a|the|another) (?:run|race)\b|\b(?:in need|benefit) of (?:a|the) run\b|\btake a run or two\b"
     r"|\bimprove (?:for|from|off|with) (?:the|this) run\b|\bat (?:his|her) best (?:in|for|later)\b"),
    ("hard race", "against",
     r"\b(?:tough|hard|difficult|stiff|competitive|strong|deep) (?:race|task|ask|assignment|field|test|contest)\b"
     r"|\bagainst (?:this|that) (?:calibre|caliber|sort|class)\b"),
    ("up in class", "against",
     r"\b(?:up|rise|rising|step(?:s|ping)? up|going up|go up) in (?:class|grade)\b|\bclass rise\b"
     r"|\b(?:stronger|tougher|harder|better) (?:race|company|field|class|opposition)\b"),
    ("bad draw", "against",
     r"\b(?:got|have|has|drawn|draws?|drew|my|our|this)\b[^.]{0,20}?\b(?:bad|awkward|wide|tricky|tough|horrible|poor|outside|terrible|difficult) (?:draw|gate|barrier)\b"
     r"|\bdrawn (?:wide|badly|poorly)\b"),
    ("weight", "against",
     r"\b(?:need|needs|has|have|got|going) to carry (?:the |a )?(?:big|heavy|top) (?:weight|way)\b"
     r"|\bcarr(?:y|ies|ying) (?:top weight|13[0-5])\b|\bweight (?:is|will be) (?:a|the) (?:problem|concern|worry|issue|query)\b"
     r"|\bgives? (?:a lot of )?weight away\b"),
    ("unsure", "against",
     r"\bnot (?:too |really |quite |so )?sure\b|\bhard to (?:know|say|tell|get a guide)\b|\btime'?s going to tell\b"
     r"|\btime will tell\b|\bremains to be seen\b|\bthe question\b|\bquestion mark\b|\bquery\b"
     r"|\bi don'?t know (?:if|whether|how)\b|\bwhether (?:he|she|we) can\b|\breserve judg(?:e)?ment\b"),
    ("raw", "against",
     r"\bwork in progress\b|\bstill (?:learning|green|immature|raw|weak)\b|\bquirks?\b|\bone[- ]trick\b"
     r"|\bover[- ]?rac(?:e|es|ing)\b|\bmiss(?:es|ed|ing)? the start\b|\black of (?:gate )?speed\b"
     r"|\bbottoms? out\b|\bempties out\b|\bungenerous\b|\bnot the easiest\b|\bdifficult horse\b"),
    ("disappointed", "against", r"\bdisappoint\w*\b|\bnot ideal\b|\bbelow par\b"),
    ("trip or track", "against",
     r"\b(?:doesn'?t|won'?t|didn'?t) (?:really )?suit\b|\bnot (?:really )?suit\w*\b|\bnot (?:his|her) (?:race|trip|track|distance|conditions)\b"
     r"|\b(?:too|bit|little bit|a bit) (?:short|sharp|far)\b"),
    ("needs luck", "against",
     r"\bneeds? (?:some |a bit of |the |a )?(?:luck|pace|speed|gaps?)\b|\bif (?:things|everything|it all|the gaps?) (?:go|goes|come|comes|fall|falls|work|works)\b"
     r"|\bwhen the chips fall\b|\b(?:needs?|has|have|going) to (?:have to )?(?:improve|step up)\b"),
    ("fitness", "against",
     r"\b(?:bit|little bit|too) fresh\b|\b(?:bit|little bit) (?:underdone|big|heavy)\b|\bunderdone\b"
     r"|\bnot (?:fully|quite) (?:fit|screwed down|wound up|ready|there)\b|\bshort of (?:a run|fitness|condition|work)\b"),
    ("worry", "against",
     r"\bworr(?:y|ied|ies|ying)\b|\bconcern(?:ed|s)?\b|\bstruggl\w*\b|\b(?:a|little|bit|some) problem\b|\bnot easy\b"),
)
_COMPILED = tuple((label, kind, re.compile(rx, re.I)) for label, kind, rx in CUES)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# The draw, the weight and the class describe a race. In a sentence about an
# earlier one ("last time we got the outside draw") they describe that race,
# not this, so those cues are not read there.
_THIS_RACE = frozenset({"good set-up", "bad draw", "weight", "up in class"})
_PAST = re.compile(r"\blast (?:time|start|run|season|year|preparation|prep)\b|\bprevious(?:ly)?\b", re.I)


@dataclass(frozen=True)
class Reading:
    stance: str                    # positive | negative | neutral
    cues_for: tuple[str, ...]
    cues_against: tuple[str, ...]


def read(text: str | None) -> Reading:
    """The lean of one answer. Questions inside it are not read."""
    said = [s for s in _SENTENCE.split(text or "") if not s.rstrip().endswith("?")]
    now = [s for s in said if not _PAST.search(s)]
    pro: list[str] = []
    con: list[str] = []
    for label, kind, rx in _COMPILED:
        where = now if label in _THIS_RACE else said
        if any(rx.search(s) for s in where):
            (pro if kind == "for" else con).append(label)
    return Reading(lean(len(pro), len(con)), tuple(pro), tuple(con))


def lean(n_for: int, n_against: int) -> str:
    """Whichever kind of cue there is more of; a tie or none is neutral. The
    same rule reads one answer and a whole interview (summed cues)."""
    if n_for > n_against:
        return "positive"
    if n_against > n_for:
        return "negative"
    return "neutral"

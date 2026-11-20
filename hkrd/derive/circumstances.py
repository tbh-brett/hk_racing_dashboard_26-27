"""What the race record says happened to a runner, in the banker review's words.
Pure functions: a run's two texts in, the circumstances they name out.

Asked for on 2026-11-20: the owner picks a banker wrong sometimes, and
sometimes circumstances decide the race, and he wants both kept track of,
going back over bankers already run. He names the circumstances himself on the
review (`jobs/review_banker`). This reads five of the same eight out of the
stewards' report and the comments on running, without him, so that the review
can show beside each banker what the record backs, and the summary can say how
often his "circumstances" verdicts are ones the record supports.

    start    awkwardly or slowly away, stumbled         the tagger
    traffic  held up, checked, crowded, hampered,       book_candidates.is_traffic,
             no clear running -- unless it was          the blackbook's own rule,
             racing keenly in that sentence             plus no clear running
    wide     without cover, three or four wide          book_candidates.WIDE_TRIP
    keen     raced keenly, over-raced, pulled hard      book_candidates.KEEN
    health   bled, lame, irregular heart, roarer;       the tagger's named vet
             saddle slipped, plate lost                 findings, EQUIPMENT
    ride, pace, bias                                    his to say: no text
                                                        reads them reliably

ONE DEFINITION. The traffic, wide, keen and gear readings are the blackbook's
(`derive/book_candidates`), so a run the book calls held up is held up here.
The one addition is "no clear running": the model and the book leave it out
because counting it measured worse as a FORECAST (`no-clear-running-tag`), and
this is not a forecast. It is the record of what happened to a horse that was
already backed.

A circumstance is not a verdict. A horse can be held up and still have been the
wrong pick, and KEEN in particular is often the horse's own doing. The review
shows what the record says; the owner decides what it cost.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from hkrd.derive import tags as tagger
from hkrd.derive.book_candidates import EQUIPMENT, KEEN, WIDE_TRIP, is_traffic
from hkrd.store.coerce import is_no_comment

__all__ = ["CIRCUMSTANCES", "LABEL", "FROM_RECORD", "read"]

# (key, label). The order the review lists them in.
CIRCUMSTANCES: tuple[tuple[str, str], ...] = (
    ("start", "Missed the start or slow away"),
    ("traffic", "Held up, checked or hampered"),
    ("wide", "Wide without cover"),
    ("keen", "Raced keenly"),
    ("ride", "The ride: tactics or an error"),
    ("pace", "The pace went against it"),
    ("bias", "Track or draw bias"),
    ("health", "Gear, injury or bleeding"),
)
LABEL = dict(CIRCUMSTANCES)

# The ones the record can say on its own.
FROM_RECORD = frozenset({"start", "traffic", "wide", "keen", "health"})

_QUOTE_MAX = 160


def _sentences(text: str | None) -> list[str]:
    if not text or not text.strip() or is_no_comment(text):
        return []
    return [s.strip() for s in re.split(r"(?<=\.)\s+", text.strip()) if s.strip()]


def _quote(source: str, sentence: str) -> str:
    s = sentence.strip()
    if len(s) > _QUOTE_MAX:
        s = s[:_QUOTE_MAX - 1].rstrip(" ,;") + "…"
    return f"{source}: “{s}”"


def _found(sentence: str) -> set[str]:
    names = {t.name for t in tagger.tag_comment(sentence)}
    out: set[str] = set()
    if names & {"awkwardly_away", "stumbled"}:
        out.add("start")
    if (is_traffic(sentence, names)
            or ("no_clear_running" in names and not KEEN.search(sentence))):
        out.add("traffic")
    if WIDE_TRIP.search(sentence):
        out.add("wide")
    if KEEN.search(sentence):
        out.add("keen")
    if names & tagger.NAMED_VET or EQUIPMENT.search(sentence):
        out.add("health")
    return out


@lru_cache(maxsize=32768)
def _read(incident: str | None, corunning: str | None) -> tuple[tuple[str, str], ...]:
    # A run's texts do not change once published, and the tagger is most of
    # the cost of a review page (200 of 320 ms over every banker since April,
    # measured 20 Nov 2026), so each pair of texts is read once per process.
    texts = ([("Stewards", s) for s in _sentences(incident)]
             + [("Running", s) for s in _sentences(corunning)])
    first: dict[str, str] = {}
    for source, sentence in texts:
        for key in _found(sentence):
            first.setdefault(key, _quote(source, sentence))
    return tuple((k, first[k]) for k, _ in CIRCUMSTANCES if k in first)


def read(incident: str | None, corunning: str | None) -> list[dict[str, Any]]:
    """[{key, label, quote}] for each circumstance the record names, in the
    review's order, each quoting the first sentence that says it -- the
    stewards' report before the comments on running, because theirs is the
    account the rider was questioned on."""
    return [{"key": k, "label": LABEL[k], "quote": q} for k, q in _read(incident, corunning)]

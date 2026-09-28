"""Which runs of a meeting are worth a blackbook entry, and why. Pure functions.

The automatic blackbook reads a meeting once its results are complete and
writes the few runs that ran better than they finished. It uses the owner's own
reasons, each read off data the dashboard already holds:

    reason                          read from                        owner's tag
    held up, checked, crowded —     stewards' report, comments on    traffic
    unless it was racing keenly     running
    wide without cover, 3-4 wide    the same two texts               bad_run
    saddle slipped, plate lost      stewards' report                 bad_run
    fastest closing section, or     sectionals, running positions    final_sectional
    far back at the turn and closing
    slow away and still close       stewards' report                 slow_start
    ran well above its price        the closing tote                 exceptional_performer
    drawn 9 or wider, and one of    the draw                         bad_draw
    the two trips above

WHAT IT IS FOR, measured 2026-09-28 on 607 meetings and the owner's 155
entries (docs/auto-book.md has the tables). A first cut of these rules found 59
of the 149 entries written off a race. Taken one at a time, NONE of them beats
the closing tote on the horse's next start: win A/E 0.87-0.97, and 0.86 for
runs with two reasons or more, because the market reads the same stewards'
report and a hard-luck story it can see is overbet. So this is not a tip list.
It does the trawl through the results, it never misses one, and it books in
the owner's vocabulary so each reason gets a mechanical record to set the
owner's own picks against.

Trials are the exception. A trial rated STANDOUT (`derive/trial_quality`) went
on to win 1.36x what the closing tote expected next start, n=225, +/-0.35.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from hkrd.derive import tags as tagger
from hkrd.store.coerce import parse_running_positions

__all__ = ["DERIVE_VERSION", "RESULTS_CAP", "TRIALS_CAP", "Evidence",
           "Candidate", "race_candidates", "pick", "trial_candidates",
           "margin_text"]

DERIVE_VERSION = "book-1.0"

# The most entries one meeting, or one trial day, may write. Every rule
# together fires on about 29 runners a meeting; of the runs each one flagged
# over April to July 2026, the owner booked roughly one in ten.
RESULTS_CAP = 6
TRIALS_CAP = 6

# Runs this far behind the winner are not "ran better than it finished". The
# closing figure gets more room because a fast finish from last is exactly the
# run that ends up beaten a few lengths.
_CLOSE_LBW = 6.0
_BACK_LBW = 4.0
_TROUBLE_LBW = 6.0
_WIDE_LBW = 5.0
_SLOW_LBW = 4.0
# Ran well above its price: 20.0 or longer, and placed or within 2.5 lengths.
_LONG_ODDS = 20.0
_OUTRAN_LBW = 2.5
# "Drawn wide", in the owner's own entries: gates 9, 10, 11, 12 and 14 are all
# described as a wide draw there.
_WIDE_GATE = 9

_TRAFFIC = frozenset({"held_up", "checked", "short_of_room", "hampered",
                      "crowded", "steadied"})
# A trip spent covering ground. `wide` alone is not it: the word turns up in
# a fifth of all comments ("shifted wide", "wide on the turn").
_WIDE_TRIP = re.compile(r"\bwithout cover\b|\bno cover\b|"
                        r"\b(?:three|four|3|4)[- ](?:wide|deep)\b", re.I)
# Trouble in the straight costs a finishing position; trouble at the 1000m
# costs a position in the run. The first is what the owner books.
_LATE = re.compile(r"\bstraight\b|\bfinal\b|\bconcluding\b|\bclosing stages\b|"
                   r"\bhome\b|\b[1-4]00\s*metres\b", re.I)
# A horse pulling for its head and being steadied off the heels in front
# brought the trouble on itself. 388 of 18,867 stewards' reports (2.1%) put
# the two in one sentence. On 2026-09-27 two of the first cut's six picks were
# exactly that — "commenced to race keenly and shifted out when being steadied"
# — and neither was a run that hid its form.
_KEEN = re.compile(r"\bkeen(?:ly)?\b|\bover[- ]?rac(?:ed|ing)\b|"
                   r"\bpull(?:ed|ing) hard\b", re.I)
# Gear that failed during the run: a saddle that slipped, a plate or shoe lost,
# an iron lost. Not in the tag vocabulary, and rare — 12 saddles and 124
# plates in 18,867 reports — but it is the whole story when it happens. THE
# BOOM BOX on 2026-09-27: "his saddle shifted back, placing him at a
# disadvantage throughout the early and middle stages", then the fastest
# closing section in the race from last at the turn.
_EQUIPMENT = re.compile(
    r"\bsaddle\b[^.]{0,40}\b(?:shifted|slipped|moved|displaced)\b|"
    r"\b(?:shifted|slipped)\b[^.]{0,20}\bsaddle\b|"
    r"\blost (?:a|an|its|his|her|the)?\s*(?:\w+\s+){0,2}(?:plate|shoe|iron)s?\b|"
    r"\bspread a plate\b|\bbit (?:slipped|through)\b|\breins? (?:broke|snapped)\b",
    re.I)
_QUOTE_MAX = 180


@dataclass(frozen=True)
class Evidence:
    kind: str                       # closing | traffic | wide | slow_start |
                                    # equipment | outran_price
    text: str                       # what the entry says about it
    tags: tuple[str, ...]           # in the owner's vocabulary
    late: bool = False              # trouble in the straight


@dataclass
class Candidate:
    """One run worth booking, with everything its entry is written from."""

    source_date: str
    source_no: int                  # race number, or trial batch number
    horse_name: str
    evidence: list[Evidence]
    finish: str                     # "6th of 12, beaten 2L at 3.0"
    horse_no: int | None = None
    lbw: float | None = None
    odds: float | None = None
    score: float = 0.0              # trials: the trial-quality score
    extra_tags: tuple[str, ...] = ()
    kind: str = "results"           # results | trials

    @property
    def kinds(self) -> list[str]:
        return sorted({e.kind for e in self.evidence})

    @property
    def tags(self) -> list[str]:
        return sorted({t for e in self.evidence for t in e.tags}
                      | set(self.extra_tags))

    @property
    def confidence(self) -> str:
        """Two separate things went against it, or one."""
        return "medium" if len(self.kinds) >= 2 or self.kind == "trials" else "low"

    def sort_key(self) -> tuple:
        """More separate reasons first, then trouble in the straight, then the
        longer price.

        Read off the owner's own choices, not fitted to a result: no ordering
        of these reasons beat the tote, so there is nothing to fit one to.
        Over April-July 2026 the owner booked 7% of the runs these rules
        flag for one reason, 14% for two and 27% for three; 14% of those with
        trouble in the straight against 8% without; and 20% of those at 50.0
        or longer against 6-9% below it. The margin, which the first cut
        sorted on, made no difference (8-12% at every distance)."""
        return (-len(self.kinds), -self.score,
                -int(any(e.late for e in self.evidence)),
                -(self.odds or 0.0),
                self.source_no, self.horse_no or 0, self.horse_name)

    def reasoning(self, *, basis: str = "full") -> str:
        text = f"{self.finish}. " + " ".join(e.text for e in self.evidence)
        if basis != "full":
            text += " (Comments on running not yet published.)"
        return text


# ── results ──────────────────────────────────────────────────────────────────

def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def margin_text(lbw: float | None) -> str:
    """Lengths as a racecaller says them: 'a neck', '1¾L'."""
    if lbw is None:
        return "no margin recorded"
    for limit, name in ((0.05, "a nose"), (0.10, "a short head"),
                        (0.20, "a head"), (0.30, "a neck")):
        if lbw <= limit:
            return name
    whole = int(lbw)
    quarter = round((lbw - whole) * 4) / 4
    if quarter == 1.0:
        whole, quarter = whole + 1, 0.0
    frac = {0.0: "", 0.25: "¼", 0.5: "½", 0.75: "¾"}[quarter]
    return f"{whole or ''}{frac}L"


def _odds_text(odds: float | None) -> str:
    """As the tote board shows it: 3.0, 9.1, 22, 78."""
    if not odds:
        return "no price"
    return f"{odds:.1f}" if odds < 10 else f"{odds:.1f}".removesuffix(".0")


def _sentences(text: str | None) -> list[str]:
    """Stewards write sentences; the comments on running write clauses. A
    reason quotes the one that says it, not the whole report."""
    if not text or not text.strip():
        return []
    return [s.strip() for s in re.split(r"(?<=\.)\s+", text.strip()) if s.strip()]


def _quote(source: str, sentence: str) -> str:
    s = sentence.strip()
    if len(s) > _QUOTE_MAX:
        s = s[:_QUOTE_MAX - 1].rstrip(" ,;") + "…"
    return f"{source}: “{s}”"


def _texts(run: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, sentence) pairs, stewards first — theirs is the account the
    rider was questioned on."""
    out = [("Stewards", s) for s in _sentences(run.get("incident"))]
    running = run.get("corunning")
    if running and running.strip():
        out.append(("Running", running.strip()))
    return out


def _is_traffic(sentence: str) -> bool:
    hit = {t.name for t in tagger.tag_comment(sentence)} & _TRAFFIC
    if not hit:
        return False
    # Its own doing. Only the SENTENCE is read, so a horse keen early and held
    # up in the straight — two sentences — still counts for the second.
    if _KEEN.search(sentence):
        return False
    # "restrained" and "taken back" are how a rider settles a horse, not
    # trouble it met. Only the words themselves count for those two.
    if hit == {"held_up"}:
        return bool(re.search(r"\bheld up\b", sentence, re.I))
    if hit == {"steadied"}:
        return bool(re.search(r"\bsteadied\b", sentence, re.I))
    return True


def _has_vet_finding(texts: list[tuple[str, str]]) -> bool:
    return any(t.kind == "vet" for _, s in texts for t in tagger.tag_comment(s))


def _turn_position(positions: object) -> int | None:
    """The last call before the finish."""
    try:
        calls = parse_running_positions(positions)
    except ValueError:
        return None
    return calls[-2] if len(calls) >= 2 else None


def _late_ranks(runners: Sequence[dict[str, Any]]) -> dict[int, int]:
    """1 = fastest closing section in the race. `late_dev` is against the
    race median, negative is faster (`derive/pace`)."""
    timed = sorted((r["late_dev"], r["horse_no"]) for r in runners
                   if r.get("late_dev") is not None)
    ranks: dict[int, int] = {}
    for i, (dev, no) in enumerate(timed):
        prev = timed[i - 1][0] if i else None
        ranks[no] = ranks[timed[i - 1][1]] if prev == dev else i + 1
    return ranks


def _evidence(run: dict[str, Any], *, field_size: int, late_rank: int | None,
              texts: list[tuple[str, str]]) -> list[Evidence]:
    place = run["place"]
    lbw = run.get("lengths_behind")
    lbw = 99.0 if lbw is None else float(lbw)
    draw = run.get("draw")
    wide_gate = draw is not None and draw >= _WIDE_GATE
    gate = ("bad_draw",) if wide_gate else ()
    out: list[Evidence] = []

    # Closing: the fastest two closing sections in the race, or far back at
    # the turn and passing four or more runners in the straight.
    turn = _turn_position(run.get("running_positions"))
    back = max(8, math.ceil(field_size * 0.7))
    passed = (turn - place) if turn else 0
    if late_rank is not None and late_rank <= 2 and lbw <= _CLOSE_LBW:
        text = ("Fastest closing section in the race" if late_rank == 1
                else "Second-fastest closing section in the race")
        if turn and passed >= 2:
            text += f", from {_ordinal(turn)} at the turn"
        out.append(Evidence("closing", text + ".", ("final_sectional", *gate)))
    elif turn and turn >= back and passed >= 4 and lbw <= _BACK_LBW:
        out.append(Evidence(
            "closing", f"From {_ordinal(turn)} at the turn, passed {passed} "
            "runners in the straight.", ("final_sectional", *gate)))

    if lbw <= _TROUBLE_LBW:
        hits = [(src, s) for src, s in texts if _is_traffic(s)]
        if hits:
            late = [h for h in hits if _LATE.search(h[1])]
            src, s = (late or hits)[0]
            out.append(Evidence("traffic", _quote(src, s),
                                ("traffic",), late=bool(late)))

    if lbw <= _WIDE_LBW:
        wide = [(src, s) for src, s in texts if _WIDE_TRIP.search(s)]
        if wide:
            src, s = wide[0]
            out.append(Evidence("wide", _quote(src, s), ("bad_run", *gate)))

    if lbw <= _SLOW_LBW:
        slow = [(src, s) for src, s in texts
                if any(t.name == "awkwardly_away" for t in tagger.tag_comment(s))]
        if slow:
            src, s = slow[0]
            out.append(Evidence("slow_start", _quote(src, s), ("slow_start",)))

    if lbw <= _TROUBLE_LBW:
        gear = [(src, s) for src, s in texts if _EQUIPMENT.search(s)]
        if gear:
            src, s = gear[0]
            out.append(Evidence("equipment", _quote(src, s), ("bad_run",)))

    odds = run.get("win_odds")
    if odds and odds >= _LONG_ODDS and (place <= 3 or lbw <= _OUTRAN_LBW):
        out.append(Evidence(
            "outran_price", f"Ran well above its price of {_odds_text(odds)}.",
            ("exceptional_performer",)))
    return out


def race_candidates(runners: Sequence[dict[str, Any]]) -> list[Candidate]:
    """Every run in one race that clears a rule.

    Each runner needs race_date, race_no, horse_no, horse_name, place,
    lengths_behind, draw, win_odds, running_positions and late_dev, and may
    carry `incident` and `corunning` text.

    Never a winner — a win is not form the market can miss — and never a run
    with a veterinary finding, whose next start is a question about the horse's
    health rather than about its trip.
    """
    field_size = len(runners)
    ranks = _late_ranks(runners)
    out: list[Candidate] = []
    for run in runners:
        place = run.get("place")
        if place is None or place < 2:
            continue
        texts = _texts(run)
        if _has_vet_finding(texts):
            continue
        found = _evidence(run, field_size=field_size,
                          late_rank=ranks.get(run["horse_no"]), texts=texts)
        if not found:
            continue
        lbw = run.get("lengths_behind")
        finish = (f"{_ordinal(place)} of {field_size}, beaten "
                  f"{margin_text(lbw)} at {_odds_text(run.get('win_odds'))}")
        if run.get("draw") and run["draw"] >= _WIDE_GATE:
            finish += f", drawn {run['draw']}"
        out.append(Candidate(
            source_date=run["race_date"], source_no=run["race_no"],
            horse_name=run["horse_name"], horse_no=run["horse_no"],
            evidence=found, finish=finish, odds=run.get("win_odds"),
            lbw=None if lbw is None else float(lbw)))
    return out


def pick(candidates: Iterable[Candidate], cap: int) -> list[Candidate]:
    """The top of the list, one entry per horse."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in sorted(candidates, key=Candidate.sort_key):
        if c.horse_name in seen:
            continue
        seen.add(c.horse_name)
        out.append(c)
        if len(out) == cap:
            break
    return out


# ── trials ───────────────────────────────────────────────────────────────────

def trial_candidates(rated: Sequence[dict[str, Any]]) -> list[Candidate]:
    """Trials rated STANDOUT, from rows `query/trials` has already rated with
    `derive/trial_quality` — the one engine every trial surface uses."""
    out: list[Candidate] = []
    for r in rated:
        if r.get("quality_band") != "STANDOUT":
            continue
        place = r.get("place")
        where = f"trial {r['trial_no']}" + (f" at {r['venue']}" if r.get("venue") else "")
        finish = (f"Won {where}" if place == 1 else
                  f"{_ordinal(place)} of {r.get('field_size')} in {where}"
                  if place else f"Ran in {where}")
        comment = (r.get("comment") or "").strip()
        text = "Rated STANDOUT." + (f" {_quote('HKJC', comment)}" if comment else "")
        out.append(Candidate(
            source_date=r["trial_date"], source_no=r["trial_no"],
            horse_name=r["horse_name"], finish=finish, kind="trials",
            evidence=[Evidence("trial", text, ("trial",))],
            score=float(r.get("quality_score") or 0.0)))
    return out

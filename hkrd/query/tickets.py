"""What a ticket costs. Pure arithmetic — no database, no I/O.

Design brief 06 Part 2 puts the combination count in front of the confirm
button because "betlines multiply faster than intuition tracks", and it is the
number that turns an intended small bet into a large one. Everything that
multiplies lives here, in one place, so the figure on the entry screen and the
figure the importer reconciles a statement against cannot disagree.

Two multiplications, and the second is the one that used to be missing.

**Within a race.** A pair pool takes two runners a line, so four selections is
C(4,2) = 6 and a banker plus four legs is 4. On top of that HKJC sells two
COMBINATION tickets — WP is a win and a place on the same horse, QQP a quinella
and a quinella place on the same pair — where the selections are identical and
only the settlement differs, so one line is struck into two pools and costs
twice. Checkable against a real statement: 2026-09-06 ref 3597 is a QQP, banker
with two others, at $10 — two lines, two pools, $40 debited.

**Across races.** An All Up is not "C(n, r)". HKJC's formula names WHICH
multiples the ticket buys: 4x11 is every double, every treble and the quadruple
over four legs — 6 + 4 + 1 = 11 — and the code IS that total. The lines are
then the product of each leg's own combination count, summed over every
included multiple, which is where the two multiplications meet. Checkable
against the same statement: ref 3593 is a 2X1 whose first leg is a QQP banker
with four (4 pairs x 2 pools = 8) and whose second is a single place (1), so
8 x 1 = 8 lines at $20 = $160, which is what was debited.

THE FORMULA TABLE IS GENERATED, NOT TYPED IN. Every formula HKJC offers over n
legs is a CONTIGUOUS range of multiple sizes — "doubles through quadruples",
never "singles and trebles but not doubles" — excluding singles alone, which is
not a chain but n separate bets. Where two ranges come to the same total the
lower one is the one HKJC lists. That rule reproduces the published table
exactly for two, three, four and five legs, which is the whole of it that was
checked against, and it extends to six without anyone having to transcribe a
row.
"""
from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any

__all__ = ["SINGLE_RACE_TYPES", "BET_TYPES", "PAIR_TYPES", "pools_per_ticket",
           "combination_count", "lines_for", "formula_text", "allup_formulas",
           "allup_formula", "allup_lines", "MULTIPLE_NAMES", "MAX_ALLUP_LEGS"]

SINGLE_RACE_TYPES: tuple[str, ...] = ("WIN", "PLACE", "WP", "QIN", "QPL", "QQP")
BET_TYPES: tuple[str, ...] = SINGLE_RACE_TYPES + ("ALLUP",)

# The pools that take a PAIR of runners rather than one.
PAIR_TYPES: frozenset[str] = frozenset({"QIN", "QPL", "QQP"})

# A pair pool takes two runners per line; WIN and PLACE take one.
_PICKS_PER_LINE = {"WIN": 1, "PLACE": 1, "WP": 1, "QIN": 2, "QPL": 2, "QQP": 2}

# The two HKJC sells as one ticket into two pools. See the module docstring.
_POOLS_PER_TICKET = {"WP": 2, "QQP": 2}

# HKJC's All Up tops out at six legs.
MAX_ALLUP_LEGS = 6

MULTIPLE_NAMES = {1: "single", 2: "double", 3: "treble", 4: "quadruple",
                  5: "quintuple", 6: "sextuple"}


def pools_per_ticket(bet_type: str) -> int:
    """How many pools one line of this ticket is struck into."""
    return _POOLS_PER_TICKET.get(bet_type.upper(), 1)


def combination_count(bet_type: str, n_selected: int, *,
                      has_banker: bool = False) -> int:
    """Lines on a single-race ticket.

    Design brief 07 §3.3 gives the table this must reproduce: four picks with
    no banker is C(4,2) = 6, five is 10, six is 15; a banker plus four legs is
    4. A banker appears in every combination, so it multiplies rather than
    combines.
    """
    per_line = _PICKS_PER_LINE.get(bet_type.upper())
    if per_line is None:
        raise ValueError(f"not a single-race bet type: {bet_type!r}")
    if n_selected < 0:
        raise ValueError("selection count cannot be negative")
    pools = pools_per_ticket(bet_type)
    if per_line == 1:
        return n_selected * pools
    if has_banker:
        # The banker is the anchor; each remaining selection forms one line
        # with it. n_selected counts the legs, not the banker.
        return n_selected * pools
    return (comb(n_selected, 2) if n_selected >= 2 else 0) * pools


def lines_for(kind: str, picks: list[int],
              banker: int | None) -> list[tuple[int, ...]]:
    """The actual combinations a ticket buys, so the count can be checked.

    One entry per SELECTION, not per pool: a QQP line is one pair struck twice,
    and listing it twice would read as two different pairs.
    """
    if kind.upper() not in PAIR_TYPES:
        base = ([banker] if banker is not None else []) + picks
        return [(p,) for p in base]
    if banker is not None:
        return [(banker, p) for p in picks]
    return [tuple(c) for c in combinations(picks, 2)]


def formula_text(kind: str, n: int, banker: int | None) -> str | None:
    if kind.upper() not in PAIR_TYPES:
        return None
    if banker is not None:
        return f"banker + {n} leg{'s' if n != 1 else ''}"
    return f"C({n},2)" if n >= 2 else None


# ── all up ──────────────────────────────────────────────────────────────────

def _multiple_label(size: int, count: int) -> str:
    name = MULTIPLE_NAMES.get(size, f"{size}-leg")
    return f"{count} {name}{'' if count == 1 else 's'}"


def allup_formulas(n_legs: int) -> list[dict[str, Any]]:
    """Every All Up formula HKJC offers over this many legs, cheapest first.

    Generated from the contiguous-range rule in the module docstring, which
    reproduces the published table exactly. Each entry carries the code the
    statement will show ("4x11"), which multiples it buys, and how many lines
    that is when every leg holds a single selection — which is the number in
    the code, and the number people mean by "how many bets".
    """
    if n_legs < 2 or n_legs > MAX_ALLUP_LEGS:
        return []
    by_total: dict[int, dict[str, Any]] = {}
    for lo in range(1, n_legs + 1):
        for hi in range(lo, n_legs + 1):
            if lo == hi == 1:
                # Singles alone is n separate bets, not a chain.
                continue
            sizes = list(range(lo, hi + 1))
            counts = [comb(n_legs, k) for k in sizes]
            total = sum(counts)
            # Two ranges can come to the same total — over five legs, doubles
            # alone and trebles alone are both 10. HKJC lists the lower one, so
            # the first sighting wins and later ones are dropped.
            if total in by_total:
                continue
            by_total[total] = {
                "code": f"{n_legs}x{total}",
                "n_legs": n_legs,
                "sizes": sizes,
                "combinations": total,
                "breakdown": [{"size": k, "count": c,
                               "name": MULTIPLE_NAMES.get(k, f"{k}-leg")}
                              for k, c in zip(sizes, counts)],
                "label": " + ".join(_multiple_label(k, c)
                                    for k, c in zip(sizes, counts)),
                # The plain-language version of what has to come in. A range
                # starting at the top is "every leg must win"; one starting
                # lower pays on part of the chain too.
                "min_correct": lo,
            }
    return [by_total[t] for t in sorted(by_total)]


def allup_formula(n_legs: int, code: str | None) -> dict[str, Any] | None:
    """One formula by its HKJC code, or None if it is not one of them.

    Accepts "4x11" or bare "11". Returns None rather than guessing: a code that
    does not exist for this many legs is a ticket that cannot be struck, and
    the interface says so instead of quietly pricing a different one.
    """
    if not code:
        return None
    wanted = str(code).strip().lower()
    tail = wanted.split("x")[-1]
    for f in allup_formulas(n_legs):
        if wanted == f["code"].lower() or tail == str(f["combinations"]):
            return f
    return None


def allup_lines(sizes: list[int], per_leg: list[int]) -> int:
    """Unit bets on an All Up whose legs hold this many combinations each.

    The sum, over every included multiple size, of the product of the legs in
    each selection of that size. With one combination per leg it collapses to
    the formula's own number; with a QQP banker-with-four in a leg (8) it does
    not, and that is exactly the case an unmultiplied count under-quotes by a
    factor of eight.
    """
    counts = [max(0, int(c)) for c in per_leg]
    if not counts or any(c == 0 for c in counts):
        return 0
    total = 0
    for size in sizes:
        if size > len(counts):
            continue
        for chosen in combinations(range(len(counts)), size):
            product = 1
            for i in chosen:
                product *= counts[i]
            total += product
    return total

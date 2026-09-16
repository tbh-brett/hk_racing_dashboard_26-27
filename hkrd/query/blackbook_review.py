"""When a blackbook thesis has had its chances — the prompt, never the verdict.

Carved out of `query/blackbook` at the 600-line cap, the same seam
`query/blackbook_band` was cut on. It earns a module of its own for a better
reason than arithmetic, though: this is the one rule in the book that decides
something ABOUT an entry rather than reporting what happened to it, and the
last version of that idea — a ninety-day expiry — closed 147 of the owner's 179
entries before anybody looked at them. A rule with that much reach should be
readable in one screen, with the measurements it was chosen from beside it.
"""
from __future__ import annotations

from typing import Any

__all__ = ["review_reason", "REVIEW_RUNS", "REVIEW_TOP", "REVIEW_UNTESTED_DAYS"]


# ── when a thesis has had its chances ────────────────────────────────────────
#
# A book that only grows is unusable within a season, so something has to say
# which entries are owed a verdict. What that something must NOT do is take the
# verdict itself: this is a prompt on a row, next to the button that closes it.
#
# The thresholds are measured against the owner's own 179 entries and the runs
# they have had since (2026-09-16):
#
#   runs a booked horse gets within 90 days   median 2, mean 2.3, max 5
#   of the 156 entries that have run since,   47% have never placed
#   entries with nothing after 3 runs          26% placed later
#                     ...    after 4 runs      24% placed later
#                     ...    after 5 runs      20% placed later (n=5)
#   days from booking to the first place      median 26, 90th 90, max 150
#
# Three things follow, and they are why this counts runs rather than days.
#
# A NINETY-DAY CLOCK IS A TWO-RUN CLOCK. That is the median a booked horse
# actually gets in the window, and at three runs a QUARTER of the theses it
# would close go on to deliver. The old expiry was not a patient rule that
# needed tuning; it was an impatient one wearing a calendar.
#
# A RUN THAT DID NOT MEET THE CONDITIONS ASKED NOTHING. A horse booked for
# 1200m and beaten four times at 1650m has not failed its thesis, so those runs
# cannot count towards retiring it. This is the whole reason `blackbook_trigger`
# exists, and the prompt is the first thing that would be wrong without it.
#
# AND "GOOD ENOUGH TO KEEP WATCHING" IS WIDER THAN THE PAYOUT. A place pays; a
# fifth of fourteen, beaten a length, is the run that says the reason is real
# and the day was not. 69% of entries record a top-five within five qualifying
# runs, and of the entries with five qualifying runs and nothing in the top
# five, none have since placed.
REVIEW_RUNS = 5
REVIEW_TOP = 5

# The other way a thesis stops earning its place: it never gets its race. Every
# entry in the archive that eventually placed did so within 150 days of being
# booked, so at 180 the silence is the answer. This one is not a failure of the
# horse — it is a prompt to ask whether the condition was ever realistic.
REVIEW_UNTESTED_DAYS = 180


def review_reason(entry: dict[str, Any]) -> str | None:
    """Why this entry is owed a verdict, in the words the row prints. Or None.

    Only ever a prompt. Nothing in this codebase closes an entry: the one thing
    that used to — a ninety-day expiry — is what `store/connect._migrate` was
    written to take out, and putting a smarter clock back in its place would be
    the same mistake with better arithmetic.
    """
    if entry["status"] != "active":
        return None                     # a closed thesis awaits nothing
    conditional = bool(entry.get("conditions"))
    on_runs = entry["runs_on_conditions"]
    if on_runs >= REVIEW_RUNS and not entry["top_on_conditions"]:
        where = " on its conditions" if conditional else ""
        return (f"{on_runs} runs{where}, none in the top {REVIEW_TOP}")
    age = entry.get("age_days") or 0
    if on_runs == 0 and age >= REVIEW_UNTESTED_DAYS:
        return (f"booked {int(age)} days ago and has not run its conditions yet"
                if conditional else
                f"booked {int(age)} days ago and has not run since")
    return None

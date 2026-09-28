"""Whose book a figure is about: the owner's picks, the system's, or both.

The automatic blackbook (`jobs/auto_book`) writes into the same table the
owner books into, because a system entry has to reach every page an owner's
entry does — the Briefing, the Race Day band, the Form Guide. What it must not
do is count as the owner's judgement. A season of system entries is several
hundred rows against the owner's 155, so a record read over both would be the
system's record with the owner's picks mixed into it, and BACKED vs MISSED
would count every system horse the owner never meant to back as a miss.

So each figure that measures the owner says which book it is over, through
this one clause:

  owner    the entries the owner booked, and the system entries they adopted
  system   everything the system booked, adopted or not. Adopting a horse does
           not take it out of the system's record: the system's record is the
           control group for the owner's own picks, and it has to stay whole
  all      both
"""
from __future__ import annotations

__all__ = ["BOOKS", "clause", "check"]

BOOKS = ("owner", "system", "all")


def check(book: str | None) -> str:
    """The book asked for, or the owner's when none was named."""
    book = (book or "owner").strip().lower()
    if book not in BOOKS:
        raise ValueError(f"book must be one of {', '.join(BOOKS)}, not {book!r}")
    return book


def clause(book: str | None = "owner", alias: str = "b") -> str:
    """A SQL condition on the `blackbook` row aliased `alias`."""
    book = check(book)
    if book == "owner":
        return f"({alias}.origin = 'owner' OR {alias}.adopted_date IS NOT NULL)"
    if book == "system":
        return f"({alias}.origin = 'system')"
    return "(1 = 1)"

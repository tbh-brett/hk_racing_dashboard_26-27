"""The clock the notes and the blackbook stamp their writes with.

`write_notes` stamps when a note was written, and `write_book` when an entry
was booked, closed, adopted or renewed, and when its status changed. Both take
every stamp from `_now` and `_today` here, and both of those read `_clock`, so
a test stops the time for the two of them at once:

    monkeypatch.setattr(clock, "_clock", lambda: at)

`scrape_track_weather` reads `_hk_now` for the same reason, and decides from it
whether today's meeting is on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Hong Kong, whose calendar every race date is on: UTC+8 all year round.
_HK = timezone(timedelta(hours=8))


def _clock() -> datetime:
    """Now, in UTC: the one read of the time here, so a test can stop it."""
    return datetime.now(timezone.utc)


def _now() -> str:
    return _clock().isoformat(timespec="seconds")


def _today() -> str:
    """Today in Hong Kong, the calendar the race dates are on: a day stamped
    here is read beside them, so it has to mean the same day they do.

    Not SQLite's `date('now')`, which is UTC whatever the machine is set to:
    an entry retired at 01:00 after a Wednesday night meeting was dated the
    Wednesday, and read as already closed over the card it was followed on.
    Not `date.today()` either, which is only right where TZ is Hong Kong.
    """
    return _clock().astimezone(_HK).date().isoformat()


def _hk_now() -> datetime:
    """Now on the Hong Kong wall clock, with no offset attached: the clock a
    card's off times and HKJC's instruments are written on, so the three
    compare directly whatever zone the machine is set to."""
    return _clock().astimezone(_HK).replace(tzinfo=None)

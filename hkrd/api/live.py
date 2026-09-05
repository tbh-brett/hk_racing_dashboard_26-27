"""Conditional replies for the two pages that follow a live market.

A market captured every minute is only worth capturing if something reads it
every minute. Race Day and Model Analysis are snapshots from page load: a card
opened ten minutes before the off shows a price from ten minutes before the
off, which is precisely when it stops being true.

So both pages poll. This is what stops that costing anything.

**How stale the screen can be is bounded by the capture, not by a second
schedule.** `query/market.poll_seconds` returns half the capture interval for
that race, read off the same ladder `jobs/scrape_odds` captures on. Overnight
that is thirty minutes; in the last ten minutes before the off it is fifteen
seconds. The server tells the browser when to come back, on every reply
including a 304, so there is one schedule and it lives with the data.

**A poll that finds nothing new costs two indexed `max()` lookups.** The Race
Day card is 36 KB and ~220ms to assemble; the poll that discovers it has not
changed answers in under a millisecond and sends no body. That is the whole
design: the expensive path runs only when the data actually moved.

ETag rather than a timestamp comparison the client does, because the client
must not be the one deciding what counts as new — and because a browser
already knows how to hold and return an ETag.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from hkrd.query import market as market_q

__all__ = ["conditional", "POLL_HEADER"]

# Named rather than inline because the browser reads it on a 304, where there
# is no body to put it in.
POLL_HEADER = "X-Poll-After"


def conditional(request: Request, *, date: str, race_no: int | None,
                build: Callable[[], dict[str, Any]],
                variant: object = None) -> Response:
    """Answer a poll: 304 if nothing changed, otherwise `build()`.

    `build` is not called at all on the unchanged path — that is the point of
    passing it as a callable rather than a result.

    `variant` is anything OTHER than the data that changes the answer — the
    blend's weight is the only one today. Without it, moving the weight slider
    and asking with the previous tag would be answered "nothing changed" and
    the reader would see the old weight's numbers under the new label. The URL
    differs, so a spec-following cache would not confuse them; a client
    carrying its own tag across the change would, and being right only when
    the client is careful is not being right.

    A poll interval rides on both answers. It is advisory: a client that
    ignores it still gets correct data, and a client that honours it stops
    asking hourly questions every thirty seconds.
    """
    current, captured_at, after = market_q.poll_state(date, race_no)
    if variant is not None:
        current = f"{current}|{variant}"
    headers = {POLL_HEADER: str(after),
               # Two different caches must not hold this. The browser's own
               # would serve a stale price with no way to notice, and a shared
               # one would serve one viewer's meeting to another.
               "Cache-Control": "no-cache, private"}
    etag = f'W/"{current}"'
    # A weak validator, and honestly so: it says "this page would render the
    # same", not "these bytes are identical". Nothing here is byte-stable —
    # dict ordering is not a promise — and a strong tag would claim it is.
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={**headers, "ETag": etag})

    payload = build()
    payload["poll_after"] = after
    # Carried in the body as well, so the page can say when the price on
    # screen was true rather than when it was fetched. `setdefault`, because a
    # payload that already names its own capture time is the more specific
    # answer -- the card's is the race's, this one is the meeting's.
    payload.setdefault("captured_at", captured_at)
    return JSONResponse(payload, headers={**headers, "ETag": etag})

"""Doubles — one bet spanning two consecutive races.

Leg N couples race N with race N+1, so a card of R races offers R-1 legs. Both
legs must win; a second-leg selection that runs second pays a consolation.

WHY IT IS WORTH CAPTURING. It is the only market that prices a race before that
race's own win pool has matured. Divide a double by its first-leg win odds and
what is left is an implied price for the second-leg runner, funded by different
money and available hours earlier than the win market on that race. Two
independent reads on the same race disagreeing is a fact no single pool can
produce.

It rides on `ingest/odds.py`'s endpoint, session and meeting check rather than
repeating them: same GraphQL call shape, same guard that every pool id begins
with the meeting id HKJC published for the date asked for.

TWO THINGS THIS POOL DOES DIFFERENTLY, and both would fail silently if assumed:

**The combination separator is `/`, not `,`.** A quinella node is `"02,04"`; a
double is `"02/04"`. The site's own front end says so —
`"DBL" === o && (v = e.combString.split("/"))` in its bundle — and
`odds._combination`, which splits on a comma, returns nothing at all for a
double rather than erroring. Verified against the published code, not guessed:
no double pool was selling while this was written, so the format could not be
read off live data.

**The pair is ORDERED.** `odds.pair_rows` sorts a quinella smallest-first so
one bet cannot have two contradictory rows. Doing that here would be a bug:
first-leg 3 with second-leg 7 is a different bet, at a different price, from
7 then 3, and sorting would collapse two prices into whichever was written
last.
"""
from __future__ import annotations

from datetime import datetime
from collections.abc import Sequence
from typing import Any

from hkrd.ingest.odds import (GRAPHQL_URL, OddsError, _horse_no, _odds, _post,
                              _POOLS_QUERY, meeting_id)

__all__ = ["DOUBLE_POOL", "COMB_SEPARATOR", "double_rows", "fetch_doubles",
           "leg_races"]

DOUBLE_POOL = "DBL"

# Not a comma. See the module docstring: the site splits a DBL combString on
# this and on nothing else, and a comma split silently yields no rows.
COMB_SEPARATOR = "/"


def leg_races(leg: dict[str, Any] | None) -> tuple[int, int] | None:
    """The two races a leg couples, as HKJC itself publishes them.

    Read from `leg { number races }` rather than computed as N and N+1. The
    arithmetic happens to hold on every card seen so far, but the endpoint
    states it, and a card that loses a race mid-meeting is exactly the case
    where a derived +1 would quietly file a leg against the wrong pair.
    """
    races = ((leg or {}).get("races")) or []
    if len(races) != 2:
        return None
    try:
        return int(races[0]), int(races[1])
    except (TypeError, ValueError):
        return None


def _combination(comb: Any) -> list[int]:
    """The two horse numbers a double node is about: '02/04' -> [2, 4]."""
    parts = [p.strip() for p in str(comb or "").split(COMB_SEPARATOR)]
    if len(parts) != 2:
        return []
    nos = [_horse_no(p) for p in parts]
    return [n for n in nos if n is not None] if all(
        n is not None for n in nos) else []


def double_rows(meeting: dict[str, Any], *, date: str, venue: str,
                expect_id: str, captured_at: str,
                legs: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """Every priced double combination in one meeting's reply.

    `expect_id` is checked on every pool for the same reason `pools_to_payloads`
    checks it: HKJC answers a date it has no meeting for with whatever meeting
    is current, and storing that would file one card's prices under another's
    numbers in a table nothing ever deletes from.
    """
    pools = meeting.get("pmPools")
    if pools is None:
        raise OddsError(f"{date} {venue}: reply has no pmPools — shape changed")

    wanted = set(legs) if legs else None
    out: list[dict[str, Any]] = []
    seen_legs = 0
    unparsed = 0
    for pool in pools:
        pid = str(pool.get("id") or "")
        if not pid.startswith(expect_id):
            raise OddsError(
                f"{date} {venue}: pool {pid!r} does not belong to meeting "
                f"{expect_id!r} — the endpoint answered about a different "
                f"meeting")
        if str(pool.get("oddsType") or "") != DOUBLE_POOL:
            continue

        pair = leg_races(pool.get("leg"))
        if pair is None:
            raise OddsError(
                f"{date} {venue}: {DOUBLE_POOL} pool {pid!r} does not name two "
                f"races in its leg ({(pool.get('leg') or {}).get('races')!r}); "
                f"a double couples exactly two")
        leg_no = (pool.get("leg") or {}).get("number")
        if leg_no is None:
            raise OddsError(f"{date} {venue}: {DOUBLE_POOL} pool {pid!r} has no leg number")
        leg_no = int(leg_no)
        if wanted is not None and leg_no not in wanted:
            continue
        seen_legs += 1

        for node in pool.get("oddsNodes") or []:
            combo = _combination(node.get("combString"))
            if len(combo) != 2:
                unparsed += 1
                continue
            first, second = combo
            out.append({
                "race_date": date, "leg_no": leg_no,
                "race_first": pair[0], "race_second": pair[1],
                "horse_first": first, "horse_second": second,
                "captured_at": captured_at,
                "odds": _odds(str(node.get("oddsValue") or "").strip()),
            })

    if seen_legs and unparsed and not out:
        # Every node present and none of them readable is the separator
        # changing under us. It is the one failure this module is most exposed
        # to, so it raises rather than reporting an empty capture.
        raise OddsError(
            f"{date} {venue}: {unparsed} {DOUBLE_POOL} nodes and not one "
            f"parsed — the combination separator is no longer "
            f"{COMB_SEPARATOR!r}")
    return out


def fetch_doubles(date: str, venue: str, legs: Sequence[int] | None = None, *,
                  session=None) -> list[dict[str, Any]]:
    """One meeting's doubles, in one request.

    A separate call from `odds.fetch_meeting` rather than another entry in its
    `POOLS`: that function requires every pool it reads to name exactly one
    race and raises otherwise, which is the right rule for the pools it serves
    and the opposite of what a double is.
    """
    expect = meeting_id(date, venue, session=session)
    if expect is None:
        raise OddsError(
            f"HKJC lists no meeting for {date} {venue}. It answers this query "
            f"with whatever meeting is current rather than with nothing, so "
            f"the capture is refused")

    captured_at = datetime.now().isoformat(timespec="seconds")
    body = _post(_POOLS_QUERY,
                 {"date": date, "venueCode": venue,
                  "oddsTypes": [DOUBLE_POOL]},
                 operation="racing", session=session)
    meetings = ((body.get("data") or {}).get("raceMeetings")) or []
    if len(meetings) != 1:
        raise OddsError(
            f"{date} {venue}: expected one meeting in the reply, got "
            f"{len(meetings)}")
    return double_rows(meetings[0], date=date, venue=venue, expect_id=expect,
                       captured_at=captured_at, legs=legs)


# Kept so the module reads as one place that knows where these come from, the
# same way odds.py exposes its own endpoint.
ENDPOINT = GRAPHQL_URL

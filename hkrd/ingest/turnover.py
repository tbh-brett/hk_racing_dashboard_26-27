"""Pool turnover — how much money is in each pool, per race, over time.

Odds are a ratio. They say how the money is DIVIDED and nothing about how much
of it there is, so a price of 4.0 in a $91,000 double leg and a price of 4.0 in
a $4,300,000 win pool are the same number describing amounts forty times apart.
Turnover is the missing denominator, and it is what turns "shortened from 4.0
to 3.5" into "$210,000 arrived on this horse".

In a pari-mutuel pool that conversion is arithmetic rather than an assumption:
the dividend is `pool x (1 - takeout) / stake`, so a runner's de-vigged share IS
its share of the money. De-vigging a bookmaker's board is a model of what it
believes; de-vigging a tote is division.

TURNOVER IS A DENOMINATOR, NOT A TIP. On 2026-09-06 race 3 held $4,288,122 of
win money against roughly $500,000 in every other race on the card — not
because the crowd had found value, but because KA YING RISING was 1.0 in a
six-horse field. Raw turnover follows field size, favourite shortness and race
profile. Nothing here ranks a horse by the money on it.

HKJC calls it `investment`, and it is not on the pools query `ingest/odds.py`
uses: that one asks for `oddsNodes`, this one for `investment`, and the
endpoint whitelists whole queries rather than fields, so it is a second
request with its own text. Both are the site's own — this query is lifted
verbatim from the bundle that renders the Pool Turnover page, because a query
edited even in its whitespace comes back WHITELIST_ERROR.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from hkrd.ingest.odds import GRAPHQL_URL, OddsError, _post, meeting_id

__all__ = ["POOLS", "TOTAL_POOL", "investment_rows", "fetch_turnover",
           "INVESTMENT_QUERY"]

# Every pool the site itself asks for turnover on, in its own order. Wider than
# the four `odds.POOLS` prices, because the question here is how much money the
# race is drawing in total and a pool left out of that is a hole in the answer.
POOLS = ("WIN", "PLA", "QIN", "QPL", "CWA", "CWB", "CWC", "IWN", "FCT", "TCE",
         "TRI", "FF", "QTT", "DBL", "TBL", "DT", "TT", "SixUP")

# The meeting-wide figure the same reply carries. Stored against race 0, which
# is not a race: it is the whole card, and giving it a real race number would
# make it double-count in any sum over the meeting.
TOTAL_POOL = "MEETING_TOTAL"

# Lifted verbatim from bet.hkjc.com's own bundle. The endpoint accepts only
# queries it recognises -- a trimmed but perfectly valid subset of this comes
# back `WHITELIST_ERROR` -- so the indentation and the `poolInvs:` alias are
# load-bearing and must not be tidied.
INVESTMENT_QUERY = """query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
          raceMeetings(date: $date, venueCode: $venueCode)
          {
            totalInvestment
            poolInvs: pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
              id
              leg {
                number
                races
              }
              status
              sellStatus
              oddsType
              investment
              mergedPoolId
              lastUpdateTime
            }
          }
      }
  """


def _money(value: Any) -> float | None:
    """An amount, or None where the pool has not taken any yet.

    Arrives as a STRING ('0', '1481817'), and null on a pool that has not
    opened. Null is not zero: one means the market is shut and the other means
    it is open and nobody has bet, and a turnover series that cannot tell them
    apart reports money arriving the moment a card opens.
    """
    if value is None:
        return None
    s = str(value).replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        amount = float(s)
    except ValueError:
        raise OddsError(f"turnover: unrecognised amount {value!r}") from None
    if amount < 0:
        raise OddsError(f"turnover: negative amount {value!r}")
    return amount


def investment_rows(meeting: dict[str, Any], *, date: str, venue: str,
                    expect_id: str, captured_at: str) -> list[dict[str, Any]]:
    """One row per pool per race, plus the meeting total.

    A cross-race pool — a double, a treble, a double trio — is filed under the
    FIRST race of its leg, which is where HKJC's own turnover page shows it.
    `leg.races` names them, so the choice is recorded rather than derived.
    """
    pools = meeting.get("poolInvs")
    if pools is None:
        raise OddsError(
            f"{date} {venue}: reply has no poolInvs — shape changed")

    out: list[dict[str, Any]] = []
    for pool in pools:
        pid = str(pool.get("id") or "")
        if not pid.startswith(expect_id):
            raise OddsError(
                f"{date} {venue}: pool {pid!r} does not belong to meeting "
                f"{expect_id!r} — the endpoint answered about a different "
                f"meeting")
        kind = str(pool.get("oddsType") or "").strip()
        if not kind:
            continue
        races = ((pool.get("leg") or {}).get("races")) or []
        if not races:
            # No leg at all: nothing says which race it belongs to, and
            # guessing would attribute money to a race that did not take it.
            continue
        try:
            race_no = int(races[0])
        except (TypeError, ValueError):
            continue
        out.append({
            "race_date": date, "race_no": race_no, "pool": kind,
            "captured_at": captured_at, "turnover": _money(pool.get("investment")),
            # Kept because a merged pool's money is reported once, under the id
            # it was merged into -- Quartet and First 4 are one line on the
            # page for that reason -- and a sum that does not know which rows
            # are merged will not reconcile with the meeting total.
            "merged_into": pool.get("mergedPoolId"),
        })

    total = _money(meeting.get("totalInvestment"))
    if total is not None:
        out.append({
            "race_date": date, "race_no": 0, "pool": TOTAL_POOL,
            "captured_at": captured_at, "turnover": total, "merged_into": None,
        })
    return out


def fetch_turnover(date: str, venue: str, *, session=None
                   ) -> list[dict[str, Any]]:
    """One meeting's pool turnover, in one request."""
    expect = meeting_id(date, venue, session=session)
    if expect is None:
        raise OddsError(
            f"HKJC lists no meeting for {date} {venue}. It answers this query "
            f"with whatever meeting is current rather than with nothing, so "
            f"the capture is refused")

    captured_at = datetime.now().isoformat(timespec="seconds")
    body = _post(INVESTMENT_QUERY,
                 {"date": date, "venueCode": venue, "raceNo": 0,
                  "oddsTypes": list(POOLS)},
                 operation="racing", session=session)
    meetings = ((body.get("data") or {}).get("raceMeetings")) or []
    if len(meetings) != 1:
        raise OddsError(
            f"{date} {venue}: expected one meeting in the reply, got "
            f"{len(meetings)}")
    return investment_rows(meetings[0], date=date, venue=venue,
                           expect_id=expect, captured_at=captured_at)


ENDPOINT = GRAPHQL_URL

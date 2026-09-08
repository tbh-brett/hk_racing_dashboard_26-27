"""Live odds — win, place, and the quinella pools.

Read from the JSON endpoint bet.hkjc.com's own front end reads, not from the
page it renders. Parsing is kept separate from fetching so the shapes can be
tested without a network.

WHY NOT A BROWSER. The first version of this module drove Chromium through
Playwright, because the odds are rendered by JavaScript and no fetch of the
HTML can see them. That was true and it still is — but the page is a
single-page app, and the thing it renders from is `info.cld.hkjc.com/graphql`,
declared in the site's own /Config/GlobalConfig.js. Reading that directly is
the same data one step earlier, and it removes, in order: a 400MB browser
download the deploy image never carried (so the cron line was never armed and
the table stayed empty); ~11 page loads and ~30s per capture, now one request
and under a second; the stale-DOM guard, because there is no DOM to go stale;
and the bounding-box reconstruction of a triangular matrix, because the pairs
arrive as `{"combString": "02,04", "oddsValue": "7.9"}`.

WHAT REPLACES THE STALE-DOM GUARD. `raceMeetings(date:, venueCode:)` does NOT
answer "no such meeting" — measured against the live endpoint, asking for a
date and venue HKJC has no meeting for returns the CURRENT meeting instead,
with no indication that the filter was ignored. Asking for 2026-09-08 ST on
2026-09-04 returned the 2026-09-05 S1 simulcast card. Storing that would put
one meeting's prices under another's race numbers, forever, in the one table
nothing ever deletes from. So every pool is checked against the meeting id
HKJC itself publishes for the date and venue asked for, and a capture that
does not match is refused rather than stored. Same lesson as the old DOM guard —
verify the thing you were handed is the thing you asked for — against a
different mechanism.

The rule that governs this module: NOTHING here ever deletes a snapshot. The
old scraper called prune_old_snapshots(keep=20) after every capture, and 17
meetings survived an entire season. Odds movement is the most informative
signal in the dataset -- the favourite changes between morning and post time in
44% of races -- and it is the only thing here that cannot be reconstructed after
the fact. A season is a few hundred megabytes.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from hkrd.ingest._client import FetchError, fetch_json, urls

__all__ = ["OddsError", "fetch_race", "fetch_meeting", "meeting_id",
           "parse_snapshot", "snapshot_rows", "pair_rows", "pools_to_payloads",
           "POOLS", "GRAPHQL_URL", "SELLING", "NO_PRICE"]

GRAPHQL_URL = urls.graphql

_NUMERIC = re.compile(r"^\d+(\.\d+)?$")


class OddsError(ValueError):
    """A snapshot could not be read. Names what was wrong."""


# HKJC's "no meaningful price" number. The tote board has four digits and no
# way to say "nothing", so an open pool with no money in it quotes 999.0 on
# every runner. It is not a 999-1 chance: on 2026-09-09 the first capture, at
# 12:01 the day before racing, was 86 rows of 999.0 across eight races, and the
# 13:01 capture had real prices on all of them. Not one of the 8,716 rows
# captured on a raced day is 999.0.
#
# Stored as a price it is poison, because it becomes the FIRST price: every
# runner then reads as a 98% firmer on the movement strip, which is what the
# whole card showed. Stored as None it is what it is — the pool was open and
# had not been bet into yet.
NO_PRICE = 999.0


def _odds(value: Any) -> float | None:
    """A price, or None where none was offered.

    Scratched runners and pre-market races show '---', 'SCR' or blank; those
    are real answers and must not become zero. So is 999.0 — see NO_PRICE.
    """
    s = str(value or "").strip()
    if not s or not _NUMERIC.match(s):
        return None
    v = float(s)
    if v >= NO_PRICE:
        return None
    return v if v > 0 else None


def _horse_no(value: Any) -> int | None:
    s = str(value or "").strip()
    return int(s) if s.isdigit() else None


def parse_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise one captured payload.

    Accepts the shape the fetch produces: win/place under `odds`, and pair
    odds under `qin_odds` / `qpl_odds`. The legacy cache on disk holds this
    same shape, which is why `jobs/import_legacy_odds` reads it unchanged and
    why the shape survived the move off Playwright.
    """
    date = str(payload.get("date") or "").strip()
    race_no = payload.get("race_no")
    if not date or race_no is None:
        raise OddsError(f"snapshot missing date or race_no: {list(payload)[:6]}")

    captured = str(payload.get("scraped_at") or "").strip()
    if not captured:
        raise OddsError(f"{date} R{race_no}: snapshot has no scraped_at timestamp")
    # A snapshot without a trustworthy timestamp is worthless: the whole value
    # of this table is knowing WHEN a price was true.
    try:
        datetime.fromisoformat(captured)
    except ValueError:
        raise OddsError(f"{date} R{race_no}: unparseable scraped_at {captured!r}") from None

    return {
        "race_date": date,
        "race_no": int(race_no),
        "venue": payload.get("venue"),
        "captured_at": captured,
        "runners": payload.get("odds") or [],
        "qin": payload.get("qin_odds") or [],
        "qpl": payload.get("qpl_odds") or [],
        # Empty for a legacy payload off disk, which predates the field. An
        # unknown status must read as "no answer", never as "shut".
        "sell_status": str(payload.get("sell_status") or "").strip(),
    }


def snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Win and place prices, one row per runner."""
    out: list[dict[str, Any]] = []
    for r in snapshot["runners"]:
        no = _horse_no(r.get("no"))
        if no is None:
            continue
        out.append({
            "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
            "horse_no": no, "captured_at": snapshot["captured_at"],
            "win_odds": _odds(r.get("win")), "place_odds": _odds(r.get("place")),
        })
    return out


def pair_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Quinella and quinella-place pools.

    Pairs are stored with horse_a < horse_b so a pair has one representation,
    not two that can disagree.
    """
    out: list[dict[str, Any]] = []
    for pool, entries in (("QIN", snapshot["qin"]), ("QPL", snapshot["qpl"])):
        for e in entries:
            a, b = _horse_no(e.get("a")), _horse_no(e.get("b"))
            if a is None or b is None or a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            out.append({
                "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
                "pool": pool, "horse_a": lo, "horse_b": hi,
                "captured_at": snapshot["captured_at"], "odds": _odds(e.get("odds")),
            })
    return out


# ── fetching ─────────────────────────────────────────────────────────────────
# The endpoint whitelists queries: a syntactically valid query it has not seen
# is refused with `Internal server error - WHITELIST_ERROR`. So these two are
# reproduced from the site's own bundle CHARACTER FOR CHARACTER, including the
# fields nothing here reads. Editing one to drop an unused field does not make
# it smaller, it makes it fail. Change them only by re-reading the bundle.

# Every meeting HKJC currently knows about, with the id it files each one
# under. The only query here that answers "does this meeting exist", which is
# the question the date/venue filter on the odds query silently does not.
_MEETINGS_QUERY = """query racingChanges {
  raceMeetings {
    id
    venueCode
    date
    changeHistories {
      type
      time
      raceNo
      runnerNo
      horseName_ch
      horseName_en
      jockeyName_ch
      jockeyName_en
      scratchHorseName_ch
      scratchHorseName_en
      handicapWeight
      scrResvIndicator
    }
  }
}"""

# Every pool of a meeting in one request. `raceNo` is accepted but not sent:
# omitting it returns all races, so a whole card costs one round trip instead
# of eleven.
_POOLS_QUERY = """query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      guarantee
      minTicketCost
      name_en
      name_ch
      leg {
        number
        races
      }
      cWinSelections {
        composite
        name_ch
        name_en
        starters
      }
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
        bankerOdds {
          combString
          oddsValue
        }
      }
    }
  }
}"""

# The four pools this dashboard prices. WIN and PLA feed every probability in
# the system; QIN and QPL are what the coverage rule actually bets into.
POOLS = ("WIN", "PLA", "QIN", "QPL")

# HKJC's own word for "betting is open on this pool". Every other value, and
# the absence of the field, means it is not selling — which before the off is a
# market that has not opened, and after it is a race that has been run. The
# capture uses the difference to stop; see `jobs/scrape_odds._shut`.
SELLING = "START_SELL"

# HKJC does not operate a quinella place pool on a field of fewer than seven
# declared starters. Measured on both meetings open when this was written:
# 2026-09-05 S1 race 2 (six runners) and 2026-09-06 ST race 3 (six runners)
# each return WIN, PLA and QIN and no QPL at all. That absence is a fact about
# the race, not a failed capture, so it is not reported as one.
QPL_MIN_FIELD = 7


def _post(query: str, variables: dict[str, Any], *, operation: str,
          session=None) -> dict[str, Any]:
    """One GraphQL call, with the shared session, throttle and retries."""
    body = {"operationName": operation, "variables": variables, "query": query}
    try:
        return fetch_json(GRAPHQL_URL, body, session=session)
    except FetchError as exc:
        raise OddsError(f"odds endpoint: {exc}") from exc


def meeting_id(date: str, venue: str, *, session=None) -> str | None:
    """The id HKJC files this meeting under, or None if it has no such meeting.

    Every pool id begins with it, which is what makes a capture checkable. Two
    formats have been observed and both are returned verbatim rather than
    parsed: `20260905S1` once a meeting is open for betting, and
    `MTG_20260906_0001` while it is still only declared. Nothing here depends
    on which, only on the prefix matching.
    """
    body = _post(_MEETINGS_QUERY, {}, operation="racingChanges", session=session)
    meetings = ((body.get("data") or {}).get("raceMeetings")) or []
    if not meetings:
        raise OddsError(
            "the meetings list came back empty — HKJC lists no meeting at all, "
            "which it does not do even between seasons, so the shape has "
            "probably changed")
    for m in meetings:
        # `date` arrives as a plain YYYY-MM-DD here, but slice defensively:
        # the same field is an ISO timestamp elsewhere in the schema.
        if str(m.get("date") or "")[:10] == date and m.get("venueCode") == venue:
            found = str(m.get("id") or "").strip()
            if not found:
                raise OddsError(f"{date} {venue}: meeting listed with no id")
            return found
    return None


def _combination(comb: Any) -> list[int]:
    """The horse numbers a pool node is about.

    '02' for a win price, '02,04' for a pair. Zero-padded, comma separated,
    and never assumed to be two — a shape this does not recognise is dropped
    by the caller rather than guessed at.
    """
    parts = [p.strip() for p in str(comb or "").split(",")]
    nos = [_horse_no(p) for p in parts]
    return [n for n in nos if n is not None] if all(
        n is not None for n in nos) and parts != [""] else []


def pools_to_payloads(meeting: dict[str, Any], *, date: str, venue: str,
                      expect_id: str, captured_at: str,
                      races: Sequence[int] | None = None
                      ) -> list[dict[str, Any]]:
    """Split one meeting's pools into the per-race payloads the store consumes.

    `expect_id` is the meeting id from `meeting_id`. Every pool must carry it
    as a prefix; one that does not means the endpoint answered about a
    different meeting than the one asked for, and the whole capture is refused.
    Partially storing it would be worse than storing none of it, because the
    races that did match would look like a complete capture.
    """
    pools = meeting.get("pmPools")
    if pools is None:
        raise OddsError(f"{date} {venue}: reply has no pmPools — shape changed")

    wanted = set(races) if races else None
    by_race: dict[int, dict[str, Any]] = {}
    seen_types: dict[int, set[str]] = {}
    field_size: dict[int, int] = {}

    for pool in pools:
        pid = str(pool.get("id") or "")
        if not pid.startswith(expect_id):
            raise OddsError(
                f"{date} {venue}: pool {pid!r} does not belong to meeting "
                f"{expect_id!r} — the endpoint answered about a different "
                f"meeting and storing it would file one card's prices under "
                f"another's race numbers")

        kind = str(pool.get("oddsType") or "")
        if kind not in POOLS:
            continue
        legs = ((pool.get("leg") or {}).get("races")) or []
        if len(legs) != 1:
            # A multi-leg pool (double, treble) has no single race to file
            # under. None is requested, so one arriving means the reply
            # changed shape and that gets said rather than guessed at.
            raise OddsError(
                f"{date} {venue}: {kind} pool {pid!r} spans races {legs}; "
                f"only single-race pools are requested")
        race_no = int(legs[0])
        if wanted is not None and race_no not in wanted:
            continue

        snap = by_race.setdefault(race_no, {
            "scraped_at": captured_at, "date": date, "venue": venue,
            "race_no": race_no, "url": GRAPHQL_URL,
            "last_update": "", "race_info": "",
            # What HKJC says about the WIN pool: START_SELL while betting is
            # open, something else once it has shut. This is the only signal
            # available that knows about a delayed start, and it is what stops
            # the capture rather than a fixed number of minutes past a
            # SCHEDULED off time that the race did not necessarily keep to.
            "sell_status": "",
            "odds": [], "qin_odds": [], "qpl_odds": [], "notes": [],
        })
        seen_types.setdefault(race_no, set()).add(kind)
        # HKJC's own clock for this pool, kept beside our capture time rather
        # than instead of it: `captured_at` is what makes rows from one run
        # group together, and these differ by a second or two between pools.
        snap["last_update"] = max(
            snap["last_update"], str(pool.get("lastUpdateTime") or ""))

        runners: dict[int, dict[str, str]] = {
            _horse_no(r["no"]): r for r in snap["odds"]}       # type: ignore[misc]
        for node in pool.get("oddsNodes") or []:
            combo = _combination(node.get("combString"))
            value = str(node.get("oddsValue") or "").strip()
            if kind in ("WIN", "PLA"):
                if len(combo) != 1:
                    continue
                no = combo[0]
                row = runners.get(no)
                if row is None:
                    row = {"no": str(no), "horse": "", "win": "", "place": ""}
                    runners[no] = row
                    snap["odds"].append(row)
                row["win" if kind == "WIN" else "place"] = value
            else:
                if len(combo) != 2:
                    continue
                a, b = combo
                key = "qin_odds" if kind == "QIN" else "qpl_odds"
                snap[key].append({"a": str(a), "b": str(b), "odds": value})

        if kind == "WIN":
            field_size[race_no] = len(pool.get("oddsNodes") or [])
            # `sellStatus` where it is offered, `status` where it is not. Both
            # were observed: an unopened meeting answers DEFINED / STOP_SELL,
            # an open one START_SELL / START_SELL.
            snap["sell_status"] = str(pool.get("sellStatus")
                                      or pool.get("status") or "").strip()

    for race_no, snap in by_race.items():
        snap["odds"].sort(key=lambda r: int(r["no"]))
        snap["n_runners"] = len(snap["odds"])
        types = seen_types.get(race_no, set())
        for kind in ("WIN", "PLA", "QIN"):
            if kind not in types:
                snap["notes"].append(f"no {kind} pool in the reply")
        # QPL is genuinely absent below seven declared starters, so it is only
        # worth reporting on a field big enough to have one.
        if "QPL" not in types and field_size.get(race_no, 0) >= QPL_MIN_FIELD:
            snap["notes"].append("no QPL pool in the reply")
        if not any(_odds(r.get("win")) for r in snap["odds"]):
            # Every pool present and every price blank is what a declared but
            # not yet open market looks like. Named, so it does not read as a
            # broken capture -- and so a market that IS open and came back
            # blank does not read as a quiet one.
            snap["notes"].append("market not open yet — no prices offered")

    return [by_race[n] for n in sorted(by_race)]


def fetch_meeting(date: str, venue: str, races: Sequence[int] | None = None, *,
                  session=None) -> list[dict[str, Any]]:
    """Every requested race of one meeting, in one request.

    Returns the payloads `parse_snapshot` consumes. `races` filters what comes
    back; it does not change what is asked for, because the whole card costs
    the same single round trip as one race of it.

    Raises OddsError when HKJC has no such meeting. That is not the same as a
    meeting with no prices yet — a declared card that has not opened returns
    pools with no odds in them, which is a successful capture of nothing and
    is reported as such.
    """
    expect = meeting_id(date, venue, session=session)
    if expect is None:
        raise OddsError(
            f"HKJC lists no meeting for {date} {venue}. It answers this query "
            f"with whatever meeting is current rather than with nothing, so "
            f"the capture is refused instead of filing another card's prices "
            f"under this one's race numbers")

    captured_at = datetime.now().isoformat(timespec="seconds")
    body = _post(_POOLS_QUERY,
                 {"date": date, "venueCode": venue, "oddsTypes": list(POOLS)},
                 operation="racing", session=session)
    meetings = ((body.get("data") or {}).get("raceMeetings")) or []
    if len(meetings) != 1:
        raise OddsError(
            f"{date} {venue}: expected one meeting in the reply, got "
            f"{len(meetings)}")
    return pools_to_payloads(meetings[0], date=date, venue=venue,
                             expect_id=expect, captured_at=captured_at,
                             races=races)


def fetch_race(date: str, venue: str, race_no: int, *,
               session=None) -> dict[str, Any]:
    """One race's odds. A convenience around `fetch_meeting`, not a saving —
    the request is the same size either way."""
    got = fetch_meeting(date, venue, [race_no], session=session)
    if not got:
        raise OddsError(f"{date} {venue} R{race_no}: no pools for that race")
    return got[0]
